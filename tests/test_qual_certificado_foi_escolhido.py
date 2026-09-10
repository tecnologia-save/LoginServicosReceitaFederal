"""O log tem de dizer QUAL certificado entrou — não só que casou."""
import inspect

from servicos_rf_login import cert_dialog


def test_o_sucesso_registra_o_certificado_e_quantos_havia():
    """"Match por SERIAL" não responde "em quê".

    Em 10/09/2026 a automação entrou com o certificado errado: o diálogo tinha
    dois — Save Inteligência em primeiro, D&S em segundo — e ela confirmou o
    primeiro. Corrigido o defeito, continuava impossível CONFERIR pelo log;
    só olhando a tela no instante do clique, que dura menos de um segundo.

    O número de candidatos vai junto porque é ele que distingue "só havia o
    nosso" de "havia dois e pegamos o certo" — que pedem confiança diferente.

    O caminho de FALHA já despejava todos os textos. O de sucesso, que é onde
    a conferência importa, não dizia nada.
    """
    fonte = inspect.getsource(cert_dialog.selecionar_certificado_no_dialogo)
    assert fonte.count("_mascarar(txt)") == 2, "serial e CN, os dois caminhos"
    assert "candidato(s) no dialogo" in fonte


def test_os_digitos_sao_mascarados():
    """O nome da empresa responde a pergunta; CNPJ e número de série, não.

    Mascarar mantém o log conferível e sem dado de terceiro — é o mesmo
    princípio do gate de dados sensíveis, resolvido sem abrir exceção nele.
    """
    saida = cert_dialog._mascarar(
        "D&S Assessoria Tributaria LTDA  12.345.678/0001-90  664325120346eb4c")
    assert "D&S Assessoria Tributaria LTDA" in saida
    assert "12.345.678" not in saida
    assert "664325120346" not in saida
    assert not any(c.isdigit() for c in saida)
