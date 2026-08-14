"""Representacao de perfil — CLICAR EM "Representar" NAO E SUCESSO.

Numa execucao real o portal apresentou um SEGUNDO captcha logo apos o clique. O
perfil nao trocou, o codigo registrou "Representacao enviada", seguiu adiante,
nao capturou token e a API respondeu 401 — a planilha saiu so com cabecalhos.

A pos-condicao vem do DOM observado do portal em tres estados. Enquanto a sessao
e pessoal — inclusive DURANTE o captcha — nao existe `representacao-atual`. Ela
so aparece quando a representacao vigora, e traz dentro o documento
representado.

Documentos aqui sao SINTETICOS; o HTML real nao entra no repositorio.
"""
import re

import pytest
from fakes_portal import CNPJ_ALVO, CNPJ_ALVO_FORMATADO, CNPJ_OUTRO, Pagina, Perfil, Portal

from servicos_rf_login import login


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch):
    """Tempo virtual: `sleep` avanca o relogio que `monotonic` le.

    Sem isto a espera da pos-condicao e o deadline de 5 min viram espera REAL.
    """
    agora = {"t": 1_000.0}
    monkeypatch.setattr(login.time, "sleep",
                        lambda s=0.0: agora.__setitem__("t", agora["t"] + max(float(s or 0), 0.01)))
    monkeypatch.setattr(login.time, "monotonic",
                        lambda: agora.__setitem__("t", agora["t"] + 0.001) or agora["t"])


def _pagina(**kw):
    portal = Portal(**kw)
    return Pagina(portal), portal


# ══ M · Testes de DOM — a pos-condicao isolada ══════════════════════════════

def test_a_sessao_pessoal_nao_confirma_perfil():
    """`representacao-atual` ausente: nada foi representado."""
    pagina, _ = _pagina()
    assert login._perfil_representado(pagina, CNPJ_ALVO) is False


def test_b_durante_o_captcha_o_perfil_continua_pessoal():
    """O estado do captcha e' equivalente ao anterior — e' o ponto do defeito."""
    pagina, portal = _pagina(captcha=True)
    assert portal.perfil is None
    assert login._perfil_representado(pagina, CNPJ_ALVO) is False


def test_c_representacao_de_outro_documento_nao_confirma():
    pagina, _ = _pagina(perfil=Perfil(CNPJ_OUTRO))
    assert login._perfil_representado(pagina, CNPJ_ALVO) is False


def test_d_documento_certo_com_papel_diferente_nao_confirma():
    """O mesmo documento pode estar ativo sob outro papel."""
    pagina, _ = _pagina(perfil=Perfil(CNPJ_ALVO, papel="Responsável Legal"))
    assert login._perfil_representado(pagina, CNPJ_ALVO) is False


def test_e_documento_certo_e_procurador_confirma():
    pagina, _ = _pagina(perfil=Perfil(CNPJ_ALVO))
    assert login._perfil_representado(pagina, CNPJ_ALVO) is True


@pytest.mark.parametrize("formato", [CNPJ_ALVO_FORMATADO, f" {CNPJ_ALVO} ",
                                     CNPJ_ALVO.lstrip("0")])
def test_f_formatacao_diferente_do_mesmo_documento_confirma(formato):
    pagina, _ = _pagina(perfil=Perfil(formato))
    assert login._perfil_representado(pagina, CNPJ_ALVO) is True


@pytest.mark.parametrize("papel", ["Procurador", " PROCURADOR ", "procurador"])
def test_papel_e_comparado_de_forma_conservadora(papel):
    pagina, _ = _pagina(perfil=Perfil(CNPJ_ALVO, papel=papel))
    assert login._perfil_representado(pagina, CNPJ_ALVO) is True


def test_seletores_sao_semanticos_e_nao_do_angular():
    """`_ngcontent-*`/`_nghost-*` mudam a cada build e nao sao contrato."""
    for seletor in (login.SEL_REPRESENTACAO_ATUAL, login.SEL_DOCUMENTO_REPRESENTADO,
                    login.SEL_PAPEL_REPRESENTACAO):
        assert "_ngcontent" not in seletor and "_nghost" not in seletor
    assert login.SEL_DOCUMENTO_REPRESENTADO == "representacao-atual .ni-representacao"
    assert login.SEL_PAPEL_REPRESENTACAO == (
        "#avatar-dropdown-trigger .papel-representacao")


