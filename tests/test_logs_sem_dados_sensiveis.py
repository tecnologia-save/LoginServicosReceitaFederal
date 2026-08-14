"""Nenhum valor sensivel pode chegar ao stdout — NORMATIVO.

O runtime do AutoHub captura o stdout do processo: o agente transforma cada
`print` solto numa entrada de `logs` da run. Uma URL com query string impressa
aqui vira dado persistido na plataforma. A regra nao e "nao imprimir dados
sensiveis" no sentido frouxo — e que o VALOR nao pode existir na saida.

Este gate e portado do LoginEcac, com UMA diferenca que este repositorio
comprou caro: **propagacao de taint por variavel intermediaria**.

O vazamento real era

    _url_atual = page.url
    print(f"  -> ({_seg + 1}s) URL: {_url_atual}")

Um nivel de indirecao. O gate do LoginEcac procura `.url` interpolado no
`print` e nao veria isto — e este repositorio nao tinha teste algum, entao
ninguem veria. O gate abaixo primeiro coleta os nomes locais atribuidos a
partir de uma fonte proibida e so depois varre as chamadas de saida.

LIMITACAO REGISTRADA: um nivel de indirecao, nao um analisador de taint. Duas
atribuicoes encadeadas ainda escapariam.
"""
import ast
import re
from pathlib import Path

import pytest

from servicos_rf_login.login import host_da_url

FONTE = Path(__file__).resolve().parent.parent / "servicos_rf_login" / "login.py"

FUNCOES_DE_SAIDA = {"print", "registrar_erro"}

# Nomes cujo VALOR nunca pode ser interpolado numa mensagem.
NOMES_PROIBIDOS = {
    "cnpj", "cert_serial", "cert_subject_cn", "subject_cn",
    "cert_pfx_path", "cert_pfx_passphrase", "cert_path", "cert_pass",
    "senha", "password", "token",
    "shot", "downloads_dir", "user_data_dir", "project_dir", "prefs_file",
}
# Atributos proibidos, na forma `<algo>.<attr>`.
ATRIBUTOS_PROIBIDOS = {"url"}

# Funcoes que SANEIAM: o que sai delas ja e seguro por construcao, e por isso a
# varredura nao desce no argumento. Entrar aqui e decisao explicita — cada nome
# desta lista precisa de teste proprio provando o que ele descarta.
SANITIZADORES = {"host_da_url"}

URL_OAUTH_SENTINELA = (
    "https://sso.acesso.gov.br/authorize?response_type=code"
    "&state=SEGREDO_TESTE_STATE&nonce=SEGREDO_TESTE_NONCE"
    "&code_challenge=SEGREDO_TESTE_CHALLENGE&login_hint=99123456000188"
)


def _arvore():
    return ast.parse(FONTE.read_text(encoding="utf-8"))


def _fonte_proibida(no) -> bool:
    """O valor atribuido vem de algo que nao pode ser logado?"""
    for filho in ast.walk(no):
        if isinstance(filho, ast.Attribute) and filho.attr in ATRIBUTOS_PROIBIDOS:
            return True
        if isinstance(filho, ast.Name) and filho.id in NOMES_PROIBIDOS:
            return True
    return False


def _nomes_contaminados(arvore) -> set:
    """Variaveis locais que recebem valor de uma fonte proibida.

    E o que o gate do LoginEcac nao cobria — e por onde o vazamento passou.
    """
    contaminados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign) and _fonte_proibida(no.value):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name):
                    contaminados.add(alvo.id)
        elif isinstance(no, ast.AnnAssign) and no.value and _fonte_proibida(no.value):
            if isinstance(no.target, ast.Name):
                contaminados.add(no.target.id)
    return contaminados


def _chamadas_de_saida(arvore):
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        nome = getattr(no.func, "id", None) or getattr(no.func, "attr", None)
        if nome in FUNCOES_DE_SAIDA:
            yield no


def _saneada(no) -> bool:
    """A expressao interpolada e a chamada de um sanitizador?"""
    return (isinstance(no, ast.Call)
            and (getattr(no.func, "id", None)
                 or getattr(no.func, "attr", None)) in SANITIZADORES)


