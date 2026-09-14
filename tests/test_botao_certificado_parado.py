"""Botão "Seu certificado digital" girando: recarregar, mas só no caso certo.

Visto pelo Jean por RDP na VM do Jurídico em 14/09/2026 (RUN-b48d4f5c): o
botão ficou girando sem janela e sem navegar, e um refresh concluiu o login.
Na RUN-7f35d298, sem ninguém na tela, a espera de 60s bateu três vezes.

O risco da correção é o oposto: recarregar no meio de um login que só estava
lento. Na RUN-69ace0ce a janela de certificado apareceu 57s depois do clique e
o login concluiu. Por isso estes testes EXECUTAM a decisão, e não só leem o
fonte — a instrumentação de aba de 11/09 passou no `py_compile` e quebrou a
automação inteira com um NameError.
"""
import inspect

import pytest

from servicos_rf_login import login

P = login.SEGUNDOS_BOTAO_CERTIFICADO_PARADO


class _Loc:
    def __init__(self, pagina):
        self._p = pagina

    @property
    def first(self):
        return self

    def is_visible(self, **_k):
        return self._p.botao_visivel


class _Frame:
    def __init__(self, url):
        self.url = url


class Pagina:
    def __init__(self, url="https://sso.acesso.gov.br/login?client_id=x",
                 depois_do_reload="botao", reload_quebra=False, frames=()):
        self.url = url
        self.botao_visivel = False
        self.logado = False
        self.reloads = 0
        self._depois = depois_do_reload
        self._quebra = reload_quebra
        self.frames = [_Frame(u) for u in frames]

    def reload(self, **_k):
        self.reloads += 1
        if self._quebra:
            raise TimeoutError("reload")
        if self._depois == "logado":
            self.logado = True
        elif self._depois == "botao":
            self.botao_visivel = True

    def locator(self, _sel):
        return _Loc(self)


@pytest.fixture(autouse=True)
def _isola(monkeypatch):
    cliques = []
    monkeypatch.setattr(login, "_ja_logado", lambda page: page.logado)
    monkeypatch.setattr(login, "captcha_presente", lambda page: False)
    monkeypatch.setattr(login, "_clicar_certificado",
                        lambda page: cliques.append(page) or True)
    monkeypatch.setattr(login.time, "sleep", lambda _s: None)
    return cliques


def _fechado():
    return False


def test_antes_do_prazo_nao_recarrega():
    p = Pagina()
    assert login._destravar_botao_certificado(p, P - 0.1, dialogo_aberto=_fechado) == ""
    assert p.reloads == 0


def test_fora_do_sso_nao_recarrega():
    """No portal da Receita a espera é do redirecionamento, não do botão."""
    p = Pagina(url="https://servicos.receitafederal.gov.br/")
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == ""
    assert p.reloads == 0


def test_com_captcha_na_tela_nao_recarrega(monkeypatch):
    """Recarregar jogaria fora um desafio que ainda pode ser resolvido."""
    monkeypatch.setattr(login, "captcha_presente", lambda page: True)
    p = Pagina()
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == "captcha"
    assert p.reloads == 0


def test_com_janela_de_certificado_aberta_nao_recarrega():
    """RUN-69ace0ce: a janela chegou tarde e o login concluiu. Recarregar ali
    cancelaria a requisição que a janela estava esperando confirmar."""
    p = Pagina()
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=lambda: True) == "dialogo"
    assert p.reloads == 0


def test_duvida_sobre_a_janela_conta_como_aberta(monkeypatch):
    """Erro ao procurar a janela não pode virar licença para recarregar."""
    def quebra(_t):
        raise RuntimeError("uia")
    monkeypatch.setattr(login, "_CERT_DIALOG_OK", True)
    monkeypatch.setattr(login, "_achar_dialogo_cert", quebra, raising=False)
    assert login._dialogo_de_certificado_aberto() is True


