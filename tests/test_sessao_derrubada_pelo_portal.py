"""404 depois do certificado: o portal já disse o que houve, na URL."""
import inspect

from servicos_rf_login import login


class _Pagina:
    def __init__(self, url):
        self.url = url


def test_reconhece_a_url_de_logout_com_codigo():
    """Capturada em print, run aa762acf, 10/09/2026.

        servicos.receitafederal.gov.br/?logoutCertificadoDigital=1&codErro=30002

    O portal descartou a sessão do certificado e mandou o navegador para uma
    URL que não existe — a tela é um "404 Not Found" cru.
    """
    codigo = login.sessao_derrubada_pelo_portal(
        _Pagina("https://servicos.receitafederal.gov.br/"
                "?logoutCertificadoDigital=1&codErro=30002"))
    assert codigo == "30002"


def test_sem_codigo_ainda_e_reconhecido():
    """O parâmetro pode faltar; o logout continua sendo o fato."""
    assert login.sessao_derrubada_pelo_portal(
        _Pagina("https://servicos.receitafederal.gov.br/?logoutCertificadoDigital=1")
    ) == "sem-codigo"


def test_portal_normal_nao_e_confundido():
    assert login.sessao_derrubada_pelo_portal(
        _Pagina("https://servicos.receitafederal.gov.br/#/dashboard")) == ""


def test_pagina_de_erro_http_nao_pegava_este_caso():
    """Por que precisou de detector novo: `pagina_de_erro_http` lê o TÍTULO
    procurando erro do SSO, e aqui o host é o certo e o título é genérico. O
    laço gastava os 60s perguntando a um 404 se ele já tinha virado portal."""
    fonte = inspect.getsource(login.pagina_de_erro_http)
    assert "page.title()" in fonte
    assert "page.url" not in fonte


def test_o_laco_sai_com_MOTIVO():
    """`break` seco cairia no caminho de sucesso, anunciando login concluído
    contra um 404 — a mesma armadilha que o `erro_sso` já documenta ali."""
    fonte = inspect.getsource(login.main)
    i = fonte.index("derrubada = sessao_derrubada_pelo_portal(page)")
    trecho = fonte[i:i + 500]
    assert "erro_sso =" in trecho
    assert "break" in trecho


def test_so_o_codigo_sai_da_funcao():
    """A query string carrega identificadores do fluxo OAuth. Mesmo princípio
    de `pagina_de_erro_http`: sai o código, nunca a URL."""
    fonte = inspect.getsource(login.sessao_derrubada_pelo_portal)
    assert "return url" not in fonte
    assert 'achado.group(1)' in fonte
