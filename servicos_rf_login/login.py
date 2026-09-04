"""Login nos Serviços da Receita Federal via Patchright com client_certificates.

Uso básico (lê certificado do .env):
    from servicos_rf_login import fazer_login
    p, context, page = fazer_login()

Uso por nome (busca em C:\\Certificados e lê a senha do senhas.json):
    from servicos_rf_login import fazer_login
    p, context, page = fazer_login(cert_name="Save Tecnologia")
    p, context, page = fazer_login(cert_name="DSR")       # match parcial
    p, context, page = fazer_login(cert_name="save tec")  # match fuzzy

Uso com caminho completo (planilha, formulário, etc.):
    from servicos_rf_login import fazer_login
    p, context, page = fazer_login(
        cert_pfx_path="C:\\\\Certificados\\\\empresa.pfx",
        cert_pfx_passphrase="senha123",
    )

Prioridade de resolução do certificado:
    1. cert_pfx_path + cert_pfx_passphrase (explícito)
    2. cert_name (busca em C:\\Certificados + senhas.json)
    3. CERT_NAME do .env (busca em C:\\Certificados + senhas.json)
    4. CERT_PFX_PATH + CERT_PFX_PASSPHRASE do .env (caminho direto)
"""
import difflib
import json
import os
import random as _random
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()

from patchright.sync_api import sync_playwright
from resolvedor_captcha import (
    TIPO_BOLA,
    TIPO_GRADE,
    TIPO_GRADE_FUSED,
    TIPO_NENHUM,
    abrir_desafio,
    captcha_presente,
    detectar_tipo_captcha,
    solve_hcaptcha,
)

from .log_manager import registrar_erro


def host_da_url(url) -> str:
    """Somente o hostname. Nunca path, query ou fragment.

    A URL do fluxo OAuth do gov.br carrega `state`, `nonce` e `code_challenge`
    na query — e, em algumas etapas, o identificador do contribuinte. Nada
    disso tem valor operacional: o que se acompanha, esperando o
    redirecionamento, e em QUAL host o navegador esta.

    Devolve "?" quando nao ha host legivel, para que o log nunca vire uma
    excecao nem caia no fallback de imprimir a URL inteira.
    """
    try:
        return urlsplit(str(url or "")).hostname or "?"
    except ValueError:
        return "?"

try:
    from .cert_dialog import selecionar_certificado_no_dialogo as _selecionar_cert_dialog
    _CERT_DIALOG_OK = True
except Exception:
    _CERT_DIALOG_OK = False

# URL de login dos Serviços da Receita Federal (gov.br SSO)
SERVICOS_RF_URL = "https://servicos.receitafederal.gov.br/"

# Domínio de sucesso — quando a URL contiver isso, o login foi concluído
SERVICOS_RF_DOMAIN = "servicos.receita.fazenda.gov.br"

# Pasta padrão onde os certificados e senhas.json ficam armazenados
CERT_DIR = Path(r"C:\Certificados")

# Origens para as quais o certificado será apresentado
CERT_ORIGINS = [
    "https://certificado.sso.acesso.gov.br",
    "https://sso.acesso.gov.br",
    "https://acesso.gov.br",
    "https://cav.receita.fazenda.gov.br",
    "https://solucoes.receita.fazenda.gov.br",
    "https://sinac.cav.receita.fazenda.gov.br",
    "https://servicos.receita.fazenda.gov.br",
    "https://restituicao.receita.fazenda.gov.br",
    "https://www.restituicao.receita.fazenda.gov.br",
    "https://cte.fazenda.gov.br",
    "https://www.cte.fazenda.gov.br",
    "https://nfe.fazenda.gov.br",
    "https://www.nfe.fazenda.gov.br",
    "https://receita.fazenda.gov.br",
    "https://www.receita.fazenda.gov.br",
]

# Seletores tentados em ordem para o botão "Seu certificado digital"
CERT_SELECTORS = [
    "#login-certificate",
    "a:has-text('Seu certificado digital')",
    "button:has-text('Seu certificado digital')",
    "text=Seu certificado digital",
    "[data-sso-type='certificate']",
]


