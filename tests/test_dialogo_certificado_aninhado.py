"""O dialogo de certificado nao e janela de topo, e o tipo dele varia."""
import inspect

from servicos_rf_login import cert_dialog as C


def test_nao_exige_botao_OK_para_olhar_o_titulo():
    """Pre-condicao que so economiza busca nao pode decidir se a busca ocorre.

    Ate 09/09/2026 o localizador so olhava o titulo de uma janela do Chrome se
    ela tivesse um descendente `Button` chamado "OK". Naquele dia o dialogo
    ficou aberto na tela — a UIA bruta o encontrou como descendente com
    `ClassName=RootView` — e a automacao esperou parada ate o timeout.

    O botao existe visualmente; quem o desenha e o Chrome, e o tipo que ele
    expoe na arvore UIA e detalhe de versao. O codigo tratava esse detalhe como
    requisito, e o custo era o login inteiro.
    """
    fonte = inspect.getsource(C._achar_dialogo)
    assert 'descendants(title="OK", control_type="Button")' not in fonte, (
        "a pre-condicao do botao OK voltou a bloquear a busca pelo titulo")


def test_ha_busca_por_titulo_em_qualquer_profundidade():
    """A rede de seguranca que a UIA bruta provou funcionar.

    O dialogo nao e janela de topo nem filho direto: esta aninhado, e o tipo de
    controle muda entre versoes do Chrome. Titulo foi a unica coisa estavel.
    """
    assert hasattr(C, "_descendente_por_titulo")
    # `control_type=` — a CHAMADA, nao a palavra: o docstring da funcao explica
    # justamente por que nao se filtra por tipo, e procurar o termo solto
    # reprovava o texto que documenta o acerto.
    fonte = inspect.getsource(C._descendente_por_titulo)
    assert "control_type=" not in fonte, (
        "filtrar por tipo aqui e o que fez a busca passar por cima do dialogo")
    assert "_descendente_por_titulo" in inspect.getsource(C._achar_dialogo)


def test_a_busca_ampla_vem_por_ULTIMO():
    """Ela varre a arvore inteira: e rede, nao criterio principal."""
    fonte = inspect.getsource(C._achar_dialogo)
    assert fonte.index("_dialogo_dentro") < fonte.index("_descendente_por_titulo")