def _interpolacoes(chamada):
    """Nomes e atributos interpolados nos argumentos f-string da chamada."""
    for arg in ast.walk(chamada):
        if not isinstance(arg, ast.FormattedValue):
            continue
        if _saneada(arg.value):
            continue   # `host_da_url(page.url)` — o `.url` ja foi descartado
        for no in ast.walk(arg.value):
            if isinstance(no, ast.Name):
                yield no.id, ast.unparse(arg.value)
            elif isinstance(no, ast.Attribute):
                yield f".{no.attr}", ast.unparse(arg.value)


# ── Camada estatica ──────────────────────────────────────────────────────────

def test_nenhum_log_interpola_valor_sensivel():
    arvore = _arvore()
    proibidos = NOMES_PROIBIDOS | _nomes_contaminados(arvore)
    achados = []
    for chamada in _chamadas_de_saida(arvore):
        for simbolo, trecho in _interpolacoes(chamada):
            if simbolo in proibidos or simbolo.lstrip(".") in ATRIBUTOS_PROIBIDOS:
                achados.append(f"linha {chamada.lineno}: {{{trecho}}}")
    assert not achados, "log com valor sensivel:\n  " + "\n  ".join(achados)


def test_o_gate_detecta_o_vazamento_por_variavel_intermediaria():
    """Poder discriminante — reproduz o codigo EXATO que vazava.

    Sem a propagacao de taint este teste passaria, e foi assim que o vazamento
    sobreviveu a varredura de higiene desta migracao.
    """
    arvore = ast.parse(
        "def f(page):\n"
        "    for _seg in range(60):\n"
        "        _url_atual = page.url\n"
        '        print(f"  -> ({_seg + 1}s) URL: {_url_atual}")\n'
    )
    assert "_url_atual" in _nomes_contaminados(arvore)
    proibidos = NOMES_PROIBIDOS | _nomes_contaminados(arvore)
    simbolos = {s for c in _chamadas_de_saida(arvore) for s, _ in _interpolacoes(c)}
    assert simbolos & proibidos


def test_o_gate_detecta_interpolacao_direta():
    arvore = ast.parse(
        "def f(cnpj, page):\n"
        '    print(f"CNPJ: {cnpj}")\n'
        '    print(f"URL: {page.url}")\n'
    )
    simbolos = {s for c in _chamadas_de_saida(arvore) for s, _ in _interpolacoes(c)}
    assert "cnpj" in simbolos
    assert ".url" in simbolos


def test_nenhum_cnpj_numerico_no_modulo():
    """Nem em docstring: exemplo numerico vira dado plausivel em copia/cola."""
    assert not re.search(r"\b\d{11,14}\b", FONTE.read_text(encoding="utf-8"))


def test_nenhum_literal_do_modulo_monta_query_oauth():
    """`host=` e o unico formato autorizado para falar de endereco.

    Varre os LITERAIS de string, nao o texto cru: `wait_for(state="visible")` e
    keyword do Playwright e nao tem relacao com o `state` do OAuth.
    """
    achados = []
    for no in ast.walk(_arvore()):
        if isinstance(no, ast.Constant) and isinstance(no.value, str):
            for proibido in ("state=", "nonce=", "code_challenge=",
                             "?response_type", "access_token"):
                if proibido in no.value:
                    achados.append(f"linha {no.lineno}: {proibido}")
    assert not achados, "literal com parametro OAuth:\n  " + "\n  ".join(achados)


def test_o_sanitizador_nao_libera_a_url_crua():
    """Poder discriminante da allowlist: so `host_da_url(...)` passa."""
    arvore = ast.parse(
        "def f(page):\n"
        '    print(f"a {host_da_url(page.url)}")\n'
        '    print(f"b {page.url}")\n'
        '    print(f"c {str(page.url)}")\n'
    )
    achados = [t for c in _chamadas_de_saida(arvore)
               for s, t in _interpolacoes(c)
               if s.lstrip(".") in ATRIBUTOS_PROIBIDOS]
    assert achados == ["page.url", "str(page.url)"]


