"""Zero elemento não é resposta: é a pergunta feita cedo demais."""
import inspect

from servicos_rf_login import cert_dialog, login


def test_a_espera_e_PELO_ALVO_e_nao_por_haver_elementos():
    """PREMISSA REVISADA — segunda vez no mesmo ponto, no mesmo dia.

    Versão 1: `time.sleep(0.8)` e UMA coleta. Zero elementos era tratado como
    "o diálogo não tem o nosso certificado", que é a conclusão oposta.

    Versão 2 (minha correção): insistir enquanto a coleta viesse vazia. Errada
    do mesmo jeito, porque o diálogo NUNCA vem vazio — o título e o botão
    Fechar existem desde o primeiro instante. Medido na RUN-a937c982:

        [cert-dialog] 2 elemento(s) com texto no dialogo (apos 1 leitura(s)).
        [cert-dialog] Nenhum elemento casou. Dump dos textos do dialogo:
          [el 0] SELECIONEUMCERTIFICADO
          [el 1] FECHAR

    Dois elementos bastavam para `if elems: break`, e a espera terminava antes
    de as LINHAS carregarem. Quem denunciou foi a instrumentação "(apos 1
    leitura(s))", que eu tinha posto para outra dúvida.

    A condição certa é o ALVO. Assim "2 elementos" (cedo demais) e "40
    elementos sem o nosso" (certificado ausente) deixam de ser a mesma coisa —
    e elas pedem correções opostas.
    """
    fonte = inspect.getsource(cert_dialog.selecionar_certificado_no_dialogo)
    assert "if escolhido is not None or time.monotonic() >= limite:" in fonte, (
        "o laço termina quando acha o alvo, não quando há elementos")
    assert "if elems:" + chr(10) + "            break" not in fonte, (
        "condição antiga: qualquer elemento encerrava a espera")
    # O match acontece DENTRO do laço — senão não adianta insistir.
    i = fonte.index("while True:")
    j = fonte.index("if escolhido is not None or time.monotonic()")
    assert "alvo_serial in txt" in fonte[i:j]


def test_a_contagem_de_leituras_vai_no_log():
    """"0 elementos após 1 leitura" e "após 40" pedem correções opostas: uma é
    coleta cedo demais, a outra é diálogo que realmente não tem o certificado.
    Sem o número, as duas se parecem."""
    fonte = inspect.getsource(cert_dialog.selecionar_certificado_no_dialogo)
    assert "leitura(s)" in fonte


def test_pagina_de_erro_do_chrome_encerra_a_espera():
    """`chromewebdata` é erro do CHROME, não do SSO.

    `pagina_de_erro_http` lê o título devolvido pelo servidor; num erro de
    rede não houve resposta nenhuma. Nenhum seletor do portal aparecerá — e o
    laço gastava os 60s inteiros imprimindo o host que já respondia a pergunta
    em todas as sessenta linhas.

    Sai com motivo, não com `break` seco: sem isso o fluxo cai no caminho de
    sucesso e anuncia login concluído contra uma tela de erro.
    """
    fonte = inspect.getsource(login.main)
    i = fonte.index('host_da_url(page.url) == "chromewebdata"')
    trecho = fonte[i:i + 400]
    assert "erro_sso =" in trecho, "sair sem motivo cai no caminho de sucesso"
    assert "break" in trecho
