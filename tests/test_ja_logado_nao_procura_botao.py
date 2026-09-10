"""Sessão viva faz o portal entrar sozinho — e aí não há botão para clicar."""
import inspect

from servicos_rf_login import login


def test_ja_logado_impede_a_busca_do_botao_govbr():
    """`_ja_logado` era observação; virou desfecho.

    O `if` só imprimia "Redirecionado automaticamente. Login concluído." e a
    execução seguia para `_clicar_entrar_govbr`, procurando um botão que —
    estando logado — legitimamente não existe. A ausência dele era declarada
    falha de login.

    Medido em 10/09/2026, RUN-1aa3607c: FONSECA COMÉRCIO DE CAFE e BY GUS
    INDUSTRIA, uma atrás da outra, com estas três linhas:

        -> Redirecionado automaticamente. Login concluído.
        Clicando em 'Entrar com gov.br'...
        -> botão 'Entrar com gov.br' não encontrado em nenhum seletor.

    A primeira linha já respondia a pergunta que as outras duas foram fazer.
    """
    fonte = inspect.getsource(login.main)
    assert "ja_entrou = _ja_logado(page)" in fonte
    assert "if not ja_entrou and not _clicar_entrar_govbr(page):" in fonte


def test_o_laco_do_certificado_ja_tinha_a_guarda():
    """A prova de que o padrão certo já existia no arquivo.

    O laço do certificado sempre saiu quando já estava logado. Este teste
    existe para que, se alguém remover aquela guarda, a comparação continue
    valendo — e para deixar registrado que o defeito não era desconhecimento
    do padrão, era um ponto que ficou de fora dele.
    """
    fonte = inspect.getsource(login.main)
    i = fonte.index("MAX_TENTATIVAS_CERT = 3")
    assert "if _ja_logado(page):" in fonte[i:i + 900]


def test_o_sandbox_do_chromium_fica_ligado():
    """Desligado, o patchright passa `--no-sandbox` e o Chrome mostra a tarja.

        if (options2.chromiumSandbox !== true)
          chromeArguments.push("--no-sandbox");

    Quem suprime essa tarja é `--enable-automation`, que `ignore_default_args`
    remove de propósito para matar a tarja de automação. Uma correção de
    detecção destapava um aviso amarelo que anuncia automação — e que ainda
    empurra a página, deslocando o que se mede por coordenada.

    Aqui é Windows com Chrome real: não há motivo para desligar o sandbox.
    """
    fonte = inspect.getsource(login.main)
    assert "chromium_sandbox=True" in fonte
