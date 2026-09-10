"""Achar o diálogo na janela errada é pior que não achar."""
import inspect

from servicos_rf_login import cert_dialog as C


class _El:
    def __init__(self, t): self._t = t
    def window_text(self): return self._t


class _Janela:
    def __init__(self, textos): self._els = [_El(t) for t in textos]
    def descendants(self): return self._els


def test_texto_de_pagina_nao_vira_dialogo_de_certificado():
    """"TEMA" casava dentro de "sisTEMA".

    A checagem colava os 200 descendentes numa string sem espaços e procurava
    cada marca como substring. Numa janela de navegador com texto de página
    isso dava True quase sempre — e "SERIAL" ainda casava em emendas de
    palavras: "poder seria legal" vira "PODERSERIALEGAL".

    Medido em 10/09/2026, run aa762acf:

        [cert-dialog] Janela encontrada: 'Servicos da Receita Federal - Google Chrome'
        [cert-dialog] 129 elemento(s) com texto no dialogo.
        [cert-dialog] Nenhum elemento casou.

    O gatilho estava na própria tela de erro: a URL era
    `?logoutCertificadoDigital=1`, e `CertificadoDigital` em maiúsculas contém
    `CERTIFI` — a marca do título. O 404 do certificado fazia a janela do
    navegador parecer o diálogo do certificado.
    """
    pagina = _Janela([
        "Sistema de atendimento virtual",
        "O poder seria legal",
        "servicos.receitafederal.gov.br/?logoutCertificadoDigital=1&codErro=30002",
        "Uso da memória em servicos.receitafederal.gov.br",
    ])
    assert C._tem_marcas_de_coluna(pagina) is False


def test_o_dialogo_de_verdade_continua_sendo_reconhecido():
    """Medido na máquina do Jean em 08/09/2026: os cabeçalhos vêm como
    `DataItem` com o texto exato."""
    dialogo = _Janela(["Selecione um certificado", "Tema", "Emissor", "Serial",
                       "26532603025EA596"])
    assert C._tem_marcas_de_coluna(dialogo) is True


def test_uma_marca_isolada_nao_basta():
    """Duas distintas, e não uma: o diálogo real expõe Tema, Emissor e Serial
    juntos. Exigir duas elimina a coincidência isolada sem depender do idioma
    — em inglês sobram Issuer e Subject, que já bastam."""
    assert C._tem_marcas_de_coluna(_Janela(["Serial"])) is False
    assert C._tem_marcas_de_coluna(_Janela(["Issuer", "Subject"])) is True


def test_a_comparacao_e_por_IGUALDADE():
    """Substring numa string colada foi a causa; o teste amarra o método."""
    fonte = inspect.getsource(C._tem_marcas_de_coluna)
    assert "in _MARCAS_COLUNA" in fonte, "igualdade contra a tupla"

    # Só as linhas de CÓDIGO: o comentário acima cita a versão antiga para
    # explicar o defeito, e a primeira versão deste teste casou com ela — o
    # teste reprovava a documentação da própria correção.
    codigo = chr(10).join(l for l in fonte.splitlines()
                          if l.strip() and not l.strip().startswith("#"))
    assert "junto" not in codigo, "sem a string colada dos 200 descendentes"
