"""O aviso de encerramento do e-CAC, se aparecer no redirecionamento, e fechado.

A migracao "porta unica" faz o SSO do Servicos RF passar pelo e-CAC
(cav.receita.fazenda.gov.br), que pode abrir o modal 'Prepare-se para a
evolucao...' (id `dialog-mensagem-encerramento-ecac`) por cima e travar a volta
ao Servicos RF. Estes testes EXECUTAM `_fechar_aviso_encerramento_ecac` com uma
pagina duble — clicar em 'Continuar no e-CAC por enquanto' resolve.
"""
from servicos_rf_login import login

SEL_AVISO = "#dialog-mensagem-encerramento-ecac"


class _Botao:
    def __init__(self, pagina):
        self._p = pagina

    @property
    def first(self):
        return self

    def click(self, **_k):
        self._p.clicado += 1


class _Aviso:
    def __init__(self, pagina):
        self._p = pagina

    @property
    def first(self):
        return self

    def is_visible(self):
        # Sem timeout: imediato, como o codigo chama dentro do laco.
        return self._p.visivel and not self._p.clicado


class _Pagina:
    def __init__(self, visivel):
        self.visivel = visivel
        self.clicado = 0
        self.por_role = 0

    def locator(self, seletor):
        return _Aviso(self) if seletor == SEL_AVISO else _Botao(self)

    def get_by_role(self, _role, name=None):
        self.por_role += 1
        return _Botao(self)

    def wait_for_timeout(self, _ms):
        return None


def test_fecha_o_aviso_quando_visivel(capsys):
    pagina = _Pagina(visivel=True)
    assert login._fechar_aviso_encerramento_ecac(pagina) is True
    assert pagina.clicado == 1
    assert pagina.por_role == 1        # clicou pelo papel 'button'
    assert "Continuar no e-CAC por enquanto" in capsys.readouterr().out


def test_nao_faz_nada_quando_o_aviso_nao_esta_na_tela():
    pagina = _Pagina(visivel=False)
    assert login._fechar_aviso_encerramento_ecac(pagina) is False
    assert pagina.clicado == 0


def test_fallback_por_texto_quando_get_by_role_falha():
    """Se `get_by_role` nao acha o botao, cai no seletor por texto — mesma acao."""
    class _SemRole(_Pagina):
        def get_by_role(self, *_a, **_k):
            raise RuntimeError("sem role")

    pagina = _SemRole(visivel=True)
    assert login._fechar_aviso_encerramento_ecac(pagina) is True
    assert pagina.clicado == 1         # fechou pelo fallback de texto


def test_best_effort_nunca_levanta():
    class _Quebrada:
        def locator(self, *_a, **_k):
            raise RuntimeError("boom")

    assert login._fechar_aviso_encerramento_ecac(_Quebrada()) is False
