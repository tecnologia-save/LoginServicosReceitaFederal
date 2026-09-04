"""Os TRES cenarios reais depois de clicar Representar.

Depois do clique existem tres mundos, nao dois:

    1. nenhum captcha;
    2. captcha que o resolvedor JA trata;
    3. captcha que precisa de humano.

A janela manual so existe no terceiro. Chamar o humano antes de tentar seria
pedir trabalho manual para algo automatizavel — e, em background,
transformaria em falha uma run que teria terminado sozinha.

CONTRATO REAL de `solve_hcaptcha`, auditado no fonte:

    sem captcha .................. True   (indistinguivel de "resolvido")
    resolvido .................... True
    nao resolvido em max_rounds .. False
    tipo desconhecido ............ cai em `_solve_imagem` -> False
    erro do modelo ............... engolido pelos `_solve_*` -> False
    GEMINI_API_KEY ausente ....... RuntimeError

Ou seja: valor de retorno = desfecho FUNCIONAL; excecao = erro TECNICO. E o
veredito do solver nao decide nada — quem decide e a pos-condicao do perfil.

Sem captcha real, sem Gemini, sem portal.
"""
import pytest
from fakes_portal import CNPJ_ALVO, CNPJ_OUTRO, Pagina, Portal
from resolvedor_captcha import (
    TIPO_BOLA,
    TIPO_CARTAO_ANIMAL,
    TIPO_DESCONHECIDO,
    TIPO_GRADE,
    TIPO_GRADE_FUSED,
    TIPO_IMAGEM,
)

from servicos_rf_login import login


@pytest.fixture(autouse=True)
def _tempo(relogio_virtual):
    """Relogio virtual em todo o arquivo — ver conftest."""


def _pagina(**kw):
    portal = Portal(**kw)
    return Pagina(portal), portal


def _chamador(respostas):
    estado = {"chamadas": 0}

    def callback(*, segundos_restantes):
        i = min(estado["chamadas"], len(respostas) - 1)
        estado["chamadas"] += 1
        return respostas[i]

    callback.estado = estado
    return callback


# ══ Cenario A · sem captcha ═════════════════════════════════════════════════

def test_a_sem_captcha_nao_chama_solver_nem_humano(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.representar(CNPJ_ALVO)
    cb = _chamador([login.CONTINUAR])

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=cb) is True
    assert solver["chamadas"] == 0
    assert cb.estado["chamadas"] == 0


# ══ Cenario B · captcha que a automacao resolve ═════════════════════════════

def test_b_captcha_automatizavel_resolve_sem_humano(solver, capsys):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_GRADE)
    cb = _chamador([login.CONTINUAR])

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=cb) is True
    assert solver["chamadas"] == 1
    assert cb.estado["chamadas"] == 0            # NENHUMA janela
    assert "Perfil representado confirmado" in capsys.readouterr().out


# ══ Cenario C · captcha que precisa de humano ═══════════════════════════════

def test_c_grade_que_o_solver_nao_conclui_cai_para_o_humano(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE, automatizavel=False)

    def humano(*, segundos_restantes):
        portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=humano) is True
    assert solver["chamadas"] == 1               # tentou automatico ANTES


def test_a_ordem_e_automatico_e_so_depois_humano(solver):
    ordem = []

    def so_registra(_portal):
        ordem.append("solver")
        return False

    solver["efeito"] = so_registra
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE, automatizavel=False)

    def humano(*, segundos_restantes):
        ordem.append("humano")
        portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                       on_manual_challenge=humano)
    assert ordem == ["solver", "humano"]


# ══ D a I ══════════════════════════════════════════════════════════════════

def test_d_solver_diz_resolvido_mas_perfil_nao_mudou(solver):
    """O veredito do solver nao decide: quem decide e a pos-condicao."""
    solver["efeito"] = lambda _portal: True      # afirma que resolveu, e mente
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE, automatizavel=False)

    def humano(*, segundos_restantes):
        portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=humano) is True


def test_e_erro_tecnico_do_solver_nao_vira_intervencao_manual(solver):
    """Chave ausente ou dependencia indisponivel: janela nao resolve isso."""
    solver["efeito"] = RuntimeError("GEMINI_API_KEY nao configurada no ambiente.")
    cb = _chamador([login.CONTINUAR])
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE, automatizavel=False)

    with pytest.raises(login.FalhaDoResolvedorCaptcha):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=cb)
    assert cb.estado["chamadas"] == 0


