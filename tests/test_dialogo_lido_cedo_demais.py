"""Zero elemento não é resposta: é a pergunta feita cedo demais."""
import inspect

from servicos_rf_login import cert_dialog, login


def test_a_coleta_insiste_ate_haver_o_que_ler():
    """Era `sleep(0.8)` e UMA leitura, com 90s de orçamento na mão.

    A janela do diálogo aparece antes de a UIA expor o conteúdo dela. Nesse
    intervalo a coleta devolve zero — indistinguível de "o diálogo não tem o
    nosso certificado", que é a conclusão oposta e leva a investigação para o
    lado errado.

    Medido em 10/09/2026, RUN-1e369205, C. CARVALHO GENEROSO:

        [cert-dialog] Janela encontrada: 'Selecione um certificado'
        [cert-dialog] 0 elemento(s) com texto no dialogo.
        [cert-dialog] Nenhum elemento casou. Dump dos textos do dialogo:

    O dump saiu vazio. Sem certificado escolhido o handshake TLS cai, e a
    empresa falhou duas vezes seguidas com erro DIFERENTE a cada vez —
    sintomas distintos da mesma causa, que foi o que me fez tratar como azar.
    """
    fonte = inspect.getsource(cert_dialog.selecionar_certificado_no_dialogo)
    assert "while time.monotonic() < limite" in fonte
    assert "if elems:" in fonte
    assert "time.sleep(0.8)\n    elems" not in fonte


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
