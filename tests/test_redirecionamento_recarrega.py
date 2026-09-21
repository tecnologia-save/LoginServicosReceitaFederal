"""Redirecionamento pós-certificado: espera 10s, recarrega e reavalia o estado.

RUN de 21/09/2026 (10:45–10:46): o certificado foi aceito ("navegação
seguiu"), o host já era o do portal, mas a tela devolveu um 404 que nenhum
seletor de erro conhecido pegou. A espera de 60s foi gasta INTEIRA contra uma
página que só um reload destravava; um refresh manual revelou o portal JÁ
autenticado, pronto para representar — enquanto a automação, na tentativa
seguinte, caçava "Seu certificado digital", uma etapa ATRÁS, e não saía dali.

Estes testes fixam a correção: (1) o prazo caiu para 10s; (2) esgotado o prazo,
o laço RECARREGA antes de decidir; (3) se o reload cai logado, o fluxo SEGUE
(break), sem voltar a procurar o botão de certificado.
"""
import inspect

from servicos_rf_login import login

FONTE = inspect.getsource(login.main)


def _bloco_redirecionamento() -> str:
    """Trecho do `main` do 'Aguardando redirecionamento' até o fim do login."""
    ini = FONTE.index("Aguardando redirecionamento final")
    return FONTE[ini:FONTE.index("Login nos Serviços RF concluído", ini)]


def test_prazo_de_redirecionamento_caiu_de_60_para_10s():
    assert login.SEGUNDOS_ESPERA_REDIRECIONAMENTO == 10
    bloco = _bloco_redirecionamento()
    assert "range(SEGUNDOS_ESPERA_REDIRECIONAMENTO)" in bloco
    assert "range(60)" not in bloco


def test_esgotado_o_prazo_recarrega_antes_de_reavaliar():
    bloco = _bloco_redirecionamento()
    assert "page.reload(" in bloco
    # o reload precede a reavaliação do estado da página.
    assert bloco.index("page.reload(") < bloco.index("_aguardar_logado_ou_botao(")


def test_reload_logado_segue_sem_recacar_o_certificado():
    bloco = _bloco_redirecionamento()
    # Reconhecido o portal autenticado após o reload, o próximo comando é SEGUIR
    # (break), e não continuar para nova busca do botão de certificado.
    depois = bloco[bloco.index('estado == "logado"'):]
    assert depois.index("break") < depois.index("continue")


def test_reavaliacao_reusa_o_helper_de_estado_existente():
    # `_aguardar_logado_ou_botao` já distingue logado / botão / nada; reusá-lo
    # evita reescrever a heurística de estado no meio do `main`.
    assert callable(login._aguardar_logado_ou_botao)
    assert login.SEGUNDOS_REAVALIACAO_POS_RELOAD == 10.0
