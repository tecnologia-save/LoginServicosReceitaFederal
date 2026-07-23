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
                    if w.class_name() == "Chrome_WidgetWin_1":
                        if w.descendants(title="OK", control_type="Button"):
                            txt = _norm(" ".join(
                                d.window_text() for d in w.descendants(control_type="Text")[:30]
                            ))
                            if "EMISSOR" in txt or "SERIAL" in txt or "TEMA" in txt:
                                return w
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