def test_avatar_visivel_nao_e_prova_de_representacao():
    """`_ja_logado` continua valendo para SESSAO, nunca para perfil."""
    pagina, portal = _pagina()
    assert login._ja_logado(pagina) is True      # avatar existe
    assert portal.perfil is None
    assert login._perfil_representado(pagina, CNPJ_ALVO) is False


# ══ N · Testes de fluxo ═════════════════════════════════════════════════════

def _chamador(respostas):
    """Callback falso: devolve as respostas na ordem e conta as chamadas."""
    estado = {"chamadas": 0, "restantes": []}

    def callback(*, segundos_restantes):
        estado["restantes"].append(segundos_restantes)
        i = min(estado["chamadas"], len(respostas) - 1)
        estado["chamadas"] += 1
        return respostas[i]

    callback.estado = estado
    return callback


def test_1_sem_captcha_confirma_sem_chamar_o_callback(capsys):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.representar(CNPJ_ALVO)
    cb = _chamador([login.CONTINUAR])

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=cb) is True
    assert cb.estado["chamadas"] == 0
    assert "Perfil representado confirmado" in capsys.readouterr().out


def test_2_captcha_resolvido_apos_continuar_confirma(capsys):
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()

    def resolve(*, segundos_restantes):
        portal.representar(CNPJ_ALVO)          # o humano resolveu de verdade
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=resolve) is True
    saida = capsys.readouterr().out
    assert "Validação manual necessária" in saida
    assert "Perfil representado confirmado" in saida


def test_3_continuar_cedo_demais_nao_confirma_e_reabre():
    """CONTINUAR nao e' confirmacao — quem confirma e' o portal."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()
    tentativas = {"n": 0}

    def talvez(*, segundos_restantes):
        tentativas["n"] += 1
        if tentativas["n"] >= 3:               # so na terceira ele resolve
            portal.representar(CNPJ_ALVO)
        return login.CONTINUAR

    assert login._representar_cnpj_procurador(
        pagina, CNPJ_ALVO, on_manual_challenge=talvez) is True
    assert tentativas["n"] == 3


def test_4_cancelar_interrompe_sem_confirmar():
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()
    with pytest.raises(login.RepresentacaoCancelada):
        login._representar_cnpj_procurador(
            pagina, CNPJ_ALVO, on_manual_challenge=_chamador([login.CANCELAR]))


def test_5_timeout_do_callback_interrompe():
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()
    with pytest.raises(login.RepresentacaoExpirada):
        login._representar_cnpj_procurador(
            pagina, CNPJ_ALVO, on_manual_challenge=_chamador([login.EXPIRADO]))


def test_5_deadline_total_e_monotonico():
    """Reabrir a intervencao NAO reinicia os 5 minutos."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()
    cb = _chamador([login.CONTINUAR])

    with pytest.raises(login.RepresentacaoExpirada):
        login._representar_cnpj_procurador(
            pagina, CNPJ_ALVO, on_manual_challenge=cb, prazo_intervencao_s=30.0)

    restantes = cb.estado["restantes"]
    assert restantes == sorted(restantes, reverse=True)   # so diminui
    assert restantes[0] <= 30.0


def test_6_sem_callback_pede_intervencao_em_vez_de_seguir():
    """Background: ninguem pode resolver, e seguir seria o defeito de novo."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()
    with pytest.raises(login.RepresentacaoRequerIntervencao):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO,
                                           on_manual_challenge=None)


def test_7_perfil_errado_nao_e_sucesso():
    """Representou, mas outra empresa: nao e' sucesso, e nao e' captcha."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.representar(CNPJ_OUTRO)
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO)