def test_e_erro_tecnico_nao_carrega_a_mensagem_original(solver):
    solver["efeito"] = RuntimeError("SEGREDO_TESTE_chave_no_ambiente")
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE, automatizavel=False)

    with pytest.raises(login.FalhaDoResolvedorCaptcha) as exc:
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO)
    assert "SEGREDO_TESTE" not in str(exc.value)
    assert "RuntimeError" in str(exc.value)      # so o TIPO sobrevive
    assert exc.value.__cause__ is None


def test_f_captcha_some_mas_perfil_errado_nao_e_sucesso(solver):
    def resolve_errado(portal):
        portal.representar(CNPJ_OUTRO)           # captcha some, perfil errado
        return True

    solver["efeito"] = resolve_errado
    cb = _chamador([login.CONTINUAR])
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE, automatizavel=False)

    with pytest.raises(login.RepresentacaoNaoConfirmada):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=cb)
    assert cb.estado["chamadas"] == 0            # nao ha o que um humano resolva


def test_g_continuar_cedo_demais_depois_do_automatico(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE, automatizavel=False)
    n = {"v": 0}

    def humano(*, segundos_restantes):
        n["v"] += 1
        if n["v"] >= 2:
            portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=humano) is True
    assert n["v"] == 2


def test_h_background_com_captcha_automatizavel_resolve_sozinho(solver):
    """Background NAO significa que todo captcha falha."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_GRADE)

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=None) is True
    assert solver["chamadas"] == 1


def test_i_background_com_captcha_manual_pede_intervencao(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE, automatizavel=False)

    with pytest.raises(login.RepresentacaoRequerIntervencao):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=None)
    assert solver["chamadas"] == 1               # tentou automatico antes


# ══ Contrato ════════════════════════════════════════════════════════════════

def test_falha_do_resolvedor_esta_na_api_publica():
    import servicos_rf_login as pacote
    assert "FalhaDoResolvedorCaptcha" in pacote.__all__


def test_o_captcha_do_login_nao_foi_alterado():
    """O desafio inicial ja funcionava e segue pelo mesmo caminho."""
    import inspect
    fonte = inspect.getsource(login._try_solve_captcha)
    assert "solve_hcaptcha(page)" in fonte
    assert "on_manual_challenge" not in fonte


# ══ Politica POR TIPO — allowlist da representacao ══════════════════════════
#
# Na run real o portal apresentou `cartao_animal` e o solver tentou: 3 rodadas,
# 12 capturas de frame, chamadas ao modelo — para cair na intervencao humana do
# mesmo jeito.
#
# CONTRATO ESTREITADO depois da run de 09:58 no QA: `grade_fused` foi
# classificado CORRETAMENTE e mesmo assim seguiu para o solver. So a grade 3x3
# normal e automatica aqui.

def test_allowlist_e_grade_e_bola():
    """Cada entrada e posta A MAO. Formato novo fica de fora por construcao."""
    assert login.TIPOS_AUTOMATICOS_REPRESENTACAO == (TIPO_GRADE, TIPO_BOLA)


def test_nenhum_outro_tipo_entra_na_allowlist():
    """Gate: a lista nao pode crescer sem que este teste caia."""
    for tipo in (TIPO_GRADE_FUSED, TIPO_CARTAO_ANIMAL, TIPO_IMAGEM,
                 TIPO_DESCONHECIDO, login.TIPO_NENHUM):
        assert tipo not in login.TIPOS_AUTOMATICOS_REPRESENTACAO, tipo


@pytest.mark.parametrize("tipo", [TIPO_GRADE, TIPO_BOLA])
def test_b_c_tipos_da_allowlist_sao_tentados(solver, tipo, capsys):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=tipo)
    cb = _chamador([login.CONTINUAR])

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=cb) is True
    assert solver["chamadas"] == 1
    assert cb.estado["chamadas"] == 0
    assert f"tipo={tipo}" in capsys.readouterr().out


@pytest.mark.parametrize("tipo", [TIPO_GRADE_FUSED, TIPO_CARTAO_ANIMAL,
                                  TIPO_IMAGEM, TIPO_DESCONHECIDO])
def test_d_e_f_tipos_fora_da_allowlist_vao_direto_ao_humano(solver, tipo, capsys):
    """ZERO chamada ao solver: nem captura de frames, nem Gemini."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=tipo)

    def humano(*, segundos_restantes):
        portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=humano) is True
    assert solver["chamadas"] == 0
    assert f"requer validação manual | tipo={tipo}" in capsys.readouterr().out


