"""Recusa do portal na representacao — `.mensagemErro` vira nova tentativa.

Execucao real: representacao pedida as 16:17:16, desafio grade as 16:17:27,
dois modelos com ReadTimeout, o terceiro respondeu, captcha concluido perto das
16:18:45. O portal entao exibiu

    <span class="mensagemErro">
        Aguarde, pelo menos, 30 segundos para representar outra pessoa.
    </span>

e a execucao terminou em `RepresentacaoNaoConfirmada`.

Duas coisas faltavam:

  1. `.mensagemErro` nao influenciava nada — as tres tentativas existentes
     cobriam so excecoes durante o PREENCHIMENTO, e paravam no primeiro envio
     bem-sucedido. Recusa depois do clique nao gerava tentativa nova;
  2. a espera pos-clique era exclusiva do perfil, 8 s, e so depois se procurava
     captcha. Uma recusa explicita levava a espera inteira para virar "nao
     confirmou".

Agora UMA tentativa e a operacao inteira, o desfecho e observado
concorrentemente, e `.mensagemErro` provoca nova tentativa apos o intervalo que
o proprio portal pediu.

Dados sinteticos. Nenhum CNPJ, empresa ou documento de cliente.
"""
import pytest

from servicos_rf_login import login

CNPJ = "00011122000133"
OUTRO = "99988877000166"
TEXTO_SENSIVEL = "Aguarde, pelo menos, 30 segundos para representar SEGREDO_TESTE."


class _Loc:
    def __init__(self, texto=None, visivel=True):
        self._texto = texto
        self._visivel = visivel

    @property
    def first(self):
        return self

    def count(self):
        return 0 if self._texto is None else 1

    def is_visible(self):
        return self._visivel

    def inner_text(self):
        if self._texto is None:
            raise RuntimeError("sem elemento")
        return self._texto


class _Teclado:
    def press(self, _tecla):
        pass


class Portal:
    """Portal da representacao como ESTADO.

    `roteiro` e a lista de desfechos por tentativa de envio, na ordem:
    "erro", "confirma", "captcha_erro", "captcha_confirma", "nada".
    """

    def __init__(self, roteiro, *, documento=CNPJ, papel="Procurador",
                 texto_erro=TEXTO_SENSIVEL, tipo=login.TIPO_GRADE):
        self.roteiro = list(roteiro)
        self.documento = documento
        self.papel = papel
        self.texto_erro = texto_erro
        self.tipo = tipo

        self.estado = "inicial"
        self.envios = 0
        self.solves = 0
        self.solves_kwargs = []
        self.keyboard = _Teclado()

    # ── o codigo real chama isto ─────────────────────────────────────────────

    def locator(self, seletor):
        if seletor == login.SEL_MENSAGEM_ERRO_REPRESENTACAO:
            return _Loc(self.texto_erro if self.estado == "erro" else None)
        if seletor == login.SEL_DOCUMENTO_REPRESENTADO:
            return _Loc(self.documento if self.estado == "confirmada" else None)
        if seletor == login.SEL_PAPEL_REPRESENTACAO:
            return _Loc(self.papel if self.estado == "confirmada" else None)
        return _Loc(None)

    # ── ganchos substituidos ────────────────────────────────────────────────

    def enviar(self):
        self.envios += 1
        passo = self.roteiro[min(self.envios - 1, len(self.roteiro) - 1)]
        self.estado = {"erro": "erro", "confirma": "confirmada",
                       "captcha_erro": "captcha", "captcha_confirma": "captcha",
                       "nada": "inicial"}[passo]
        self._apos_captcha = {"captcha_erro": "erro",
                              "captcha_confirma": "confirmada"}.get(passo)

    def ha_captcha(self):
        return self.estado == "captcha"

    def resolver(self, **kwargs):
        self.solves += 1
        self.solves_kwargs.append(kwargs)
        self.estado = self._apos_captcha or "inicial"
        return True