def _build_auto_select_cert_flag(subject_cn: str = "") -> str:
    """Constrói --auto-select-certificate-for-urls filtrando pelo CN do cert selecionado.

    Em vez de passar o .pfx ao Patchright (cujo proxy TLS do Node falha com
    ICP-Brasil — SSL alert 40), o Chrome apresenta o certificado JÁ INSTALADO no
    Windows Certificate Store nativamente (CAPI). Com o CN definido, o Chrome
    escolhe exatamente o cert correto quando há múltiplos instalados, sem diálogo.
    Sem CN, usa filtro vazio (primeiro disponível).
    """
    subject_cn = (subject_cn or os.getenv("CERT_SUBJECT_CN", "")).strip()
    filt = {"SUBJECT": {"CN": subject_cn}} if subject_cn else {}
    patterns = [
        "https://[*.]acesso.gov.br",
        "https://[*.]receita.fazenda.gov.br",
        "https://[*.]fazenda.gov.br",
        "https://[*.]receitafederal.gov.br",
    ]
    entries = json.dumps(
        [{"pattern": p, "filter": filt} for p in patterns],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"--auto-select-certificate-for-urls={entries}"


# ---------------------------------------------------------------------------
# Resolução do certificado
# ---------------------------------------------------------------------------

def _carregar_senhas() -> dict[str, str]:
    """Carrega o mapeamento filename → senha do senhas.json em C:\\Certificados."""
    senhas_file = CERT_DIR / "senhas.json"
    if not senhas_file.exists():
        print("[cert] senhas.json não encontrado no diretório de certificados.")
        return {}
    try:
        return json.loads(senhas_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[cert] Erro ao ler senhas.json: {type(e).__name__}")
        return {}


def _listar_certs_disponiveis() -> list[str]:
    """Retorna os nomes dos arquivos .pfx/.p12 em C:\\Certificados."""
    if not CERT_DIR.exists():
        return []
    return [
        f.name
        for f in CERT_DIR.iterdir()
        if f.suffix.lower() in {".pfx", ".p12"}
    ]


def _buscar_cert_por_nome(nome: str) -> tuple[str, str] | tuple[None, None]:
    """Encontra o melhor certificado em C:\\Certificados que dê match com `nome`.

    Estratégia (em ordem de prioridade):
        1. Correspondência exata do stem (ex: "DSR" → "DSR.pfx")
        2. O stem do arquivo contém `nome` (ex: "save tec" contém "save")
           OU `nome` contém o stem do arquivo
        3. Match fuzzy via difflib (similaridade ≥ 0.4)

    Em todos os casos a comparação é case-insensitive e ignora espaços extras.
    A senha é lida automaticamente do senhas.json.

    Returns:
        (caminho_absoluto, senha) ou (None, None) se nenhum cert for encontrado.
    """
    certs = _listar_certs_disponiveis()
    if not certs:
        print("[cert] Nenhum certificado (.pfx/.p12) encontrado no diretório de certificados.")
        return None, None

    senhas = _carregar_senhas()
    nome_lower = nome.strip().lower()

    # ---- 1) Correspondência exata pelo stem ----
    for filename in certs:
        stem = Path(filename).stem.strip().lower()
        if stem == nome_lower:
            return _retornar_cert(filename, senhas)

    # ---- 2) Substring bidirecional ----
    # O nome informado está contido no stem do arquivo  →  "save" bate em "Save Tecnologia"
    # O stem do arquivo está contido no nome informado  →  "DSR" bate em "DSR backup"
    substring_matches = [
        fn for fn in certs
        if nome_lower in Path(fn).stem.strip().lower()
        or Path(fn).stem.strip().lower() in nome_lower
    ]
    if len(substring_matches) == 1:
        return _retornar_cert(substring_matches[0], senhas)
    if len(substring_matches) > 1:
        # Múltiplos matches de substring: escolhe o cujo stem é mais próximo (menor diferença de tamanho)
        substring_matches.sort(key=lambda fn: abs(len(Path(fn).stem) - len(nome)))
        print(f"[cert] Múltiplos matches ({len(substring_matches)}) para o nome "
              "informado; usando o mais próximo.")
        return _retornar_cert(substring_matches[0], senhas)

    # ---- 3) Match fuzzy (difflib) ----
    stems = [Path(fn).stem.strip().lower() for fn in certs]
    close = difflib.get_close_matches(nome_lower, stems, n=1, cutoff=0.4)
    if close:
        idx = stems.index(close[0])
        print("[cert] Certificado resolvido por correspondência aproximada.")
        return _retornar_cert(certs[idx], senhas)

    print(f"[cert] Nenhum certificado encontrado para o nome informado "
          f"({len(certs)} disponível(is)).")
    return None, None


def _retornar_cert(filename: str, senhas: dict) -> tuple[str, str] | tuple[None, None]:
    """Monta o caminho absoluto e busca a senha no senhas.json."""
    caminho = str(CERT_DIR / filename)
    senha = senhas.get(filename)
    if not senha:
        print("[cert] Senha não encontrada em senhas.json para o certificado indicado.")
        return None, None
    print("[cert] Certificado selecionado.")
    return caminho, senha


def _resolver_certificado(
    cert_pfx_path: str | None,
    cert_pfx_passphrase: str | None,
    cert_name: str | None,
    project_dir: Path,
) -> tuple[str, str] | tuple[None, None]:
    """Resolve o caminho e a senha do certificado.

    Prioridade:
        1. cert_pfx_path + cert_pfx_passphrase  (parâmetros explícitos)
        2. cert_name                             (busca em C:\\Certificados)
        3. CERT_NAME do .env                     (busca em C:\\Certificados)
        4. CERT_PFX_PATH + CERT_PFX_PASSPHRASE  (caminho direto no .env)
    """
    # 1) Parâmetros explícitos de caminho
    if cert_pfx_path and cert_pfx_passphrase:
        if not os.path.isfile(cert_pfx_path):
            print("[cert] Arquivo de certificado não encontrado.")
            return None, None
        print("[cert] Usando certificado PFX informado por parâmetro.")
        return cert_pfx_path, cert_pfx_passphrase

    # 2) Nome fornecido como parâmetro → busca em C:\Certificados
    if cert_name:
        path, pw = _buscar_cert_por_nome(cert_name)
        if path and pw:
            return path, pw

    # 3 e 4) Lê do .env
    load_dotenv(dotenv_path=project_dir / ".env", override=True)

    # 3) CERT_NAME no .env → busca em C:\Certificados
    name_env = os.environ.get("CERT_NAME")
    if name_env:
        path, pw = _buscar_cert_por_nome(name_env)
        if path and pw:
            return path, pw

    # 4) Caminho direto no .env
    path_env = os.environ.get("CERT_PFX_PATH")
    pass_env = os.environ.get("CERT_PFX_PASSPHRASE")
    if path_env and pass_env:
        if not os.path.isfile(path_env):
            print("[cert] Arquivo de certificado não encontrado.")
            return None, None
        print("[cert] Usando certificado PFX indicado pelo ambiente.")
        return path_env, pass_env

    print(
        "[cert] Nenhuma configuração de certificado encontrada. "
        "Forneça cert_name, cert_pfx_path+passphrase, ou configure o .env."
    )
    return None, None


# ---------------------------------------------------------------------------
# Helpers do navegador
# ---------------------------------------------------------------------------

def _configurar_download(user_data_dir: str) -> None:
    """Configura o diretório de download do perfil Chrome para a pasta Downloads do usuário."""
    downloads_dir = str(Path.home() / "Downloads")
    prefs_dir = Path(user_data_dir) / "Default"
    prefs_dir.mkdir(parents=True, exist_ok=True)
    prefs_file = prefs_dir / "Preferences"

    try:
        prefs = json.loads(prefs_file.read_text(encoding="utf-8")) if prefs_file.exists() else {}
    except Exception:
        prefs = {}

    prefs.setdefault("download", {})
    prefs["download"]["default_directory"] = downloads_dir
    prefs["download"]["prompt_for_download"] = False
    prefs["download"]["directory_upgrade"] = True
    prefs.setdefault("savefile", {})
    prefs["savefile"]["default_directory"] = downloads_dir
    prefs.setdefault("plugins", {})
    prefs["plugins"]["always_open_pdf_externally"] = True

    prefs_file.write_text(json.dumps(prefs), encoding="utf-8")
    print("[download] Diretório de download configurado.")


def _build_client_certificates(cert_path: str, cert_pass: str) -> list[dict]:
    """Monta a lista de client_certificates para todas as origens relevantes."""
    return [
        {"origin": origin, "pfxPath": cert_path, "passphrase": cert_pass}
        for origin in CERT_ORIGINS
    ]


def _clicar_certificado(page) -> bool:
    """Tenta clicar no botão 'Seu certificado digital' usando múltiplos seletores."""
    print("Procurando botão 'Seu certificado digital'...")
    for i, sel in enumerate(CERT_SELECTORS):
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=20_000 if i == 0 else 2_000)
            print(f"  -> match com: {sel}")
            loc.click()
            return True
        except Exception:
            continue
    print("  -> botão 'Seu certificado digital' não encontrado.")
    return False


def _try_solve_captcha(page, etapa: str, max_attempts: int = 3) -> bool:
    """Tenta resolver o hCaptcha até `max_attempts` vezes.

    Move o mouse uma única vez antes de resolver para evitar detecção de automação.
    """
    print(f"[{etapa}] Verificando hCaptcha (até {max_attempts} tentativas)...")
    for tentativa in range(1, max_attempts + 1):
        try:
            resultado = solve_hcaptcha(page)
            if resultado:
                print(f"[{etapa}] tentativa {tentativa}/{max_attempts}: OK (resolvido ou ausente).")
                return True
            print(f"[{etapa}] tentativa {tentativa}/{max_attempts}: solver retornou False.")
        except Exception as e:
            print(f"[{etapa}] tentativa {tentativa}/{max_attempts}: "
                  f"{type(e).__name__}")
    return False


def _abortar(p, context):
    """Fecha o navegador e ENCERRA o Playwright antes de desistir do login.

    Sem isso, o loop de eventos do Playwright continua rodando nesta thread e a
    tentativa seguinte falha com:
        "It looks like you are using Playwright Sync API inside the asyncio loop."
    (o patchright checa asyncio.get_running_loop() ao iniciar). Também evita
    deixar janelas do Chrome órfãs a cada tentativa.

    Sempre retorna None, para uso direto em `return _abortar(p, context)`.
    """
    try:
        if context is not None:
            context.close()
    except Exception:
        pass
    try:
        if p is not None:
            p.stop()
    except Exception:
        pass
    return None


def _ja_logado(page) -> bool:
    """Retorna True se o usuário está realmente autenticado (avatar visível no portal)."""
    try:
        return page.locator('#avatar-dropdown-trigger').count() > 0
    except Exception:
        return False


def _fechar_popups_iniciais(page) -> None:
    """Fecha os popups que o portal exibe ao abrir: barra de cookies e tour de boas-vindas.

    1) Cookiebar — clica em "Aceitar".
    2) Tour de boas-vindas — se aparecer, clica em "Pular Tutorial".

    Tudo é best-effort: a ausência de qualquer popup não é erro.
    """
    # 1) Barra de cookies — botão "Aceitar"
    try:
        aceitar = page.locator('button.br-button.primary.small[aria-label="Aceitar"]').first
        aceitar.wait_for(state="visible", timeout=5_000)
        aceitar.click()
        print("[popup] Cookies aceitos.")
    except Exception:
        pass

    # 2) Tour de boas-vindas ("Primeira vez no Portal de Serviços?") — "Pular Tutorial"
    try:
        pular = page.locator('a.skip-tutorial-modal').first
        pular.wait_for(state="visible", timeout=4_000)
        pular.click()
        print("[popup] Tutorial pulado.")
    except Exception:
        pass


def _acesso_bloqueado(page) -> bool:
    """Detecta a mensagem de bloqueio por comportamento automatizado."""
    try:
        return page.locator("p:has-text('acesso foi bloqueado')").count() > 0
    except Exception:
        return False


def _recuperar_acesso_bloqueado(page) -> bool:
    """Volta à página anterior, re-clica 'Entrar com gov.br' e resolve captcha se aparecer.

    Retorna True se a recuperação foi concluída (captcha resolvido ou ausente).
    """
    print("[bloqueado] Mensagem de acesso bloqueado detectada. Retornando...")
    try:
        page.go_back(wait_until="domcontentloaded", timeout=15_000)
    except Exception as e:
        print(f"[bloqueado] go_back falhou ({type(e).__name__}). "
              "Recarregando URL de login...")
        try:
            page.goto(SERVICOS_RF_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

    print("[bloqueado] Re-clicando 'Entrar com gov.br'...")
    govbr_btn = page.locator('xpath=//*[@id="home-heading"]/div[1]/div/button').first
    try:
        govbr_btn.wait_for(state="visible", timeout=10_000)
        govbr_btn.click()
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
    except Exception as e:
        print("[bloqueado] Botão 'Entrar com gov.br' não encontrado após "
              f"go_back: {type(e).__name__}")
        return False

    return _try_solve_captcha(page, "captcha-pos-bloqueado")


# ---------------------------------------------------------------------------
# Representação de CNPJ
# ---------------------------------------------------------------------------

def _normalizar_cnpj(valor: str) -> str:
    """Remove formatação e retorna 14 dígitos."""
    return re.sub(r"\D", "", str(valor)).zfill(14)


# ──────────────────────────────────────────────────────────────────────────────
# Representação de perfil — pós-condição e intervenção humana
# ──────────────────────────────────────────────────────────────────────────────
#
# CLICAR EM "Representar" NÃO É SUCESSO. Numa execução real o portal apresentou
# um SEGUNDO captcha logo após o clique; o perfil não trocou, o código registrou
# "Representação enviada", seguiu adiante, não conseguiu capturar token e a API
# respondeu 401 — a planilha saiu só com cabeçalhos.
#
# A pós-condição vem do DOM observado do portal em três estados. Enquanto a
# sessão é pessoal — inclusive DURANTE o captcha — não existe `representacao-atual`.
# Ela só aparece quando a representação vigora, e traz dentro o documento
# representado. Por isso a prova é a presença DESSE elemento com o documento
# certo, e não avatar visível, modal fechado, clique realizado ou token obtido.
#
# Seletores deliberadamente semânticos. `_ngcontent-*`/`_nghost-*` são gerados
# pelo Angular a cada build e não são contrato de coisa alguma.
SEL_REPRESENTACAO_ATUAL = "representacao-atual"
SEL_DOCUMENTO_REPRESENTADO = "representacao-atual .ni-representacao"
SEL_PAPEL_REPRESENTACAO = "#avatar-dropdown-trigger .papel-representacao"

PAPEL_ESPERADO = "procurador"

# ALLOWLIST — política DESTE fluxo, não do resolvedor.
#
# Numa execução real o portal apresentou um `cartao_animal` ao representar. O
# solver tentou: 3 rodadas, 12 capturas de frame, chamadas ao modelo — e
# terminou em intervenção humana do mesmo jeito. Formato que exige animação e
# leitura de cartas não é o que se quer tentar no meio de um login com prazo.
#
# Allowlist, não denylist: um formato NOVO do portal cai automaticamente no
# caminho humano, em vez de ser tentado só porque ninguém o proibiu ainda. O
# mesmo vale para `desconhecido`, que é o que a classificação devolve quando
# não consegue decidir.
#
# Isto NÃO remove suporte a nada no ResolvedorCaptcha: `cartao_animal` e
# `imagem` seguem resolvíveis por outros consumidores e pelo captcha do login.
# SOMENTE grade 3x3 normal — 9 tiles reais — é resolvida automaticamente aqui.
#
# `grade_fused` saiu depois de uma execução no QA em que ele foi classificado
# CORRETAMENTE e mesmo assim seguiu para o solver, que passou a chamar o modelo
# repetidamente. Não é caso de melhorar a heurística: o pedido é que qualquer
# formato que não seja a grade normal vá para o humano.
#
# Formato novo, futuro ou `desconhecido` cai fora dela por construção — nunca
# por esquecimento. Cada entrada precisa ser posta aqui A MÃO, derrubando o gate
# em tests/test_representacao_captcha.py.
#
# Isto NÃO remove suporte a nada no ResolvedorCaptcha: `grade_fused`,
# `cartao_animal` e `imagem` seguem resolvíveis por outros consumidores e pelo
# captcha do login. A restrição é da representação no Serviços RF.
#
# `bola_em_movimento` ENTROU em 04/09/2026, e é a primeira adição desde que a
# tupla foi fechada. Três coisas mudaram desde então:
#
#  1. O formato aparece AQUI, na hora de setar o CNPJ — observado diretamente
#     pelo Jean, não inferido. Sem entrar na allowlist, toda representação que
#     receber a bola vai para intervenção humana, que é exatamente o custo que
#     este trabalho existe para eliminar.
#  2. O tipo passou a EXISTIR. Até hoje `bola_em_movimento` não era vocabulário
#     de nenhuma das duas bibliotecas: a área é uma imagem única e quadrada,
#     então o fallback geométrico a classificava `grade_fused` e ela seguia para
#     um resolvedor que olha UM quadro — incapaz, por construção, de resolver um
#     desafio cuja resposta só existe na sequência.
#  3. O motivo pelo qual `grade_fused` saiu daqui — "seguiu para o solver, que
#     passou a chamar o modelo repetidamente" — era um laço sem teto: as rodadas
#     internas dos resolvedores não checavam o orçamento, só a cadeia de modelos
#     checava. Isso foi corrigido no ResolvedorCaptcha; `_solve_bola` para na
#     rodada em que o orçamento acaba, e se RECUSA a rodar sem deadline.
#
# `grade_fused` continua FORA: o motivo dele era o laço, mas nada mede que ele
# resolva, e não é este trabalho que traz essa medida.
TIPOS_AUTOMATICOS_REPRESENTACAO = (TIPO_GRADE, TIPO_BOLA)

# Janela curta para o SPA refletir a troca antes de concluirmos que ela não
# ocorreu. Curta de propósito: quando há captcha, esperar mais não muda nada.
ESPERA_POS_CONDICAO_S = 8.0
INTERVALO_POS_CONDICAO_S = 0.5

# Mensagem de erro da representação. A CLASSE é o contrato; o texto, não — ele
# muda e pode passar a carregar informação de quem se tenta representar.
SEL_MENSAGEM_ERRO_REPRESENTACAO = ".mensagemErro"

# Uma tentativa é a OPERAÇÃO inteira: formulário, envio, desfecho e captcha.
MAX_TENTATIVAS_REPRESENTACAO = 3

# O portal pediu "pelo menos 30 segundos". Margem mínima e determinística: não
# há jitter nem randomização — isto é respeito ao throttle observado, não
# técnica para parecer outra coisa.
COOLDOWN_ERRO_REPRESENTACAO_S = 31.0

# Orçamento de tempo do captcha DA REPRESENTAÇÃO. O padrão do resolvedor (30 s
# por chamada, sem teto total) é o certo para o captcha de login e caro demais
# aqui: numa execução real dois timeouts consecutivos consumiram mais de um
# minuto, e o portal recusou a representação em seguida.
TIMEOUT_GEMINI_REPRESENTACAO_MS = 10_000
DEADLINE_CAPTCHA_REPRESENTACAO_S = 25.0

# Orçamento da BOLA, separado — e MEDIDO, ao contrário do de cima.
#
# Este formato não tem como caber nos 25 s da grade: a resposta só existe na
# sequência, então há uma captura de 14 quadros a 0,5 s (7 s) ANTES da primeira
# chamada ao modelo. O que sobraria para o Gemini seriam 18 s, e é pouco pela
# medida abaixo.
#
# Medido em 04/09/2026 contra as 3 amostras arquivadas, com o prompt de grade
# que foi para produção (testar_portado.py, 3/3 com eliminação fechada, nome
# certo e célula certa):
#
#     preparo 0,6-0,8 s   |   Gemini 5,4 s / 6,5 s / 9,6 s
#
# Os 9,6 s são o motivo do teto por chamada ser 14 s e não os 10 s da grade: a
# chamada mais lenta das três passou a 400 ms do teto antigo. Num dia um pouco
# pior ela morre por timeout e a rodada inteira se perde — não porque o modelo
# errou, mas porque o teto nunca tinha sido medido com este prompt.
#
# A UNIDADE DE CUSTO É A RODADA, E O DESAFIO TEM DUAS.
#
# O hCaptcha faz duas rodadas por desafio, e a segunda traz animais e trajetória
# NOVOS — ou seja, ela paga outra captura de 7 s inteira. O primeiro valor posto
# aqui foi 35 s, dimensionado para UMA rodada mais uma retentativa de chamada.
# Essa conta não descreve o problema, e a RUN-0ee6428b (dev, 04/09/2026) mostrou
# como ela falha — cronometrada, não estimada:
#
#     19:50:27.441  === Iteração 1/6 ===
#     19:50:33.067  Rodada 1/2 — capturando animação...      (+5,6 s)
#     19:50:50.214  Clicado em 'galinha' célula=(3,13)       (+17,1 s)  ACERTOU
#     19:50:52.424  Rodada 2/2 — capturando animação...      (+2,2 s)
#     19:51:05.015  orçamento de tempo esgotado
#     19:51:07.361  [cnpj] Validação manual necessária.      (total 37,6 s)
#
# A rodada 1 acertou com confidence=high. A rodada 2 entrou com ~7 s de
# orçamento, `timeout_efetivo = min(14 s, restante)` virou 7 s, e duas das três
# latências medidas não cabem nisso: o modelo nem chegou a responder. Visto da
# tela, "acertou a primeira leva e fechou sozinho".
#
# Os 5,6 s de ABERTURA E CLASSIFICAÇÃO antes da rodada 1 são a parcela que a
# conta original esquecia — o relógio do deadline começa na entrada do
# `solve_hcaptcha`, não na primeira captura.
#
#     abertura 5,6 + 2 x (rodada de ~19,3 s)      = 44,3 s   observado
#     idem, com o Gemini na pior latência medida  = 47,4 s
#
# 60 s cobrem esse pior caso com 12,6 s de folga — escolha do Jean, e ela chega
# a 1,4 s de cobrir até o cenário seguinte: duas rodadas MAIS uma chamada que
# estoura os 14 s (61,4 s). Na prática só esse último caso ainda termina em
# intervenção humana.
#
# BARATEAR A CAPTURA foi considerado e descartado, não esquecido: o ciclo da
# animação é ~9,9 s, então cortar a captura para ~5 s veria metade dele. A bola
# PAUSA sobre cada animal; ver metade do ciclo é arriscar não ver onde ela
# passou. Invalidaria a medida de 3/3 e provavelmente pioraria o acerto. Com o
# limite do portal medido abaixo, não é necessário.
#
# LIMITE SUPERIOR — agora MEDIDO, e a anedota que estava aqui era errada.
# Levantamento do histórico de dev, intervalo entre "Clicando em Representar" e
# o primeiro desfecho:
#
#     confirmado           n=11   min 10,4 s   média 22,3 s   MÁX 70,3 s
#     recusado pelo portal n= 5   min  5,0 s   média 47,6 s   máx 96,8 s
#
# Existe representação ACEITA 70,3 s depois do clique, então o "pouco mais de um
# minuto" que este comentário citava subestimava o portal. E a recusa mais
# rápida veio em 5,0 s: se demora fosse o gatilho, não haveria recusa em cinco
# segundos — a recusa é sobre procuração/permissão, não sobre tempo. O caso
# anedótico original juntou duas coisas independentes.
#
# Amostra pequena (11 e 5, tudo de dev) e o 70,3 s é caso único. 60 s ficam
# 10,3 s abaixo dele — margem menor do que eu escolheria sozinho, e o sinal de
# que passou do ponto é específico: representação recusada DEPOIS de
# "Representação enviada", não falha durante a resolução do captcha.
#
# Vale SÓ para este tipo. `_orcamento_do_captcha` devolve isto apenas quando
# `tipo == TIPO_BOLA`; a grade 3x3, que roda todo dia, segue nos 25 s / 10 s.
TIMEOUT_GEMINI_BOLA_MS = 14_000
DEADLINE_CAPTCHA_BOLA_S = 60.0


def _orcamento_do_captcha(tipo: str) -> tuple[int, float]:
    """(timeout por chamada, teto total) do tipo — cada um com a sua medida."""
    if tipo == TIPO_BOLA:
        return TIMEOUT_GEMINI_BOLA_MS, DEADLINE_CAPTCHA_BOLA_S
    return TIMEOUT_GEMINI_REPRESENTACAO_MS, DEADLINE_CAPTCHA_REPRESENTACAO_S

# Janela para surgir QUALQUER desfecho depois de Representar. NÃO é a latência
# do perfil: o `ESPERA_POS_CONDICAO_S` de 8 s nasceu para isso e ficou governando
# uma máquina de estados maior. Numa execução real o captcha da representação só
# apareceu ~11 s depois do clique — 8 s não cobrem nem um comportamento já
# observado em produção.
ESPERA_DESFECHO_REPRESENTACAO_S = 20.0

# Quantas vezes o captcha de UMA tentativa é tratado antes de se desistir.
# Existe porque a classificação pode não se sustentar (widget que não abre): sem
# teto, "há captcha" e "tipo nenhum" se alternariam para sempre. Esgotado o
# teto com captcha ainda ativo, o desfecho é falha TÉCNICA — nunca uma nova
# submissão por cima do captcha da tentativa em curso.
MAX_TRATAMENTOS_CAPTCHA = 2

# Desfechos possíveis do clique em Representar. Vocabulário FECHADO.
DESFECHO_CONFIRMADA = "confirmada"
DESFECHO_ERRO_PORTAL = "erro_portal"
DESFECHO_CAPTCHA = "captcha"
DESFECHO_PERFIL_OUTRO = "perfil_outro"
DESFECHO_SEM_RESPOSTA = "sem_resposta"

# Estados do perfil ativo. `outro` é observação EXPLÍCITA de representação
# errada — repetir a solicitação três vezes não a conserta.
PERFIL_CORRETO = "correto"
PERFIL_OUTRO = "outro"
PERFIL_AUSENTE = "ausente"

CONTINUAR = "continuar"
CANCELAR = "cancelar"
EXPIRADO = "expirado"


class FalhaDoResolvedorCaptcha(RuntimeError):
    """O resolvedor automático falhou TECNICAMENTE — não é caso de humano.

    Chave ausente, dependência indisponível, página morta. Mascarar isso como
    "o usuário precisa resolver o captcha" abriria uma janela que não resolve
    nada e esconderia o defeito real.
    """


class RepresentacaoNaoConfirmada(RuntimeError):
    """O portal não confirmou a representação e não há captcha para explicar.

    Mensagem constante: nem o documento solicitado nem o encontrado entram aqui.
    """


class RepresentacaoRejeitadaPeloPortal(RuntimeError):
    """O portal recusou a representação em todas as tentativas.

    Distinta de `RepresentacaoNaoConfirmada`: aqui houve recusa EXPLÍCITA e
    repetida, com o intervalo pedido cumprido entre elas. Continua sendo falha
    técnica do fluxo para quem consome — não é caso de humano nem de retry no
    runner.
    """


class RepresentacaoRequerIntervencao(RuntimeError):
    """Há captcha na representação e ninguém pode resolvê-lo nesta execução."""


class RepresentacaoCancelada(RuntimeError):
    """A validação manual foi cancelada por quem operava."""


class RepresentacaoExpirada(RuntimeError):
    """A validação manual não foi concluída dentro do prazo."""


def _texto_do_seletor(page, seletor: str) -> str | None:
    """Texto do primeiro elemento, ou None se ele não existir/estiver ilegível."""
    try:
        loc = page.locator(seletor).first
        if loc.count() == 0:
            return None
        return loc.inner_text()
    except Exception:  # noqa: BLE001 — ausência e erro dão no mesmo: sem prova
        return None


def _estado_do_perfil(page, cnpj_alvo: str) -> str:
    """Que perfil está ativo — CORRETO, OUTRO ou AUSENTE.

    Compara internamente; nenhum dos dois documentos vai para log. `OUTRO` só
    é dito quando há representação ATIVA e ela não é a pedida: é observação
    explícita, e por isso encerra o fluxo em vez de virar mais uma tentativa.
    Ausência de sinal é `AUSENTE` — sem prova não há representação nenhuma.
    """
    documento = _texto_do_seletor(page, SEL_DOCUMENTO_REPRESENTADO)
    if documento is None or not _normalizar_cnpj(documento):
        return PERFIL_AUSENTE
    if _normalizar_cnpj(documento) != _normalizar_cnpj(cnpj_alvo):
        return PERFIL_OUTRO

    # Defesa adicional: o mesmo documento poderia estar ativo sob outro papel.
    papel = _texto_do_seletor(page, SEL_PAPEL_REPRESENTACAO)
    if papel is None:
        return PERFIL_AUSENTE
    if PAPEL_ESPERADO in " ".join(papel.split()).casefold():
        return PERFIL_CORRETO
    return PERFIL_OUTRO


def _perfil_representado(page, cnpj_alvo: str) -> bool:
    """O perfil ATIVO é o CNPJ solicitado, como Procurador?"""
    return _estado_do_perfil(page, cnpj_alvo) == PERFIL_CORRETO


def _aguardar_perfil_representado(page, cnpj_alvo: str,
                                  limite_s: float = ESPERA_POS_CONDICAO_S) -> bool:
    """Espera curta pela pós-condição. Não é retry do clique: é só latência."""
    fim = time.monotonic() + limite_s
    while True:
        if _perfil_representado(page, cnpj_alvo):
            return True
        if time.monotonic() >= fim:
            return False
        time.sleep(INTERVALO_POS_CONDICAO_S)


def _tipo_do_desafio(page) -> str:
    """Tipo do desafio atual — só INSPEÇÃO, nunca resolução.

    `TIPO_NENHUM` na indeterminação: dizer "não há desafio" leva a
    `RepresentacaoNaoConfirmada`, que é o desfecho seguro. Chutar um tipo
    poderia mandar um formato desconhecido para o caminho automático.
    """
    try:
        return detectar_tipo_captcha(page)
    except Exception:  # noqa: BLE001 — indeterminado não pode virar automático
        return TIPO_NENHUM


def _preencher_formulario_representacao(page, cnpj: str) -> None:
    """Abre o avatar, preenche o identificador, escolhe Procurador e envia."""
    print("[cnpj] Clicando no avatar...")
    avatar = page.locator('#avatar-dropdown-trigger').first
    avatar.wait_for(state="visible", timeout=20_000)
    avatar.click()

    print("[cnpj] Preenchendo identificador do perfil PJ...")
    campo = page.locator('#input-representar-cpfcnpj').first
    campo.wait_for(state="visible", timeout=10_000)
    campo.fill(cnpj)

    print("[cnpj] Selecionando Procurador...")
    ng_select = page.locator(
        'xpath=//*[@id="formularioRepresentacao"]/form/div/div[2]'
        '/br-select/div/div/div[1]/ng-select'
    ).first
    ng_select.wait_for(state="visible", timeout=10_000)
    ng_select.click()

    opcao = page.get_by_role("option", name="Procurador").first
    opcao.wait_for(state="visible", timeout=5_000)
    opcao.click()

    print("[cnpj] Clicando em Representar...")
    btn = page.locator(
        'xpath=//*[@id="formularioRepresentacao"]/form/div/button'
    ).first
    btn.wait_for(state="visible", timeout=10_000)
    btn.click()
    print("[cnpj] Representação solicitada.")


def _erro_representacao_visivel(page) -> bool:
    """QUALQUER `.mensagemErro` visível — não só a primeira do documento.

    O contrato sempre foi "qualquer", mas o código consultava `.first`: bastava
    o portal manter um `span` oculto na frente na ordem do DOM para a mensagem
    real, logo depois, não ser vista.

    O texto NÃO é lido nem registrado: ele muda com o tempo e pode passar a
    carregar informação de quem se tenta representar. A classe é o contrato; a
    frase, não.
    """
    try:
        locator = page.locator(SEL_MENSAGEM_ERRO_REPRESENTACAO)
        for i in range(locator.count()):
            if locator.nth(i).is_visible():
                return True
        return False
    except Exception:  # noqa: BLE001 — não observar é não haver prova de erro
        return False


def _ha_captcha(page) -> bool:
    """Inspeção barata de presença. Indeterminação = não há."""
    try:
        return bool(captcha_presente(page))
    except Exception:  # noqa: BLE001 — sem prova, segue o fluxo normal
        return False


def _aguardar_desfecho(page, cnpj_alvo: str,
                       limite_s: float = ESPERA_DESFECHO_REPRESENTACAO_S) -> str:
    """Observa os desfechos possíveis do clique em Representar, CONCORRENTEMENTE.

    Prioridade: perfil correto > erro do portal > captcha > perfil de outro >
    prazo. Perfil correto ganha de mensagem residual — quem decide a
    representação é a pós-condição. Perfil de OUTRO fica por último porque a
    troca pode estar em curso; só vale quando nada melhor apareceu na janela.

    `captcha_presente` é a inspeção BARATA; a classificação do tipo custa mais e
    acontece uma única vez, depois, quando o desfecho for CAPTCHA.

    `DESFECHO_SEM_RESPOSTA` significa "nada observável nesta janela" — e isso
    NÃO é falha definitiva: o portal pode estar processando, montando o iframe
    do captcha ou esperando a API. Tratá-lo como terminal foi o que fez a run
    de 16:56 morrer sem uma segunda tentativa.
    """
    fim = time.monotonic() + limite_s
    while True:
        estado = _estado_do_perfil(page, cnpj_alvo)
        if estado == PERFIL_CORRETO:
            return DESFECHO_CONFIRMADA
        if _erro_representacao_visivel(page):
            return DESFECHO_ERRO_PORTAL
        if _ha_captcha(page):
            return DESFECHO_CAPTCHA
        if time.monotonic() >= fim:
            return (DESFECHO_PERFIL_OUTRO if estado == PERFIL_OUTRO
                    else DESFECHO_SEM_RESPOSTA)
        time.sleep(INTERVALO_POS_CONDICAO_S)


def _observar_intervalo(page, cnpj_alvo: str, enviado_em: float,
                        intervalo_s: float = COOLDOWN_ERRO_REPRESENTACAO_S,
                        *, erro_ja_visto: bool = False) -> str:
    """Cumpre o intervalo desde o ÚLTIMO ENVIO SEM parar de observar.

    Contado a partir do envio, não do fim da observação: a solicitação anterior
    pode ainda estar em processamento, e clicar de novo em cima dela é dupla
    submissão. Determinístico, sem jitter — é o throttle que o portal pediu
    ("pelo menos 30 segundos"), cumprido, não disfarçado.

    E a espera NÃO é cega. A razão de existir do intervalo é justamente que a
    solicitação anterior pode ainda estar em curso; esperar para não duplicar e
    ao mesmo tempo ignorar a resposta que chega durante a espera contraria o
    próprio motivo. Observa os mesmos desfechos de `_aguardar_desfecho`, com a
    mesma prioridade — antes só o perfil correto era percebido, e um captcha que
    surgisse no meio do intervalo passava despercebido.

    Devolve o desfecho observado, ou `DESFECHO_SEM_RESPOSTA` se o intervalo
    acabar sem nada. Volta imediatamente quando o intervalo já passou.

    `erro_ja_visto` existe porque a mensagem de recusa PERMANECE na tela: sem
    isso, esperar o intervalo depois de uma recusa terminaria no primeiro
    instante, relatando de novo o erro que motivou a espera — e o intervalo
    nunca seria cumprido.
    """
    fim = enviado_em + intervalo_s
    if time.monotonic() < fim:
        print("[cnpj] Aguardando intervalo antes de nova tentativa.")
    while True:
        estado = _estado_do_perfil(page, cnpj_alvo)
        if estado == PERFIL_CORRETO:
            return DESFECHO_CONFIRMADA
        if not erro_ja_visto and _erro_representacao_visivel(page):
            return DESFECHO_ERRO_PORTAL
        if _ha_captcha(page):
            return DESFECHO_CAPTCHA
        if time.monotonic() >= fim:
            return (DESFECHO_PERFIL_OUTRO if estado == PERFIL_OUTRO
                    else DESFECHO_SEM_RESPOSTA)
        time.sleep(INTERVALO_POS_CONDICAO_S)


def _restaurar_formulario(page) -> None:
    """Fecha o que estiver aberto. Nada do estado anterior é reaproveitado."""
    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001, S110 — o formulário será reaberto do zero
        pass


def _resolver_desafio_da_representacao(page, cnpj: str, *, on_manual_challenge,
                                       fim_intervencao: float):
    """Trata o captcha da representação. Devolve True ou um DESFECHO_*.

    True significa perfil confirmado. Qualquer outro retorno é o desfecho
    observado depois da tentativa, para quem chamou decidir entre nova
    tentativa e falha.
    """
    tipo = _tipo_do_desafio(page)          # classificação UMA vez, aqui
    if tipo == TIPO_NENHUM and _ha_captcha(page):
        # CHECKBOX PRESENTE não é CHALLENGE ABERTO. `detectar_tipo_captcha` só
        # enxerga desafio aberto, então com o widget "Sou humano" ainda fechado
        # não há tipo a classificar — e o fluxo ficava girando entre "há
        # captcha" e "tipo nenhum" até esgotar as tentativas.
        #
        # `abrir_desafio` abre e NÃO resolve: a allowlist continua decidindo
        # depois, com o tipo em mãos. Chamar `solve_hcaptcha` aqui resolveria
        # qualquer tipo e passaria por cima dela.
        print("[cnpj] Widget de captcha detectado; aguardando abertura do desafio.")
        try:
            aberto = abrir_desafio(page)
        # BLE001: abrir é do resolvedor, e falhar aqui é erro técnico, não
        # trabalho para humano — mesma fronteira do `solve_hcaptcha`.
        except Exception as e:  # noqa: BLE001
            raise FalhaDoResolvedorCaptcha(
                f"nao foi possivel abrir o desafio ({type(e).__name__})."
            ) from None
        if aberto:
            tipo = _tipo_do_desafio(page)

    if tipo == TIPO_NENHUM:
        # A presença detectada não se sustentou na classificação. Já foi um
        # caminho MUDO; agora ele fala, porque é indistinguível de um timeout
        # no log e foi um dos dois suspeitos da run de 16:56.
        print("[cnpj] Presença de captcha não se confirmou na classificação.")
        return _aguardar_desfecho(page, cnpj)

    print(f"[cnpj] Desafio aberto | tipo={tipo}")
    if tipo in TIPOS_AUTOMATICOS_REPRESENTACAO:
        print(f"[cnpj] Desafio automatizável detectado | tipo={tipo}")
        try:
            # Orçamento CURTO, só aqui: a representação tem o ritmo do portal, e
            # uma resolução que se estende por um minuto chega tarde demais para
            # servir. Os demais consumidores mantêm o padrão do resolvedor.
            #
            # POR TIPO, e não um teto único: o tipo já foi classificado acima,
            # então não há motivo para a grade 3x3 — que responde numa chamada
            # sobre um screenshot parado — carregar a folga que a animação
            # precisa. Afrouxar um teto único para caber a bola encompridaria
            # também o pior caso da grade, que é o formato que roda todo dia.
            timeout_ms, deadline_s = _orcamento_do_captcha(tipo)
            automatico = solve_hcaptcha(
                page,
                gemini_timeout_ms=timeout_ms,
                deadline_s=deadline_s)
        # BLE001: a captura ampla é o ponto. O resolvedor pode falhar de muitas
        # formas — chave ausente, dependência indisponível, página morta — e
        # todas significam o mesmo aqui: erro técnico, não trabalho para humano.
        except Exception as e:  # noqa: BLE001
            raise FalhaDoResolvedorCaptcha(
                f"o resolvedor de captcha falhou tecnicamente ({type(e).__name__})."
            ) from None
        print(f"[cnpj] Resolução automática: "
              f"{'concluída' if automatico else 'não concluída'}.")

        desfecho = _aguardar_desfecho(page, cnpj)
        if desfecho == DESFECHO_CONFIRMADA:
            return True
        if desfecho != DESFECHO_CAPTCHA:
            # Erro do portal, perfil de outro ou nada observável: quem chamou
            # decide, e há tentativa nova quando cabe.
            return desfecho
        # Ainda há captcha: o automático não bastou.
    else:
        print(f"[cnpj] Desafio requer validação manual | tipo={tipo}")

    print("[cnpj] Validação manual necessária.")
    if on_manual_challenge is None:
        raise RepresentacaoRequerIntervencao(
            "a representacao exige validacao manual e nao ha como solicita-la.")

    while True:
        restantes = fim_intervencao - time.monotonic()
        if restantes <= 0:
            raise RepresentacaoExpirada(
                "a validacao manual nao foi concluida no prazo.")

        resposta = on_manual_challenge(segundos_restantes=restantes)
        if resposta == CANCELAR:
            raise RepresentacaoCancelada("a validacao manual foi cancelada.")
        if resposta == EXPIRADO:
            raise RepresentacaoExpirada(
                "a validacao manual nao foi concluida no prazo.")

        # CONTINUAR NÃO É CONFIRMAÇÃO. Quem confirma é o portal.
        print("[cnpj] Aguardando desfecho da representação...")
        desfecho = _aguardar_desfecho(page, cnpj)
        if desfecho == DESFECHO_CONFIRMADA:
            return True
        if desfecho != DESFECHO_CAPTCHA:
            return desfecho


def _representar_cnpj_procurador(page, cnpj: str, *,
                                 on_manual_challenge=None,
                                 prazo_intervencao_s: float = 300.0) -> bool:
    """Representa o CNPJ como Procurador e CONFIRMA que o perfil trocou.

    UMA tentativa é a OPERAÇÃO inteira: abrir o formulário, preencher, escolher
    Procurador, enviar, observar o desfecho, resolver captcha se houver, e
    observar de novo.

    Dois desfechos pedem nova tentativa, por motivos diferentes:

      * `.mensagemErro` visível — o portal recusou, e disse isso;
      * nada observável na janela — o portal não respondeu nada que se possa
        interpretar. Isso NÃO é prova de recusa, e por isso o log não diz que
        houve uma; mas também não é prova de falha definitiva, e tratá-lo como
        terminal deixou uma run morrer na primeira tentativa.

    Nos dois casos espera-se o intervalo mínimo DESDE O ENVIO antes de repetir —
    a solicitação anterior pode ainda estar em processamento.

    Perfil de OUTRO documento ativo encerra na hora: repetir não conserta.

    `on_manual_challenge` é opcional e BLOQUEANTE: chamado apenas quando há
    captcha na representação **e a resolução automática não bastou**, deve
    devolver `CONTINUAR`, `CANCELAR` ou `EXPIRADO`. Recebe `segundos_restantes`
    do prazo TOTAL. A biblioteca não conhece a interface que o implementa.

    `prazo_intervencao_s` é um deadline MONOTÔNICO total: reabrir a intervenção
    não reinicia a contagem, senão uma sequência de tentativas esticaria a
    espera indefinidamente.

    Devolve True só com a pós-condição confirmada. Nunca devolve True por
    clique realizado. Levanta uma das exceções tipadas acima quando não confirma.
    """
    cnpj = _normalizar_cnpj(cnpj)
    print("[cnpj] Iniciando representação do perfil PJ como Procurador...")
    fim_intervencao = time.monotonic() + prazo_intervencao_s
    recusas = 0

    for tentativa in range(1, MAX_TENTATIVAS_REPRESENTACAO + 1):
        if tentativa > 1:
            print(f"[cnpj] Tentativa {tentativa}/{MAX_TENTATIVAS_REPRESENTACAO} "
                  "da representação.")
        try:
            _preencher_formulario_representacao(page, cnpj)
        except Exception as e:
            print(f"[cnpj] Erro ao enviar o formulário: {type(e).__name__}")
            if tentativa == MAX_TENTATIVAS_REPRESENTACAO:
                raise RepresentacaoNaoConfirmada(
                    "nao foi possivel enviar o formulario de representacao.") from None
            _restaurar_formulario(page)
            time.sleep(1)
            continue

        enviado_em = time.monotonic()
        print("[cnpj] Aguardando desfecho da representação...")
        desfecho = _aguardar_desfecho(page, cnpj)

        # ── Governo dos desfechos DESTA tentativa ────────────────────────────
        #
        # Enquanto não houve nova submissão, tudo o que se observa pertence à
        # tentativa em curso. Antes o desfecho descoberto durante o intervalo
        # era observado e depois DESCARTADO: só `confirmada` e `perfil_outro`
        # tinham efeito, e uma recusa ou um captcha tardios sumiam.
        #
        # Este laço é o ÚNICO lugar onde um `DESFECHO_*` tem semântica. Sair
        # dele significa que a tentativa acabou e uma nova pode começar.
        recusou = False
        anunciados = set()
        tratamentos = 0

        while True:
            if desfecho not in anunciados:
                print(f"[cnpj] Desfecho observado | tipo={desfecho}")
                anunciados.add(desfecho)

            if desfecho == DESFECHO_CONFIRMADA:
                print("[cnpj] Perfil representado confirmado.")
                return True

            if desfecho == DESFECHO_PERFIL_OUTRO:
                # Observação EXPLÍCITA de representação errada. Repetir às
                # cegas três vezes não a transforma na certa.
                raise RepresentacaoNaoConfirmada(
                    "o perfil ativo nao e o solicitado.")

            if desfecho == DESFECHO_CAPTCHA:
                if tratamentos >= MAX_TRATAMENTOS_CAPTCHA:
                    # Evidência EXPLÍCITA: há captcha ativo e o fluxo não
                    # conseguiu avançá-lo. Isso não é "sem resposta", e não
                    # autoriza clicar Representar de novo — enviar por cima de
                    # um captcha que pertence a esta tentativa seria submeter
                    # às cegas. Nem cabe esperar o intervalo: ele existe ANTES
                    # de uma nova submissão, e não haverá nenhuma.
                    print(f"[cnpj] Captcha continua ativo após {tratamentos} "
                          "tratamento(s) — encerrando por falha técnica.")
                    raise FalhaDoResolvedorCaptcha(
                        "o captcha da representacao nao pode ser tratado.")
                tratamentos += 1
                resultado = _resolver_desafio_da_representacao(
                    page, cnpj, on_manual_challenge=on_manual_challenge,
                    fim_intervencao=fim_intervencao)
                desfecho = (DESFECHO_CONFIRMADA if resultado is True
                            else resultado)
                continue

            if desfecho == DESFECHO_ERRO_PORTAL:
                if not recusou:
                    recusas += 1
                    recusou = True
                print("[cnpj] Portal recusou a tentativa; aguardando intervalo "
                      "antes de repetir.")
            else:
                print("[cnpj] Nenhum desfecho observável dentro da janela.")
                print("[cnpj] Representação sem desfecho observável; aguardando "
                      "intervalo antes de repetir.")

            if tentativa == MAX_TENTATIVAS_REPRESENTACAO:
                break

            # Completa o intervalo que faltar — imediato se já passou — sem
            # deixar de observar. O que aparecer aqui volta a ser governado
            # pelo mesmo laço, e não descartado.
            proximo = _observar_intervalo(page, cnpj, enviado_em,
                                          erro_ja_visto=recusou)
            if proximo == DESFECHO_SEM_RESPOSTA:
                break                  # intervalo cumprido e nada novo
            desfecho = proximo

        if tentativa == MAX_TENTATIVAS_REPRESENTACAO:
            break
        _restaurar_formulario(page)

    if recusas == MAX_TENTATIVAS_REPRESENTACAO:
        raise RepresentacaoRejeitadaPeloPortal(
            "o portal recusou a representacao em todas as tentativas.")
    raise RepresentacaoNaoConfirmada(
        "o portal nao confirmou a representacao do perfil.")


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def main(
    cert_name: str | None = None,
    cert_pfx_path: str | None = None,
    cert_pfx_passphrase: str | None = None,
    project_dir: "Path | str | None" = None,
    cnpj: str | None = None,
    cert_subject_cn: str | None = None,
    cert_serial: str = "",
    policy_ok: bool = True,
    on_manual_challenge=None,
    prazo_intervencao_s: float = 300.0,
):
    """Realiza o login nos Serviços da Receita Federal e retorna (playwright, context, page).

    Há duas formas de informar o certificado:

    A) Certificado do Windows Certificate Store (recomendado, igual ao eCAC) —
       passe `cert_subject_cn` (CN do cert escolhido pelo usuário num dropdown que
       lista os certs instalados na máquina). O Chrome apresenta o cert via CAPI e
       a flag --auto-select-certificate-for-urls escolhe o correto pelo CN, sem
       diálogo. Não usa arquivo .pfx nem senha. Requer que a policy de auto-seleção
       esteja ativa (ver cert_windows.iniciar_guarda no projeto chamador); se não
       estiver (`policy_ok=False`), um fallback via pywinauto seleciona o cert pelo
       serial na janela nativa do Chrome.

    B) Arquivo .pfx (legado) — via `cert_name`, `cert_pfx_path`+`cert_pfx_passphrase`
       ou as variáveis do .env. Passa o certificado ao Patchright como
       client_certificates. Pode falhar com certs ICP-Brasil (SSL alert 40).

    Args:
        cert_name:
            Nome (ou parte do nome) do certificado em C:\\Certificados (modo B).
            A senha é lida automaticamente do senhas.json.

        cert_pfx_path:
            Caminho absoluto para o arquivo .pfx (modo B). Requer cert_pfx_passphrase.

        cert_pfx_passphrase:
            Senha do .pfx informado em cert_pfx_path (modo B).

        project_dir:
            Diretório do projeto chamador. Usado para localizar o .env e
            salvar o perfil do Chrome. Padrão: Path.cwd().

        cnpj:
            CNPJ da empresa a representar como Procurador após o login.
            Aceita com ou sem formatação. Se None, retorna sem representar.

        cert_subject_cn:
            CN do certificado escolhido no Windows Certificate Store (modo A).
            Quando informado, tem prioridade sobre o modo B.

        cert_serial:
            Serial do cert escolhido (modo A) — usado pelo fallback pywinauto para
            casar a linha certa na janela nativa quando há CNs iguais.

        policy_ok:
            True se a policy de auto-seleção do registro está ativa (Chrome escolhe
            sozinho). False ativa o fallback pywinauto na janela de certificado.

    Returns:
        Tupla (p, context, page) em caso de sucesso, ou None em caso de falha.

    Exemplos:
        # Modo A — cert do Windows Store (CN escolhido no formulário):
        resultado = fazer_login(cert_subject_cn="<EMPRESA>:<CNPJ>", cnpj="<CNPJ>")

        # Modo B — legado, via .pfx:
        resultado = fazer_login(cert_name="<nome do certificado>", cnpj="<CNPJ>")
    """
    if project_dir is None:
        project_dir = Path.cwd()
    project_dir = Path(project_dir)

    # --- Modo A: certificado do Windows Certificate Store (via CN) ---
    usar_windows_store = bool(cert_subject_cn and cert_subject_cn.strip())
    resolved_path = resolved_pass = None

    if usar_windows_store:
        os.environ["CERT_SUBJECT_CN"] = cert_subject_cn.strip()
        print("[cert] Usando certificado do Windows Store.")
    else:
        # --- Modo B (legado): resolver .pfx ---
        resolved_path, resolved_pass = _resolver_certificado(
            cert_pfx_path, cert_pfx_passphrase, cert_name, project_dir
        )

    user_data_dir = str(project_dir / "chrome_debug_profile")
    os.makedirs(user_data_dir, exist_ok=True)
    _configurar_download(user_data_dir)

    # --- Montar argumentos de lançamento do Chrome ---
    chrome_args = ["--start-maximized", "--remote-debugging-port=9222"]
    if usar_windows_store:
        chrome_args.append(_build_auto_select_cert_flag(cert_subject_cn))

    launch_kwargs = dict(
        user_data_dir=user_data_dir,
        channel="chrome",
        headless=False,
        no_viewport=True,
        ignore_https_errors=True,
        accept_downloads=True,
        args=chrome_args,
    )
    if not usar_windows_store and resolved_path and resolved_pass:
        launch_kwargs["client_certificates"] = _build_client_certificates(
            resolved_path, resolved_pass
        )
    elif not usar_windows_store:
        print("[cert] Nenhum certificado configurado. O navegador abrirá sem certificado embutido.")

    # --- Iniciar Playwright e Chrome ---
    # Qualquer falha daqui em diante precisa encerrar o Playwright (ver _abortar):
    # deixar a instância viva mantém o event loop rodando na thread e quebra a
    # próxima tentativa com "Sync API inside the asyncio loop".
    p = sync_playwright().start()
    try:
        print("Lançando Chrome...")
        context = p.chromium.launch_persistent_context(**launch_kwargs)
        print("Chrome lançado.")

        page = context.pages[0] if context.pages else context.new_page()
        print("Página obtida.")
    except Exception:
        _abortar(p, None)
        raise

    try:
        # --- Verificar sessão já ativa ---
        if _ja_logado(page):
            print("  -> Sessão ativa detectada. Pulando etapas de autenticação.")

        # --- 1ª navegação para a URL de login ---
        print("[1ª navegação] Abrindo o portal Serviços RF ...")
        try:
            page.goto(SERVICOS_RF_URL, wait_until="domcontentloaded", timeout=30_000)
            print("  -> página inicial carregada.")
        except Exception as e:
            print(f"  -> erro no goto: {type(e).__name__}")
            registrar_erro(f"Login: erro ao abrir a página de login. {type(e).__name__}")
            return _abortar(p, context)

        # Fecha popups que aparecem ao abrir o portal (cookies + tour de boas-vindas)
        _fechar_popups_iniciais(page)

        if _ja_logado(page):
            print("  -> Redirecionado automaticamente. Login concluído.")

        # --- Clicar em "Entrar com gov.br" ---
        print("Clicando em 'Entrar com gov.br'...")
        govbr_btn = page.locator('xpath=//*[@id="home-heading"]/div[1]/div/button').first
        try:
            govbr_btn.wait_for(state="visible", timeout=15_000)
            govbr_btn.click()
            print("  -> clicado.")
        except Exception as e:
            registrar_erro("Login: botão 'Entrar com gov.br' não encontrado. "
                           f"{type(e).__name__}")
            print(f"  -> botão não encontrado: {type(e).__name__}")
            try:
                shot = str(project_dir / "_debug_govbr_btn.png")
                page.screenshot(path=shot, full_page=True)
                print("     screenshot de debug gravado.")
            except Exception:
                pass
            return _abortar(p, context)

        try:
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception:
            pass
        print("  -> navegação após 'Entrar com gov.br' concluída.")

        if _ja_logado(page):
            print("  -> Redirecionado automaticamente após gov.br. Login concluído.")

        # --- Resolver captcha após "Entrar com gov.br" (se aparecer) ---
        if not _try_solve_captcha(page, "captcha-pos-govbr"):
            if _ja_logado(page):
                print("  -> Captcha falhou mas já está logado. Continuando.")
            else:
                registrar_erro("Login: captcha não resolvido após 'Entrar com gov.br'.")
                print("[captcha] 3 tentativas falharam. Abortando.")
                return _abortar(p, context)

        # Verifica bloqueio logo após resolver captcha do govbr
        if not _ja_logado(page) and _acesso_bloqueado(page):
            if not _recuperar_acesso_bloqueado(page):
                registrar_erro("Login: acesso bloqueado após 'Entrar com gov.br' — recuperação falhou.")
                return _abortar(p, context)

        if _ja_logado(page):
            print("  -> Login concluído após captcha gov.br.")

        # --- Clicar em "Seu certificado digital" ---
        MAX_TENTATIVAS_CERT = 3
        for tentativa in range(1, MAX_TENTATIVAS_CERT + 1):
            print(f"[cert] Tentativa {tentativa}/{MAX_TENTATIVAS_CERT}...")

            if _ja_logado(page):
                print("  -> Já logado no início da tentativa. Saindo do loop.")
                break

            if not _clicar_certificado(page):
                registrar_erro("Login: botão 'Seu certificado digital' não encontrado.")
                if tentativa == MAX_TENTATIVAS_CERT:
                    print("[cert] Botão não encontrado após todas as tentativas. Abortando.")
                    try:
                        shot = str(project_dir / "_debug_cert_button.png")
                        page.screenshot(path=shot, full_page=True)
                        print("     screenshot de debug gravado.")
                    except Exception:
                        pass
                    return _abortar(p, context)
                print("  -> Recarregando e tentando novamente...")
                page.goto(SERVICOS_RF_URL, wait_until="domcontentloaded", timeout=30_000)
                continue

            # Fallback: se a policy de auto-seleção não está ativa, o Chrome exibe a
            # janela nativa "Selecione um certificado". pywinauto seleciona o cert
            # correto pelo serial/CN e clica OK. Roda em thread porque o clique acima
            # pode bloquear até a janela ser resolvida.
            if usar_windows_store and not policy_ok and _CERT_DIALOG_OK:
                _cn = os.getenv("CERT_SUBJECT_CN", "").strip()
                threading.Thread(
                    target=_selecionar_cert_dialog,
                    args=(_cn, cert_serial),
                    kwargs={"timeout": 90.0},
                    daemon=True,
                ).start()
            elif usar_windows_store and tentativa == 1:
                print("[cert] Policy de auto-seleção ativa — Chrome escolhe o certificado sozinho.")

            print("  -> Clicado. Aguardando página carregar...")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
            except Exception:
                pass
            print("  -> certificado apresentado; navegação seguiu.")

            if _ja_logado(page):
                print("  -> Login realizado sem captcha.")
                break

            # --- Resolver captcha caso apareça após o clique no certificado ---
            if not _try_solve_captcha(page, f"captcha-pos-cert-t{tentativa}"):
                print(f"[captcha] tentativa {tentativa}: falhou ao resolver captcha.")

            if _ja_logado(page):
                print("  -> Login realizado após captcha.")
                break

            # Verifica bloqueio após captcha do certificado
            if _acesso_bloqueado(page):
                print(f"[cert-t{tentativa}] Acesso bloqueado. Tentando recuperar...")
                if not _recuperar_acesso_bloqueado(page):
                    if tentativa == MAX_TENTATIVAS_CERT:
                        registrar_erro("Login: acesso bloqueado após certificado — recuperação esgotada.")
                        return _abortar(p, context)
                continue

            # Aguarda redirecionamento final (até 60s)
            print("Aguardando redirecionamento final para receita.fazenda.gov.br (até 60s)...")
            for _seg in range(60):
                print(f"  -> ({_seg + 1}s) aguardando redirecionamento | "
                      f"host={host_da_url(page.url)}")
                if _ja_logado(page):
                    print("  -> Redirecionamento confirmado.")
                    break
                time.sleep(1)
            else:
                print("  -> Timeout aguardando o portal autenticado.")
                if tentativa == MAX_TENTATIVAS_CERT:
                    registrar_erro("Login: redirecionamento após o certificado não ocorreu.")
                    try:
                        shot = str(project_dir / "_debug_pos_cert.png")
                        page.screenshot(path=shot, full_page=True)
                        print("     screenshot de debug gravado.")
                    except Exception:
                        pass
                    return _abortar(p, context)
                continue
            break

        print("Login nos Serviços RF concluído.")

        # Fecha popups que podem surgir ao cair no portal autenticado (tour de boas-vindas)
        _fechar_popups_iniciais(page)

        # --- Representar CNPJ como Procurador (se informado) ---
        if cnpj:
            # Tour guiado por passos (rodapé com "Pular Tutorial", classe skip-tutorial)
            # pode estar ativo sobre a etapa de Representação — pula se existir.
            try:
                skip_tour = page.locator('a.skip-tutorial').first
                if skip_tour.is_visible(timeout=3_000):
                    skip_tour.click()
                    print("[popup] Tour guiado pulado (skip-tutorial).")
            except Exception:
                pass

            print("Representando o perfil PJ como Procurador...")
            # Sem representacao confirmada NAO ha pagina utilizavel: devolve-la
            # levaria a automacao a consultar a API com o perfil pessoal e
            # receber 401 — que foi o desfecho da run do QA. A excecao tipada
            # sobe; o `except` abaixo encerra o Playwright antes de propagar.
            _representar_cnpj_procurador(
                page, cnpj,
                on_manual_challenge=on_manual_challenge,
                prazo_intervencao_s=prazo_intervencao_s)
    except Exception:
        # Falha inesperada: encerra o Playwright para não vazar o event loop
        # (a próxima tentativa falharia com 'Sync API inside the asyncio loop').
        _abortar(p, context)
        raise

    return p, context, page