def test_d_cartao_animal_reproduz_a_run_real(solver):
    """O caso exato que a execucao produziu."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_CARTAO_ANIMAL)
    chamadas = []

    def humano(*, segundos_restantes):
        chamadas.append(segundos_restantes)
        portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=humano) is True
    assert solver["chamadas"] == 0          # nada de 12 frames
    assert len(chamadas) == 1               # humano chamado IMEDIATAMENTE


def test_h_background_com_grade_resolve_sozinho(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_GRADE)
    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=None) is True
    assert solver["chamadas"] == 1


def test_i_background_com_cartao_animal_pede_intervencao(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_CARTAO_ANIMAL)
    with pytest.raises(login.RepresentacaoRequerIntervencao):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=None)
    assert solver["chamadas"] == 0          # nem tentou, e nem devia


def test_n_continuar_cedo_demais_mantem_o_mesmo_deadline(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_CARTAO_ANIMAL)
    restantes = []

    def humano(*, segundos_restantes):
        restantes.append(segundos_restantes)
        if len(restantes) >= 3:
            portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=humano,
        prazo_intervencao_s=300.0) is True
    assert restantes == sorted(restantes, reverse=True)   # deadline nao reinicia
    assert solver["chamadas"] == 0


def test_o_solver_global_mantem_todos_os_tipos():
    """A allowlist e politica DESTE fluxo — nada foi removido do resolvedor."""
    import resolvedor_captcha
    assert TIPO_CARTAO_ANIMAL in resolvedor_captcha.TIPOS_CONHECIDOS
    assert TIPO_IMAGEM in resolvedor_captcha.TIPOS_CONHECIDOS
    assert hasattr(resolvedor_captcha.solver, "_solve_cartao_animal")
    assert hasattr(resolvedor_captcha.solver, "_solve_imagem")


# ══ RED da run de 09:58 no QA ═══════════════════════════════════════════════
#
#   09:58:25 Desfecho observado | tipo=captcha
#   09:58:25 Tipo: grade fused
#   09:58:27 Desafio aberto | tipo=grade_fused
#   09:58:27 Desafio automatizavel detectado | tipo=grade_fused
#
# Nao houve ambiguidade: o classificador acertou, e a allowlist e que mandou o
# desafio para o solver. Depois disso o Gemini foi chamado varias vezes.

def test_grade_fused_nao_chama_o_gemini(solver, capsys):
    """RED 1: o caso EXATO da run. ZERO chamada ao solver, uma ao humano."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_GRADE_FUSED)
    chamadas = []

    def humano(*, segundos_restantes):
        chamadas.append(segundos_restantes)
        portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=humano) is True
    assert solver["chamadas"] == 0
    assert len(chamadas) == 1
    saida = capsys.readouterr().out
    assert f"requer validação manual | tipo={TIPO_GRADE_FUSED}" in saida
    assert "Desafio automatizável detectado" not in saida


def test_grade_fused_sem_janela_manual_e_fail_safe(solver):
    """RED 2: em background nao ha humano — e ainda assim ZERO Gemini."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_GRADE_FUSED)

    with pytest.raises(login.RepresentacaoRequerIntervencao):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=None)
    assert solver["chamadas"] == 0


def test_a_grade_normal_continua_automatica(solver):
    """O que a allowlist ainda autoriza — e so isso."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=TIPO_GRADE)
    cb = _chamador([login.CONTINUAR])

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=cb) is True
    assert solver["chamadas"] == 1
    assert cb.estado["chamadas"] == 0


def test_o_resolvedor_continua_suportando_grade_fused():
    """A restricao e DESTA integracao, nao do resolvedor.

    Outros consumidores — e o captcha do proprio login — continuam podendo
    resolver `grade_fused`, `cartao_animal` e `imagem`.
    """
    from resolvedor_captcha import solver as rc

    assert rc.TIPO_GRADE_FUSED in rc.TIPOS_CONHECIDOS
    assert rc.TIPO_CARTAO_ANIMAL in rc.TIPOS_CONHECIDOS


# ── Orcamento POR TIPO ───────────────────────────────────────────────────────
#
# A bola tem captura de 7s antes da primeira chamada; a grade nao tem captura
# nenhuma. Um teto unico ou aperta a bola ou afrouxa a grade.

def test_bola_tem_orcamento_proprio_e_maior():
    """Os 25s da grade nao cabem 7s de captura + a latencia medida do modelo."""
    t_bola, d_bola = login._orcamento_do_captcha(TIPO_BOLA)
    t_grade, d_grade = login._orcamento_do_captcha(TIPO_GRADE)
    assert (t_bola, d_bola) == (login.TIMEOUT_GEMINI_BOLA_MS,
                                login.DEADLINE_CAPTCHA_BOLA_S)
    assert (t_grade, d_grade) == (login.TIMEOUT_GEMINI_REPRESENTACAO_MS,
                                  login.DEADLINE_CAPTCHA_REPRESENTACAO_S)
    assert d_bola > d_grade and t_bola > t_grade


