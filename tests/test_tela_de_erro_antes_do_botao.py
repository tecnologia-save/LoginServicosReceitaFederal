"""Página de erro não tem botão — procurar nela é gastar o teto à toa."""
import inspect

from servicos_rf_login import login

FONTE = inspect.getsource(login.main)


def test_a_primeira_tela_e_conferida_antes_de_cacar_o_botao():
    """O projeto já fazia este argumento para o botão do CERTIFICADO, e a
    proteção ficou só lá:

        "a automação caça 'Seu certificado digital' dentro de uma tela de
        408/404, onde ele legitimamente não existe, e reporta 'botão não
        encontrado' — mandando quem lê investigar o seletor, que é o lugar
        errado"

    Medido em 11/09/2026, RUN-7e9b0015:

        -> página inicial carregada.
        Clicando em 'Entrar com gov.br'...
        -> botão 'Entrar com gov.br' não encontrado em nenhum seletor.
        Login concluído em 15.4s

    Quinze segundos — o teto do primeiro seletor — varrendo uma tela que não
    tinha o botão.
    """
    i = FONTE.index("erro_inicial = pagina_de_erro_http(page, inspecionar_corpo=True)")
    j = FONTE.index("_clicar_entrar_govbr(page)")
    assert i < j, "conferir a tela ANTES de procurar o botão"


def test_inspeciona_o_corpo_porque_ha_tres_variantes():
    """O portal produziu três formas de 404 em 10/09: a crua sem título, a
    estilizada com título da marca, e o `?logoutCertificadoDigital&codErro`.
    Só a checagem de título não cobre as três."""
    i = FONTE.index("erro_inicial =")
    assert "inspecionar_corpo=True" in FONTE[i:i + 120]


def test_recarrega_uma_vez_antes_de_desistir():
    """As três variantes observadas foram transitórias — o Jean deu refresh à
    mão numa delas e o portal voltou ao normal."""
    i = FONTE.index("erro_inicial =")
    j = FONTE.index("_limite_de_dispositivos(page)", i)
    trecho = FONTE[i:j]
    assert "page.goto(SERVICOS_RF_URL" in trecho
    assert trecho.count("pagina_de_erro_http") == 2, "confere de novo após recarregar"


def test_o_balao_de_permissao_nao_aparece():
    """Bolha nativa do Chrome não é elemento da página: nenhum seletor a
    fecha, e ela fica por cima.

        sso.acesso.gov.br quer / Saber sua localização

    Declarar `permissions` (ainda que vazia) faz o Playwright responder pelo
    protocolo em vez de o Chrome desenhar o balão. Vazia e não
    `["geolocation"]`: conceder entregaria a localização real da VM, e o que
    se quer é que o pedido seja RESPONDIDO, não atendido.
    """
    assert "permissions=[]," in FONTE
