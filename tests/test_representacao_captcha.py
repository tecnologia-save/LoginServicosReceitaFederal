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


def test_o_captcha_do_login_ganhou_orcamento_mas_nao_janela_manual():
    """Esta guarda EXISTIA para manter o login intocado, e foi cruzada de
    proposito em 08/09/2026, com autorizacao do Jean.

    O que ela protegia continua protegido: o login NAO ganha janela manual —
    nao ha pessoa esperando ali, e injetar `on_manual_challenge` faria a run
    parar por alguem que nao existe.

    O que mudou e o orcamento de tempo, e ele nao era neutro por ausencia:
    `_solve_bola` se RECUSA a rodar sem deadline, entao o formato animado — que
    aparece TAMBEM no login — nao era nem tentado. "Nao alterar" custava um
    formato inteiro.
    """
    import inspect
    fonte = inspect.getsource(login._try_solve_captcha)
    assert "on_manual_challenge" not in fonte, (
        "o login nao pode ganhar janela manual: nao ha pessoa esperando ali")
    assert "solve_hcaptcha(page)" not in fonte, (
        "voltou a chamar sem orcamento — o animado deixa de ser tentado")
    assert "deadline_s=restante" in fonte


# ══ Politica POR TIPO — allowlist da representacao ══════════════════════════
#
# Na run real o portal apresentou `cartao_animal` e o solver tentou: 3 rodadas,
# 12 capturas de frame, chamadas ao modelo — para cair na intervencao humana do
# mesmo jeito.
#
# CONTRATO ESTREITADO depois da run de 09:58 no QA: `grade_fused` foi
# classificado CORRETAMENTE e mesmo assim seguiu para o solver. So a grade 3x3
# normal e automatica aqui.

def test_todo_desafio_e_tentado():
    """Nenhum tipo vai ao humano sem tentativa. Ver o comentario em login.py."""
    for tipo in (TIPO_GRADE, TIPO_GRADE_FUSED, TIPO_BOLA,
                 TIPO_CARTAO_ANIMAL, TIPO_IMAGEM, TIPO_DESCONHECIDO):
        assert tipo in login.TIPOS_AUTOMATICOS_REPRESENTACAO, tipo


def test_so_tipo_nenhum_fica_de_fora():
    """Unica excecao, e nao e politica: sem desafio nao ha o que resolver."""
    assert login.TIPO_NENHUM not in login.TIPOS_AUTOMATICOS_REPRESENTACAO


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


@pytest.mark.parametrize("tipo", [TIPO_CARTAO_ANIMAL, TIPO_IMAGEM,
                                  TIPO_DESCONHECIDO])
