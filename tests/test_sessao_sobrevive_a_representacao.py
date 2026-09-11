"""Perder a empresa não pode custar o LOGIN."""
import inspect

from servicos_rf_login import login


def test_desfecho_da_representacao_nao_aborta_o_navegador():
    """RUN-385b9699, 11/09/2026:

        11:05:16  LEONARDO VIEIRA  -> Lançando Chrome   (login 1)
        11:07:45  LINHARES & CIA   -> Lançando Chrome   (login 2)
        11:10:10  dispositivos_maximo

    A LEONARDO caiu num captcha que não se automatiza, a sessão foi
    descartada, e a empresa seguinte pagou outro login — outro dispositivo no
    gov.br. Três desses derrubam a run.

    O runner SEMPRE esperou isto. O `except CaptchaHumano` dele diz: "A sessão
    sobrevive: o captcha barrou esta empresa, não o login, e as seguintes do
    mesmo certificado ainda a aproveitam". Ele só nunca recebia.
    """
    fonte = inspect.getsource(login.main)
    i = fonte.index("except DESFECHOS_COM_SESSAO_VIVA as e:")
    j = fonte.index("except Exception:", i)
    trecho = fonte[i:j]
    assert "e.sessao = (p, context, page)" in trecho
    assert "_abortar" not in trecho, "desfecho do portal não fecha o navegador"


def test_falha_inesperada_continua_abortando():
    """Sessão que quebrou de verdade tem de ser encerrada: sem isso a próxima
    tentativa falha com 'Sync API inside the asyncio loop'."""
    fonte = inspect.getsource(login.main)
    i = fonte.index("except Exception:", fonte.index("except DESFECHOS_COM_SESSAO_VIVA"))
    assert "_abortar(p, context)" in fonte[i:i + 400]


def test_o_bloqueio_por_automacao_fica_de_fora():
    """Ali a sessão é justamente o que o portal recusou — reaproveitá-la é
    insistir no que causou o bloqueio."""
    tipos = login._desfechos_com_sessao_viva()
    assert login.BloqueioPorAutomacao not in tipos
    assert login.RepresentacaoRequerIntervencao in tipos
    assert login.RepresentacaoRejeitadaPeloPortal in tipos


def test_a_pagina_nao_e_devolvida_como_utilizavel():
    """O que o comentário original protegia continua valendo: sem
    representação confirmada, consultar com o perfil pessoal dá 401. A sessão
    vai ANEXADA À EXCEÇÃO, e quem trata decide — não é um `return`."""
    fonte = inspect.getsource(login.main)
    i = fonte.index("except DESFECHOS_COM_SESSAO_VIVA as e:")
    j = fonte.index("except Exception:", i)
    assert "return" not in fonte[i:j]
    assert "raise" in fonte[i:j]
