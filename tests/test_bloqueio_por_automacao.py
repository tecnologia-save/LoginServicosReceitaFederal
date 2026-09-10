"""Bloqueio de sessão não é recusa de procuração — e virava uma."""
import inspect

from servicos_rf_login import login


def test_o_bloqueio_tem_desfecho_PROPRIO():
    """A classe diz que HÁ erro; só o texto diz QUAL.

    `_erro_representacao_visivel` casa por `.mensagemErro` e não lê o texto, o
    que está certo para detectar erro — a classe é o contrato. Mas o portal
    manda no MESMO elemento duas coisas opostas:

        Procuração não encontrada / vencida       → problema da EMPRESA
        O seu acesso foi bloqueado por possuir
        atributos que o caracteriza como um
        acesso automatizado                        → problema da SESSÃO

    Capturado em print pelo Jean em 10/09/2026, dentro do painel Representar.

    Sem distinguir, o segundo virava `perfil_recusado`, e o Save Process
    marcava `procuracao_cancelada_em` na empresa — mandando o jurídico cobrar
    do cliente uma procuração que provavelmente está perfeita. O erro não
    parava na run: ficava gravado no cadastro.
    """
    assert login.DESFECHO_BLOQUEIO_AUTOMACAO != login.DESFECHO_ERRO_PORTAL

    fonte = inspect.getsource(login._aguardar_desfecho)
    i = fonte.index("_erro_representacao_visivel(page)")
    j = fonte.index("DESFECHO_ERRO_PORTAL", i)
    assert "_erro_e_bloqueio_por_automacao" in fonte[i:j], (
        "a pergunta 'qual erro' tem de vir antes de chamar de recusa")


def test_bloqueio_nao_gasta_tentativa_nem_intervalo():
    """Não há o que retentar, e retentar piora.

    Cada nova ida reforça o sinal que causou o bloqueio, e custa um login e um
    captcha por empresa para ouvir a mesma frase. Por isso levanta na hora, em
    vez de consumir recusa e esperar os 31s.
    """
    fonte = inspect.getsource(login._representar_cnpj_procurador)
    i = fonte.index("DESFECHO_BLOQUEIO_AUTOMACAO")
    j = fonte.index("if desfecho == DESFECHO_ERRO_PORTAL")
    assert i < j, "o bloqueio tem de ser decidido antes da contagem de recusas"
    assert "raise BloqueioPorAutomacao" in fonte[i:j]
    assert "recusas += 1" not in fonte[i:j]


def test_a_frase_e_casada_por_trecho_curto():
    """O portal já mudou a redação antes; "acesso automatizado" é o núcleo que
    sobrevive. E o texto inteiro não vai para log — pode carregar quem se tenta
    representar."""
    assert login._MARCA_BLOQUEIO_AUTOMACAO == "acesso automatizado"
    fonte = inspect.getsource(login._erro_e_bloqueio_por_automacao)
    assert "print(" not in fonte, "não registrar o texto lido"
