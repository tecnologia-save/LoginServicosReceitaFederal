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
    TIPO_CARTAO_ANIMAL,
    TIPO_DESCONHECIDO,
    TIPO_GRADE,
    TIPO_GRADE_FUSED,
    TIPO_IMAGEM,
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
# Seletores tentados em ordem para "Entrar com gov.br".
#
# Era UM XPath posicional — `//*[@id="home-heading"]/div[1]/div/button` — em dois
# lugares do arquivo. Em 08/09/2026 ele parou de casar: a RUN-002583a5 morreu com
# "botão 'Entrar com gov.br' não encontrado. TimeoutError" enquanto o botão
# estava VISÍVEL no canto superior direito da tela. XPath por posição quebra com
# qualquer `div` que a Receita insira no caminho, e não avisa — só some.
#
# Mesma estrutura de CERT_SELECTORS abaixo, e pelo mesmo motivo: o primeiro é o
# específico (barato quando o DOM está como se espera), e os seguintes são por
# TEXTO, que sobrevive a rearranjo de layout.
GOVBR_SELECTORS = [
    'xpath=//*[@id="home-heading"]/div[1]/div/button',
    "#home-heading button",
    "button:has-text('Entrar com gov.br')",
    "a:has-text('Entrar com gov.br')",
    "[aria-label*='Entrar com gov.br']",
    "button:has-text('Entrar com')",
    "text=Entrar com gov.br",
]

CERT_SELECTORS = [
    "#login-certificate",
    "a:has-text('Seu certificado digital')",
    "button:has-text('Seu certificado digital')",
    "text=Seu certificado digital",
    "[data-sso-type='certificate']",
]


# Onde a política do Chrome vive no Windows. As três chaves, porque a instalação
# varia: 32/64 bits e por-máquina/por-usuário.
_CHAVES_POLITICA_CERT = (
    (r"SOFTWARE\Policies\Google\Chrome\AutoSelectCertificateForUrls", "HKLM"),
    (r"SOFTWARE\WOW6432Node\Policies\Google\Chrome\AutoSelectCertificateForUrls", "HKLM"),
    (r"SOFTWARE\Policies\Google\Chrome\AutoSelectCertificateForUrls", "HKCU"),
)


def politica_de_certificado_instalada() -> bool:
    """A política que faz o Chrome escolher o certificado sozinho existe AQUI?

    Isto era ASSUMIDO, e a suposição estava errada nesta máquina.

    `--auto-select-certificate-for-urls` na linha de comando NÃO é um switch do
    Chrome: o mecanismo real é a política corporativa `AutoSelectCertificateForUrls`,
    lida do registro. A flag é aceita em silêncio e ignorada — não há erro, não
    há log, o Chrome simplesmente pergunta.

    Com `policy_ok=True` fixo, o fallback que clica no diálogo nunca era armado,
    e a automação ficava parada num diálogo que ninguém ia fechar. Observado com
    DOIS certificados instalados e de novo com UM só, o que descarta a teoria de
    "candidato ambíguo": nunca esteve valendo.

    Perguntar ao registro faz as duas máquinas funcionarem: onde a política
    existe, o Chrome resolve e ninguém clica; onde não existe, o fallback entra.
    Falha de leitura devolve False — o custo de armar o clicador à toa é uma
    thread ociosa; o de NÃO armar é a run travar.
    """
    try:
        import winreg
    except Exception:  # noqa: BLE001 — fora do Windows não há política nenhuma
        return False
    raizes = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    for caminho, raiz in _CHAVES_POLITICA_CERT:
        try:
            with winreg.OpenKey(raizes[raiz], caminho) as chave:
                if winreg.QueryInfoKey(chave)[1] > 0:   # ao menos um valor
                    return True
        except OSError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return False


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


def _refazer_entrada_govbr(page) -> bool:
    """Volta ao portal e refaz o caminho ATÉ a tela de escolha do certificado.

    Recarregar a home não basta: o botão "Seu certificado digital" só existe
    depois de "Entrar com gov.br". Uma tentativa que só recarrega procura, na
    home, um botão que vive duas telas adiante — e falha sempre.
    """
    try:
        page.goto(SERVICOS_RF_URL, wait_until="domcontentloaded", timeout=30_000)
    except Exception:  # noqa: BLE001
        print("  -> Falha ao reabrir o portal.")
        return False
    _fechar_popups_iniciais(page)
    if _ja_logado(page):
        return True
    if not _clicar_entrar_govbr(page):
        return False
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
    except Exception:  # noqa: BLE001, S110 — a espera de load é best-effort
        pass
    return True


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


_MARCAS_LIMITE_DISPOSITIVOS = (
    "numero maximo de dispositivos",
    "número máximo de dispositivos",
    "dispositivos conectados simultaneamente",
)


def _limite_de_dispositivos(page) -> bool:
    """A tarja de limite de dispositivos esta na tela?

    Casa por TRECHO e sem acento obrigatorio: a frase ja apareceu com e sem
    acentuacao dependendo de onde e renderizada, e comparar a frase inteira
    quebraria na primeira virgula que o gov.br mudasse.
    """
    try:
        texto = (page.inner_text("body", timeout=3_000) or "").lower()
    except Exception:  # noqa: BLE001 — pagina instavel nao afirma nada
        return False
    return any(m in texto for m in _MARCAS_LIMITE_DISPOSITIVOS)


def _clicar_entrar_govbr(page) -> bool:
    """Clica em "Entrar com gov.br" tentando os seletores em ordem.

    Espelha `_clicar_certificado`: o primeiro seletor ganha a espera longa
    (é o caminho esperado), os demais são verificações rápidas de fallback.
    """
    print("Clicando em 'Entrar com gov.br'...")
    achou = False
    for i, sel in enumerate(GOVBR_SELECTORS):
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=15_000 if i == 0 else 2_000)
        except Exception:
            continue

        # A partir daqui o botão EXISTE e está visível. O que falhar agora é
        # outra coisa, e o log precisa dizer qual.
        achou = True
        if i:
            print(f"  -> match com seletor alternativo: {sel}")
        try:
            loc.click()
            print("  -> clicado.")
            return True
        except Exception as e:
            # Clique interceptado: popup que renderizou DEPOIS da varredura.
            #
            # `_fechar_popups_iniciais` roda antes daqui, mas espera 5s pelos
            # cookies e 4s pelo tutorial — e quando o perfil do Chrome é novo
            # os dois aparecem, às vezes só depois dessas esperas vencerem. O
            # modal "Primeira vez no Portal de Serviços?" cobre o botão.
            #
            # Medido em 10/09/2026, RUN-11ca8a52, perfil `sessao-732c4178d9b8`
            # recém-criado, com as duas empresas do lote caindo aqui:
            #
            #     -> match com seletor alternativo: #home-heading button
            #     -> match com seletor alternativo: button:has-text('Entrar com')
            #     -> botão 'Entrar com gov.br' não encontrado em nenhum seletor.
            #
            # Ele ACHOU em dois seletores e mesmo assim disse "não
            # encontrado". Foi essa mensagem que me mandou procurar seletor
            # quando o problema era um véu por cima.
            print(f"  -> clique não passou ({type(e).__name__}); fechando "
                  "popups e tentando de novo.")
            _fechar_popups_iniciais(page)
            try:
                loc.click(timeout=5_000)
                print("  -> clicado depois de fechar os popups.")
                return True
            except Exception:
                continue

    if achou:
        # NÃO dizer "não encontrado" quando foi encontrado. São investigações
        # opostas: seletor errado se conserta no seletor; clique bloqueado se
        # conserta no que está por cima.
        print("  -> botão 'Entrar com gov.br' encontrado, mas o clique não "
              "passou — provavelmente há um popup ou véu sobre ele.")
    else:
        print("  -> botão 'Entrar com gov.br' não encontrado em nenhum seletor.")
    return False


