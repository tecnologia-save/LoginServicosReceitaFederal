"""Resolver o primeiro desafio e não ter orçamento para o segundo."""
from servicos_rf_login import login


def test_o_bonus_paga_um_desafio_de_verdade():
    """O "+5s" do log não era o bônus — era a sobra até o teto.

    A extensão soma o orçamento INTEIRO (55s) e é cortada pelo teto duro:
    `min(T+60, T+110)` deixava 5 segundos. Cinco segundos não pagam um desafio
    novo; uma rodada resolvida custa 15 a 25s.

    Medido na RUN-2c68037a, três empresas seguidas:

        3 Desafio aberto | 3 Captcha resolvido | 3 Desafio ainda ativo
        6 SegundoProvedorSemOrcamento | 3 não concluída

    Resolvia o primeiro, o portal abria o segundo, e acabava o tempo.
    """
    bonus = login.DEADLINE_MAX_COM_PROGRESSO_S - login.DEADLINE_CAPTCHA_REPRESENTACAO_S
    assert bonus >= 10.0, "o bônus tem de pagar mais que uma sobra"


def test_o_teto_nao_passa_do_que_o_portal_ja_aceitou():
    """70,3s é a maior representação CONFIRMADA no histórico de dev. Passar
    disso seria apostar contra o único limite que temos medido — e o preço de
    errar para cima é o portal recusar, o que vira `perfil_recusado` e marca a
    empresa como sem procuração."""
    assert 70.3 - login.DEADLINE_MAX_COM_PROGRESSO_S >= 5.0


def test_a_extensao_continua_condicional():
    """Só ganha tempo quem submeteu uma rodada com sucesso e viu outra
    aparecer. Um desafio que nunca fecha rodada nenhuma não recebe extensão —
    senão o caso ruim consome o teto inteiro antes de desistir."""
    import inspect
    from resolvedor_captcha import solver
    fonte = inspect.getsource(solver.solve_hcaptcha)
    assert "progresso comprovado" in fonte
    assert "inicio_orcamento is not None" in fonte