def test_8_sem_confirmacao_e_sem_captcha_e_erro_proprio():
    """Portal mudou ou caiu — nao pode virar "captcha" por eliminacao."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: None      # nada acontece
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO)


def test_captcha_nunca_e_resolvido_automaticamente(monkeypatch):
    """Este segundo desafio vai para intervencao humana, ponto."""
    def proibido(*_a, **_k):
        raise AssertionError("solve_hcaptcha nao pode ser chamado aqui")

    monkeypatch.setattr(login, "solve_hcaptcha", proibido)
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.exigir_captcha()
    with pytest.raises(login.RepresentacaoRequerIntervencao):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO)


def test_documento_digitado_e_o_solicitado():
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.representar(CNPJ_ALVO)
    login._representar_cnpj_procurador(pagina, CNPJ_ALVO)
    assert portal.documento_digitado == CNPJ_ALVO


# ══ O · ZERO chamada de API antes da confirmacao ════════════════════════════

@pytest.mark.parametrize(("preparar", "esperada"), [
    (lambda p: p.exigir_captcha(), login.RepresentacaoRequerIntervencao),
    (lambda p: p.representar(CNPJ_OUTRO), login.RepresentacaoNaoConfirmada),
    (lambda p: None, login.RepresentacaoNaoConfirmada),
])
def test_o_sem_confirmacao_a_funcao_nunca_devolve_sucesso(preparar, esperada):
    """A porta para token/API e' o `True`. Sem confirmacao, ele nao existe."""
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: preparar(p)
    with pytest.raises(esperada):
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO)


def test_o_falso_positivo_antigo_nao_existe_mais():
    """"Representacao enviada" + `return True` era o defeito.

    Varre as CHAMADAS de print por AST: testar o texto cru daria falso positivo
    com o proprio comentario que explica por que a frase saiu.
    """
    import ast
    import pathlib
    arvore = ast.parse(pathlib.Path(login.__file__).read_text(encoding="utf-8"))
    mensagens = [ast.unparse(n) for n in ast.walk(arvore)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "print"]
    assert not [m for m in mensagens if "enviada" in m]
    assert any("Representa" in m and "solicitada" in m for m in mensagens)


# ══ P · Logs sem documento ══════════════════════════════════════════════════

@pytest.mark.parametrize("cenario", ["sucesso", "captcha", "outro"])
def test_nenhum_documento_aparece_no_log(capsys, cenario):
    pagina, portal = _pagina()
    portal.ao_representar = {
        "sucesso": lambda p: p.representar(CNPJ_ALVO),
        "captcha": lambda p: p.exigir_captcha(),
        "outro": lambda p: p.representar(CNPJ_OUTRO),
    }[cenario]
    try:
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO)
    except RuntimeError:
        pass
    saida = capsys.readouterr().out
    for documento in (CNPJ_ALVO, CNPJ_OUTRO, CNPJ_ALVO_FORMATADO):
        assert documento not in saida
    assert not re.search(r"\d{11,14}", saida)


def test_mensagens_das_excecoes_sao_constantes():
    """Nem o solicitado nem o encontrado entram na mensagem."""
    for classe in (login.RepresentacaoNaoConfirmada, login.RepresentacaoRequerIntervencao,
                   login.RepresentacaoCancelada, login.RepresentacaoExpirada):
        assert classe.__doc__
    pagina, portal = _pagina()
    portal.ao_representar = lambda p: p.representar(CNPJ_OUTRO)
    with pytest.raises(login.RepresentacaoNaoConfirmada) as exc:
        login._representar_cnpj_procurador(pagina, CNPJ_ALVO)
    assert not re.search(r"\d{11,14}", str(exc.value))


# ══ Contrato publico ════════════════════════════════════════════════════════

def test_vocabulario_e_excecoes_estao_na_api_publica():
    """Quem injeta o callback precisa das respostas e dos desfechos sem
    alcancar o submodulo `login`."""
    import servicos_rf_login as pacote
    for nome in ("CONTINUAR", "CANCELAR", "EXPIRADO", "fazer_login",
                 "RepresentacaoNaoConfirmada", "RepresentacaoRequerIntervencao",
                 "RepresentacaoCancelada", "RepresentacaoExpirada"):
        assert nome in pacote.__all__
        assert getattr(pacote, nome) is getattr(login, nome, None) or nome == "fazer_login"


def test_respostas_sao_distintas():
    assert len({login.CONTINUAR, login.CANCELAR, login.EXPIRADO}) == 3
