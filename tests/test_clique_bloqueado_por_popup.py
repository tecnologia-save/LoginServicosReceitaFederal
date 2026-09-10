"""Achar o botão e não conseguir clicar não é "não encontrado"."""
import inspect

from servicos_rf_login import login

FONTE = inspect.getsource(login._clicar_entrar_govbr)


def test_clique_interceptado_fecha_popups_e_tenta_de_novo():
    """Perfil de Chrome novo traz cookies E tutorial, e o modal cobre o botão.

    `_fechar_popups_iniciais` roda antes daqui, mas espera 5s pelos cookies e
    4s pelo tutorial. Quando os popups renderizam DEPOIS dessas esperas
    vencerem, a varredura passa em branco e o clique bate no véu.

    Medido em 10/09/2026, RUN-11ca8a52, perfil `sessao-732c4178d9b8` recém
    criado: as DUAS empresas do lote caíram aqui, e a run inteira falhou com
    `nenhuma_empresa_consultada`.
    """
    assert "_fechar_popups_iniciais(page)" in FONTE
    i = FONTE.index("loc.click()")
    j = FONTE.index("_fechar_popups_iniciais(page)")
    assert i < j, "fecha os popups DEPOIS de o clique falhar, não antes de tudo"


def test_encontrado_e_nao_encontrado_saem_diferentes_no_log():
    """São investigações opostas: seletor errado se conserta no seletor;
    clique bloqueado se conserta no que está por cima.

    O log dizia "não encontrado em nenhum seletor" logo depois de imprimir
    "match com seletor alternativo" DUAS vezes. Foi essa contradição que me
    mandou procurar seletor quando o problema era um véu.
    """
    assert "achou = True" in FONTE
    assert "encontrado, mas o clique não" in FONTE

    # Só as linhas de CÓDIGO: o comentário acima cita o log antigo para
    # explicar o defeito, e contar o fonte inteiro reprovaria a documentação
    # da própria correção. Já caí nisso hoje, noutro arquivo.
    codigo = [l for l in FONTE.splitlines() if not l.strip().startswith("#")]
    saidas = [l for l in codigo if "não encontrado em nenhum seletor" in l]
    assert len(saidas) == 1, "a mensagem de 'não achei' só pode sair num caminho"


def test_a_espera_do_seletor_e_separada_do_clique():
    """Enquanto os dois estavam no mesmo `try`, falha de clique e ausência de
    elemento eram indistinguíveis — e por isso o log só sabia dizer uma
    delas."""
    i = FONTE.index('wait_for(state="visible"')
    j = FONTE.index("achou = True")
    assert i < j
    assert FONTE[i:j].count("except Exception:") == 1
