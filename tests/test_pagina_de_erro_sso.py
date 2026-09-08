"""Paginas de erro do SSO (`sso.acesso.gov.br`).

Reportado pelo Jean em 08/09/2026 como recorrente — 408, 404 e 403 no passo de
autorizacao, "e a automacao se perde".

Ela se perdia porque essas telas sao paginas CRUAS do servidor: nenhum seletor
do portal existe nelas, entao o laco de redirecionamento gastava os 60 s
inteiros contra uma tela que ja tinha respondido, com o log repetindo
"aguardando redirecionamento".

O 408 tem causa provavel, registrada como hipotese: o passo
`authorize?...govbr_recupera_certificadox509` pede o certificado do cliente
DURANTE o handshake TLS. Enquanto o dialogo "Selecione um certificado" fica
aberto esperando alguem clicar, a requisicao nao se completa — e a mensagem e
literalmente "Your browser didn't send a complete request in time".
"""
import pytest

from servicos_rf_login import login


class Pagina:
    def __init__(self, titulo):
        self._titulo = titulo

    def title(self):
        return self._titulo


@pytest.mark.parametrize("titulo,esperado", [
    ("408 Request Time-out", "408"),
    ("Request Timeout", "408"),
    ("403 Forbidden", "403"),
    ("404 Not Found", "404"),
    ("502 Bad Gateway", "502"),
    ("503 Service Unavailable", "503"),
    ("504 Gateway Time-out", "504"),
])
def test_reconhece_as_paginas_de_erro(titulo, esperado):
    assert login.pagina_de_erro_http(Pagina(titulo)) == esperado


@pytest.mark.parametrize("titulo", [
    "",
    "gov.br - Acesse sua conta",
    "Serviços da Receita Federal",
    "e-Processo Contribuinte",
])
def test_nao_confunde_tela_legitima_com_erro(titulo):
    assert login.pagina_de_erro_http(Pagina(titulo)) == ""


def test_titulo_longo_nao_e_pagina_de_erro():
    """Pagina de erro do servidor tem titulo curto; portal de verdade e longo.

    Sem esse corte, um portal que mencionasse "404" em qualquer lugar do titulo
    seria confundido com uma tela de erro.
    """
    longo = "Portal de Servicos " + ("x" * 200) + " 404"
    assert login.pagina_de_erro_http(Pagina(longo)) == ""


def test_pagina_que_explode_nao_derruba():
    class Quebrada:
        def title(self):
            raise RuntimeError("sem pagina")

    assert login.pagina_de_erro_http(Quebrada()) == ""


def test_so_o_CODIGO_sai_daqui():
    """Nunca o corpo: a URL do fluxo OAuth carrega `state`, `nonce` e, em
    algumas etapas, o documento do contribuinte."""
    codigo = login.pagina_de_erro_http(Pagina("408 Request Time-out"))
    assert codigo == "408"
    assert len(codigo) <= 3


def test_erro_do_sso_nao_pode_virar_login_concluido():
    """O `break` que sai do laco de espera NAO pode cair no caminho de sucesso.

    Primeira versao desta correcao fazia exatamente isso: interrompia a espera e
    seguia para "Login nos Servicos RF concluido" — anunciando sucesso olhando
    uma tela de erro, que e pior do que a espera cega que ela veio corrigir.
    """
    import inspect
    fonte = inspect.getsource(login.main)
    assert "erro_sso" in fonte, "o motivo nao sai do laco"
    pos_flag = fonte.index('if erro_sso:')
    pos_sucesso = fonte.index('print("Login nos Serviços RF concluído.")')
    assert pos_flag < pos_sucesso, (
        "o erro do SSO precisa ser tratado ANTES do caminho de sucesso")
    trecho = fonte[pos_flag:pos_sucesso]
    assert "continue" in trecho, "erro do SSO tem de gerar nova tentativa"
    assert "_abortar" in trecho, "esgotadas as tentativas, tem de abortar"