@pytest.mark.parametrize("sentinela", [
    URL_OAUTH_SENTINELA, "SEGREDO_TESTE_STATE", "99123456000188",
])
def test_sentinelas_nao_aparecem_no_codigo_fonte(sentinela):
    assert sentinela not in FONTE.read_text(encoding="utf-8")


# ── Camada dinamica: o helper que substituiu a URL crua ──────────────────────

def test_host_da_url_devolve_so_o_hostname():
    assert host_da_url(URL_OAUTH_SENTINELA) == "sso.acesso.gov.br"


def test_host_da_url_descarta_query_fragment_e_path():
    resultado = host_da_url(URL_OAUTH_SENTINELA + "#frag")
    for proibido in ("state", "nonce", "code_challenge", "authorize",
                     "99123456000188", "?", "#", "/"):
        assert proibido not in resultado


def test_host_da_url_nao_imprime_nada(capsys):
    host_da_url(URL_OAUTH_SENTINELA)
    saida = capsys.readouterr()
    assert "SEGREDO_TESTE" not in saida.out
    assert "SEGREDO_TESTE" not in saida.err


@pytest.mark.parametrize("entrada", ["", None, "nao-e-url", "http://", ":://x"])
def test_host_da_url_nunca_levanta_nem_devolve_a_entrada(entrada):
    """Log nao pode virar excecao, e o fallback nao pode ser a URL inteira."""
    resultado = host_da_url(entrada)
    assert isinstance(resultado, str)
    assert resultado == "?" or "/" not in resultado


def test_host_da_url_preserva_o_host_do_portal():
    assert host_da_url("https://servicos.receitafederal.gov.br/a/b?x=1") == (
        "servicos.receitafederal.gov.br")


# ── Excecao crua em log ──────────────────────────────────────────────────────
#
# Gate equivalente ao do ResolvedorCaptcha. Mensagem de excecao do Playwright
# embute seletor, URL de frame e trechos do DOM; a do gov.br pode trazer a URL
# do fluxo OAuth. So o NOME DA CLASSE pode sair.

def test_nenhum_log_interpola_a_excecao_capturada():
    """Varre o fonte inteiro, nao so os pontos ja conhecidos."""
    arvore = _arvore()
    ofensores = []
    for h in [n for n in ast.walk(arvore) if isinstance(n, ast.ExceptHandler)]:
        if not h.name:
            continue
        for no in ast.walk(h):
            if not (isinstance(no, ast.Call)
                    and (getattr(no.func, "id", None) in FUNCOES_DE_SAIDA
                         or getattr(no.func, "attr", None) in FUNCOES_DE_SAIDA)):
                continue
            texto = ast.unparse(no)
            for forma in (f"{{{h.name}}}", f"str({h.name})", f"repr({h.name})",
                          f"{h.name}.args"):
                if forma in texto:
                    ofensores.append(f"linha {no.lineno}: {forma}")
    assert ofensores == [], "excecao crua em log:\n  " + "\n  ".join(ofensores)


def test_o_gate_de_excecao_crua_detecta():
    """Poder discriminante — reproduz as formas que existiam no arquivo."""
    arvore = ast.parse(
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except Exception as e:\n"
        '        print(f"a: {e}")\n'
        '        print(f"b: {type(e).__name__}: {e}")\n'
    )
    achados = []
    for h in [n for n in ast.walk(arvore) if isinstance(n, ast.ExceptHandler)]:
        for no in ast.walk(h):
            if isinstance(no, ast.Call) and getattr(no.func, "id", None) == "print":
                if f"{{{h.name}}}" in ast.unparse(no):
                    achados.append(no.lineno)
    assert len(achados) == 2


def test_tipo_da_excecao_continua_permitido():
    """`type(e).__name__` e' diagnostico e nao carrega valor."""
    fonte = FONTE.read_text(encoding="utf-8")
    assert "type(e).__name__" in fonte
