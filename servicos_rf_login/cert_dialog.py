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
        # Cada elemento comparado SOZINHO, e por igualdade — nao por substring
        # numa string gigante.
        #
        # Antes: `_norm(" ".join(textos))` colava os 200 descendentes num
        # unico texto sem espacos, e procurava cada marca como substring. Com
        # isso "TEMA" casa dentro de "sisTEMA", e "SERIAL" casa em qualquer
        # emenda de palavras ("...poder SERIA Legal..." vira "PODERSERIALEGAL").
        # Numa janela de navegador com texto de pagina, dava True quase sempre.
        #
        # Medido em 10/09/2026, run aa762acf:
        #
        #     [cert-dialog] Janela encontrada: 'Servicos da Receita Federal - Google Chrome'
        #     [cert-dialog] 129 elemento(s) com texto no dialogo.
        #     [cert-dialog] Nenhum elemento casou.
        #
        # Ele "achou o dialogo" na janela do NAVEGADOR. O gatilho estava na
        # propria tela de erro: a URL era `?logoutCertificadoDigital=1`, e
        # `CertificadoDigital` em maiusculas contem `CERTIFI`, a marca do
        # titulo. O 404 do certificado fazia a janela parecer o dialogo dele.
        #
        # DUAS marcas distintas, e nao uma: o dialogo real expoe `Tema`,
        # `Emissor` e `Serial` como DataItem separados. Exigir duas elimina a
        # coincidencia isolada sem depender do idioma da tabela — em ingles
        # sobram `Issuer` e `Subject`, que ja bastam.
        achadas = set()
        for d in w.descendants()[:200]:
            try:
                t = _norm(d.window_text())
            except Exception:  # noqa: BLE001
                continue
            if t in _MARCAS_COLUNA:
                achadas.add(t)
                if len(achadas) >= 2:
                    return True
        return False
    except Exception:
        return False


# Titulos de janela que podem hospedar o dialogo de certificado.
#
# `Chrome_WidgetWin_1` nao distingue nada: VS Code, Brave e o Chrome da
# automacao usam a mesma classe, porque todos sao Chromium. Em 09/09/2026 a
# janela do VS Code — com este arquivo aberto, contendo a frase que se procura —
# casou na busca ampla e vinha ANTES do Chrome na enumeracao.
_JANELA_HOSPEDEIRA_RE = __import__("re").compile(
    r"gov\.br|receita|acesso\.gov|Google Chrome", __import__("re").I)


def _descendente_por_titulo(janela):
    """Procura, em QUALQUER profundidade, um elemento cujo titulo case E que
    pareca mesmo a lista de certificados.

    Sem filtro de `control_type`: o dialogo do Chrome apareceu com
    `ClassName=RootView` em 09/09/2026, e filtrar por tipo foi o que fez a
    busca passar por cima dele.

    Mas titulo sozinho NAO basta, e isso quase passou despercebido: na mesma
    maquina, a janela do VS Code casou — o codigo-fonte aberto na tela contem a
    frase que se procura, e ela vem ANTES do Chrome na enumeracao. Um editor
    com este arquivo aberto viraria "dialogo de certificado", e o clique
    seguinte iria para o lugar errado.

    Por isso todo candidato passa por `_tem_marcas_de_coluna`: o dialogo de
    verdade tem as colunas Assunto/Emissor/Serial. Texto que so MENCIONA
    certificado nao tem.

    Custa uma varredura da arvore, entao roda por ultimo — depois das buscas
    dirigidas, e so quando elas nao acharam nada.
    """
    try:
        for e in janela.descendants():
            try:
                if not TITULO_RE.search(e.window_text() or ""):
                    continue
                if _tem_marcas_de_coluna(e):
                    return e
            except Exception:
                continue
    except Exception:
        return None
    return None


def _pids_do_perfil(perfil: str) -> set:
    """PIDs do Chrome lancado com ESTE diretorio de perfil.

    Escopo por PROCESSO, e nao por titulo de janela. Em 10/09/2026 a busca por
    titulo casou com a JANELA DO CHAT em que este problema estava sendo
    discutido — o log registrou:

        [cert-dialog] Janela encontrada: 'Cara, o que ta faltando, na moral?'

    e o login seguiu como concluido. A automacao le a arvore de acessibilidade
    do desktop inteiro, e naquele desktop as palavras "Selecione um
    certificado", "Tema", "Emissor", "Serial" — e ate o numero de serie —
    estavam escritas na tela, porque era o assunto da conversa.

    Nenhum filtro de texto resolve isso: qualquer palavra que identifique o
    dialogo pode aparecer num navegador aberto ao lado. O que NAO pode aparecer
    e o processo: o diretorio de perfil e criado por sessao (`sessao-xxxx`) e
    so o Chrome desta run o carrega.

    Conjunto vazio significa "nao consegui determinar", e quem chama decide —
    aqui, cair para a busca sem escopo, que e o comportamento antigo.
    """
    if not perfil:
        return set()
    try:
        import subprocess
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20)
        import json as _json
        dados = _json.loads(saida.stdout or "[]")
        if isinstance(dados, dict):
            dados = [dados]
    except Exception:  # noqa: BLE001 — sem escopo e pior que travar
        return set()
    alvo = perfil.replace("/", "\\").lower()
    return {int(d["ProcessId"]) for d in dados
            if d.get("CommandLine") and alvo in d["CommandLine"].replace("/", "\\").lower()}


