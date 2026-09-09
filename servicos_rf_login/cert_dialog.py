"""Seleciona o certificado na janela nativa do Chrome ("Selecione um certificado").

Fallback para quando a policy de registro não pôde ser escrita (UAC negado ou
máquina gerenciada): a janela de seleção de certificado aparece. Este módulo
automatiza essa janela via UI Automation (pywinauto): localiza a linha do
certificado escolhido — casando pelo SERIAL, que é único mesmo quando dois
certificados têm o mesmo CN — e clica OK.

Uso típico (em outra thread, antes de clicar em "Seu certificado digital"):
    import threading
    threading.Thread(target=selecionar_certificado_no_dialogo,
                     args=(cn, serial), daemon=True).start()
"""
import re
import time

try:
    from pywinauto import Desktop
    _PYWINAUTO_OK = True
except Exception:
    _PYWINAUTO_OK = False


TITULO_RE = re.compile(r"selecion\w*\b.*\bcertificad|select\b.*\bcertificate", re.IGNORECASE)


def _norm(s: str) -> str:
    return re.sub(r"[\s:.\-]", "", (s or "")).upper()


# Marcadores das COLUNAS do dialogo. Localizados: sao os cabecalhos que o
# Chrome desenha em pt-BR. Servem so como ultimo criterio — o primeiro e o
# titulo da janela aninhada, que nao depende de idioma da tabela.
_MARCAS_COLUNA = ("EMISSOR", "SERIAL", "TEMA", "ISSUER", "SUBJECT")


def _dialogo_dentro(w):
    """A janela ANINHADA do dialogo, dentro do top-level do Chrome.

    O dialogo de certificado do Chrome nao e uma janela de topo: ele e um
    `Window` DENTRO da janela do navegador. `Desktop().windows()` devolve so
    top-level, entao o titulo 'Selecione um certificado' — que casa com
    TITULO_RE — nunca era visto, e a busca caia no criterio de colunas.

    Devolver a janela ANINHADA, e nao o top-level do Chrome, tambem estreita o
    resto: `_coletar_elementos` passa a varrer so os controles do dialogo, em
    vez da pagina inteira do navegador. Sem isso, o serial poderia casar com
    algum texto da pagina por tras.
    """
    try:
        for d in w.descendants(control_type="Window"):
            try:
                if TITULO_RE.search(d.window_text() or ""):
                    return d
            except Exception:
                continue
    except Exception:
        pass
    return None


def _tem_marcas_de_coluna(w) -> bool:
    """Procura TEMA/EMISSOR/SERIAL em QUALQUER controle com texto.

    Antes olhava so `control_type="Text"`, e no dialogo real os cabecalhos sao
    `DataItem` — medido na maquina do Jean em 08/09/2026:

        Text     (2): 'Selecione um certificado', 'Selecione um certificado p...'
        DataItem (8): 'Tema', 'Emissor', 'Serial', '26532603025EA596', ...

    Ou seja, os dois unicos `Text` do dialogo sao o titulo e o subtitulo, e
    nenhum dos marcadores estava entre eles. A busca falhava sempre, em
    silencio, e o clicador desistia como se o dialogo nao existisse.
    """
    try:
        textos = []
        for d in w.descendants()[:200]:
            try:
                t = d.window_text()
            except Exception:
                continue
            if t:
                textos.append(t)
        junto = _norm(" ".join(textos))
        return any(m in junto for m in _MARCAS_COLUNA)
    except Exception:
        return False


def _descendente_por_titulo(janela):
    """Procura, em QUALQUER profundidade, um elemento cujo titulo case.

    Sem filtro de `control_type`: o dialogo de certificado do Chrome apareceu
    com `ClassName=RootView` em 09/09/2026, e filtrar por tipo foi o que fez a
    busca passar por cima dele.

    Custa uma varredura da arvore, entao roda por ultimo — depois das buscas
    dirigidas, e so quando elas nao acharam nada.
    """
    try:
        for e in janela.descendants():
            try:
                if TITULO_RE.search(e.window_text() or ""):
                    return e
            except Exception:
                continue
    except Exception:
        return None
    return None