# A UNIDADE DE CUSTO E A RODADA, E O DESAFIO TEM DUAS.
#
# A versao anterior deste teste somava UMA captura e passava verde com o
# orcamento furado — ele codificava o modelo errado do problema e por isso deu
# falsa confianca em vez de pegar o defeito. O hCaptcha faz duas rodadas por
# desafio, e a segunda traz animais e trajetoria novos: paga outra captura
# inteira. Estes dois testes existem para que encurtar o teto volte a doer.

CAPTURA_S = 7.0    # BOLA_FRAMES (14) * BOLA_INTERVALO_S (0,5), no ResolvedorCaptcha
PREPARO_S = 0.8    # medido: 0,6-0,8s
ESPERA_S = 3.0     # _wait_for_resolve depois do submit
GEMINI_BOM_S = 6.5   # mediana das 3 amostras medidas em 04/09/2026
GEMINI_RUIM_S = 9.6  # pior das 3
RODADAS = 2          # o hCaptcha faz duas por desafio


def _custo_de_rodada(gemini_s):
    return CAPTURA_S + PREPARO_S + gemini_s + ESPERA_S


def test_orcamento_da_bola_cabe_DUAS_rodadas_no_caso_bom():
    """35s cabiam por 0,4s — teto sem folga, e a segunda rodada morria."""
    assert RODADAS * _custo_de_rodada(GEMINI_BOM_S) <= login.DEADLINE_CAPTCHA_BOLA_S


def test_orcamento_da_bola_cabe_DUAS_rodadas_no_caso_RUIM():
    """A folga tem de sobreviver a duas chamadas na pior latencia medida."""
    assert RODADAS * _custo_de_rodada(GEMINI_RUIM_S) <= login.DEADLINE_CAPTCHA_BOLA_S


def test_segunda_rodada_ainda_recebe_orcamento_util():
    """O que sobra para a rodada 2 tem de caber a pior latencia medida.

    `timeout_efetivo = min(TIMEOUT_GEMINI_BOLA_MS, restante)`. Com 35s a rodada 2
    entrava com ~7s e duas das tres latencias medidas nao cabiam nisso.
    """
    gasto_na_rodada_1 = _custo_de_rodada(GEMINI_RUIM_S)
    sobra = login.DEADLINE_CAPTCHA_BOLA_S - gasto_na_rodada_1 - CAPTURA_S - PREPARO_S
    assert sobra >= GEMINI_RUIM_S


def test_teto_da_bola_acomoda_a_chamada_mais_lenta_medida():
    """9,6s foi a pior das 3 amostras — passou a 400ms do teto antigo de 10s."""
    assert login.TIMEOUT_GEMINI_BOLA_MS >= 12_000


# O limite do portal era ANEDOTA ("~1min, uma run"), e o levantamento do
# historico de dev a derrubou: existe representacao CONFIRMADA 70,3s depois do
# clique em Representar, e a recusa mais RAPIDA veio em 5,0s. Se demora fosse o
# gatilho da recusa, nao haveria recusa em cinco segundos.
MAIOR_REPRESENTACAO_CONFIRMADA_S = 70.3


def test_nenhum_orcamento_passa_do_limite_medido_do_portal():
    """Abaixo do maior sucesso observado, com folga — nao encostado nele."""
    for teto in (login.DEADLINE_CAPTCHA_BOLA_S,
                 login.DEADLINE_CAPTCHA_REPRESENTACAO_S):
        assert MAIOR_REPRESENTACAO_CONFIRMADA_S - teto >= 10.0, teto


def test_tipo_fora_da_allowlist_usa_o_orcamento_padrao():
    """Nao existe caminho em que um tipo novo herde a folga da bola."""
    for tipo in (TIPO_GRADE_FUSED, TIPO_CARTAO_ANIMAL, TIPO_IMAGEM,
                 TIPO_DESCONHECIDO):
        assert login._orcamento_do_captcha(tipo) == (
            login.TIMEOUT_GEMINI_REPRESENTACAO_MS,
            login.DEADLINE_CAPTCHA_REPRESENTACAO_S), tipo


def test_orcamento_maior_vale_SO_para_a_bola():
    """A grade 3x3 roda todo dia e nao herda nada da folga da animacao."""
    assert login._orcamento_do_captcha(TIPO_GRADE) == (10_000, 25.0)
    assert login.DEADLINE_CAPTCHA_REPRESENTACAO_S == 25.0
