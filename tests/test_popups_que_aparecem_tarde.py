"""Popups da home que aparecem tarde também são fechados.

RUN-72904844 (15/09/2026), VM do Jurídico: nenhum "[popup]" no log, e o Jean
encontrou a tela parada com a barra de cookies e o tutorial abertos. As esperas
eram fixas e em sequência (5 s e 4 s); nas runs anteriores os dois sempre
fecharam.

Estes testes EXECUTAM `_fechar_popups_iniciais` com uma página dublê e um
relógio falso.
"""
import pytest

from servicos_rf_login import login


class _Relogio:
    def __init__(self):
        self.agora = 0.0

    def monotonic(self):
        return self.agora


class _Alvo:
    def __init__(self, pagina, nome):
        self._p, self._nome = pagina, nome

    @property
    def first(self):
        return self

    def is_visible(self):
        aparece = self._p.aparece_em.get(self._nome)
        return (aparece is not None and self._p.relogio.agora >= aparece
                and self._nome not in self._p.fechados)

    def click(self, **_k):
        self._p.fechados.append(self._nome)


class _Pagina:
    def __init__(self, relogio, aparece_em):
        self.relogio = relogio
        self.aparece_em = aparece_em
        self.fechados = []

    def wait_for_load_state(self, *_a, **_k):
        return None

    def locator(self, seletor):
        nome = "cookies" if seletor == login.SELETOR_ACEITAR_COOKIES else "tutorial"
        return _Alvo(self, nome)

    def wait_for_timeout(self, ms):
        self.relogio.agora += ms / 1000


@pytest.fixture
def relogio(monkeypatch):
    r = _Relogio()
    monkeypatch.setattr(login.time, "monotonic", r.monotonic)
    return r


def test_fecha_os_dois_mesmo_aparecendo_depois_das_esperas_antigas(relogio, capsys):
    """Os 5 s + 4 s de antes não viam um popup que chegasse aos 12 s."""
    pagina = _Pagina(relogio, {"cookies": 12.0, "tutorial": 13.5})
    login._fechar_popups_iniciais(pagina)
    assert sorted(pagina.fechados) == ["cookies", "tutorial"]
    saida = capsys.readouterr().out
    assert "Cookies aceitos" in saida and "Tutorial pulado" in saida


def test_sai_assim_que_os_dois_foram_fechados(relogio):
    """O caso de toda run medida não pode passar a pagar o teto."""
    pagina = _Pagina(relogio, {"cookies": 1.0, "tutorial": 1.0})
    login._fechar_popups_iniciais(pagina)
    assert relogio.agora < 3.0


def test_nao_espera_um_popup_depois_do_outro(relogio):
    """Tutorial primeiro e cookies depois: os dois são vigiados juntos."""
    pagina = _Pagina(relogio, {"tutorial": 2.0, "cookies": 6.0})
    login._fechar_popups_iniciais(pagina)
    assert pagina.fechados == ["tutorial", "cookies"]
    assert relogio.agora < 8.0


def test_fechado_um_nao_espera_o_teto_inteiro_pelo_outro(relogio):
    """Depois de recarregar, o aceite de cookies já está no perfil e só o
    tutorial volta."""
    pagina = _Pagina(relogio, {"tutorial": 1.0})
    login._fechar_popups_iniciais(pagina)
    assert pagina.fechados == ["tutorial"]
    assert relogio.agora < 1.0 + login.ESPERA_PELO_OUTRO_POPUP_S + 1.0


def test_popup_que_nunca_aparece_para_no_teto_e_diz_qual(relogio, capsys):
    pagina = _Pagina(relogio, {"cookies": 1.0})
    login._fechar_popups_iniciais(pagina, teto_s=5.0)
    assert pagina.fechados == ["cookies"]
    assert 5.0 <= relogio.agora < 6.0
    assert "Não apareceram em 5s: tutorial" in capsys.readouterr().out
