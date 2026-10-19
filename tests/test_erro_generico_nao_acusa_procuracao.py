"""Erro do portal que não fala em procuração NÃO vira acusação no cadastro.

21/09/2026, medido em prd: 21 desfechos `erro_portal` num dia e 10 empresas
marcadas com `procuracao_cancelada_em`. Entre elas a SUPERMERCADO OPCAO, que
tinha sido consultada com sucesso DUAS vezes pelo mesmo certificado — prova de
que a procuração existia e que a marca era falsa.

A causa: `_erro_representacao_visivel` casa pela CLASSE `.mensagemErro`, e o
portal manda no mesmo elemento coisas opostas. Duas quaisquer delas viravam
`RepresentacaoRejeitadaPeloPortal` → `perfil_recusado` → `procuracao_cancelada_em`.

A frase da recusa real, capturada em print pelo Jean em 10/09/2026, nomeia a
procuração ("Procuração não encontrada / vencida"). O erro comum não nomeia. É
essa diferença que estes testes fixam — sem ler nem registrar a frase, como
`_MARCA_BLOQUEIO_AUTOMACAO` já fazia para o bloqueio de sessão.
"""
import inspect

import pytest

from servicos_rf_login import login

from test_representacao_erro_portal import (  # noqa: F401 — fixture
    CNPJ, Portal, portal, representar,
)


ERRO_COMUM = "Não foi possível processar sua solicitação. Tente novamente."
RECUSA = "Procuração não encontrada / vencida."
RECUSA_SEM_ACENTO = "PROCURACAO NAO ENCONTRADA"


# ══ 1 · O desfecho é outro ══════════════════════════════════════════════════

def test_erro_generico_tem_desfecho_proprio():
    assert login.DESFECHO_ERRO_GENERICO != login.DESFECHO_ERRO_PORTAL
    assert login.DESFECHO_ERRO_GENERICO != login.DESFECHO_BLOQUEIO_AUTOMACAO


@pytest.mark.parametrize("texto, recusa", [
    (RECUSA, True),
    (RECUSA_SEM_ACENTO, True),
    ("Você não é procurador deste CNPJ.", True),
    ("O outorgante não concedeu sem poderes bastantes.", True),
    (ERRO_COMUM, False),
    ("Erro inesperado. Código 500.", False),
    ("Serviço temporariamente indisponível.", False),
])
def test_so_a_mensagem_que_nomeia_procuracao_e_recusa(portal, texto, recusa):
    p = portal(Portal(["erro"], texto_erro=texto))
    # O dublê só expõe a `.mensagemErro` quando ESTÁ no estado de erro, e o
    # detector é chamado aqui direto, sem passar pelo envio do formulário que
    # levaria o portal até lá. Sem isto o teste media "não há mensagem na
    # tela", que é outra pergunta e responde False para todos.
    p.estado = "erro"
    assert login._erro_e_recusa_de_procuracao(p) is recusa


def test_o_detector_nao_registra_a_frase():
    """Mesma regra do bloqueio: lê para classificar, nunca para contar."""
    fonte = inspect.getsource(login._erro_e_recusa_de_procuracao)
    assert "print(" not in fonte


# ══ 2 · O efeito no cadastro ════════════════════════════════════════════════

def test_erro_comum_repetido_nao_vira_recusa_de_procuracao(portal):
    """O coração da correção.

    `RepresentacaoRejeitadaPeloPortal` é o que o adaptador traduz para
    `PerfilRecusado`, e o Save Process grava `procuracao_cancelada_em`.
    `RepresentacaoNaoConfirmada` vira `ErroPerfil`: a empresa volta pela rotina
    e ninguém cobra procuração do cliente.
    """
    p = portal(Portal(["erro", "erro", "erro"], texto_erro=ERRO_COMUM))
    with pytest.raises(login.RepresentacaoNaoConfirmada) as erro:
        representar(p)
    assert not isinstance(erro.value, login.RepresentacaoRejeitadaPeloPortal)


def test_a_recusa_de_verdade_continua_acusando(portal):
    """A proteção não foi trocada por silêncio: o sinal verdadeiro sobrevive."""
    p = portal(Portal(["erro", "erro", "erro"], texto_erro=RECUSA))
    with pytest.raises(login.RepresentacaoRejeitadaPeloPortal):
        representar(p)
    assert p.envios == login.RECUSAS_PARA_DESISTIR


def test_erro_comum_ainda_gasta_as_tentativas(portal):
    """Não contar recusa não é desistir: as tentativas previstas continuam.

    O erro pode ser passageiro — é exatamente a hipótese que ele não nomeia a
    procuração. Reenviar depois do intervalo é o que dá chance a ela.
    """
    p = portal(Portal(["erro", "erro", "confirma"], texto_erro=ERRO_COMUM))
    assert representar(p) is True
    assert p.envios == login.MAX_TENTATIVAS_REPRESENTACAO


def test_erro_comum_cumpre_o_intervalo_entre_tentativas(portal, monkeypatch):
    """`erro_ja_visto` tem de valer para o erro comum também.

    A mensagem permanece na tela. Se o intervalo não souber disso, ele termina
    no primeiro instante relatando o mesmo erro, e os 31s pedidos pelo portal
    não são cumpridos — que é dupla submissão, justamente o que o intervalo
    existe para evitar.
    """
    vistos = []
    original = login._observar_intervalo
    monkeypatch.setattr(login, "_observar_intervalo",
                        lambda *a, **k: (vistos.append(k.get("erro_ja_visto")),
                                         original(*a, **k))[1])
    p = portal(Portal(["erro", "confirma"], texto_erro=ERRO_COMUM))
    representar(p)
    assert vistos and all(vistos), "o erro na tela tem de ser anunciado ao intervalo"


# ══ 3 · O bloqueio de sessão continua acima dos dois ════════════════════════

def test_bloqueio_vence_a_classificacao_nova(portal):
    p = portal(Portal(["erro"], texto_erro="Seu acesso automatizado foi bloqueado."))
    with pytest.raises(login.BloqueioPorAutomacao):
        representar(p)


def test_o_texto_continua_fora_do_log(portal, capsys):
    """A classificação nova não abriu porta para a frase no log."""
    p = portal(Portal(["erro", "erro", "erro"],
                      texto_erro=f"{ERRO_COMUM} SEGREDO_TESTE"))
    with pytest.raises(login.RepresentacaoNaoConfirmada):
        representar(p)
    saida = capsys.readouterr()
    assert "SEGREDO_TESTE" not in saida.out
    assert "SEGREDO_TESTE" not in saida.err
    assert CNPJ not in saida.out
