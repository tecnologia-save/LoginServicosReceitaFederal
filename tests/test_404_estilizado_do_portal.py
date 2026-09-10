"""Terceira variante do 404 num dia — e a única com título legítimo."""
import inspect

from servicos_rf_login import login


class _Pagina:
    def __init__(self, titulo="", corpo=""):
        self._t, self._c = titulo, corpo
    def title(self): return self._t
    def inner_text(self, _sel, timeout=None): return self._c


CORPO_404_ESTILIZADO = (
    "Portal não encontrado\n"
    "Talvez você tenha se equivocado ao digitar o endereço URL ou quem sabe "
    "nós tenhamos cometido uma falha por aqui.\n"
    "Verifique se o endereço digitado está correto. Caso o erro persista, "
    "tente novamente mais tarde."
)


def test_a_pagina_404_do_portal_e_reconhecida_pelo_corpo():
    """Capturada em print, 10/09/2026. Passa pelos três detectores anteriores:

      - URL limpa: `servicos.receitafederal.gov.br`, a própria raiz, sem
        `logoutCertificadoDigital` nem `codErro`.
      - Título legítimo: "Portal de Serviços Digitais da Receita Federal" —
        não contém "404" nem "not found".
      - O fallback por corpo só rodava quando o título estava VAZIO.

    É a página 404 estilizada do portal, com cabeçalho e marca. Só a frase do
    corpo denuncia.
    """
    assert login.pagina_de_erro_http(
        _Pagina(titulo="Portal de Serviços Digitais da Receita Federal",
                corpo=CORPO_404_ESTILIZADO),
        inspecionar_corpo=True) == "404"


def test_sem_inspecionar_corpo_continua_barato():
    """Ler o corpo custa uma ida ao navegador, e o laço roda 60 vezes. Quem
    chama decide a frequência — o mesmo cuidado que `_limite_de_dispositivos`
    já recebe ali."""
    assert login.pagina_de_erro_http(
        _Pagina(titulo="Portal de Serviços Digitais da Receita Federal",
                corpo=CORPO_404_ESTILIZADO)) == ""


def test_o_laco_inspeciona_o_corpo_a_cada_tres_segundos():
    fonte = inspect.getsource(login.main)
    assert "inspecionar_corpo=(_seg % 3 == 0)" in fonte


def test_portal_saudavel_nao_e_confundido():
    """A guarda de tamanho segue sendo o que torna a inspeção segura."""
    assert login.pagina_de_erro_http(
        _Pagina(titulo="Portal de Serviços Digitais da Receita Federal",
                corpo="Serviços " * 200),
        inspecionar_corpo=True) == ""


def test_as_tres_variantes_de_hoje_continuam_cobertas():
    """Regressão das duas anteriores, para a terceira não desfazer nenhuma."""
    class _P:
        url = ("https://servicos.receitafederal.gov.br/"
               "?logoutCertificadoDigital=1&codErro=30002")
    assert login.sessao_derrubada_pelo_portal(_P()) == "30002"
    assert login.pagina_de_erro_http(
        _Pagina(titulo="", corpo="404 Not Found\nThe requested URL was not found.")
    ) == "404"
