"""Visível não é pronto: o botão de certificado só é clicado com a tela carregada.

RUN-0f2765c6, VM do Jurídico, 14/09/2026. O Jean viu na tela e o log confirma:
o clique saiu no mesmo segundo em que o captcha foi resolvido, e de novo 1s
depois de recarregar — com 2 frames do hCaptcha ainda carregando. Nas duas
vezes o botão girou até o timeout de 60s.

Estes testes EXECUTAM a espera com uma página falsa.
"""
import pytest

from servicos_rf_login import login


class _Frame:
    def __init__(self, url, log):
        self.url = url
        self._log = log

    def wait_for_load_state(self, estado, timeout=None):
        self._log.append(("frame_load", self.url))


class Pagina:
    """Um PASSO por consulta de `readyState` — é uma por volta do laço.

    A primeira versão deste dublê avançava a cada leitura de `frames`, e a
    espera lê `frames` duas vezes por volta: a sequência corria em dobro e o
    teste acusava a espera de liberar cedo, quando o cedo era do dublê.
    """

    def __init__(self, estados=("complete",), frames_por_passo=((),),
                 evaluate_quebra=False):
        self._estados = list(estados)
        self._frames = [list(f) for f in frames_por_passo]
        self._quebra = evaluate_quebra
        self._passo = 0
        self.log = []

    def wait_for_load_state(self, estado, timeout=None):
        self.log.append(("load", estado))

    def evaluate(self, _js):
        if self._quebra:
            raise RuntimeError("sem JS")
        i = min(self._passo, len(self._estados) - 1)
        self._passo += 1
        return self._estados[i]

    @property
    def frames(self):
        i = min(max(self._passo - 1, 0), len(self._frames) - 1)
        return [_Frame(u, self.log) for u in self._frames[i]]


@pytest.fixture(autouse=True)
def _sem_espera_real(monkeypatch):
    dormiu = []
    monkeypatch.setattr(login.time, "sleep", lambda s: dormiu.append(s))
    return dormiu


HC = "https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha.html#frame=checkbox"
HC2 = "https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha.html#frame=challenge"


def test_espera_o_load_da_pagina():
    p = Pagina()
    assert login._aguardar_tela_pronta(p) is True
    assert ("load", "load") in p.log


def test_nao_libera_enquanto_readystate_nao_e_complete(_sem_espera_real):
    p = Pagina(estados=["loading"] * 10 + ["interactive"] * 5 + ["complete"])
    assert login._aguardar_tela_pronta(p) is True
    assert len(_sem_espera_real) >= 15, "liberou antes do readyState=complete"


def test_espera_o_numero_de_frames_do_hcaptcha_parar_de_mudar(_sem_espera_real):
    """É o caso do log: 2 frames, o segundo chegando depois do primeiro."""
    passos = [()] * 2 + [(HC,)] * 3 + [(HC, HC2)]
    p = Pagina(frames_por_passo=passos)
    assert login._aguardar_tela_pronta(p) is True
    # só libera com os DOIS frames estáveis por ESTAVEL_POR_PASSOS consultas
    assert len(_sem_espera_real) >= 5 + login.ESTAVEL_POR_PASSOS
    carregados = {u for (k, u) in p.log if k == "frame_load"}
    assert carregados == {HC, HC2}, "cada frame do hCaptcha precisa do próprio load"


def test_tela_que_nunca_fica_pronta_libera_no_teto_sem_travar(capsys):
    p = Pagina(estados=["loading"])
    assert login._aguardar_tela_pronta(p, teto_s=2.0) is False
    assert "NÃO ficou pronta" in capsys.readouterr().out


def test_sem_como_perguntar_o_readystate_nao_vira_espera_longa(_sem_espera_real):
    """Fake de outros testes e página estranha: não pode custar 20s por clique."""
    p = Pagina(evaluate_quebra=True)
    assert login._aguardar_tela_pronta(p) is True
    assert len(_sem_espera_real) <= login.ESTAVEL_POR_PASSOS + 1


def test_o_clique_no_certificado_so_sai_depois_da_tela_pronta(monkeypatch):
    ordem = []

    class Loc:
        @property
        def first(self):
            return self

        def wait_for(self, **_k):
            ordem.append("visivel")

        def click(self, **_k):
            ordem.append("clique")

    class P:
        def locator(self, _sel):
            return Loc()

    monkeypatch.setattr(login, "_aguardar_tela_pronta",
                        lambda page, *a, **k: ordem.append("pronta") or True)
    assert login._clicar_certificado(P()) is True
    assert ordem == ["visivel", "pronta", "clique"]


def test_o_teto_nao_trava_o_login_mais_que_a_espera_de_redirecionamento():
    assert 5 <= login.TETO_TELA_PRONTA_S <= 30