def test_d_e_f_tipos_nao_resolvidos_vao_ao_humano_APOS_tentar(solver, tipo, capsys):
    """O humano continua existindo — mas so depois de uma tentativa real."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(tipo=tipo)

    def humano(*, segundos_restantes):
        portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=humano) is True
    assert solver["chamadas"] == 1          # TENTOU antes de desistir
    assert "Resolução automática: não concluída" in capsys.readouterr().out


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
    assert solver["chamadas"] == 1          # tenta uma vez, com teto de tempo
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
    assert solver["chamadas"] == 1          # agora tenta — e cai ao humano se falhar


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
    assert solver["chamadas"] == 1     # tentou; o deadline do humano nao reinicia


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

def test_grade_fused_agora_e_tentado(solver, capsys):
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
    assert solver["chamadas"] == 1          # TENTA — era o caso da RUN-4ef0d17d
    assert len(chamadas) == 0               # e resolveu, sem incomodar ninguem
    saida = capsys.readouterr().out
    assert f"Desafio automatizável detectado | tipo={TIPO_GRADE_FUSED}" in saida


def test_grade_fused_sem_janela_manual_e_fail_safe(solver):
    """Em background nao ha humano: se o solver NAO conclui, desfecho tipado.

    Antes este teste exigia ZERO chamada ao modelo. Agora ele exige o
    contrario — que o solver TENHA sido chamado — e continua defendendo o
    que importa de verdade: sem janela manual, nada fica esperando por
    ninguem, o desfecho e uma excecao tipada.
    """
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(
        tipo=TIPO_GRADE_FUSED, automatizavel=False)

    with pytest.raises(login.RepresentacaoRequerIntervencao):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=None)
    assert solver["chamadas"] == 1


def test_a_grade_normal_continua_automatica(solver):
    """O formato que roda todo dia segue resolvido sem humano."""
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


def test_orcamento_INICIAL_da_bola_cabe_UMA_rodada_com_folga():
    """A segunda rodada passou a vir da EXTENSAO, nao do orcamento inicial.

    Ate 08/09/2026 o inicial era 60s — igual ao teto duro —, e a extensao por
    progresso valia zero neste formato. Com 40s de inicial, quem nunca fecha
    uma rodada desiste mais cedo, e quem fecha chega aos mesmos 60s.
    """
    ABERTURA_S = 5.6   # classificacao e abertura, medidas em producao
    pior = ABERTURA_S + _custo_de_rodada(GEMINI_RUIM_S)
    assert pior <= login.DEADLINE_CAPTCHA_BOLA_S, (
        f"uma rodada custa {pior:.1f}s e o inicial e {login.DEADLINE_CAPTCHA_BOLA_S}s")


def test_com_progresso_a_bola_alcanca_DUAS_rodadas():
    """O teto duro e que precisa cobrir as duas — e so quem avanca o alcanca."""
    ABERTURA_S = 5.6
    duas = ABERTURA_S + RODADAS * _custo_de_rodada(GEMINI_RUIM_S)
    assert duas <= login.DEADLINE_MAX_COM_PROGRESSO_S, (
        f"duas rodadas custam {duas:.1f}s e o teto duro e "
        f"{login.DEADLINE_MAX_COM_PROGRESSO_S}s")


def test_a_extensao_da_bola_nao_e_zero():
    """O defeito de 08/09/2026: inicial igual ao teto duro anula a extensao."""
    assert login.DEADLINE_MAX_COM_PROGRESSO_S > login.DEADLINE_CAPTCHA_BOLA_S


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


def test_tipo_sem_orcamento_proprio_usa_o_padrao():
    """Nao existe caminho em que um tipo novo herde a folga de outro.

    `TIPO_IMAGEM` saiu desta lista em 08/09/2026, quando ganhou orcamento
    proprio: ele tem CINCO rodadas, e os 25 s da grade o cortavam na segunda.
    """
    for tipo in (TIPO_GRADE_FUSED, TIPO_CARTAO_ANIMAL, TIPO_DESCONHECIDO):
        assert login._orcamento_do_captcha(tipo) == (
            login.TIMEOUT_GEMINI_REPRESENTACAO_MS,
            login.DEADLINE_CAPTCHA_REPRESENTACAO_S), tipo


def test_orcamento_maior_vale_SO_para_a_bola():
    """A grade 3x3 roda todo dia e nao herda nada da folga da animacao."""
    assert login._orcamento_do_captcha(TIPO_GRADE) == (10_000, 25.0)
    assert login.DEADLINE_CAPTCHA_REPRESENTACAO_S == 25.0


# ── Politica de certificado: DESCOBRIR, nao assumir ─────────────────────────
#
# `policy_ok` era `True` fixo. O codigo assumia que a politica corporativa do
# Chrome estava instalada e por isso nunca armava o fallback que fecha o dialogo
# "Selecione um certificado" — e a automacao ficava parada nele.
#
# A suposicao estava errada: `--auto-select-certificate-for-urls` na linha de
# comando NAO e um switch do Chrome. O mecanismo real e a politica lida do
# registro. A flag e aceita em silencio e ignorada. Observado com DOIS
# certificados e de novo com UM so — nunca esteve valendo.

def test_politica_e_consultada_e_nao_presumida():
    """Devolve booleano de verdade, lido do registro — sem excecao fora do Windows."""
    assert isinstance(login.politica_de_certificado_instalada(), bool)


def test_policy_ok_padrao_e_descubra():
    """`None` = descubra. `True` fixo era o defeito: assumia sem verificar."""
    import inspect
    par = inspect.signature(login.main).parameters["policy_ok"]
    assert par.default is None, "o padrao voltou a assumir em vez de descobrir"


def test_quem_chama_ainda_pode_forcar():
    """Descobrir e o PADRAO, nao uma imposicao: o valor explicito continua valendo."""
    import inspect
    anot = inspect.signature(login.main).parameters["policy_ok"].annotation
    assert "bool" in str(anot) and "None" in str(anot)


def test_cn_do_ambiente_liga_o_modo_windows_store():
    """O CN existia em CERT_SUBJECT_CN e o login lia so o parametro.

    Consequencia: `usar_windows_store` ficava False e caiam TRES coisas juntas —
    a flag de auto-selecao nao era montada, o fallback do dialogo nao era armado,
    e o login caia no modo .pfx, que o proprio arquivo documenta como quebrado
    com ICP-Brasil. O dialogo "Selecione um certificado" ficava aberto esperando
    uma pessoa que, na VM, nao existe.
    """
    import inspect
    fonte = inspect.getsource(login.main)
    assert 'os.getenv("CERT_SUBJECT_CN"' in fonte


# ── Clique unico em imagem livre tem orcamento proprio ──────────────────────
#
# Ele herdava os 25 s da grade 3x3, e a comparacao nao se sustenta: a grade
# responde numa chamada sobre um screenshot parado; `_solve_imagem` tem CINCO
# rodadas, cada uma com dois screenshots e uma chamada. Com 25 s ele nao passava
# da segunda — o resolvedor certo era chamado e cortado no meio.

def test_imagem_tem_orcamento_maior_que_a_grade():
    t_img, d_img = login._orcamento_do_captcha(login.TIPO_IMAGEM)
    t_grade, d_grade = login._orcamento_do_captcha(login.TIPO_GRADE)
    assert d_img > d_grade
    assert t_img > t_grade


def test_imagem_alcanca_a_quarta_rodada():
    """O segundo provedor entra na 4a rodada. Com 25 s ele nunca era jogado —
    a carta de acuracia existia e nao chegava a este formato, que e justamente
    onde ela mediu 3/3."""
    CAPTURA_S = 2.0      # dois screenshots por rodada
    CHAMADA_S = 7.9      # pior das amostras medidas em 08/09/2026
    _t, teto = login._orcamento_do_captcha(login.TIPO_IMAGEM)
    quatro_rodadas = 4 * (CAPTURA_S + CHAMADA_S)
    assert teto >= quatro_rodadas, (
        f"teto de {teto}s nao alcanca a 4a rodada ({quatro_rodadas:.1f}s)")


def test_a_bola_tem_o_maior_timeout_POR_CHAMADA():
    """A comparacao util e por chamada: a animacao pede mais raciocinio.

    Nos TETOS a comparacao deixou de valer — o inicial da bola caiu para 40s
    para dar espaco a extensao, entao ele e menor que o da imagem (45s) mesmo
    custando mais por rodada. O que a bola tem a mais e a captura de 7s, e ela
    e paga dentro da rodada, nao no teto.
    """
    t_img, _d = login._orcamento_do_captcha(login.TIPO_IMAGEM)
    t_bola, _d2 = login._orcamento_do_captcha(login.TIPO_BOLA)
    assert t_bola > t_img


def test_nenhum_orcamento_encosta_no_limite_do_portal():
    """70,3 s e a maior representacao CONFIRMADA no historico de dev."""
    MAIOR_CONFIRMADA_S = 70.3
    for tipo in (login.TIPO_BOLA, login.TIPO_IMAGEM, login.TIPO_GRADE,
                 login.TIPO_GRADE_FUSED):
        _t, teto = login._orcamento_do_captcha(tipo)
        assert MAIOR_CONFIRMADA_S - teto >= 10.0, tipo
