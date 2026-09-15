"""Botão "Entrar com gov.br" que não aparece: recarrega uma vez antes de desistir.

RUN-35b011a7 (14/09/2026), YAGO DAMASCENO, primeira empresa do lote:

    20:35:13  -> página inicial carregada.
    20:35:24  Clicando em 'Entrar com gov.br'...
    20:35:47  -> botão 'Entrar com gov.br' não encontrado em nenhum seletor.

Sem página de erro e sem tarja. A empresa foi dada como perdida em 40 s.
"""
import inspect

from servicos_rf_login import login

FONTE = inspect.getsource(login.main)


def _ramo_do_botao_ausente() -> str:
    i = FONTE.index("if not ja_entrou and not _clicar_entrar_govbr(page):")
    return FONTE[i:FONTE.index("page.wait_for_load_state", i)]


def test_recarrega_antes_de_registrar_o_erro():
    ramo = _ramo_do_botao_ausente()
    assert "_refazer_entrada_govbr(page)" in ramo
    assert ramo.index("_refazer_entrada_govbr(page)") < ramo.index("registrar_erro(")


def test_so_aborta_se_o_recarregamento_tambem_falhar():
    ramo = _ramo_do_botao_ausente()
    assert "if not _refazer_entrada_govbr(page):" in ramo
    assert ramo.index("if not _refazer_entrada_govbr(page):") < ramo.index("_abortar(p, context)")


def test_o_recarregamento_aceita_a_sessao_que_voltou_sozinha():
    """Se o refresh cai logado, o fluxo segue — o `_ja_logado` logo abaixo encerra."""
    refazer = inspect.getsource(login._refazer_entrada_govbr)
    assert refazer.index("_ja_logado(page)") < refazer.index("_clicar_entrar_govbr(page)")
    depois = FONTE[FONTE.index("if not ja_entrou and not _clicar_entrar_govbr(page):"):]
    assert "if _ja_logado(page):" in depois[:3000]
