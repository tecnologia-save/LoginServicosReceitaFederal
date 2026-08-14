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
    portal.ao_representar = lambda p: p.exigir_captcha(automatizavel=True)
    cb = _chamador([login.CONTINUAR])

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=cb) is True
    assert solver["chamadas"] == 1
    assert cb.estado["chamadas"] == 0            # NENHUMA janela
    assert "Perfil representado confirmado" in capsys.readouterr().out


# ══ Cenario C · captcha que precisa de humano ═══════════════════════════════

def test_c_captcha_nao_automatizavel_cai_para_o_humano(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(automatizavel=False)

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
    portal.ao_representar = lambda p: p.exigir_captcha()

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
    portal.ao_representar = lambda p: p.exigir_captcha()

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
    portal.ao_representar = lambda p: p.exigir_captcha()

    with pytest.raises(login.FalhaDoResolvedorCaptcha):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=cb)
    assert cb.estado["chamadas"] == 0


def test_e_erro_tecnico_nao_carrega_a_mensagem_original(solver):
    solver["efeito"] = RuntimeError("SEGREDO_TESTE_chave_no_ambiente")
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()

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
    portal.ao_representar = lambda p: p.exigir_captcha()

    with pytest.raises(login.RepresentacaoNaoConfirmada):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=cb)
    assert cb.estado["chamadas"] == 0            # nao ha o que um humano resolva


def test_g_continuar_cedo_demais_depois_do_automatico(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()
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
    portal.ao_representar = lambda p: p.exigir_captcha(automatizavel=True)

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=None) is True
    assert solver["chamadas"] == 1


def test_i_background_com_captcha_manual_pede_intervencao(solver):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha(automatizavel=False)

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