# Orçamento de tempo do captcha DE LOGIN.
#
# Antes não havia nenhum: a chamada era `solve_hcaptcha(page)` pelado, com o
# padrão do resolvedor (30 s por chamada, teto total nenhum). Três coisas
# tornaram isso insustentável.
#
# 1) O LOGIN TEM PRAZO, ao contrário do que "sem teto" assumia. Observado
#    diretamente numa run com o desafio animado: "passou o tempo limite e o eCAC
#    fechou o captcha". Sem teto, a automação segue resolvendo um desafio que já
#    morreu na tela.
#
# 2) `_solve_bola` se RECUSA a rodar sem deadline, de propósito — captura os
#    quadros antes de qualquer chamada, e sem prazo isso vira consumo aberto.
#    Como o desafio animado aparece TAMBÉM no login (confirmado em 08/09/2026),
#    sem orçamento aqui ele não era nem tentado.
#
# 3) O 408 do SSO. O passo `authorize?...govbr_recupera_certificadox509` pede o
#    certificado durante o handshake TLS; uma resolução que se estende sem teto
#    deixa a requisição pendurada, e a mensagem do 408 é literalmente "Your
#    browser didn't send a complete request in time".
#
# 120 s, MAIOR que o da representação de propósito: aqui não existe o relógio do
# portal, que lá limita tudo a ~70 s. É essa folga que permite ao resolvedor
# animado usar a janela de captura longa sem apertar nada — na representação ela
# mal cabe.
#
# Teto TOTAL, dividido entre as tentativas, e não por tentativa: com 3 tentativas
# de 120 s cada o pior caso passaria de seis minutos. O que cada tentativa recebe
# é o que SOBROU.
TIMEOUT_GEMINI_LOGIN_MS = 20_000
# 09/09/2026: os orcamentos TOTAIS subiram junto com os tetos por chamada.
#
# Aumentar o teto por chamada sem aumentar o total nao adianta nada: o
# `timeout_efetivo_ms()` e `min(teto, restante)`, entao o total corta antes e o
# teto vira enfeite. Era o caso do captcha da representacao, com teto de 40s
# dentro de um orcamento de 25s.
#
# O que justifica a folga: dos 30 erros do Gemini medidos no dia, 26 eram teto
# NOSSO — 9 `504 DEADLINE_EXCEEDED` ("o prazo que voce me deu expirou"), 12
# `ReadTimeout` e 5 `400` por prazo abaixo do minimo. Apertar o relogio nao
# economizava tempo: gerava falha, e falha custa a rodada inteira.
#
# Decisao do Jean, explicita: "foda-se o orcamento". Vale onde nao ha teto
# fisico — o login subiu de 120s para 300s sem cerimonia.
#
# Na REPRESENTACAO ha teto, e ele e medido: a representacao confirmada mais
# demorada ja observada levou 70,3s depois do clique em Representar
# (`MAIOR_REPRESENTACAO_CONFIRMADA_S`, em tests/). Orcamento maior que isso nao
# compra chance nenhuma — gasta tempo num desafio que o portal ja abandonou. Os
# valores daqui ficam com folga de 10s abaixo dele, que e a guarda que a suite
# ja cobrava e me impediu de exagerar.
DEADLINE_CAPTCHA_LOGIN_S = 300.0


def _try_solve_captcha(page, etapa: str, max_attempts: int = 3) -> bool:
    """Tenta resolver o hCaptcha até `max_attempts` vezes, dentro de um teto TOTAL.

    Move o mouse uma única vez antes de resolver para evitar detecção de automação.
    """
    print(f"[{etapa}] Verificando hCaptcha (até {max_attempts} tentativas, "
          f"teto total {DEADLINE_CAPTCHA_LOGIN_S:.0f}s)...")
    fim = time.monotonic() + DEADLINE_CAPTCHA_LOGIN_S
    for tentativa in range(1, max_attempts + 1):
        restante = fim - time.monotonic()
        if restante <= 10.0:
            # Menos que isso não dá nem para a captura da animação começar; a
            # tentativa só gastaria o desafio sem chance de concluí-la.
            print(f"[{etapa}] orçamento esgotado ({restante:.0f}s restantes) — "
                  f"parando na tentativa {tentativa}.")
            break
        try:
            resultado = solve_hcaptcha(
                page,
                gemini_timeout_ms=TIMEOUT_GEMINI_LOGIN_MS,
                deadline_s=restante)
            if resultado:
                print(f"[{etapa}] tentativa {tentativa}/{max_attempts}: OK (resolvido ou ausente).")
                return True
            print(f"[{etapa}] tentativa {tentativa}/{max_attempts}: solver retornou False "
                  f"({fim - time.monotonic():.0f}s restantes).")
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


# Páginas de erro do SSO, pelo TÍTULO que o servidor devolve. São telas cruas
# do servidor, sem nada do portal — nenhum seletor que a automação espera existe
# nelas, então ela fica esperando um elemento que nunca vai aparecer, até o
# timeout, e o log só mostra "aguardando redirecionamento" repetido.
#
# Relatado pelo Jean em 08/09/2026 como recorrente, junto com 404 e 403.
#
# O 408 tem causa provável conhecida, e vale registrar: o passo
# `authorize?...govbr_recupera_certificadox509` pede o certificado do cliente
# DURANTE o handshake TLS. Enquanto o diálogo "Selecione um certificado" fica
# aberto esperando alguém clicar, a requisição não se completa — e a mensagem do
# 408 é literalmente "Your browser didn't send a complete request in time". Se
# for isso, o 408 é sintoma do diálogo, e some quando ele for fechado sozinho.
_ERROS_HTTP_DO_SSO = (
    ("408", "request time-out", "request timeout"),
    ("403", "forbidden", "acesso negado"),
    # "portal nao encontrado" e a pagina 404 ESTILIZADA do portal — com o
    # titulo da marca, nao "404 Not Found". Ver `pagina_de_erro_http`.
    ("404", "not found", "página não encontrada", "portal não encontrado"),
    ("502", "bad gateway"),
    ("503", "service unavailable"),
    ("504", "gateway time-out", "gateway timeout"),
)


