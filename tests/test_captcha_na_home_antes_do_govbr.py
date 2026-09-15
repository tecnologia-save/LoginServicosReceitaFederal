"""Captcha que abre na HOME depois de "Entrar com gov.br" é resolvido ali.

RUN-74516863 (15/09/2026), print do Jean: o log procurava "Seu certificado
digital" com a tela ainda na home do portal e um hCaptcha de grade aberto por
cima. A verificação de captcha tinha desistido antes de ele aparecer, e o
recomeço jogou o desafio fora.

Estes testes EXECUTAM `_aguardar_saida_da_home` com página e relógio falsos.
"""
import inspect

import pytest

from servicos_rf_login import login

HOME = login.SERVICOS_RF_URL
SSO = "https://sso.acesso.gov.br/login?client_id=p-servicos.receitafederal.gov.br"


class _Relogio:
    def __init__(self):
        self.agora = 0.0

    def monotonic(self):
        return self.agora


class _Pagina:
    """`captcha_em`: quando o captcha aparece; `sai_em`: quando a URL troca
    (contado a partir da resolução, se houver captcha)."""

    def __init__(self, relogio, captcha_em=None, sai_em=None):
        self.relogio = relogio
        self.captcha_em = captcha_em
        self.sai_em = sai_em
        self.resolvido = False

    @property
    def url(self):
        if self.sai_em is not None and self.relogio.agora >= self.sai_em and (
                self.captcha_em is None or self.resolvido):
            return SSO
        return HOME

    def wait_for_timeout(self, ms):
        self.relogio.agora += ms / 1000


@pytest.fixture
def cena(monkeypatch):
    relogio = _Relogio()
    estado = {"resolucoes": [], "logado": False, "resolve": True}
    monkeypatch.setattr(login.time, "monotonic", relogio.monotonic)
    monkeypatch.setattr(login, "_ja_logado", lambda page: estado["logado"])

    def presente(page):
        return (page.captcha_em is not None and not page.resolvido
                and relogio.agora >= page.captcha_em)

    def resolver(page, etapa, max_attempts=3):
        estado["resolucoes"].append(etapa)
        relogio.agora += 20
        page.resolvido = estado["resolve"]
        if page.resolvido and page.sai_em is not None:
            page.sai_em = relogio.agora + 2
        return estado["resolve"]

    monkeypatch.setattr(login, "captcha_presente", presente)
    monkeypatch.setattr(login, "_try_solve_captcha", resolver)
    return relogio, estado


def test_captcha_que_abre_na_home_depois_de_10s_e_resolvido(cena):
    """O caso do print: a verificação antiga desistia em 10 s."""
    relogio, estado = cena
    pagina = _Pagina(relogio, captcha_em=12.0, sai_em=0.0)
    assert login._aguardar_saida_da_home(pagina, "captcha-pos-govbr") == "saiu"
    assert estado["resolucoes"] == ["captcha-pos-govbr"]


def test_sem_captcha_sai_direto(cena):
    relogio, estado = cena
    pagina = _Pagina(relogio, sai_em=3.0)
    assert login._aguardar_saida_da_home(pagina, "captcha-pos-govbr") == "saiu"
    assert estado["resolucoes"] == []
    assert relogio.agora < 4.0


def test_sessao_que_voltou_logada(cena):
    relogio, estado = cena
    estado["logado"] = True
    assert login._aguardar_saida_da_home(_Pagina(relogio), "x") == "logado"


def test_captcha_nao_resolvido_nao_segue_para_o_certificado(cena):
    relogio, estado = cena
    estado["resolve"] = False
    pagina = _Pagina(relogio, captcha_em=1.0, sai_em=0.0)
    assert login._aguardar_saida_da_home(pagina, "x") == "captcha_falhou"


def test_tela_parada_para_no_teto(cena):
    relogio, _ = cena
    assert login._aguardar_saida_da_home(_Pagina(relogio), "x", teto_s=5.0) == "parado"
    assert 5.0 <= relogio.agora < 6.0


def test_o_login_espera_sair_da_home_antes_do_certificado():
    fonte = inspect.getsource(login.main)
    i = fonte.index("_aguardar_saida_da_home(page, \"captcha-pos-govbr\")")
    assert i < fonte.index("[cert] Tentativa")
    assert fonte.index("if not ja_entrou and not _clicar_entrar_govbr(page):") < i


def test_o_recomeco_tambem_espera_sair_da_home():
    fonte = inspect.getsource(login._refazer_entrada_govbr)
    assert fonte.index("_clicar_entrar_govbr(page)") < fonte.index("_aguardar_saida_da_home(")


def test_o_recomeco_nao_paga_o_teto_inteiro_dos_popups():
    """RUN-74516863: "[popup] Não apareceram em 20s: cookies, tutorial"."""
    fonte = inspect.getsource(login._refazer_entrada_govbr)
    assert "_fechar_popups_iniciais(page, teto_s=ESPERA_PELO_OUTRO_POPUP_S)" in fonte
