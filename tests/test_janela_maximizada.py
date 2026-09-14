"""A janela do Chrome volta a ficar maximizada depois do login.

RUN-ac11ba1a, VM do Jurídico, 14/09/2026: a janela saiu de maximizada logo
depois do login, e o logout da mesma run não achou o menu do avatar. Os testes
EXECUTAM a função com uma sessão CDP falsa.
"""
import inspect

from servicos_rf_login import garantir_janela_maximizada, login


class Sessao:
    def __init__(self, estado, quebra=False):
        self.estado = estado
        self.quebra = quebra
        self.enviados = []
        self.desligada = False

    def send(self, metodo, params=None):
        if self.quebra:
            raise RuntimeError("cdp")
        self.enviados.append((metodo, params))
        if metodo == "Browser.getWindowForTarget":
            return {"windowId": 7, "bounds": {"windowState": self.estado}}
        if metodo == "Browser.setWindowBounds":
            self.estado = params["bounds"]["windowState"]
        return {}

    def detach(self):
        self.desligada = True


class Contexto:
    def __init__(self, sessao):
        self.sessao = sessao

    def new_cdp_session(self, _page):
        return self.sessao


class Pagina:
    def __init__(self, sessao):
        self.context = Contexto(sessao)


def _ajustes(sessao):
    return [p["bounds"]["windowState"] for m, p in sessao.enviados
            if m == "Browser.setWindowBounds"]


def test_ja_maximizada_nao_mexe():
    s = Sessao("maximized")
    assert garantir_janela_maximizada(Pagina(s)) == "maximized"
    assert _ajustes(s) == []
    assert s.desligada


def test_normal_volta_a_maximizada(capsys):
    s = Sessao("normal")
    assert garantir_janela_maximizada(Pagina(s)) == "normal"
    assert _ajustes(s) == ["maximized"]
    assert s.estado == "maximized"
    assert "estava 'normal'" in capsys.readouterr().out


def test_minimizada_passa_por_normal_antes():
    s = Sessao("minimized")
    assert garantir_janela_maximizada(Pagina(s)) == "minimized"
    assert _ajustes(s) == ["normal", "maximized"]


def test_tela_cheia_nao_e_desfeita():
    s = Sessao("fullscreen")
    assert garantir_janela_maximizada(Pagina(s)) == "fullscreen"
    assert _ajustes(s) == []


def test_cdp_que_falha_nao_derruba_nada():
    assert garantir_janela_maximizada(Pagina(Sessao("normal", quebra=True))) == ""


def test_pagina_sem_contexto_nao_derruba_nada():
    assert garantir_janela_maximizada(object()) == ""


def test_o_login_confere_a_janela_logo_depois_de_concluir():
    fonte = inspect.getsource(login)
    i = fonte.index('print("Login nos Serviços RF concluído.")')
    j = fonte.index("garantir_janela_maximizada(page)", i)
    assert j - i < 400, "tem de vir logo depois do login, antes da representação"
    assert j < fonte.index("_representar_cnpj_procurador(\n", i)
