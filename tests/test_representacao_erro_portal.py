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
    """Locator com N elementos, cada um com sua propria visibilidade.

    `.mensagemErro` pode ter mais de um match: o portal mantem spans ocultos, e
    o contrato e "QUALQUER visivel".
    """

    def __init__(self, itens=(), idx=None):
        # itens: lista de (texto, visivel)
        self._itens = list(itens)
        self._idx = idx

    @property
    def first(self):
        return self.nth(0)

    def nth(self, i):
        return _Loc(self._itens, idx=i)

    def count(self):
        return len(self._itens)

    def _atual(self):
        i = 0 if self._idx is None else self._idx
        return self._itens[i] if i < len(self._itens) else (None, False)

    def is_visible(self):
        return bool(self._atual()[1])

    def inner_text(self):
        texto = self._atual()[0]
        if texto is None:
            raise RuntimeError("sem elemento")
        return texto


def _um(texto, visivel=True):
    return _Loc([(texto, visivel)]) if texto is not None else _Loc()


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
        self.mensagens = [(texto_erro, True)]
        self.tipo = tipo

        self.estado = "inicial"
        self.envios = 0
        self.solves = 0
        self.solves_kwargs = []
        self.aberturas_de_desafio = 0
        self.abre_desafio = True
        self.keyboard = _Teclado()

    # ── o codigo real chama isto ─────────────────────────────────────────────

    def locator(self, seletor):
        if seletor == login.SEL_MENSAGEM_ERRO_REPRESENTACAO:
            return _Loc(self.mensagens if self.estado == "erro" else [])
        if seletor == login.SEL_DOCUMENTO_REPRESENTADO:
            return _um(self.documento if self.estado == "confirmada" else None)
        if seletor == login.SEL_PAPEL_REPRESENTACAO:
            return _um(self.papel if self.estado == "confirmada" else None)
        return _Loc()

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

    def abrir(self):
        """`abrir_desafio`: sai do widget fechado para o desafio aberto.

        `abre_desafio=False` modela o widget que nao abre — o tipo continua
        indeterminado e o fluxo nao pode ficar girando nele.
        """
        self.aberturas_de_desafio += 1
        return self.abre_desafio

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
        monkeypatch.setattr(login, "abrir_desafio", lambda _pg, **_kw: p.abrir())
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
    p.locator = lambda sel: (_um("mensagem antiga")
                             if sel == login.SEL_MENSAGEM_ERRO_REPRESENTACAO
                             else p.locator_original(sel))
    assert representar(p) is True
    assert p.envios == 1


def test_tres_tentativas_sem_desfecho_terminam_nao_confirmada(portal):
    """PREMISSA INVALIDADA PELO QA.

    Este teste dizia `envios == 1`: "sem erro e sem captcha" era falha
    definitiva na primeira tentativa. A run de 16:56 mostrou exatamente esse
    caminho — login concluido, representacao enviada, nenhum sinal observavel,
    e o fluxo desistindo sem nunca ter tentado de novo.

    O desfecho final continua o mesmo; o que mudou e quantas vezes se tenta
    antes dele.
    """
    p = portal(Portal(["nada"]))
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)
    assert p.envios == login.MAX_TENTATIVAS_REPRESENTACAO


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


# ══ 5 · A run de 16:56 — indeterminado nao pode ser terminal ════════════════
#
# 16:56:30 login concluido; 16:56:36 representacao solicitada; 16:56:41 falha.
# Nao houve `Desafio automatizavel detectado`, nem `O portal recusou`, nem
# perfil confirmado. A representacao caiu no estado sem desfecho observavel — e
# esse estado era TERMINAL, com zero retry.
#
# Duas coisas se somavam: a janela de 8 s nasceu como latencia do PERFIL e
# passou a governar uma maquina de estados maior, e "nada visto" era tratado
# como "nada ha".

def test_sem_desfecho_na_primeira_janela_ainda_tem_segunda_tentativa(portal):
    """RED da run: nada aparece, e o fluxo tenta de novo em vez de desistir."""
    p = portal(Portal(["nada", "confirma"]))
    assert representar(p) is True
    assert p.envios == 2