@pytest.fixture
def portal(monkeypatch):
    """Instala o portal falso; o relogio das esperas e virtual."""
    agora = [1000.0]
    monkeypatch.setattr(login.time, "monotonic", lambda: agora[0])
    monkeypatch.setattr(login.time, "sleep",
                        lambda s: agora.__setitem__(0, agora[0] + s))

    def instalar(p):
        monkeypatch.setattr(login, "_preencher_formulario_representacao",
                            lambda _pg, _cnpj: p.enviar())
        monkeypatch.setattr(login, "captcha_presente", lambda _pg: p.ha_captcha())
        monkeypatch.setattr(login, "detectar_tipo_captcha",
                            lambda _pg: p.tipo if p.ha_captcha() else login.TIPO_NENHUM)
        monkeypatch.setattr(login, "solve_hcaptcha",
                            lambda _pg, **kw: p.resolver(**kw))
        p.relogio = agora
        return p

    return instalar


def representar(p, **kwargs):
    return login._representar_cnpj_procurador(p, CNPJ, **kwargs)


# ══ 1 · `.mensagemErro` vira nova tentativa ═════════════════════════════════

def test_erro_do_portal_gera_nova_tentativa_e_confirma(portal):
    """RED 1: recusa na primeira, sucesso na segunda."""
    p = portal(Portal(["erro", "confirma"]))
    assert representar(p) is True
    assert p.envios == 2


def test_a_segunda_tentativa_respeita_o_intervalo_pedido(portal):
    """Nada de retry imediato: o portal pediu 30 s e o codigo cumpre 31."""
    p = portal(Portal(["erro", "confirma"]))
    inicio = p.relogio[0]
    representar(p)
    assert p.relogio[0] - inicio >= login.COOLDOWN_ERRO_REPRESENTACAO_S


def test_erro_depois_do_captcha_tambem_gera_nova_tentativa(portal):
    """RED 2, o caso EXATO da run: resolve o captcha e o portal recusa."""
    p = portal(Portal(["captcha_erro", "confirma"]))
    assert representar(p) is True
    assert p.solves == 1
    assert p.envios == 2


def test_erro_persistente_termina_em_excecao_tipada(portal):
    """RED 3: tres recusas. Nao devolve True, nao chama humano, nao insiste."""
    p = portal(Portal(["erro"]))
    with pytest.raises(login.RepresentacaoRejeitadaPeloPortal):
        representar(p)
    assert p.envios == login.MAX_TENTATIVAS_REPRESENTACAO


def test_texto_diferente_mesma_classe_tem_o_mesmo_tratamento(portal):
    """RED 4: o contrato e a classe, nao a frase."""
    p = portal(Portal(["erro", "confirma"], texto_erro="Outra coisa qualquer."))
    assert representar(p) is True
    assert p.envios == 2


def test_o_texto_da_mensagem_nao_entra_no_log(portal, capsys):
    """RED 5: a frase pode mudar e pode passar a carregar dado de terceiro."""
    p = portal(Portal(["erro"]))
    with pytest.raises(login.RepresentacaoRejeitadaPeloPortal):
        representar(p)
    saida = capsys.readouterr()
    assert "SEGREDO_TESTE" not in saida.out
    assert "SEGREDO_TESTE" not in saida.err


def test_nem_o_documento_entra_no_log(portal, capsys):
    p = portal(Portal(["erro", "confirma"]))
    representar(p)
    saida = capsys.readouterr()
    assert CNPJ not in saida.out
    assert CNPJ not in saida.err


# ══ 2 · Prioridade dos desfechos ════════════════════════════════════════════

def test_perfil_confirmado_vence_mensagem_residual(portal):
    """RED 6: com os dois na tela, quem decide e a pos-condicao."""
    p = portal(Portal(["confirma"]))
    p.locator_original = p.locator
    p.locator = lambda sel: (_Loc("mensagem antiga")
                             if sel == login.SEL_MENSAGEM_ERRO_REPRESENTACAO
                             else p.locator_original(sel))
    assert representar(p) is True
    assert p.envios == 1


def test_sem_erro_e_sem_captcha_continua_nao_confirmada(portal):
    """RED 7: o desfecho de sempre, preservado."""
    p = portal(Portal(["nada"]))
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)
    assert p.envios == 1        # nao ha o que tentar de novo


def test_documento_diferente_nao_confirma(portal):
    """A pos-condicao continua comparando o documento, sem logar nenhum."""
    p = portal(Portal(["confirma"]), )
    p.documento = OUTRO
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)


