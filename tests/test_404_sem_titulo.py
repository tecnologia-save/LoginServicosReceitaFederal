"""Página de erro sem <title> era descartada antes de ser olhada."""
import inspect

from servicos_rf_login import login


class _Pagina:
    def __init__(self, titulo="", corpo=""):
        self._t, self._c = titulo, corpo
    def title(self): return self._t
    def inner_text(self, _sel, timeout=None): return self._c


def test_404_sem_titulo_e_reconhecido_pelo_corpo():
    """Capturado em print, run 90ef18c4, 10/09/2026, DURANTE o login:

        servicos.receitafederal.gov.br/home
        404 Not Found
        The requested URL was not found.

    A aba estava nomeada pela URL — sinal de página sem `<title>`. Nesse caso
    `page.title()` volta vazia, e o guard `if not titulo: return ""` descartava
    ali mesmo. "404" e "not found" já estavam na lista de marcas há semanas e
    nunca tiveram chance de casar.

    Este `/home` não é nosso: `SERVICOS_RF_URL` termina em `/`. É o portal que
    redireciona para lá depois do certificado, e a rota não existe.
    """
    assert login.pagina_de_erro_http(
        _Pagina(titulo="", corpo="404 Not Found\nThe requested URL was not found.")
    ) == "404"


def test_titulo_continua_tendo_precedencia():
    assert login.pagina_de_erro_http(
        _Pagina(titulo="408 Request Time-out", corpo="")) == "408"


def test_portal_de_verdade_nao_e_confundido():
    """O limite de tamanho é o que torna a inspeção do corpo segura: página de
    erro crua tem duas linhas; o portal tem menus, rodapé e marca."""
    portal = "Serviços " * 200 + " 404 "
    assert login.pagina_de_erro_http(_Pagina(titulo="", corpo=portal)) == ""


def test_o_corpo_e_lido_sem_titulo_OU_sob_pedido():
    """PREMISSA REVISADA NO MESMO DIA.

    Este teste dizia "corpo só quando não há título", e era assim que eu tinha
    escrito: o título resolve a maioria dos casos e ler o corpo custa uma ida
    ao navegador.

    A terceira variante do 404 derrubou a regra. A página 404 ESTILIZADA do
    portal tem título legítimo — "Portal de Serviços Digitais da Receita
    Federal" — e URL limpa, a própria raiz. Nada além do corpo a distingue de
    um portal saudável.

    O barato continua sendo o padrão: `inspecionar_corpo` é opcional, e quem
    chama num laço de 60 iterações decide a frequência. O laço do
    redirecionamento pede a cada 3s, mesma cadência do
    `_limite_de_dispositivos` e pela mesma razão.
    """
    fonte = inspect.getsource(login.pagina_de_erro_http)
    assert "if titulo and not inspecionar_corpo:" in fonte, (
        "sem pedido explícito, título presente encerra sem ler o corpo")
    assert "inner_text" in fonte


def test_so_o_codigo_sai_daqui():
    """A regra que já valia: o corpo pode carregar identificadores do fluxo
    OAuth. Inspecionar é permitido; devolver, não."""
    fonte = inspect.getsource(login.pagina_de_erro_http)
    assert "return corpo" not in fonte
    assert "return codigo" in fonte