def test_a_segunda_tentativa_respeita_o_intervalo_desde_o_ENVIO(portal):
    """Protecao contra dupla submissao: a solicitacao anterior pode estar em
    processamento. O intervalo conta do envio, nao do fim da observacao."""
    p = portal(Portal(["nada", "confirma"]))
    inicio = p.relogio[0]
    representar(p)
    assert p.relogio[0] - inicio >= login.COOLDOWN_ERRO_REPRESENTACAO_S


def test_sem_desfecho_nao_diz_que_o_portal_recusou(portal, capsys):
    """Nao vimos `.mensagemErro`: afirmar recusa seria inventar evidencia."""
    p = portal(Portal(["nada", "confirma"]))
    representar(p)
    saida = capsys.readouterr().out
    assert "Nenhum desfecho observável dentro da janela." in saida
    assert "Portal recusou a tentativa" not in saida


def test_tres_sem_desfecho_nao_viram_rejeicao_do_portal(portal):
    """Sem `.mensagemErro` nenhuma, o desfecho tipado e o de nao confirmada."""
    p = portal(Portal(["nada"]))
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)


def test_a_janela_de_desfecho_cobre_o_captcha_de_onze_segundos(portal):
    """O RED mais importante: tempo JA OBSERVADO em producao.

    Numa run anterior a representacao foi pedida as 16:17:16 e o desafio so foi
    detectado as 16:17:27 — 11 s depois. Com a janela de 8 s daquele desenho,
    isso viraria "sem desfecho".
    """
    assert login.ESPERA_DESFECHO_REPRESENTACAO_S >= 11.0

    p = portal(Portal(["captcha_confirma"]))
    inicio = p.relogio[0]
    p.estado = "inicial"
    original = p.ha_captcha

    def ha_captcha():
        # O iframe so aparece 11 s depois do clique.
        if p.relogio[0] - inicio >= 11.0 and p.estado == "inicial":
            p.estado = "captcha"
        return original()

    p.ha_captcha = ha_captcha
    assert representar(p) is True
    assert p.envios == 1
    assert p.solves == 1


def test_a_janela_de_desfecho_e_uma_constante_propria(portal):
    """Responsabilidades distintas: latencia do perfil x tempo para QUALQUER
    desfecho. Amarrar as duas na mesma constante foi o que estreitou a janela."""
    assert (login.ESPERA_DESFECHO_REPRESENTACAO_S
            != login.ESPERA_POS_CONDICAO_S)


# ══ 6 · `.mensagemErro`: QUALQUER visivel, nao a primeira ═══════════════════

def test_primeira_mensagem_oculta_e_segunda_visivel_conta_como_erro(portal):
    """RED do `.first`: o portal mantem span oculto na frente no DOM."""
    p = portal(Portal(["erro", "confirma"]))
    p.mensagens = [("oculta", False), (TEXTO_SENSIVEL, True)]
    assert representar(p) is True
    assert p.envios == 2


def test_todas_as_mensagens_ocultas_nao_sao_erro(portal):
    """Existir no DOM nao basta — o contrato e VISIVEL."""
    p = portal(Portal(["erro"]))
    p.mensagens = [("oculta", False), ("tambem oculta", False)]
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)          # sem erro VISIVEL, o caminho e o de sem-resposta


def test_o_detector_le_todos_os_elementos():
    """Unitario do detector, sem a maquina de estados em volta."""
    class _P:
        def __init__(self, itens):
            self.itens = itens

        def locator(self, _sel):
            return _Loc(self.itens)

    assert login._erro_representacao_visivel(_P([("a", False), ("b", True)])) is True
    assert login._erro_representacao_visivel(_P([("a", False)])) is False
    assert login._erro_representacao_visivel(_P([])) is False
    assert login._erro_representacao_visivel(_P([("a", True)])) is True


def test_o_detector_nao_le_o_texto():
    """Gate: `inner_text` no detector abriria caminho para o texto no log."""
    import inspect
    fonte = inspect.getsource(login._erro_representacao_visivel)
    corpo = fonte[fonte.rindex('"""') + 3:]      # so o codigo, sem o docstring
    assert "inner_text" not in corpo
    assert ".first" not in corpo


# ══ 7 · Perfil de OUTRO documento nao vira retry cego ═══════════════════════

