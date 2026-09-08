"""Orcamento de tempo do captcha DE LOGIN.

Ate 08/09/2026 nao havia nenhum: `solve_hcaptcha(page)` pelado, com o padrao do
resolvedor (30 s por chamada, teto total nenhum). Tres consequencias, todas
observadas:

  - o eCAC fecha o captcha por tempo, e a automacao seguia resolvendo um desafio
    ja morto na tela;
  - `_solve_bola` se RECUSA a rodar sem deadline, entao o formato animado — que
    aparece TAMBEM no login — nao era nem tentado;
  - o 408 do SSO, cuja mensagem e "Your browser didn't send a complete request
    in time".
"""
import inspect

from servicos_rf_login import login


def test_o_login_tem_teto_de_tempo():
    assert login.DEADLINE_CAPTCHA_LOGIN_S > 0
    assert login.TIMEOUT_GEMINI_LOGIN_MS > 0


def test_o_solver_recebe_o_orcamento():
    """Sem deadline, `_solve_bola` se recusa a rodar e o animado nao e tentado."""
    fonte = inspect.getsource(login._try_solve_captcha)
    assert "deadline_s=restante" in fonte
    assert "gemini_timeout_ms=TIMEOUT_GEMINI_LOGIN_MS" in fonte
    assert "solve_hcaptcha(page)" not in fonte, "voltou a chamar sem orcamento"


def test_o_teto_e_TOTAL_e_nao_por_tentativa():
    """3 tentativas de 120 s cada dariam seis minutos no pior caso."""
    fonte = inspect.getsource(login._try_solve_captcha)
    assert "fim = time.monotonic() + DEADLINE_CAPTCHA_LOGIN_S" in fonte
    assert "restante = fim - time.monotonic()" in fonte


def test_para_quando_nao_cabe_mais_uma_tentativa():
    """Menos que o minimo nao da nem para a captura comecar."""
    fonte = inspect.getsource(login._try_solve_captcha)
    assert "restante <= 10.0" in fonte
    assert "orcamento esgotado" in fonte or "orçamento esgotado" in fonte


def test_o_login_tem_MAIS_folga_que_a_representacao():
    """Aqui nao ha o relogio do portal, que la limita tudo a ~70 s.

    E essa folga que permite ao resolvedor animado usar a janela de captura
    longa sem apertar nada.
    """
    assert login.DEADLINE_CAPTCHA_LOGIN_S > login.DEADLINE_MAX_COM_PROGRESSO_S
    assert login.DEADLINE_CAPTCHA_LOGIN_S > login.DEADLINE_CAPTCHA_BOLA_S


def test_o_animado_cabe_no_login_com_a_janela_longa():
    """Pior caso das duas rodadas progressivas, com os tempos medidos."""
    ABERTURA, PREP, CHAMADA, ESPERA = 5.6, 1.0, 20.0, 3.0
    rodada1 = ABERTURA + 7 + PREP + CHAMADA + ESPERA
    rodada2 = 15 + PREP + CHAMADA + ESPERA
    assert rodada1 + rodada2 <= login.DEADLINE_CAPTCHA_LOGIN_S
