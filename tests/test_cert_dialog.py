"""O localizador do dialogo "Selecione um certificado".

A arvore UIA aqui NAO e inventada: foi medida na maquina do Jean em 08/09/2026,
com o dialogo aberto na tela, por uma sonda que so lia (nao clicava):

    [3] Chrome_WidgetWin_1 · 'gov.br - Acesse sua conta - Google Chrome'
        botoes 'OK' (control_type=Button): 1
        Text (30 primeiros): 2 | marcadores: NENHUM
        por tipo: Pane 12, DataItem 8, Button 4, Text 2, Window 1, DataGrid 1
        COM texto:
            Window    'Selecione um certificado'
            Text      'Selecione um certificado'
            Text      'Selecione um certificado para se a...'
            DataItem  'Tema'
            DataItem  'Emissor'
            DataItem  'Serial'
            DataItem  'SAVE INTELIGENCIA TRIBUTARIA LTDA:...'
            DataItem  '26532603025EA596'
            Button    'OK'

Dois fatos que derrubavam o codigo antigo:

  1. o dialogo NAO e janela de topo — e um `Window` DENTRO da janela do Chrome,
     e `Desktop().windows()` so devolve top-level. O titulo que casaria com o
     regex nunca era visto;
  2. os cabecalhos Tema/Emissor/Serial sao `DataItem`, e a busca olhava so
     `control_type="Text"` — onde so existem o titulo e o subtitulo.

Resultado: `_achar_dialogo` devolvia None sempre, em silencio, e a automacao
ficava parada esperando uma pessoa clicar.
"""
import sys
import types

import pytest

from servicos_rf_login import cert_dialog


class Elem:
    """Controle UIA de mentira, com o minimo que o localizador consulta."""

    def __init__(self, texto="", tipo="Pane", classe="", filhos=()):
        self.texto = texto
        self.tipo = tipo
        self.classe = classe
        self.filhos = list(filhos)
        self.element_info = types.SimpleNamespace(control_type=tipo)

    def window_text(self):
        return self.texto

    def class_name(self):
        return self.classe

    def _todos(self):
        saida = []
        for f in self.filhos:
            saida.append(f)
            saida.extend(f._todos())
        return saida

    def descendants(self, title=None, control_type=None):
        return [d for d in self._todos()
                if (title is None or d.texto == title)
                and (control_type is None or d.tipo == control_type)]


def _dialogo_real():
    """A arvore medida, reproduzida."""
    interno = Elem("Selecione um certificado", "Window", filhos=[
        Elem("Selecione um certificado", "Text"),
        Elem("Selecione um certificado para se autenticar...", "Text"),
        Elem("Tema", "DataItem"),
        Elem("Emissor", "DataItem"),
        Elem("Serial", "DataItem"),
        Elem("SAVE INTELIGENCIA TRIBUTARIA LTDA:24245826000189", "DataItem"),
        Elem("AC SOLUTI Multipla v5", "DataItem"),
        Elem("26532603025EA596", "DataItem"),
        Elem("Informações do certificado", "Button"),
        Elem("OK", "Button"),
        Elem("Cancelar", "Button"),
    ])
    return Elem("gov.br - Acesse sua conta - Google Chrome", "Pane",
                classe="Chrome_WidgetWin_1", filhos=[interno])


def _instalar_desktop(monkeypatch, janelas):
    class FakeDesktop:
        def __init__(self, backend=None):
            pass

        def windows(self):
            return list(janelas)

    monkeypatch.setattr(cert_dialog, "Desktop", FakeDesktop)
    monkeypatch.setattr(cert_dialog, "_PYWINAUTO_OK", True)