def test_perfil_de_outro_documento_encerra_sem_retry(portal):
    """Observacao EXPLICITA de representacao errada. Repetir nao conserta."""
    p = portal(Portal(["confirma"], documento=OUTRO))
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)
    assert p.envios == 1


def test_papel_errado_tambem_encerra_sem_retry(portal):
    p = portal(Portal(["confirma"], papel="Responsável"))
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)
    assert p.envios == 1


def test_o_documento_encontrado_nao_entra_no_log(portal, capsys):
    p = portal(Portal(["confirma"], documento=OUTRO))
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)
    saida = capsys.readouterr()
    assert OUTRO not in saida.out
    assert OUTRO not in saida.err


def test_estado_do_perfil_distingue_os_tres_casos():
    """Unitario: ausente, outro e correto sao coisas diferentes."""
    class _P:
        def __init__(self, doc, papel):
            self.doc, self.papel = doc, papel

        def locator(self, sel):
            if sel == login.SEL_DOCUMENTO_REPRESENTADO:
                return _um(self.doc)
            if sel == login.SEL_PAPEL_REPRESENTACAO:
                return _um(self.papel)
            return _Loc()

    assert login._estado_do_perfil(_P(None, None), CNPJ) == login.PERFIL_AUSENTE
    assert login._estado_do_perfil(_P(OUTRO, "Procurador"), CNPJ) == login.PERFIL_OUTRO
    assert login._estado_do_perfil(_P(CNPJ, "Responsável"), CNPJ) == login.PERFIL_OUTRO
    assert login._estado_do_perfil(_P(CNPJ, "Procurador"), CNPJ) == login.PERFIL_CORRETO


# ══ 8 · Observabilidade ═════════════════════════════════════════════════════

def test_cada_desfecho_observado_aparece_no_log(portal, capsys):
    p = portal(Portal(["captcha_confirma"]))
    representar(p)
    saida = capsys.readouterr().out
    assert "Aguardando desfecho da representação..." in saida
    assert "Desfecho observado | tipo=captcha" in saida
    assert "Desfecho observado | tipo=confirmada" in saida


def test_presenca_de_captcha_que_nao_se_confirma_deixa_de_ser_muda(portal, capsys):
    """Um dos dois caminhos suspeitos da run era SILENCIOSO: presenca detectada,
    classificacao devolvendo `nenhum`, e nenhuma linha no log."""
    p = portal(Portal(["captcha_confirma"]))
    p.tipo = login.TIPO_NENHUM
    p.abre_desafio = False
    with pytest.raises(login.FalhaDoResolvedorCaptcha):
        representar(p)
    assert "Presença de captcha não se confirmou na classificação." in \
        capsys.readouterr().out


# ══ 9 · O intervalo continua observando a tentativa em curso ════════════════
#
# O intervalo existe porque a solicitacao anterior PODE AINDA ESTAR EM
# PROCESSAMENTO. Esperar para nao duplicar e, ao mesmo tempo, ignorar a resposta
# que chega durante a espera contraria o proprio motivo — e era o que acontecia:
# entre t=20 e t=31 so o perfil correto era percebido.

def test_captcha_que_surge_durante_o_intervalo_e_tratado_sem_novo_envio(portal):
    """RED: janela de 20 s vazia, captcha em t=23, e NENHUMA segunda submissao."""
    p = portal(Portal(["captcha_confirma"]))
    inicio = p.relogio[0]
    p.estado = "inicial"

    def ha_captcha():
        if p.relogio[0] - inicio >= 23.0 and p.estado == "inicial":
            p.estado = "captcha"
        return p.estado == "captcha"

    p.ha_captcha = ha_captcha
    assert representar(p) is True
    assert p.envios == 1          # a resposta era da tentativa ORIGINAL
    assert p.solves == 1


def test_perfil_que_confirma_durante_o_intervalo_encerra_sem_novo_envio(portal):
    p = portal(Portal(["nada"]))
    inicio = p.relogio[0]
    original = p.locator

    def locator(sel):
        if p.relogio[0] - inicio >= 24.0:
            p.estado = "confirmada"
        return original(sel)

    p.locator = locator
    assert representar(p) is True
    assert p.envios == 1