def _achar_dialogo(timeout: float, pids: set | None = None):
    if not _PYWINAUTO_OK:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for w in Desktop(backend="uia").windows():
                # Fora do processo desta run, nada e candidato — nem que o
                # titulo case perfeitamente. Ver `_pids_do_perfil`.
                if pids:
                    try:
                        if w.element_info.process_id not in pids:
                            continue
                    except Exception:  # noqa: BLE001
                        continue
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
                    # So em janela que hospeda o dialogo de verdade. O
                    # `class_name` ja filtra Chrome_WidgetWin_1 acima, mas o
                    # VS Code e o Brave usam a MESMA classe — sao Electron e
                    # Chromium. O que separa e o titulo da janela: o navegador
                    # da automacao esta no gov.br.
                    if not _JANELA_HOSPEDEIRA_RE.search(w.window_text() or ""):
                        continue
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


def _mascarar(texto: str, limite: int = 70) -> str:
    """O texto da linha do certificado, com os digitos trocados por `#`.

    O nome da empresa e o que responde "qual certificado foi escolhido"; os
    digitos ao lado sao CNPJ e numero de serie, e nao acrescentam nada a essa
    pergunta. Mascarar deixa o log util e sem dado de terceiro.

    Existe porque o log dizia apenas "Match por SERIAL", sem dizer em QUE. Em
    10/09/2026 a automacao entrou com o certificado errado — o dialogo tinha
    dois, Save Inteligencia em primeiro e D&S em segundo, e ela confirmou o
    primeiro. Depois de corrigido, continuava impossivel CONFERIR pelo log: so
    olhando a tela no instante do clique, que dura menos de um segundo.

    O caminho de FALHA ja despejava os textos todos. O de sucesso, que e onde
    a conferencia importa, nao dizia nada.
    """
    import re as _re
    return _re.sub(r"\d", "#", (texto or "").strip())[:limite]


def selecionar_certificado_no_dialogo(cn: str = "", serial: str = "",
                                      timeout: float = 30.0,
                                      perfil: str = "") -> bool:
    """Localiza a janela de certificado, seleciona o cert pelo serial (ou CN) e clica OK.

    `perfil` e o `user_data_dir` do Chrome desta run. Com ele a busca so olha
    janelas DESTE processo — sem ele, olha o desktop inteiro, e ai qualquer
    janela que exiba as palavras do dialogo vira candidata.
    """
    if not _PYWINAUTO_OK:
        print("[cert-dialog] pywinauto nao disponivel — fallback desativado.")
        return False
    alvo_serial = _norm(serial)
    alvo_cn     = _norm(cn)
    pids = _pids_do_perfil(perfil)
    print(f"[cert-dialog] Aguardando janela de certificado (ate {int(timeout)}s"
          f"{f', {len(pids)} processo(s) desta sessao' if pids else ', SEM escopo de processo'})...")
    dlg = _achar_dialogo(timeout, pids)
    if dlg is None:
        print("[cert-dialog] Janela nao apareceu.")
        return False
    print(f"[cert-dialog] Janela encontrada: '{dlg.window_text()}'")

    # Insiste ate os filhos existirem, em vez de uma espera fixa e uma leitura.
    #
    # Era `time.sleep(0.8)` seguido de UMA coleta. A janela aparece antes de a
    # UIA expor o conteudo dela, e nesse intervalo a coleta devolve zero — que
    # e indistinguivel de "dialogo sem o nosso certificado". Desistiamos ali,
    # com 90s de orcamento intactos.
    #
    # Medido em 10/09/2026, RUN-1e369205, C. CARVALHO GENEROSO:
    #
    #     [cert-dialog] Janela encontrada: 'Selecione um certificado'
    #     [cert-dialog] 0 elemento(s) com texto no dialogo.
    #     [cert-dialog] Nenhum elemento casou. Dump dos textos do dialogo:
    #
    # O dump saiu vazio — nao havia o que casar porque nao havia o que ler
    # ainda. Sem o certificado escolhido a autenticacao TLS falha, o Chrome
    # mostra a pagina de erro (`host=chromewebdata`) e o login gasta mais 60s
    # perguntando a um erro de rede se ele ja virou portal. Foi assim que essa
    # empresa falhou duas vezes seguidas, com erro diferente a cada vez —
    # sintomas distintos da mesma causa.
    #
    # Zero elemento nao e resposta: e a pergunta feita cedo demais.
    limite = time.monotonic() + max(3.0, min(15.0, timeout / 4.0))
    elems = []
    tentativas = 0
    while time.monotonic() < limite:
        tentativas += 1
        elems = _coletar_elementos(dlg)
        if elems:
            break
        time.sleep(0.3)
    print(f"[cert-dialog] {len(elems)} elemento(s) com texto no dialogo "
          f"(apos {tentativas} leitura(s)).")

    escolhido = None
    if alvo_serial:
        for e, txt in elems:
            if alvo_serial in txt:
                escolhido = e
                print(f"[cert-dialog] Match por SERIAL em {_mascarar(txt)}"
                      f" ({len(elems)} candidato(s) no dialogo).")
                break
    if escolhido is None and alvo_cn:
        for e, txt in elems:
            if alvo_cn in txt:
                escolhido = e
                print(f"[cert-dialog] Match por CN em {_mascarar(txt)}"
                      f" ({len(elems)} candidato(s) no dialogo).")
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
