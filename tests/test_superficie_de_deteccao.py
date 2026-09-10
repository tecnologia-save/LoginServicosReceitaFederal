"""O que o navegador conta sobre si mesmo antes de qualquer captcha aparecer."""
import inspect

from servicos_rf_login import login


def _fonte_do_lancamento() -> str:
    """Só o CÓDIGO — comentários fora.

    A primeira versão procurava a palavra na fonte inteira e reprovava o
    comentário que EXPLICA a remoção. É o mesmo engano que já apareceu duas
    vezes nesta suíte: procurar termo solto reprova o texto que documenta o
    acerto. O que importa é o que o Chrome recebe, não o que está escrito ao
    lado.
    """
    linhas = inspect.getsource(login.main).splitlines()
    return chr(10).join(l for l in linhas if not l.lstrip().startswith("#"))


def test_sem_porta_de_depuracao_aberta():
    """`--remote-debugging-port=9222` saiu em 10/09/2026.

    Ninguém se conectava nela — era a única referência a 9222 nos três repos —
    e custava duas coisas: marcador de automação, e um navegador AUTENTICADO no
    e-CAC dirigível por qualquer processo local.
    """
    assert "remote-debugging-port" not in _fonte_do_lancamento()


def test_desliga_o_sinalizador_de_automacao():
    """Sem isto `navigator.webdriver` responde `true`.

    A página descobre que é automação em uma linha de JavaScript. Importa
    direto no problema de 09-10/09/2026: o hCaptcha escala dificuldade quando
    desconfia, uma sessão levou 17 desafios seguidos, e depois a conta foi
    bloqueada por atividade automatizada. Otimizar o RESOLVEDOR não ataca isso.
    """
    fonte = _fonte_do_lancamento()
    assert "--disable-blink-features=AutomationControlled" in fonte
    # Metade da correção não serve: o Playwright injeta `--enable-automation`
    # por conta própria, e ele alimenta os mesmos sinais.
    assert 'ignore_default_args=["--enable-automation"]' in fonte
