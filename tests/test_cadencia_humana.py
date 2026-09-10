"""O que denuncia a automação não é a velocidade — é a regularidade."""
import inspect

from servicos_rf_login import login


def test_o_cnpj_e_digitado_e_nao_injetado():
    """`fill()` faz um CNPJ de 14 dígitos aparecer num evento só.

    Nenhum teclado produz isso. `press_sequentially` emite os eventos de tecla
    de verdade, e é a diferença entre "preencheu o campo" e "digitou".
    """
    fonte = inspect.getsource(login._preencher_formulario_representacao)
    assert "_digitar_humano" in fonte
    assert ".fill(cnpj)" not in fonte

    # O fallback para `fill` continua existindo dentro de `_digitar_humano`:
    # cadência não pode derrubar a representação numa versão de API diferente.
    assert "campo.fill(texto)" in inspect.getsource(login._digitar_humano)


def test_a_pausa_e_sorteada_e_nao_fixa():
    """Intervalo constante é tão artificial quanto intervalo nenhum.

    Um robô que espera exatamente 800ms entre cada ação é tão reconhecível
    quanto um que não espera — a assinatura é a variância zero, não a duração.
    """
    fonte = inspect.getsource(login._pausa_humana)
    assert "random.uniform" in fonte
    assert login._PAUSA_MIN_S < login._PAUSA_MAX_S

    teclas = inspect.getsource(login._digitar_humano)
    assert "random.randint" in teclas
    assert login._TECLA_MIN_MS < login._TECLA_MAX_MS


def test_o_custo_por_empresa_fica_em_poucos_segundos():
    """Em 09/09/2026 foram cortados 3min30s de espera morta por empresa.

    Devolver aquilo em nome de "parecer humano" trocaria um problema por outro.
    O pior caso somado aqui — quatro pausas e a digitação — tem de continuar
    sendo uma fração pequena disso.
    """
    pior_pausas = 3 * login._PAUSA_MAX_S + 1.6
    pior_teclas = 14 * login._TECLA_MAX_MS / 1000
    assert pior_pausas + pior_teclas < 12.0