def test_papel_diferente_nao_confirma(portal):
    p = portal(Portal(["confirma"]))
    p.papel = "Responsável"
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)


# ══ 3 · Captcha: allowlist, orcamento e caminho manual ══════════════════════

def test_a_representacao_usa_orcamento_curto_no_solver(portal):
    """O captcha da representacao nao pode custar um minuto."""
    p = portal(Portal(["captcha_confirma"]))
    assert representar(p) is True
    assert p.solves_kwargs == [{
        "gemini_timeout_ms": login.TIMEOUT_GEMINI_REPRESENTACAO_MS,
        "deadline_s": login.DEADLINE_CAPTCHA_REPRESENTACAO_S,
    }]


def test_captcha_fora_da_allowlist_vai_para_o_humano(portal):
    """RED 8: `cartao_animal` continua sendo caso de intervencao."""
    p = portal(Portal(["captcha_confirma"], tipo="cartao_animal"))
    chamadas = []

    def manual(*, segundos_restantes):
        chamadas.append(segundos_restantes)
        p.estado = "confirmada"
        return login.CONTINUAR

    assert representar(p, on_manual_challenge=manual) is True
    assert p.solves == 0                 # nao tentou resolver automaticamente
    assert len(chamadas) == 1


def test_sem_janela_manual_o_desfecho_e_tipado(portal):
    """RED 9: background continua fail-safe — nada de esperar por ninguem."""
    p = portal(Portal(["captcha_confirma"], tipo="cartao_animal"))
    with pytest.raises(login.RepresentacaoRequerIntervencao):
        representar(p, on_manual_challenge=None)


def test_cancelar_a_janela_manual_e_tipado(portal):
    p = portal(Portal(["captcha_confirma"], tipo="cartao_animal"))
    with pytest.raises(login.RepresentacaoCancelada):
        representar(p, on_manual_challenge=lambda **_k: login.CANCELAR)


def test_prazo_manual_esgotado_e_tipado(portal):
    p = portal(Portal(["captcha_confirma"], tipo="cartao_animal"))
    with pytest.raises(login.RepresentacaoExpirada):
        representar(p, on_manual_challenge=lambda **_k: login.EXPIRADO,
                    prazo_intervencao_s=60.0)


def test_falha_tecnica_do_resolvedor_nao_vira_trabalho_humano(portal):
    """Chave ausente, dependencia morta: erro tecnico, nao caso de humano."""
    p = portal(Portal(["captcha_confirma"]))

    def explodir(_pg, **_kw):
        raise RuntimeError("GEMINI_API_KEY nao configurada")

    p.resolver = explodir
    import servicos_rf_login.login as mod
    mod.solve_hcaptcha = lambda _pg, **kw: explodir(_pg, **kw)
    with pytest.raises(login.FalhaDoResolvedorCaptcha):
        representar(p)


# ══ 4 · Uma tentativa e a operacao inteira ══════════════════════════════════

def test_falha_ao_enviar_o_formulario_ainda_tem_tres_chances(portal):
    """O retry do formulario nao se perdeu — mudou de dono."""
    p = portal(Portal(["confirma"]))
    falhas = [0]
    original = p.enviar

    def enviar():
        falhas[0] += 1
        if falhas[0] < 3:
            raise RuntimeError("avatar nao apareceu")
        return original()

    p.enviar = enviar
    assert representar(p) is True
    assert falhas[0] == 3


def test_nao_ha_tres_tentativas_dentro_de_tres_tentativas(portal):
    """Um unico conceito de tentativa: no maximo MAX envios."""
    p = portal(Portal(["erro"]))
    with pytest.raises(login.RepresentacaoRejeitadaPeloPortal):
        representar(p)
    assert p.envios == 3


def test_o_perfil_confirmado_durante_o_intervalo_encerra_na_hora(portal):
    """Se o portal confirmar sozinho durante a espera, nao ha o que retentar."""
    p = portal(Portal(["erro"]))
    original = p.locator

    def locator(sel):
        # Depois do primeiro erro, o perfil aparece durante o cooldown.
        if p.envios == 1 and p.relogio[0] > 1005.0:
            p.estado = "confirmada"
        return original(sel)

    p.locator = locator
    assert representar(p) is True
    assert p.envios == 1