def test_erro_que_aparece_durante_o_intervalo_conta_como_recusa(portal):
    """Recusa tardia e recusa: entra na conta e respeita o intervalo."""
    p = portal(Portal(["nada", "confirma"]))
    inicio = p.relogio[0]
    original = p.locator

    def locator(sel):
        if p.relogio[0] - inicio >= 22.0 and p.estado == "inicial":
            p.estado = "erro"
        return original(sel)

    p.locator = locator
    assert representar(p) is True
    assert p.envios == 2


def test_o_intervalo_e_cumprido_mesmo_com_a_mensagem_ainda_na_tela(portal):
    """A mensagem de recusa PERMANECE visivel.

    Sem `erro_ja_visto`, a espera terminaria no primeiro instante relatando de
    novo o erro que a motivou — e o intervalo nunca seria cumprido.
    """
    p = portal(Portal(["erro", "confirma"]))
    inicio = p.relogio[0]
    representar(p)
    assert p.relogio[0] - inicio >= login.COOLDOWN_ERRO_REPRESENTACAO_S


def test_o_intervalo_ja_vencido_nao_faz_esperar_de_novo(portal):
    """Depois de 20 s de janela + 11 s de intervalo, nada de mais 31."""
    p = portal(Portal(["nada", "confirma"]))
    inicio = p.relogio[0]
    representar(p)
    assert p.relogio[0] - inicio < 2 * login.COOLDOWN_ERRO_REPRESENTACAO_S


# ══ 10 · Checkbox presente nao e desafio aberto ═════════════════════════════
#
# `detectar_tipo_captcha` so enxerga desafio ABERTO. Com o widget "Sou humano"
# ainda fechado nao ha tipo a classificar, e o fluxo girava entre "ha captcha" e
# "tipo nenhum" ate esgotar as tentativas.

def test_checkbox_fechado_abre_o_desafio_antes_de_classificar(portal, monkeypatch):
    """RED: widget visivel, desafio ainda fechado. Abrir e o que faltava."""
    p = portal(Portal(["captcha_confirma"]))
    tipos = iter([login.TIPO_NENHUM, login.TIPO_GRADE])
    monkeypatch.setattr(login, "detectar_tipo_captcha",
                        lambda _pg: next(tipos, login.TIPO_GRADE))

    assert representar(p) is True
    assert p.aberturas_de_desafio == 1
    assert p.solves == 1
    assert p.envios == 1


def test_checkbox_que_abre_em_cartao_animal_vai_para_o_humano(portal, monkeypatch):
    """A allowlist decide DEPOIS de abrir — abrir nao autoriza resolver."""
    p = portal(Portal(["captcha_confirma"]))
    tipos = iter([login.TIPO_NENHUM, "cartao_animal"])
    monkeypatch.setattr(login, "detectar_tipo_captcha",
                        lambda _pg: next(tipos, "cartao_animal"))

    chamadas = []

    def manual(*, segundos_restantes):
        chamadas.append(segundos_restantes)
        p.estado = "confirmada"
        return login.CONTINUAR

    assert representar(p, on_manual_challenge=manual) is True
    assert p.aberturas_de_desafio == 1
    assert p.solves == 0                 # ZERO Gemini automatico
    assert len(chamadas) == 1


def test_desafio_que_nao_abre_nao_fica_girando(portal, monkeypatch):
    """Widget que nao vira desafio: sem laco infinito e sem reenvio.

    O captcha continua ativo, entao o desfecho e falha tecnica do tratamento.
    """
    p = portal(Portal(["captcha_confirma"]))
    p.abre_desafio = False
    monkeypatch.setattr(login, "detectar_tipo_captcha",
                        lambda _pg: login.TIPO_NENHUM)

    with pytest.raises(login.FalhaDoResolvedorCaptcha):
        representar(p)
    assert p.solves == 0
    assert p.envios == 1


def test_falha_ao_abrir_o_desafio_e_erro_tecnico(portal, monkeypatch):
    """Abrir e do resolvedor: falhar ali nao e trabalho para humano."""
    p = portal(Portal(["captcha_confirma"]))
    monkeypatch.setattr(login, "detectar_tipo_captcha",
                        lambda _pg: login.TIPO_NENHUM)

    def explodir(_pg, **_kw):
        raise RuntimeError("pagina morta")

    monkeypatch.setattr(login, "abrir_desafio", explodir)
    with pytest.raises(login.FalhaDoResolvedorCaptcha):
        representar(p)