def _achar_dialogo(timeout: float):
    if not _PYWINAUTO_OK:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for w in Desktop(backend="uia").windows():
                try:
                    titulo = w.window_text() or ""
                except Exception:
                    titulo = ""
                if TITULO_RE.search(titulo):
                    return w
                try:
                    if w.class_name() != "Chrome_WidgetWin_1":
                        continue
                    # A exigencia de um Button "OK" DESCENDENTE saiu daqui.
                    #
                    # Ela era pre-condicao: sem achar o botao, nem se olhava o
                    # titulo. Em 09/09/2026 o dialogo ficou aberto na tela, a
                    # UIA bruta o encontrou como descendente com
                    # `ClassName=RootView`, e a automacao esperou parada. O
                    # botao existe visualmente, mas quem o desenha e o Chrome —
                    # o tipo que ele expoe na arvore UIA e detalhe de versao, e
                    # o codigo tratava esse detalhe como requisito.
                    #
                    # Pre-condicao que so serve para ECONOMIZAR busca nao pode
                    # decidir se a busca acontece. O custo dela era o login
                    # inteiro; a economia era uma varredura de arvore.
                    #
                    # 1) A janela aninhada, pelo titulo. Criterio principal.
                    interno = _dialogo_dentro(w)
                    if interno is not None:
                        return interno
                    # 2) Colunas, em qualquer tipo de controle. Rede de seguranca
                    #    para uma arvore UIA diferente da medida.
                    if _tem_marcas_de_coluna(w):
                        return w
                    # 3) Qualquer DESCENDENTE cujo titulo case, sem exigir tipo.
                    #
                    # Foi assim que a UIA bruta achou o dialogo que este codigo
                    # nao achava: ele nao e janela de topo nem filho direto —
                    # esta aninhado, e o tipo de controle varia. Titulo e a
                    # unica coisa que se manteve estavel entre as versoes.
                    aninhado = _descendente_por_titulo(w)
                    if aninhado is not None:
                        return aninhado
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.4)
    return None


def _coletar_elementos(dlg):
    elems = []
    try:
        todos = dlg.descendants()
    except Exception:
        todos = []
    for e in todos:
        try:
            t = e.window_text() or ""
        except Exception:
            t = ""
        if t.strip():
            elems.append((e, _norm(t)))
    return elems


def _clicar_ok(dlg) -> bool:
    for getter in (
        lambda: dlg.child_window(title="OK", control_type="Button"),
        lambda: dlg.child_window(title_re="OK|Ok", control_type="Button"),
    ):
        try:
            ok = getter()
            ok.wait("enabled", timeout=5)
            try:
                ok.click()
            except Exception:
                ok.click_input()
            print("[cert-dialog] OK clicado.")
            return True
        except Exception:
            continue
    return False


def selecionar_certificado_no_dialogo(cn: str = "", serial: str = "", timeout: float = 30.0) -> bool:
    """Localiza a janela de certificado, seleciona o cert pelo serial (ou CN) e clica OK."""
    if not _PYWINAUTO_OK:
        print("[cert-dialog] pywinauto nao disponivel — fallback desativado.")
        return False
    alvo_serial = _norm(serial)
    alvo_cn     = _norm(cn)
    print(f"[cert-dialog] Aguardando janela de certificado (ate {int(timeout)}s)...")
    dlg = _achar_dialogo(timeout)
    if dlg is None:
        print("[cert-dialog] Janela nao apareceu.")
        return False
    print(f"[cert-dialog] Janela encontrada: '{dlg.window_text()}'")

    time.sleep(0.8)
    elems = _coletar_elementos(dlg)
    print(f"[cert-dialog] {len(elems)} elemento(s) com texto no dialogo.")

    escolhido = None
    if alvo_serial:
        for e, txt in elems:
            if alvo_serial in txt:
                escolhido = e
                print("[cert-dialog] Match por SERIAL.")
                break
    if escolhido is None and alvo_cn:
        for e, txt in elems:
            if alvo_cn in txt:
                escolhido = e
                print("[cert-dialog] Match por CN.")
                break

    if escolhido is None:
        print("[cert-dialog] Nenhum elemento casou. Dump dos textos do dialogo:")
        for i, (_, txt) in enumerate(elems):
            print(f"  [el {i}] {txt[:80]}")
        return False

    try:
        escolhido.click_input()
    except Exception as e:
        print(f"[cert-dialog] falha ao clicar na linha: {type(e).__name__}: {e}")
        return False

    time.sleep(0.3)

    if _clicar_ok(dlg):
        print("[cert-dialog] Certificado selecionado e confirmado.")
        return True

    try:
        escolhido.double_click_input()
        print("[cert-dialog] Confirmado via duplo-clique.")
        return True
    except Exception as e:
        print(f"[cert-dialog] falha ao confirmar: {type(e).__name__}: {e}")
        return False