def pagina_de_erro_http(page, inspecionar_corpo: bool = False) -> str:
    """Devolve o código do erro se a tela for uma página de erro crua do SSO.

    Só o CÓDIGO sai daqui — nunca o corpo da página, que pode carregar
    identificadores do fluxo OAuth (`state`, `nonce`, e em algumas etapas o
    documento do contribuinte).

    Devolve "" quando não é página de erro.
    """
    try:
        titulo = (page.title() or "").lower()
    except Exception:  # noqa: BLE001
        return ""

    if titulo and len(titulo) <= 120:
        for codigo, *marcas in _ERROS_HTTP_DO_SSO:
            if codigo in titulo or any(m in titulo for m in marcas):
                return codigo

    # SEM título: o corpo decide, e só nesse caso.
    #
    # Página sem `<title>` faz o Chrome usar a URL como nome da aba, e
    # `page.title()` volta vazia. Era descartado aqui mesmo, antes de olhar
    # qualquer coisa — e "404" e "not found" já estavam na lista de marcas há
    # semanas, sem nunca terem chance de casar.
    #
    # Capturado em print pelo Jean em 10/09/2026, run 90ef18c4, DURANTE o
    # login: `servicos.receitafederal.gov.br/home` respondendo
    #
    #     404 Not Found
    #     The requested URL was not found.
    #
    # com a aba nomeada pela URL. Este `/home` nao e nosso — `SERVICOS_RF_URL`
    # termina em `/`; e o proprio portal que redireciona para la depois do
    # certificado, e a rota nao existe. Sem deteccao, o laco gastava os 60s.
    #
    # O limite de tamanho e o que torna a inspecao segura: pagina de erro crua
    # tem duas linhas. Portal de verdade tem menus, rodape e marca — nao passa
    # nem perto. E continua saindo so o CODIGO, nunca o corpo.
    # TERCEIRA variante do 404 num dia. As duas primeiras foram resolvidas por
    # URL e por corpo-sem-titulo; esta tem URL limpa (a raiz do portal) E
    # titulo de verdade — "Portal de Servicos Digitais da Receita Federal" —,
    # e so o corpo denuncia:
    #
    #     Portal nao encontrado
    #     Talvez voce tenha se equivocado ao digitar o endereco URL...
    #
    # E a pagina 404 ESTILIZADA do portal, com cabecalho e marca. Por isso o
    # corpo deixa de ser consultado apenas quando falta titulo.
    #
    # Continua opcional: ler o corpo custa uma ida ao navegador, e quem chama
    # num laco de 60 iteracoes decide a frequencia — o mesmo cuidado que
    # `_limite_de_dispositivos` ja recebe ali (`if _seg % 3 == 0`).
    if titulo and not inspecionar_corpo:
        return ""
    try:
        corpo = (page.inner_text("body", timeout=2_000) or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""
    if len(corpo) > 300:
        return ""
    for codigo, *marcas in _ERROS_HTTP_DO_SSO:
        if codigo in corpo or any(m in corpo for m in marcas):
            return codigo
    return ""


def sessao_derrubada_pelo_portal(page) -> str:
    """O portal descartou a sessao do certificado e disse o codigo. Ou "".

    Le a URL, nao a pagina. Quando o certificado nao e apresentado, o portal
    manda o navegador para

        servicos.receitafederal.gov.br/?logoutCertificadoDigital=1&codErro=30002

    e essa URL nao existe — a tela e um "404 Not Found" cru. `pagina_de_erro_http`
    nao pega: ela le o TITULO procurando erro do SSO, e aqui o host e o certo e
    o titulo e generico. Resultado: o laco de redirecionamento gastava os 60s
    perguntando a um 404 se ele ja virou portal.

    Capturado em print pelo Jean em 10/09/2026, run aa762acf, com
    `[cert-dialog] Janela nao apareceu.` quarenta linhas acima — o dialogo do
    certificado nunca abriu, entao nao havia o que apresentar.

    Recarregar nao resolve: a URL 404 continua 404. O que resolve e refazer a
    entrada pelo gov.br, que e o que a tentativa seguinte do laco do
    certificado ja faz — ela so precisava ser alcancada sessenta segundos
    antes.

    So o CODIGO sai daqui, pelo mesmo motivo de `pagina_de_erro_http`: a query
    string carrega identificadores do fluxo OAuth.
    """
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        return ""
    if "logoutcertificadodigital" not in url.lower():
        return ""
    achado = re.search(r"codErro=(\w{1,12})", url, re.I)
    return achado.group(1) if achado else "sem-codigo"


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
    if not _clicar_entrar_govbr(page):
        print("[bloqueado] Botão 'Entrar com gov.br' não encontrado após go_back.")
        return False
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
    except Exception:  # noqa: BLE001, S110 — a espera de load é best-effort
        pass

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
# DEIXOU DE SER ALLOWLIST EM 04/09/2026: agora TENTA TODO desafio.
#
# Decisão do Jean, depois da RUN-4ef0d17d, e ele já tinha pedido `grade_fused`
# de volta antes — foi julgamento meu mantê-lo fora, e estava errado. O que a
# run mostrou:
#
#     20:07:54.038  [cnpj] Clicando em Representar...
#     20:08:00.115  [captcha] Tipo: grade fused (0 tiles, 520x402px ratio=1.29)
#     20:08:00.115  [cnpj] Desafio requer validação manual | tipo=grade_fused
#
# Seis segundos entre pedir a representação e desistir, com ZERO chamada ao
# modelo. A empresa foi para pendência humana sem que nada fosse tentado.
#
# O raciocínio original da allowlist — "formato novo cai no caminho humano por
# construção, nunca por esquecimento" — protegia contra um custo que não existe
# mais. Ele nasceu de uma run em que `grade_fused` "passou a chamar o modelo
# repetidamente", e isso era um laço sem teto: as rodadas internas dos
# resolvedores não checavam o orçamento, só a cadeia de modelos checava. Com o
# teto por rodada corrigido no ResolvedorCaptcha e o orçamento POR TIPO abaixo,
# uma tentativa que não dá certo custa segundos limitados e termina no mesmo
# lugar em que teria terminado sem tentar: no humano.
#
# Ou seja: o que a allowlist comprava era evitar gasto; o que ela custava era
# desistir sem tentar. Depois do conserto do laço, o preço ficou maior que o
# benefício.
#
# `TIPO_NENHUM` fica fora porque não há desafio para resolver — não é política,
# é ausência de objeto. A intervenção humana continua existindo, e é para onde
# vai quem o resolvedor TENTOU e não conseguiu.
TIPOS_AUTOMATICOS_REPRESENTACAO = (TIPO_GRADE, TIPO_GRADE_FUSED, TIPO_BOLA,
                                   TIPO_CARTAO_ANIMAL, TIPO_IMAGEM,
                                   TIPO_DESCONHECIDO)

# Janela curta para o SPA refletir a troca antes de concluirmos que ela não
# ocorreu. Curta de propósito: quando há captcha, esperar mais não muda nada.
ESPERA_POS_CONDICAO_S = 8.0
INTERVALO_POS_CONDICAO_S = 0.5

# Mensagem de erro da representação. A CLASSE é o contrato; o texto, não — ele
# muda e pode passar a carregar informação de quem se tenta representar.
SEL_MENSAGEM_ERRO_REPRESENTACAO = ".mensagemErro"

# Uma tentativa é a OPERAÇÃO inteira: formulário, envio, desfecho e captcha.
MAX_TENTATIVAS_REPRESENTACAO = 3

# Quantas RECUSAS bastam para desistir, sem gastar as três tentativas.
#
# Recusa não é ausência de resposta: o portal respondeu, e respondeu não. As
# três tentativas existem para o caso de ele NÃO responder — outra coisa, com
# outro motivo. Duas recusas seguidas indicam que a resposta é a resposta, e a
# terceira paga mais 31 s de intervalo e mais um captcha para ouvir de novo.
#
# Duas, e não uma, porque não há medição dizendo que recusa nunca é transitória.
# Enquanto não houver, a segunda tentativa é o preço de não decidir por dedução.
# Se um dia se medir que a primeira recusa nunca reverte, isto vira 1.
#
# O critério é a CONTAGEM, e não o texto da mensagem: `_erro_representacao_visivel`
# não lê a frase de propósito — ela muda com o tempo e pode carregar informação
# de quem se tenta representar. Contar recusas respeita essa decisão.
RECUSAS_PARA_DESISTIR = 2

# O portal pediu "pelo menos 30 segundos". Margem mínima e determinística: não
# há jitter nem randomização — isto é respeito ao throttle observado, não
# técnica para parecer outra coisa.
COOLDOWN_ERRO_REPRESENTACAO_S = 31.0

# Orçamento de tempo do captcha DA REPRESENTAÇÃO. O padrão do resolvedor (30 s
# por chamada, sem teto total) é o certo para o captcha de login e caro demais
# aqui: numa execução real dois timeouts consecutivos consumiram mais de um
# minuto, e o portal recusou a representação em seguida.
TIMEOUT_GEMINI_REPRESENTACAO_MS = 10_000
DEADLINE_CAPTCHA_REPRESENTACAO_S = 55.0

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

# 08/09/2026: este valor CAIU de 60 s para 40 s, e isso é conserto, não recuo.
#
# Com 60 s ele era igual ao teto duro `DEADLINE_MAX_COM_PROGRESSO_S`, e a
# extensão por progresso — criada justamente para o caso "resolveu a primeira
# rodada e foi cortado na segunda" — valia ZERO neste formato. A run das 15:24
# provou: classificou `bola_em_movimento` corretamente, com a versão nova
# instalada, e não ganhou um segundo.
#
# 40 s cobrem UMA rodada com folga (abertura 5,6 + captura 7 + preparo 1 +
# chamada até 14 = ~28 s), e a segunda rodada vem da extensão, até os 60 s. O
# resultado é melhor nas duas pontas:
#
#     desafio que nunca fecha rodada  ->  desiste em 40 s, não em 60
#     desafio que fecha a primeira    ->  chega aos mesmos 60 s de antes
# 08/09/2026, segunda mudança do dia: 40 s -> 58 s.
#
# A captura passou a ser PROGRESSIVA — rodada 1 com 7 s, rodada 2 com 15 s — e a
# aritmética do pior caso, com os tempos medidos, é:
#
#     rodada 1   abertura 5,6 + captura 7 + preparo 1 + chamada 14 + espera 3
#                = 30,6 s
#     rodada 2   captura 15 + preparo 1 + chamada 14 + espera 3 = 33,0 s
#     total      63,6 s
#
# Com 40 s a rodada 2 nem começava, e ela é justamente a que olha o dobro do
# tempo — a única com chance no formato da abelha.
#
# A extensão por progresso NÃO ajuda aqui: ela exige uma rodada CONCLUÍDA, e no
# caso da abelha nenhuma fecha. Por isso o orçamento inicial precisa cobrir as
# duas sozinho.
#
# O CUSTO É REAL e vale dito: uma abelha que não fecha passa a gastar ~58 s por
# empresa, contra 40 s antes. Foi decisão do Jean pedir captura mais longa, e
# esse é o preço dela.
DEADLINE_CAPTCHA_BOLA_S = 58.0

# Orçamento do CLIQUE ÚNICO em imagem livre ("clique na figura diferente",
# "clique no ícone que quebra o padrão").
#
# Ele estava herdando os 25 s da grade 3x3, e a comparação não se sustenta: a
# grade responde numa chamada sobre um screenshot parado, enquanto
# `_solve_imagem` tem CINCO rodadas, cada uma com dois screenshots e uma chamada
# ao modelo. Com 25 s ele não passa da segunda — o resolvedor certo era chamado
# e cortado no meio.
#
# Medido em 08/09/2026, contra as amostras arquivadas deste formato: a chamada
# leva 5,2 a 7,9 s. Uma rodada custa ~2 s de captura + a chamada, então:
#
#     3 rodadas (o rodízio ouve os três modelos do Gemini)   ~30 s
#     4ª rodada, já no segundo provedor                      ~10 s
#
# 45 s cobrem isso. NÃO são os 60 s da bola porque aqui não há captura de
# animação — a de 7 s por rodada é o que faz a bola custar o dobro.
#
# O astra entra na 4ª rodada (ver `_gemini_call`), e com 25 s ele nunca era
# alcançado: a carta de acurácia existia e não chegava a ser jogada neste
# formato, que é justamente onde ele mediu 3/3.
#
# Teto superior continua sendo o do portal: 70,3 s é a maior representação
# CONFIRMADA no histórico. 45 s ficam 25 s abaixo.
TIMEOUT_GEMINI_IMAGEM_MS = 12_000
DEADLINE_CAPTCHA_IMAGEM_S = 58.0


# TETO DURO, alcançável só com progresso comprovado.
#
# O hCaptcha faz DUAS rodadas por desafio. Um teto contado desde o início não
# sabe disso: resolve a primeira e é cortado no meio da segunda, jogando fora o
# trabalho já feito. Relatado pelo Jean em 08/09/2026 — "solucionou o primeiro,
# e depois o outro não".
#
# Aumentar o teto fixo resolveria isso e pagaria caro no caso ruim: um desafio
# que nunca fecha rodada nenhuma consumiria o teto inteiro antes de desistir.
# Por isso a extensão é CONDICIONAL — só ganha tempo quem submeteu uma rodada
# com sucesso e viu outra aparecer.
#
# 60 s ficam 10,3 s abaixo dos 70,3 s da maior representação CONFIRMADA no
# histórico de dev. É o mesmo teto da bola, e pelo mesmo motivo: é o limite do
# portal que manda, não o do resolvedor.
DEADLINE_MAX_COM_PROGRESSO_S = 60.0


def _orcamento_do_captcha(tipo: str) -> tuple[int, float]:
    """(timeout por chamada, teto total) do tipo — cada um com a sua medida."""
    if tipo == TIPO_BOLA:
        return TIMEOUT_GEMINI_BOLA_MS, DEADLINE_CAPTCHA_BOLA_S
    if tipo == TIPO_IMAGEM:
        return TIMEOUT_GEMINI_IMAGEM_MS, DEADLINE_CAPTCHA_IMAGEM_S
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
DESFECHO_BLOQUEIO_AUTOMACAO = "bloqueio_automacao"
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


class LimiteDeDispositivosGovBr(RuntimeError):
    """O gov.br recusou a conta por excesso de dispositivos conectados.

    A tarja aparece no topo do portal, ANTES de qualquer autenticacao:

        "Voce atingiu o numero maximo de dispositivos conectados
         simultaneamente com esta conta. Saia da sua conta em um dos
         dispositivos para entrar por aqui."

    Nao adianta insistir. Nao e captcha, nao e lentidao, nao e certificado: a
    conta esta bloqueada para novas sessoes ate alguem desconectar um
    dispositivo em acesso.gov.br, ou ate as sessoes antigas expirarem sozinhas
    (~30 min). Toda tentativa nesse intervalo morre no mesmo lugar, e cada uma
    delas ainda consome um desafio de captcha e o orcamento inteiro do login.

    Por isso encerra na hora, com motivo proprio: uma empresa marcada assim e
    reprocessavel mais tarde sem trabalho manual, enquanto "falhou no login"
    exigiria alguem abrir o log para descobrir que a causa nem estava aqui.
    """


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


class BloqueioPorAutomacao(RuntimeError):
    """O portal barrou a SESSAO por parecer automatizada, ao representar.

    Nao e recusa de procuracao, e por isso nao pode virar `perfil_recusado`.
    A frase, capturada em print pelo Jean em 10/09/2026, e explicita:

        O seu acesso foi bloqueado por possuir atributos que o caracteriza
        como um acesso automatizado. Favor tentar novamente.

    Ela vem no MESMO `.mensagemErro` que a recusa de procuracao, e por isso
    era classificada como recusa. O estrago nao para na tentativa perdida:
    duas recusas viram `RepresentacaoRejeitadaPeloPortal`, o runner reporta
    `perfil_recusado`, e o Save Process marca a empresa com
    `procuracao_cancelada_em` — mandando o juridico cobrar do cliente uma
    procuracao que provavelmente esta perfeita.

    E estado da CONTA, nao da empresa: a proxima empresa bate no mesmo muro,
    gastando um login e um captcha para ouvir o mesmo. Termina a run.
    """


# Desfechos em que o login esta BOM e so a representacao nao fechou.
#
# Sao respostas do portal, nao falhas de navegador: a sessao gov.br continua de
# pe e serve a proxima empresa do mesmo certificado. Por isso a excecao leva a
# sessao anexada em vez de o navegador ser fechado — ver o `except` em `main`.
#
# `BloqueioPorAutomacao` fica DE FORA de proposito: ali a sessao e justamente o
# que o portal recusou, e reaproveita-la e insistir no que causou o bloqueio.
def _desfechos_com_sessao_viva():
    return (
        RepresentacaoNaoConfirmada,
        RepresentacaoRejeitadaPeloPortal,
        RepresentacaoRequerIntervencao,
        RepresentacaoCancelada,
        RepresentacaoExpirada,
        FalhaDoResolvedorCaptcha,
    )


class RepresentacaoRequerIntervencao(RuntimeError):
    """Há captcha na representação e ninguém pode resolvê-lo nesta execução."""


class RepresentacaoCancelada(RuntimeError):
    """A validação manual foi cancelada por quem operava."""


class RepresentacaoExpirada(RuntimeError):
    """A validação manual não foi concluída dentro do prazo."""


# Materializada aqui, depois de todas as classes existirem.
DESFECHOS_COM_SESSAO_VIVA = _desfechos_com_sessao_viva()


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


# Pausa curta e VARIAVEL entre acoes do formulario.
#
# O que denuncia nao e a velocidade — e a REGULARIDADE. Uma pessoa hesita, e
# hesita diferente a cada vez; um robo faz tudo no mesmo intervalo, ou em
# intervalo nenhum. Ate 10/09/2026 este formulario era preenchido inteiro em
# milissegundos: o avatar abria, o CNPJ de 14 digitos aparecia de uma vez, e
# tres cliques saiam em sequencia sem respiro.
#
# A conta foi bloqueada por atividade automatizada duas vezes naquele dia, com
# login manual passando liso na MESMA maquina e no MESMO IP — o que descarta
# conta, maquina e endereco, e deixa comportamento.
#
# Curto de proposito. Em 09/09/2026 eu tinha acabado de cortar 3min30s de
# espera morta por empresa, e devolver aquilo seria trocar um problema por
# outro. O orcamento aqui e de poucos segundos por empresa, e ele compra
# variacao, nao lentidao.
_PAUSA_MIN_S = 0.35
_PAUSA_MAX_S = 1.10
_TECLA_MIN_MS = 55
_TECLA_MAX_MS = 130


def _pausa_humana(minimo: float = _PAUSA_MIN_S, maximo: float = _PAUSA_MAX_S) -> None:
    """Hesitacao antes da proxima acao, com duracao sorteada."""
    import random
    time.sleep(random.uniform(minimo, maximo))


def _digitar_humano(campo, texto: str) -> None:
    """Uma tecla por vez, com intervalo sorteado entre elas.

    `fill()` injeta o valor inteiro num evento so — um CNPJ de 14 digitos
    aparece instantaneamente, o que nenhum teclado produz. `press_sequentially`
    emite os eventos de teclado de verdade, e o `delay` sorteado por caractere
    evita a cadencia perfeita, que e tao artificial quanto a instantanea.
    """
    import random
    try:
        campo.press_sequentially(
            texto, delay=random.randint(_TECLA_MIN_MS, _TECLA_MAX_MS))
    except Exception:  # noqa: BLE001
        # Versao antiga da API, ou campo que recusa digitacao: preencher e
        # melhor do que falhar a representacao inteira por causa da cadencia.
        campo.fill(texto)


def _preencher_formulario_representacao(page, cnpj: str) -> None:
    """Abre o avatar, preenche o identificador, escolhe Procurador e envia."""
    print("[cnpj] Clicando no avatar...")
    avatar = page.locator('#avatar-dropdown-trigger').first
    avatar.wait_for(state="visible", timeout=20_000)
    avatar.click()
    _pausa_humana()

    print("[cnpj] Preenchendo identificador do perfil PJ...")
    campo = page.locator('#input-representar-cpfcnpj').first
    campo.wait_for(state="visible", timeout=10_000)
    campo.click()
    _digitar_humano(campo, cnpj)
    _pausa_humana()

    print("[cnpj] Selecionando Procurador...")
    ng_select = page.locator(
        'xpath=//*[@id="formularioRepresentacao"]/form/div/div[2]'
        '/br-select/div/div/div[1]/ng-select'
    ).first
    ng_select.wait_for(state="visible", timeout=10_000)
    ng_select.click()
    _pausa_humana()

    opcao = page.get_by_role("option", name="Procurador").first
    opcao.wait_for(state="visible", timeout=5_000)
    opcao.click()
    # Antes de ENVIAR a pausa e um pouco maior: e o ponto em que uma pessoa
    # confere o que digitou.
    _pausa_humana(0.6, 1.6)

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


# A frase do bloqueio por automacao, dentro do mesmo `.mensagemErro`.
#
# Casar por trecho curto e em minusculas: o portal ja mudou a redacao antes, e
# "acesso automatizado" e o nucleo que sobrevive. Nao guardamos o texto inteiro
# em log — ele pode carregar quem se tenta representar.
_MARCA_BLOQUEIO_AUTOMACAO = "acesso automatizado"


def _erro_e_bloqueio_por_automacao(page) -> bool:
    """Le a mensagem de erro SO para separar bloqueio de recusa.

    O `_erro_representacao_visivel` decide que HA erro pela classe, e faz certo:
    a classe e o contrato. Mas QUAL erro e outra pergunta, e essa so o texto
    responde — sao desfechos opostos. Recusa de procuracao e da empresa e o
    juridico resolve; bloqueio por automacao e da sessao e a run tem de parar.
    """
    try:
        locator = page.locator(SEL_MENSAGEM_ERRO_REPRESENTACAO)
        for i in range(locator.count()):
            alvo = locator.nth(i)
            if not alvo.is_visible():
                continue
            texto = (alvo.inner_text(timeout=2_000) or "").lower()
            if _MARCA_BLOQUEIO_AUTOMACAO in texto:
                return True
    except Exception:  # noqa: BLE001 — sem prova, segue como recusa comum
        pass
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
            # Qual erro, antes de chamar de recusa.
            if _erro_e_bloqueio_por_automacao(page):
                return DESFECHO_BLOQUEIO_AUTOMACAO
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
            # O tipo vai JUNTO. Sem isto o solver reclassificava por dentro, e
            # era a segunda decisão que escolhia o resolvedor — duas leituras
            # independentes da mesma tela, que podem discordar. Discordaram em
            # 08/09/2026: aqui deu `bola_em_movimento` (sonda 0,33%) e lá dentro
            # `grade_fused` (sonda 0,25%), com limiar em 0,3%. O desafio animado
            # foi para o resolvedor de quadro parado, respondeu certo três vezes
            # e teve as três descartadas pelo guardião de frescor — porque num
            # desafio que se mexe a impressão digital muda sempre.
            #
            # Também torna verdadeiro o que o comentário da classificação acima
            # já afirmava: "classificação UMA vez, aqui".
            automatico = solve_hcaptcha(
                page,
                gemini_timeout_ms=timeout_ms,
                deadline_s=deadline_s,
                # Fôlego extra SÓ com progresso comprovado — ver
                # DEADLINE_MAX_COM_PROGRESSO_S. Resolver a primeira rodada e ser
                # cortado na segunda desperdiça o trabalho já feito.
                deadline_max_s=DEADLINE_MAX_COM_PROGRESSO_S,
                tipo_ja_classificado=tipo)
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

            # Bloqueio da SESSAO nao gasta tentativa: nao ha o que retentar.
            #
            # Cai fora na hora, sem o intervalo de 31s e sem consumir recusa.
            # Insistir aqui e o pior movimento possivel: cada nova ida reforca
            # o sinal que causou o bloqueio, e o custo e um login e um captcha
            # por empresa para ouvir a mesma frase.
            if desfecho == DESFECHO_BLOQUEIO_AUTOMACAO:
                print("[cnpj] Portal bloqueou o acesso por considerar a sessao "
                      "automatizada. Nao e recusa de procuracao — encerrando.")
                raise BloqueioPorAutomacao(
                    "o portal bloqueou o acesso por caracterizar a sessao como "
                    "automatizada. A procuracao NAO foi recusada; a sessao foi. "
                    "Nenhuma empresa restante foi consultada.")

            if desfecho == DESFECHO_ERRO_PORTAL:
                if not recusou:
                    recusas += 1
                    recusou = True
                if recusas >= RECUSAS_PARA_DESISTIR:
                    print(f"[cnpj] Portal recusou {recusas} vezes — a resposta "
                          "é a resposta. Não há terceira tentativa.")
                    break
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

        # As DUAS saídas: o teto de tentativas e o teto de recusas.
        #
        # O `break` lá dentro sai só do `while` — o `for` continuava para a
        # tentativa seguinte, `recusou` era reposto a False, e a recusa nova
        # contava de novo. Medido na RUN-039e0604: três empresas com "o portal
        # recusou a representacao 3 vez(es)", com o limite valendo 2.
        #
        # A mensagem já era a nova, o que provava que o código estava no ar e
        # mesmo assim gastava a terceira tentativa. Guarda que existe e não
        # interrompe é pior que guarda nenhuma: dá a impressão de estar
        # protegendo.
        if tentativa == MAX_TENTATIVAS_REPRESENTACAO or recusas >= RECUSAS_PARA_DESISTIR:
            break
        _restaurar_formulario(page)

    if recusas >= RECUSAS_PARA_DESISTIR:
        raise RepresentacaoRejeitadaPeloPortal(
            f"o portal recusou a representacao {recusas} vez(es).")
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
    policy_ok: bool | None = None,
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

    # `None` (o padrão) significa DESCUBRA, não "assuma que sim". Quem chama
    # pode continuar forçando o valor; ninguém mais é obrigado a saber como a
    # máquina está configurada para que o login funcione nela.
    if policy_ok is None:
        policy_ok = politica_de_certificado_instalada()
        print(f"[cert] Política AutoSelectCertificateForUrls: "
              f"{'encontrada' if policy_ok else 'AUSENTE — o diálogo será fechado pelo fallback'}.")

    # --- Modo A: certificado do Windows Certificate Store (via CN) ---
    #
    # O CN também vem do AMBIENTE quando o parâmetro não foi passado.
    #
    # Quem instala o certificado já publica `CERT_SUBJECT_CN` no ambiente antes
    # de chamar o login — mas esta função lia só o parâmetro. Resultado: o CN
    # existia, ninguém lia, `usar_windows_store` ficava False e caíam TRÊS
    # coisas de uma vez: a flag de auto-seleção não era montada, o fallback que
    # fecha o diálogo não era armado, e o login caía no modo .pfx, que este
    # próprio arquivo documenta como quebrado com ICP-Brasil (o proxy TLS do
    # Node responde SSL alert 40).
    #
    # O diálogo "Selecione um certificado" ficava aberto esperando uma pessoa.
    if not (cert_subject_cn and cert_subject_cn.strip()):
        do_ambiente = os.getenv("CERT_SUBJECT_CN", "").strip()
        if do_ambiente:
            cert_subject_cn = do_ambiente
            print("[cert] CN lido do ambiente (CERT_SUBJECT_CN).")
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
    # `--remote-debugging-port=9222` SAIU daqui em 10/09/2026.
    #
    # Ninguem se conectava nela: era a unica referencia a 9222 nos tres repos.
    # Em troca custava duas coisas. Marcador de automacao — porta de depuracao
    # aberta e um dos sinais que fingerprinting procura. E buraco de seguranca:
    # qualquer processo local podia se conectar e dirigir um navegador
    # autenticado no e-CAC, com certificado carregado.
    #
    # `--disable-blink-features=AutomationControlled` entrou junto. Sem ele
    # `navigator.webdriver` responde `true`, e a pagina descobre que e automacao
    # em uma linha de JavaScript. Isso importa direto no problema de hoje: o
    # hCaptcha escala dificuldade quando desconfia do cliente, e em 09/09/2026
    # uma sessao levou 17 desafios seguidos. Estavamos otimizando o RESOLVEDOR
    # enquanto o portal ja tinha classificado a sessao.
    chrome_args = [
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
    ]
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
        # O Playwright adiciona `--enable-automation` por conta propria, e e ele
        # que produz a barra "o Chrome esta sendo controlado por um software de
        # teste" e alimenta os mesmos sinais que o `AutomationControlled`
        # desliga. Tirar a flag sem tirar o default seria meio caminho.
        ignore_default_args=["--enable-automation"],
        # Sem isto o patchright acrescenta `--no-sandbox` sozinho:
        #
        #     if (options2.chromiumSandbox !== true)
        #       chromeArguments.push("--no-sandbox");
        #
        # E o Chrome exibe a tarja amarela "Você está usando uma sinalização
        # de linha de comando não suportada: --no-sandbox". Quem normalmente
        # SUPRIME essa tarja é `--enable-automation` — que a linha acima tira,
        # de proposito, para matar a outra tarja. Uma correcao de deteccao
        # destapou um aviso que anuncia automacao em letras garrafais.
        #
        # Visto em print da tela em 10/09/2026, com a tarja no topo do e-CAC.
        # Ela ainda empurra a pagina uns 40px para baixo, o que desloca tudo
        # que se mede por coordenada.
        #
        # Aqui e Windows com Chrome real: nao ha motivo para desligar o
        # sandbox. Ligado, a flag nao e passada e a tarja nao existe.
        chromium_sandbox=True,
        # Nenhuma permissao concedida, e — o que importa — nenhum BALAO.
        #
        # Declarar a lista (ainda que vazia) faz o Playwright responder aos
        # pedidos de permissao pelo protocolo, em vez de deixar o Chrome
        # desenhar a bolha nativa. Sem isto, perfil novo + `headless=False`
        # produz o balao de verdade na tela.
        #
        # Capturado em print pelo Jean em 10/09/2026:
        #
        #     sso.acesso.gov.br quer / Saber sua localizacao
        #     [Permitir ao acessar o site] [Permitir desta vez] [Nunca permitir]
        #
        # Bolha nativa nao e elemento da pagina: nenhum seletor a fecha, e ela
        # fica por cima. E a mesma familia do modal de cookies e do tutorial —
        # UI atravessando a automacao —, so que esta nem e do site.
        #
        # Vazia, e nao `["geolocation"]`: conceder entregaria a localizacao
        # real da VM sem necessidade. O que se quer e que o pedido seja
        # RESPONDIDO, nao atendido.
        permissions=[],
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

        # A tela ABRIU, mas abriu o quê? Página de erro não tem botão nenhum.
        #
        # Este projeto já fez este mesmo argumento para o botão do
        # CERTIFICADO, e a proteção ficou só lá: "a automação caça 'Seu
        # certificado digital' dentro de uma tela de 408/404, onde ele
        # legitimamente não existe, e reporta 'botão não encontrado' —
        # mandando quem lê investigar o seletor, que é o lugar errado".
        #
        # Vale igual aqui, e hoje custou caro. Medido em 11/09/2026,
        # RUN-7e9b0015, com perfil novo `sessao-3b166773531c`:
        #
        #     -> página inicial carregada.
        #     Clicando em 'Entrar com gov.br'...
        #     -> botão 'Entrar com gov.br' não encontrado em nenhum seletor.
        #     Login concluído em 15.4s
        #
        # Quinze segundos — o teto do primeiro seletor — gastos varrendo uma
        # tela que não tinha o botão. E o portal produziu TRÊS variantes de
        # 404 só em 10/09: a crua sem título, a estilizada com título da
        # marca, e o `?logoutCertificadoDigital&codErro`. `inspecionar_corpo`
        # cobre as três.
        #
        # Recarregar uma vez antes de desistir: as três variantes que vimos
        # foram transitórias — o Jean deu refresh à mão numa delas e o portal
        # voltou ao normal.
        erro_inicial = pagina_de_erro_http(page, inspecionar_corpo=True)
        if erro_inicial:
            print(f"  -> portal respondeu HTTP {erro_inicial} na primeira "
                  "tela. Recarregando antes de procurar o botão.")
            try:
                page.goto(SERVICOS_RF_URL, wait_until="domcontentloaded",
                          timeout=30_000)
                page.wait_for_timeout(1_500)
                _fechar_popups_iniciais(page)
            except Exception:  # noqa: BLE001
                pass
            erro_inicial = pagina_de_erro_http(page, inspecionar_corpo=True)
            if erro_inicial:
                registrar_erro(
                    f"Login: portal respondeu HTTP {erro_inicial} na primeira "
                    "tela, e o recarregamento não resolveu.")
                return _abortar(p, context)

        # ANTES de gastar captcha: a tarja de limite de dispositivos aparece
        # ja na primeira tela, e nenhuma tentativa passa enquanto ela estiver
        # ali. Seguir daqui custaria um desafio de captcha e o orcamento do
        # login inteiro para terminar no mesmo lugar.
        if _limite_de_dispositivos(page):
            print("  -> gov.br: limite de dispositivos conectados. Encerrando "
                  "sem tentar autenticar.")
            _abortar(p, context)
            raise LimiteDeDispositivosGovBr(
                "gov.br recusou: número máximo de dispositivos conectados "
                "simultaneamente com esta conta. Desconecte um dispositivo em "
                "acesso.gov.br (Meus dispositivos conectados) ou aguarde as "
                "sessões antigas expirarem.")

        # Já logado é DESFECHO, não observação.
        #
        # Este `if` só imprimia. A execução seguia para `_clicar_entrar_govbr`
        # e procurava um botão que, estando logado, legitimamente não existe —
        # e a ausência dele era declarada falha de login.
        #
        # Medido em 10/09/2026, RUN-1aa3607c, duas empresas seguidas:
        #
        #     -> Redirecionado automaticamente. Login concluído.
        #     Clicando em 'Entrar com gov.br'...
        #     -> botão 'Entrar com gov.br' não encontrado em nenhum seletor.
        #     ERRO: Login: botão não encontrado em nenhum dos seletores.
        #
        # Três linhas de log em que a primeira já respondia a pergunta.
        #
        # Vira alcançável quando a sessão anterior NÃO morreu — e ela não
        # morre: o logout do Serviços RF ainda não está mapeado, e o log diz
        # isso em bom português ("seguirá ocupando um dispositivo no gov.br
        # até expirar"). Perfil com sessão viva entra sozinho. Ou seja, quanto
        # PIOR o encerramento, mais empresas caem aqui.
        #
        # O laço do certificado, logo abaixo, sempre teve a guarda certa
        # (`if _ja_logado(page): break`). Aqui ela faltava.
        ja_entrou = _ja_logado(page)
        if ja_entrou:
            print("  -> Redirecionado automaticamente. Login concluído.")

        # --- Clicar em "Entrar com gov.br" ---
        if not ja_entrou and not _clicar_entrar_govbr(page):
            registrar_erro("Login: botão 'Entrar com gov.br' não encontrado "
                           "em nenhum dos seletores.")
            try:
                shot = str(project_dir / "_debug_govbr_btn.png")
                page.screenshot(path=shot, full_page=True)
                print("     screenshot de debug gravado.")
            except Exception:  # noqa: BLE001, S110 — debug nunca derruba
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

            # Página de erro do SSO ANTES de procurar o botão.
            #
            # Sem isto a automação caça "Seu certificado digital" dentro de uma
            # tela de 408/404, onde ele legitimamente não existe, e reporta
            # "botão não encontrado" — mandando quem lê investigar o seletor,
            # que é o lugar errado. Foi o que aconteceu em 08/09/2026 às
            # 15:31:38 e 15:32:07.
            erro_antes = pagina_de_erro_http(page)
            if erro_antes:
                print(f"[cert] SSO respondeu HTTP {erro_antes} — não há tela de "
                      "certificado para procurar.")
                if tentativa == MAX_TENTATIVAS_CERT:
                    registrar_erro(
                        f"Login: SSO respondeu HTTP {erro_antes} antes da "
                        "escolha do certificado.")
                    return _abortar(p, context)
                if not _refazer_entrada_govbr(page):
                    registrar_erro(
                        "Login: não foi possível refazer a entrada pelo gov.br.")
                    return _abortar(p, context)
                continue

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
                # RECOMEÇAR É REFAZER O CAMINHO, não só recarregar.
                #
                # Antes isto fazia `goto(SERVICOS_RF_URL)` e caía direto no
                # `continue` — voltava para a HOME do portal e procurava ali o
                # botão "Seu certificado digital", que só existe DEPOIS de
                # clicar em "Entrar com gov.br". As tentativas 2 e 3 eram
                # perdidas por construção, e o sintoma é exatamente o relatado:
                # "ficou nessa tela inicial, sem tentativa nova".
                print("  -> Refazendo a entrada pelo gov.br e tentando novamente...")
                if not _refazer_entrada_govbr(page):
                    registrar_erro(
                        "Login: não foi possível refazer a entrada pelo gov.br.")
                    return _abortar(p, context)
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
                    # `perfil` escopa a busca ao processo DESTE Chrome. Sem
                    # ele a varredura olha o desktop inteiro — e em 10/09/2026
                    # casou com a janela do chat onde o problema estava sendo
                    # discutido, porque as palavras do diálogo estavam na tela.
                    kwargs={"timeout": 90.0, "perfil": user_data_dir},
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
            # O erro do SSO precisa SAIR do laço carregando o motivo, e não só
            # interrompê-lo: um `break` seco cairia no caminho de sucesso e a
            # automação anunciaria "login concluído" olhando uma tela de erro —
            # que é pior do que a espera cega que ele veio corrigir.
            erro_sso = ""
            for _seg in range(60):
                print(f"  -> ({_seg + 1}s) aguardando redirecionamento | "
                      f"host={host_da_url(page.url)}")
                if _ja_logado(page):
                    print("  -> Redirecionamento confirmado.")
                    break
                # Numa página de erro do SSO não há o que esperar: nenhum
                # seletor do portal existe ali. Sem isto a automação gasta os
                # 60 s inteiros contra uma tela que já respondeu, e o log só
                # mostra "aguardando redirecionamento" repetido — que foi o que
                # o Jean descreveu como "a automação se perde".
                # A cada 3s o corpo entra na conta — a variante estilizada do
                # 404 tem titulo legitimo e so o corpo a distingue. Mesma
                # cadencia do `_limite_de_dispositivos` abaixo, pela mesma
                # razao: e a checagem cara.
                erro_sso = pagina_de_erro_http(page, inspecionar_corpo=(_seg % 3 == 0))
                if erro_sso:
                    print(f"  -> Página de erro do SSO (HTTP {erro_sso}) — "
                          "não adianta esperar.")
                    break

                # `chromewebdata` é a página de erro DO CHROME, não do SSO.
                #
                # `pagina_de_erro_http` lê o título que o servidor devolveu, e
                # aqui não houve resposta nenhuma: a navegação morreu na rede
                # ou no handshake TLS. Nenhum seletor do portal vai aparecer,
                # nunca — esperar é esperar por nada.
                #
                # Medido em 10/09/2026, RUN-1e369205, C. CARVALHO GENEROSO: o
                # diálogo do certificado não foi lido a tempo, o certificado
                # nunca foi apresentado, e o handshake caiu. O host aparecia em
                # TODAS as sessenta linhas do laço, e nada olhava para ele:
                #
                #     -> (1s) aguardando redirecionamento | host=chromewebdata
                #     ... sessenta vezes ...
                #     -> Timeout aguardando o portal autenticado.
                #
                # Sai com motivo, e não com `break` seco, para não cair no
                # caminho de sucesso anunciando login concluído contra uma tela
                # de erro — a mesma razão que o `erro_sso` acima já documenta.
                # O portal derrubou a sessao do certificado e mandou o
                # navegador para uma URL que nao existe. Ver
                # `sessao_derrubada_pelo_portal`.
                derrubada = sessao_derrubada_pelo_portal(page)
                if derrubada:
                    erro_sso = f"portal descartou a sessao do certificado (codErro={derrubada})"
                    print(f"  -> Portal derrubou a sessao do certificado "
                          f"(codErro={derrubada}) e a URL de retorno e 404. "
                          "Refazendo a entrada em vez de esperar.")
                    break

                if host_da_url(page.url) == "chromewebdata":
                    erro_sso = "erro de rede do Chrome (certificado não apresentado?)"
                    print("  -> Página de erro do Chrome — a navegação nem "
                          "chegou ao servidor. Não adianta esperar.")
                    break

                # A tarja de dispositivos aparece AQUI, e não só na home.
                #
                # Medido em 09/09/2026, run 454a82dc: a checagem na primeira
                # navegação passou limpa, o gov.br e o certificado seguiram, e a
                # tarja surgiu no retorno — com a automação contando "(18s)
                # aguardando redirecionamento" contra uma tela que já tinha
                # respondido não. Sessenta segundos por tentativa, três
                # tentativas, e no fim um erro genérico de redirecionamento que
                # não menciona dispositivo nenhum.
                #
                # A cada 3s, e não a cada 1s: `inner_text("body")` não é de
                # graça, e a tarja não some sozinha — atrasar a deteção em dois
                # segundos não custa nada perto dos 180 que ela evita.
                if _seg % 3 == 0 and _limite_de_dispositivos(page):
                    print("  -> gov.br: limite de dispositivos conectados no "
                          "retorno do certificado. Encerrando.")
                    _abortar(p, context)
                    raise LimiteDeDispositivosGovBr(
                        "gov.br recusou: número máximo de dispositivos "
                        "conectados simultaneamente com esta conta. Desconecte "
                        "um dispositivo em acesso.gov.br (Meus dispositivos "
                        "conectados) ou aguarde as sessões antigas expirarem.")
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

            if erro_sso:
                # Página de erro é tentativa PERDIDA, não login concluído. O
                # fluxo é o mesmo do timeout: repete enquanto houver tentativa,
                # e só então desiste — com o CÓDIGO no log, que é o que separa
                # "o SSO recusou" de "o portal demorou".
                if tentativa == MAX_TENTATIVAS_CERT:
                    registrar_erro(
                        f"Login: SSO respondeu HTTP {erro_sso} no fluxo de "
                        "autorização.")
                    return _abortar(p, context)
                print(f"  -> Nova tentativa após HTTP {erro_sso} "
                      f"({tentativa}/{MAX_TENTATIVAS_CERT}).")
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
    except DESFECHOS_COM_SESSAO_VIVA as e:
        # O LOGIN passou; só a representação não fechou. A sessão gov.br está
        # de pé e serve a PRÓXIMA empresa do mesmo certificado.
        #
        # Abortar aqui é o que transforma "perdi uma empresa" em "perdi um
        # dispositivo". Medido em 11/09/2026, RUN-385b9699:
        #
        #     11:05:16  LEONARDO VIEIRA  -> Lançando Chrome   (login 1)
        #     11:07:45  LINHARES & CIA   -> Lançando Chrome   (login 2)
        #     11:10:10  dispositivos_maximo
        #
        # A LEONARDO caiu num captcha que não se automatiza, a sessão foi
        # descartada, e a empresa seguinte pagou outro login — outro
        # dispositivo no gov.br. Três desses derrubam a run inteira.
        #
        # O runner SEMPRE esperou isto. O `except CaptchaHumano` dele diz, em
        # bom português: "A sessão sobrevive: o captcha barrou esta empresa,
        # não o login, e as seguintes do mesmo certificado ainda a aproveitam".
        # Ele só nunca recebeu o que esperava.
        #
        # A página NÃO é devolvida como utilizável — isso continua valendo, e
        # é o que o comentário original protegia: sem representação confirmada,
        # consultar com o perfil pessoal dá 401. A sessão vai ANEXADA À
        # EXCEÇÃO, e quem trata decide. Mesma convenção que `SemProcuracao` já
        # usa do lado do runner.
        e.sessao = (p, context, page)
        raise
    except Exception:
        # Falha inesperada: encerra o Playwright para não vazar o event loop
        # (a próxima tentativa falharia com 'Sync API inside the asyncio loop').
        _abortar(p, context)
        raise

    return p, context, page
