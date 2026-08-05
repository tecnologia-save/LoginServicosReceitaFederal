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

from dotenv import load_dotenv

load_dotenv()

from patchright.sync_api import sync_playwright
from resolvedor_captcha import solve_hcaptcha

from .log_manager import registrar_erro

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
        print(f"[cert] senhas.json não encontrado em {CERT_DIR}")
        return {}
    try:
        return json.loads(senhas_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[cert] Erro ao ler senhas.json: {e}")
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
        print(f"[cert] Nenhum certificado (.pfx/.p12) encontrado em {CERT_DIR}")
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
        print(
            f"[cert] Múltiplos matches para '{nome}': "
            + ", ".join(substring_matches)
            + f". Usando '{substring_matches[0]}'."
        )
        return _retornar_cert(substring_matches[0], senhas)

    # ---- 3) Match fuzzy (difflib) ----
    stems = [Path(fn).stem.strip().lower() for fn in certs]
    close = difflib.get_close_matches(nome_lower, stems, n=1, cutoff=0.4)
    if close:
        idx = stems.index(close[0])
        print(f"[cert] Match fuzzy para '{nome}': '{certs[idx]}'.")
        return _retornar_cert(certs[idx], senhas)

    print(
        f"[cert] Nenhum certificado encontrado para '{nome}'. "
        f"Disponíveis: {', '.join(certs)}"
    )
    return None, None


def _retornar_cert(filename: str, senhas: dict) -> tuple[str, str] | tuple[None, None]:
    """Monta o caminho absoluto e busca a senha no senhas.json."""
    caminho = str(CERT_DIR / filename)
    senha = senhas.get(filename)
    if not senha:
        print(f"[cert] Senha não encontrada em senhas.json para '{filename}'.")
        return None, None
    print(f"[cert] Certificado selecionado: {filename}")
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
            print(f"[cert] Arquivo não encontrado: {cert_pfx_path}")
            return None, None
        print(f"[cert] Usando certificado passado por parâmetro: {cert_pfx_path}")
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
            print(f"[cert] Arquivo não encontrado: {path_env}")
            return None, None
        print(f"[cert] Usando certificado do .env (CERT_PFX_PATH): {path_env}")
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
    print(f"[download] Diretório configurado: {downloads_dir}")


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
            print(f"[{etapa}] tentativa {tentativa}/{max_attempts}: {type(e).__name__}: {e}")
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
        print(f"[bloqueado] go_back falhou ({e}). Recarregando URL de login...")
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
        print(f"[bloqueado] Botão 'Entrar com gov.br' não encontrado após go_back: {e}")
        return False

    return _try_solve_captcha(page, "captcha-pos-bloqueado")


# ---------------------------------------------------------------------------
# Representação de CNPJ
# ---------------------------------------------------------------------------

def _normalizar_cnpj(valor: str) -> str:
    """Remove formatação e retorna 14 dígitos."""
    return re.sub(r"\D", "", str(valor)).zfill(14)


def _representar_cnpj_procurador(page, cnpj: str) -> bool:
    """Representa o CNPJ como Procurador no portal Serviços RF.

    Fluxo: abre menu avatar → preenche CNPJ → seleciona Procurador → clica Representar.
    Até 3 tentativas em caso de falha.
    """
    cnpj = _normalizar_cnpj(cnpj)
    print(f"[cnpj] Iniciando representação do CNPJ {cnpj} como Procurador...")

    for tentativa in range(1, 4):
        if tentativa > 1:
            print(f"[cnpj] Tentativa {tentativa}/3...")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            time.sleep(1)

        try:
            # 1. Abre menu do avatar
            print("[cnpj] Clicando no avatar...")
            avatar = page.locator('#avatar-dropdown-trigger').first
            avatar.wait_for(state="visible", timeout=20_000)
            avatar.click()

            # 2. Preenche CNPJ
            print(f"[cnpj] Preenchendo CNPJ {cnpj}...")
            campo = page.locator('#input-representar-cpfcnpj').first
            campo.wait_for(state="visible", timeout=10_000)
            campo.fill(cnpj)

            # 3. Seleciona "Procurador" no dropdown
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

            # 4. Clica Representar
            print("[cnpj] Clicando em Representar...")
            btn = page.locator(
                'xpath=//*[@id="formularioRepresentacao"]/form/div/button'
            ).first
            btn.wait_for(state="visible", timeout=10_000)
            btn.click()

            print("[cnpj] Representação enviada.")
            return True

        except Exception as e:
            print(f"[cnpj] Erro na tentativa {tentativa}/3: {type(e).__name__}: {e}")
            if tentativa == 3:
                return False

    return False


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
        resultado = fazer_login(cert_subject_cn="EMPRESA LTDA:12345678000190", cnpj="...")

        # Modo B — legado, via .pfx:
        resultado = fazer_login(cert_name="DSR", cnpj="12345678000190")
    """
    if project_dir is None:
        project_dir = Path.cwd()
    project_dir = Path(project_dir)

    # --- Modo A: certificado do Windows Certificate Store (via CN) ---
    usar_windows_store = bool(cert_subject_cn and cert_subject_cn.strip())
    resolved_path = resolved_pass = None

    if usar_windows_store:
        os.environ["CERT_SUBJECT_CN"] = cert_subject_cn.strip()
        print(f"[cert] Usando certificado do Windows Store. CN: {cert_subject_cn.strip()}")
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
        print(f"[1ª navegação] Abrindo {SERVICOS_RF_URL} ...")
        try:
            page.goto(SERVICOS_RF_URL, wait_until="domcontentloaded", timeout=30_000)
            print(f"  -> URL: {page.url}")
        except Exception as e:
            print(f"  -> erro no goto: {type(e).__name__}: {e}")
            registrar_erro(f"Login: erro ao abrir URL (1ª navegação). {type(e).__name__}: {e}")
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
            registrar_erro(f"Login: botão 'Entrar com gov.br' não encontrado. {type(e).__name__}: {e}")
            print(f"  -> botão não encontrado: {type(e).__name__}: {e}")
            try:
                shot = str(project_dir / "_debug_govbr_btn.png")
                page.screenshot(path=shot, full_page=True)
                print(f"     screenshot: {shot}")
            except Exception:
                pass
            return _abortar(p, context)

        try:
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception:
            pass
        print(f"  -> URL após 'Entrar com gov.br': {page.url}")

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
                        print(f"     screenshot: {shot}")
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
            print(f"  -> URL após certificado: {page.url}")

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
                _url_atual = page.url
                print(f"  -> ({_seg + 1}s) URL: {_url_atual}")
                if _ja_logado(page):
                    print("  -> Redirecionamento confirmado.")
                    break
                time.sleep(1)
            else:
                print(f"  -> Timeout. URL final: {page.url}")
                if tentativa == MAX_TENTATIVAS_CERT:
                    registrar_erro(
                        f"Login: redirecionamento não ocorreu. URL atual: {page.url}"
                    )
                    try:
                        shot = str(project_dir / "_debug_pos_cert.png")
                        page.screenshot(path=shot, full_page=True)
                        print(f"     screenshot: {shot}")
                    except Exception:
                        pass
                    return _abortar(p, context)
                continue
            break

        print(f"Login nos Serviços RF concluído. URL final: {page.url}")

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

            print(f"Representando CNPJ {_normalizar_cnpj(cnpj)} como Procurador...")
            if not _representar_cnpj_procurador(page, cnpj):
                registrar_erro(f"Login: falha ao representar CNPJ {cnpj}.")
                print(f"[cnpj] Falha ao representar CNPJ {cnpj}. Retornando página sem representação.")
    except Exception:
        # Falha inesperada: encerra o Playwright para não vazar o event loop
        # (a próxima tentativa falharia com 'Sync API inside the asyncio loop').
        _abortar(p, context)
        raise

    return p, context, page
