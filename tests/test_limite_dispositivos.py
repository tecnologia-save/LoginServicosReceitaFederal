"""A tarja de dispositivos do gov.br aparece em MAIS DE UM ponto do login."""
import inspect

from servicos_rf_login import login


def test_checa_na_home_E_no_retorno_do_certificado():
    """Duas checagens, e a segunda existe porque a primeira nao bastou.

    Em 09/09/2026, run 454a82dc: a checagem da primeira navegacao passou limpa
    — a home ainda nao tinha a tarja —, o gov.br e o certificado seguiram, e ela
    apareceu no RETORNO. A automacao ficou contando "(18s) aguardando
    redirecionamento" contra uma tela que ja tinha respondido nao: 60s por
    tentativa, tres tentativas, e no fim um erro generico de redirecionamento
    que nao menciona dispositivo nenhum.

    Checar so na entrada supoe que o estado da conta nao muda durante o login.
    Ele muda — inclusive por causa da propria tentativa, que abre sessao.
    """
    fonte = inspect.getsource(login)
    assert fonte.count("_limite_de_dispositivos(page)") >= 2, (
        "voltou a checar em um lugar so")


def test_o_retorno_levanta_em_vez_de_seguir_esperando():
    """`break` cairia no caminho de sucesso; aqui o desfecho tem de ser tipado.

    E o mesmo erro que ja aconteceu com a pagina de erro do SSO, documentado
    logo acima no proprio arquivo: um break seco anunciou "login concluido"
    olhando uma tela de erro.
    """
    fonte = inspect.getsource(login)
    assert fonte.count("raise LimiteDeDispositivosGovBr") >= 2, (
        "os dois pontos de deteção têm de levantar tipado, não só o primeiro")


def test_o_texto_real_da_tarja_casa():
    """O texto exato da tela, copiado de uma captura de 09/09/2026.

    Um teste que so exercita a frase que EU escrevi no detector nao prova nada:
    ele passaria mesmo se o gov.br usasse outra redacao. Esta e a tela.
    """
    class _Pagina:
        def __init__(self, t): self._t = t
        def inner_text(self, seletor, timeout=None): return self._t

    tela = ("Serviços da Receita Federal O que você procura? "
            "Você atingiu o número máximo de dispositivos conectados "
            "simultaneamente com esta conta. Saia da sua conta em um dos "
            "dispositivos para entrar por aqui. Acesse gov.br")
    assert login._limite_de_dispositivos(_Pagina(tela)) is True
    # Sem acento e em caixa alta tambem: a frase ja apareceu das duas formas.
    assert login._limite_de_dispositivos(
        _Pagina("voce atingiu o numero maximo de dispositivos conectados")) is True
    # E a home normal nao pode dar falso positivo — seria pior que nao detectar.
    assert login._limite_de_dispositivos(
        _Pagina("Acesse gov.br Faça o login na conta gov.br")) is False
