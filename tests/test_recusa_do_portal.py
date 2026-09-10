"""Recusa não é ausência de resposta, e não custa o mesmo."""
import inspect

from servicos_rf_login import login


def test_desiste_na_segunda_recusa_e_nao_na_terceira():
    """As três tentativas existem para o portal NÃO responder.

    Recusa é outra coisa: ele respondeu, e respondeu não. A terceira tentativa
    paga mais 31s de intervalo (o throttle que o portal pediu) e mais um captcha
    para ouvir a mesma resposta.
    """
    assert login.RECUSAS_PARA_DESISTIR == 2
    assert login.RECUSAS_PARA_DESISTIR < login.MAX_TENTATIVAS_REPRESENTACAO


def test_o_intervalo_entre_tentativas_permanece():
    """O corte é a TERCEIRA tentativa, não a espera.

    `COOLDOWN_ERRO_REPRESENTACAO_S` é o throttle que o próprio portal pediu —
    "pelo menos 30 segundos". Encurtar isso para ganhar tempo seria desrespeitar
    um pedido explícito dele, e é o oposto do que esta mudança faz: ela remove
    uma tentativa desnecessária, não a educação entre as necessárias.
    """
    assert login.COOLDOWN_ERRO_REPRESENTACAO_S >= 30.0


def test_a_decisao_e_por_CONTAGEM_e_nao_pelo_texto():
    """`_erro_representacao_visivel` não lê a frase, de propósito.

    Ela muda com o tempo e pode carregar informação de quem se tenta
    representar — a classe é o contrato. Reconhecer "recusas definitivas" pelo
    texto teria sido a saída óbvia, e violaria essa decisão nos dois pontos.
    Contar recusas chega ao mesmo lugar sem tocar na mensagem.
    """
    fonte = inspect.getsource(login._erro_representacao_visivel)
    assert "inner_text" not in fonte and "text_content" not in fonte
    assert "RECUSAS_PARA_DESISTIR" in inspect.getsource(login)


def test_recusa_vira_desfecho_TIPADO():
    """`RepresentacaoRejeitadaPeloPortal` é o que faz a empresa virar pendência
    de cadastro em vez de falha técnica — quem lê o painel precisa saber que
    falta procuração, não que a automação quebrou."""
    # `rindex`, e nao `index`: a condicao aparece DUAS vezes — no laco, para
    # parar cedo, e no fim, para tipar o desfecho. A primeira e seguida de
    # `break`, e procurar a excecao ali reprova o codigo certo.
    fonte = inspect.getsource(login)
    i = fonte.rindex("if recusas >= RECUSAS_PARA_DESISTIR:")
    assert "RepresentacaoRejeitadaPeloPortal" in fonte[i:i + 300]