def test_acha_a_janela_ANINHADA_e_nao_o_top_level(monkeypatch):
    """O que se devolve tem de ser o dialogo, nao a janela inteira do Chrome.

    Devolver o top-level faria `_coletar_elementos` varrer a pagina inteira do
    navegador, e o serial poderia casar com texto atras do dialogo.
    """
    topo = _dialogo_real()
    _instalar_desktop(monkeypatch, [topo])
    achado = cert_dialog._achar_dialogo(timeout=1.0)
    assert achado is not None, "nao achou o dialogo"
    assert achado.window_text() == "Selecione um certificado"
    assert achado is not topo


def test_o_codigo_ANTIGO_falharia_nesta_mesma_arvore():
    """Prova que os marcadores nao estao entre os `Text` — a causa raiz."""
    topo = _dialogo_real()
    textos = topo.descendants(control_type="Text")[:30]
    junto = cert_dialog._norm(" ".join(t.window_text() for t in textos))
    assert not any(m in junto for m in ("EMISSOR", "SERIAL", "TEMA"))


def test_marcas_de_coluna_agora_enxergam_DataItem():
    """A rede de seguranca, para uma arvore UIA diferente da medida."""
    assert cert_dialog._tem_marcas_de_coluna(_dialogo_real()) is True


def test_o_serial_esta_entre_os_elementos_coletados():
    """Sem isto o dialogo seria achado e mesmo assim nada seria clicado."""
    interno = cert_dialog._dialogo_dentro(_dialogo_real())
    elems = cert_dialog._coletar_elementos(interno)
    alvo = cert_dialog._norm("26532603025EA596")
    assert any(alvo in txt for _e, txt in elems)


def test_janela_do_chrome_sem_dialogo_nao_e_confundida(monkeypatch):
    """Ha varias janelas Chrome_WidgetWin_1 na maquina (Brave, Spotify, Teams)."""
    outra = Elem("Spotify Premium", "Pane", classe="Chrome_WidgetWin_1",
                 filhos=[Elem("Tocando agora", "Text")])
    _instalar_desktop(monkeypatch, [outra])
    assert cert_dialog._achar_dialogo(timeout=0.6) is None


def test_sem_pywinauto_nao_explode(monkeypatch):
    monkeypatch.setattr(cert_dialog, "_PYWINAUTO_OK", False)
    assert cert_dialog._achar_dialogo(timeout=0.5) is None


# ── "Entrar com gov.br": XPath posicional era ponto unico de falha ──────────
#
# A RUN-002583a5 (08/09/2026) morreu com "botão 'Entrar com gov.br' não
# encontrado. TimeoutError" enquanto o botao estava VISIVEL no canto superior
# direito da tela. O seletor era um XPath por POSICAO —
# //*[@id="home-heading"]/div[1]/div/button — que quebra com qualquer div que a
# Receita insira no caminho, e nao avisa: so para de casar.

def test_ha_mais_de_um_seletor_para_o_botao_govbr():
    from servicos_rf_login import login
    assert len(login.GOVBR_SELECTORS) >= 3


def test_ha_fallback_por_TEXTO_e_nao_so_por_posicao():
    """Texto sobrevive a rearranjo de layout; XPath posicional nao."""
    from servicos_rf_login import login
    por_texto = [s for s in login.GOVBR_SELECTORS
                 if "gov.br" in s.lower() and "xpath" not in s.lower()]
    assert por_texto, login.GOVBR_SELECTORS


def test_o_xpath_original_continua_sendo_o_primeiro():
    """Barato quando o DOM esta como se espera — os outros sao rede."""
    from servicos_rf_login import login
    assert "home-heading" in login.GOVBR_SELECTORS[0]


def test_os_dois_caminhos_usam_o_mesmo_helper():
    """O seletor vivia duplicado em dois lugares; consertar um deixaria o outro."""
    import inspect
    from servicos_rf_login import login
    fonte = inspect.getsource(login)
    assert fonte.count('xpath=//*[@id="home-heading"]/div[1]/div/button') == 1, (
        "o XPath voltou a ser escrito a mao em algum call site")
    assert fonte.count("_clicar_entrar_govbr(page)") >= 2
