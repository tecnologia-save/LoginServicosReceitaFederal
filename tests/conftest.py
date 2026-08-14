"""Fixtures compartilhadas do fluxo de representacao.

O duble do resolvedor mora aqui porque os dois arquivos de teste do fluxo
precisam dele: depois de clicar Representar, a tentativa AUTOMATICA acontece
antes de qualquer janela, entao nenhum teste do caminho manual roda sem ele.
Sem este duble, `solve_hcaptcha` de verdade seria chamado e levantaria por
falta de GEMINI_API_KEY.
"""
import pytest

from servicos_rf_login import login


@pytest.fixture
def relogio_virtual(monkeypatch):
    """`sleep` avanca o relogio que `monotonic` le.

    Sem isto a espera da pos-condicao e o deadline de 5 min viram espera REAL,
    e os testes passariam a depender do tempo da maquina.
    """
    agora = {"t": 1_000.0}
    monkeypatch.setattr(
        login.time, "sleep",
        lambda s=0.0: agora.__setitem__("t", agora["t"] + max(float(s or 0), 0.01)))
    monkeypatch.setattr(
        login.time, "monotonic",
        lambda: agora.__setitem__("t", agora["t"] + 0.001) or agora["t"])
    return agora


@pytest.fixture(autouse=True)
def detector(monkeypatch):
    """Classificacao do desafio a partir do estado do portal falso.

    Autouse: a politica de allowlist consulta o tipo ANTES de qualquer coisa,
    entao nenhum teste do fluxo roda sem isto.
    """
    def falso(pagina, *_a, **_k):
        portal = getattr(pagina, "portal", None)
        if portal is None or not portal.captcha:
            return login.TIPO_NENHUM
        return portal.captcha_tipo

    monkeypatch.setattr(login, "detectar_tipo_captcha", falso)


@pytest.fixture
def solver(monkeypatch):
    """Duble do resolvedor automatico de captcha.

    Por padrao NAO resolve — modela o formato que exige humano. O teste liga o
    caso automatizavel com `portal.exigir_captcha(automatizavel=True)`, ou
    substitui `estado["efeito"]` por uma funcao/excecao.
    """
    estado = {"chamadas": 0, "efeito": None}

    def falso(pagina, *_a, **_k):
        estado["chamadas"] += 1
        portal = pagina.portal
        efeito = estado["efeito"]
        if isinstance(efeito, BaseException):
            raise efeito
        if callable(efeito):
            return efeito(portal)
        if portal.captcha_automatizavel:      # formato que a automacao trata
            portal.representar(portal.documento_digitado)
            return True
        return False

    monkeypatch.setattr(login, "solve_hcaptcha", falso)
    return estado