def test_o_tipo_aberto_aparece_no_log(portal, capsys):
    p = portal(Portal(["captcha_confirma"]))
    representar(p)
    saida = capsys.readouterr().out
    assert "Desafio aberto | tipo=" in saida


# ══ 11 · Todo DESFECHO_* tem governo — inclusive o tardio ═══════════════════
#
# `_observar_intervalo` passou a observar corretamente, mas quem chamava so
# usava CONFIRMADA e PERFIL_OUTRO: um erro do portal ou um captcha descobertos
# durante o intervalo eram observados e DESCARTADOS. Observar nao basta — o
# desfecho observado precisa governar a execucao.
#
# O governo virou um laco unico: enquanto nao houve nova submissao, tudo o que
# se observa pertence a tentativa em curso.

def _apos_o_solver(p, quando, estado_novo):
    """Depois do PRIMEIRO solver a pagina fica sem resposta por `quando` segundos.

    Uma vez so: o que vem depois disso e a reacao do portal, e o duble nao pode
    ficar reescrevendo-a a cada tratamento.
    """
    inicio = [None]
    aplicado = [False]
    resolver_real = p.resolver

    def resolver(**kw):
        resolver_real(**kw)
        if aplicado[0]:
            return True
        aplicado[0] = True
        p.estado = "inicial"          # nem perfil, nem erro, nem captcha
        inicio[0] = p.relogio[0]
        return True

    original = p.locator

    def locator(sel):
        if inicio[0] is not None and p.relogio[0] - inicio[0] >= quando:
            p.estado = estado_novo
            inicio[0] = None          # uma vez so: a proxima tentativa e outra
        return original(sel)

    p.resolver, p.locator = resolver, locator


def test_erro_do_portal_descoberto_depois_do_solver_e_contabilizado(portal, capsys):
    """RED A: solver roda, nada aparece, `.mensagemErro` surge no intervalo.

    Antes esse ERRO_PORTAL era observado e jogado fora.
    """
    p = portal(Portal(["captcha_erro", "confirma"]))
    _apos_o_solver(p, quando=23.0, estado_novo="erro")

    assert representar(p) is True
    assert p.solves == 1
    assert p.envios == 2                       # houve retry, depois do intervalo
    assert "Desfecho observado | tipo=erro_portal" in capsys.readouterr().out


def test_erro_tardio_respeita_o_intervalo_desde_o_envio(portal):
    p = portal(Portal(["captcha_erro", "confirma"]))
    _apos_o_solver(p, quando=23.0, estado_novo="erro")
    inicio = p.relogio[0]
    representar(p)
    assert p.relogio[0] - inicio >= login.COOLDOWN_ERRO_REPRESENTACAO_S


def test_tres_recusas_contando_a_tardia_terminam_rejeitadas(portal):
    """A recusa tardia entra na CONTA, e nao so no log."""
    p = portal(Portal(["captcha_erro", "erro", "erro"]))
    _apos_o_solver(p, quando=23.0, estado_novo="erro")
    with pytest.raises(login.RepresentacaoRejeitadaPeloPortal):
        representar(p)


def test_captcha_descoberto_no_intervalo_e_tratado_sem_novo_envio(portal):
    """RED B: o solver roda, a janela de 20 s passa em branco, e o captcha
    reaparece DURANTE o intervalo — ainda desta tentativa.

    Nada de restaurar formulario nem clicar Representar antes de governar esse
    estado. (Captcha que reaparece DENTRO da janela e outro caso: ali o
    automatico ja nao bastou, e o caminho e a validacao manual.)
    """
    p = portal(Portal(["captcha_confirma"]))
    _apos_o_solver(p, quando=23.0, estado_novo="captcha")
    p._apos_captcha = "confirmada"

    assert representar(p) is True
    assert p.envios == 1                       # NENHUMA segunda submissao
    assert p.solves == 2                       # o segundo tratamento aconteceu