def test_parado_recarrega_e_se_ja_entrou_nao_clica_de_novo(_isola):
    """É o caso visto na VM: o refresh sozinho concluiu o login."""
    p = Pagina(depois_do_reload="logado")
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == "logado"
    assert p.reloads == 1
    assert _isola == [], "já logado: clicar de novo apresentaria o certificado à toa"


def test_parado_recarrega_e_clica_de_novo_quando_o_botao_volta(_isola):
    p = Pagina(depois_do_reload="botao")
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == "clicado"
    assert p.reloads == 1
    assert len(_isola) == 1


def test_recarregar_que_quebra_nao_derruba_o_login():
    p = Pagina(reload_quebra=True)
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == "falhou"


def test_sem_login_nem_botao_segue_a_espera(_isola):
    p = Pagina(depois_do_reload="nada")
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == "sem_botao"
    assert _isola == []


def test_frame_do_hcaptcha_na_pagina_impede_o_recarregar(capsys):
    """RUN-0f2765c6: recarregou com `captcha_presente` dizendo "não" e 2 frames
    do hCaptcha no DOM; o desafio chegou logo depois e se perdeu."""
    p = Pagina(depois_do_reload="logado",
               frames=("https://newassets.hcaptcha.com/x#frame=checkbox",
                       "https://sso.acesso.gov.br/"))
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == "frames_hcaptcha"
    assert p.reloads == 0
    assert "1 frame(s) do hCaptcha" in capsys.readouterr().out


def test_sem_conseguir_contar_frames_nao_recarrega(monkeypatch):
    monkeypatch.setattr(login, "_frames_hcaptcha", lambda page: -1)
    p = Pagina()
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == "frames_hcaptcha"
    assert p.reloads == 0


def test_so_recarrega_com_zero_frames_e_o_log_diz_isso(capsys):
    p = Pagina(depois_do_reload="logado", frames=("https://sso.acesso.gov.br/",))
    assert login._destravar_botao_certificado(p, P, dialogo_aberto=_fechado) == "logado"
    assert p.reloads == 1
    assert "frames hCaptcha no DOM: 0" in capsys.readouterr().out


def test_o_laco_de_redirecionamento_chama_o_destravamento_uma_vez_por_tentativa():
    fonte = inspect.getsource(login)
    i_clique = fonte.index("t_clique_cert = time.monotonic()")
    i_laco = fonte.index('print("Aguardando redirecionamento final')
    i_chamada = fonte.index("_destravar_botao_certificado(\n")
    assert i_clique < i_laco < i_chamada, "o prazo nasce no clique, antes da espera"
    trecho = fonte[i_laco:i_chamada + 200]
    assert "destravou = False" in trecho
    assert "if not destravou and _destravar_botao_certificado(" in trecho
    assert "destravou = True" in trecho


def test_o_prazo_nao_atropela_o_captcha_tardio():
    """RUN-0f2765c6: o desafio chega 15-19s depois do clique. Os 15s da
    primeira versão recarregaram aos 16s e jogaram o captcha fora."""
    assert login.SEGUNDOS_BOTAO_CERTIFICADO_PARADO >= 30
    assert login.SEGUNDOS_BOTAO_CERTIFICADO_PARADO < 60, "tem de caber na espera de 60s"


def test_o_laco_resolve_captcha_tardio_antes_de_pensar_em_recarregar():
    fonte = inspect.getsource(login)
    i_laco = fonte.index('print("Aguardando redirecionamento final')
    i_reload = fonte.index("_destravar_botao_certificado(" + chr(10))
    trecho = fonte[i_laco:i_reload]
    assert "captchas_tardios = 0" in trecho
    assert "captchas_tardios < MAX_CAPTCHAS_TARDIOS" in trecho
    assert "captcha_presente(page)" in trecho
    assert '_try_solve_captcha(page, f"captcha-tardio-t{tentativa}")' in trecho
    assert 1 <= login.MAX_CAPTCHAS_TARDIOS <= 3