def test_captcha_persistente_termina_em_falha_tecnica_sem_reenviar(portal, monkeypatch):
    """CONTRATO CORRIGIDO.

    A versao anterior esperava `envios == MAX_TENTATIVAS_REPRESENTACAO`: com o
    captcha ainda ativo, o fluxo restaurava o formulario e clicava Representar
    de novo. Isso e submeter as cegas por cima de um captcha que pertence a
    tentativa em curso.

    Evidencia explicita de captcha ativo que nao avancou e falha TECNICA do
    tratamento: nem `sem resposta`, nem `nao confirmada` generica, nem retry.
    """
    p = portal(Portal(["captcha_confirma"]))
    monkeypatch.setattr(login, "detectar_tipo_captcha",
                        lambda _pg: login.TIPO_NENHUM)
    p.abre_desafio = False

    with pytest.raises(login.FalhaDoResolvedorCaptcha):
        representar(p)
    assert p.envios == 1                                    # ZERO reenvio
    assert p.aberturas_de_desafio == login.MAX_TRATAMENTOS_CAPTCHA


def test_nenhuma_restauracao_de_formulario_com_captcha_ativo(portal, monkeypatch):
    """GATE: o teto nao pode abrir caminho para uma nova submissao."""
    p = portal(Portal(["captcha_confirma"]))
    monkeypatch.setattr(login, "detectar_tipo_captcha",
                        lambda _pg: login.TIPO_NENHUM)
    p.abre_desafio = False

    restauracoes = []
    monkeypatch.setattr(login, "_restaurar_formulario",
                        lambda _pg: restauracoes.append(1))

    with pytest.raises(login.FalhaDoResolvedorCaptcha):
        representar(p)
    assert restauracoes == []
    assert p.envios == 1


def test_a_falha_do_captcha_persistente_nao_carrega_dado_externo(portal, monkeypatch, capsys):
    """Mensagem constante: nem documento, nem texto do portal, nem HTML."""
    p = portal(Portal(["captcha_confirma"]))
    monkeypatch.setattr(login, "detectar_tipo_captcha",
                        lambda _pg: login.TIPO_NENHUM)
    p.abre_desafio = False

    with pytest.raises(login.FalhaDoResolvedorCaptcha) as erro:
        representar(p)
    mensagem = str(erro.value)
    assert CNPJ not in mensagem
    assert "SEGREDO_TESTE" not in mensagem
    saida = capsys.readouterr()
    assert CNPJ not in saida.out and "SEGREDO_TESTE" not in saida.out


def test_grade_que_nao_some_continua_indo_para_o_humano(portal, monkeypatch):
    """NAO confundir com o teto: um desafio CLASSIFICADO que o automatico nao
    resolveu e caso de validacao manual, como sempre foi.

    O teto so vale quando o captcha nao consegue nem ser avancado.
    """
    p = portal(Portal(["captcha_confirma"]))
    monkeypatch.setattr(login, "detectar_tipo_captcha", lambda _pg: login.TIPO_GRADE)

    def resolver(**kw):
        p.solves += 1
        p.solves_kwargs.append(kw)
        p.estado = "captcha"          # o desafio nunca sai da tela
        return False

    p.resolver = resolver
    with pytest.raises(login.RepresentacaoRequerIntervencao):
        representar(p)
    assert p.solves == 1
    assert p.envios == 1


def test_perfil_confirmado_no_intervalo_depois_do_solver(portal):
    """RED E, na variante pos-solver."""
    p = portal(Portal(["captcha_confirma"]))
    _apos_o_solver(p, quando=23.0, estado_novo="confirmada")
    assert representar(p) is True
    assert p.envios == 1


def test_perfil_outro_no_intervalo_depois_do_solver(portal):
    """RED F: falha sem novo envio."""
    p = portal(Portal(["captcha_confirma"], documento=OUTRO))
    _apos_o_solver(p, quando=23.0, estado_novo="confirmada")
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)
    assert p.envios == 1


def test_o_mesmo_desfecho_nao_e_anunciado_duas_vezes(portal, capsys):
    """Polling nao pode virar repeticao de log."""
    p = portal(Portal(["erro", "confirma"]))
    representar(p)
    saida = capsys.readouterr().out
    assert saida.count("Desfecho observado | tipo=erro_portal") == 1
