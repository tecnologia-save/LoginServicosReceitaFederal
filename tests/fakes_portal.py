"""Duble do portal Servicos RF — so o necessario para a representacao.

O DOM real observado contem dados pessoais e NAO entra no repositorio. Este
duble reproduz apenas a ESTRUTURA relevante, com documentos sinteticos:

    sessao pessoal   -> `representacao-atual` NAO existe
    durante captcha  -> continua sem `representacao-atual`
    representado     -> `representacao-atual .ni-representacao` traz o documento
                        e `#avatar-dropdown-trigger .papel-representacao` o papel

E essa a diferenca que a pos-condicao verifica. Avatar visivel existe nos tres
estados — e por isso nunca serviu de prova.
"""
from resolvedor_captcha import TIPO_CARTAO_ANIMAL, solver

from servicos_rf_login import login

# Documentos SINTETICOS. Repeticao de digito de proposito: nenhum e' plausivel
# como documento real, e a intencao fica obvia para quem ler.
CNPJ_ALVO = "11111111111111"
CNPJ_ALVO_FORMATADO = "11.111.111/1111-11"
CNPJ_OUTRO = "22222222222222"


class Perfil:
    """O que o portal mostra como perfil ATIVO."""

    def __init__(self, documento: str, papel: str = "Procurador"):
        self.documento = documento
        self.papel = papel


class Portal:
    """Estado observavel do portal. E o que o teste manipula."""

    def __init__(self, perfil=None, captcha=False):
        self.perfil = perfil            # None = sessao pessoal
        self.captcha = captcha
        self.representar_clicado = 0
        self.documento_digitado = None
        self.ao_representar = None      # o que o portal faz apos o clique
        self.captcha_automatizavel = False
        self.captcha_tipo = login.TIPO_NENHUM

    # ── acoes do teste ───────────────────────────────────────────────────────
    def representar(self, documento, papel="Procurador"):
        self.perfil = Perfil(documento, papel)
        self.captcha = False
        self.captcha_tipo = login.TIPO_NENHUM

    def exigir_captcha(self, tipo=TIPO_CARTAO_ANIMAL, automatizavel=None):
        """O portal exibiu um desafio de `tipo`.

        O TIPO decide a politica: so `grade` e `grade_fused` podem ser tentados
        automaticamente na representacao. `automatizavel` diz se o duble do
        solver consegue resolve-lo — por padrao, sim para os tipos da
        allowlist.
        """
        self.captcha = True
        self.captcha_tipo = tipo
        self.captcha_automatizavel = (
            tipo in login.TIPOS_AUTOMATICOS_REPRESENTACAO
            if automatizavel is None else automatizavel)


class _Locator:
    def __init__(self, portal, seletor, existe=True, texto=None):
        self._portal, self._seletor = portal, seletor
        self._existe, self._texto = existe, texto

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self._existe else 0

    def inner_text(self):
        if not self._existe:
            raise RuntimeError("elemento ausente")
        return self._texto

    def wait_for(self, state=None, timeout=None):
        if not self._existe:
            raise TimeoutError("elemento ausente")

    def click(self, **_k):
        if not self._existe:
            raise RuntimeError("elemento ausente")
        if self._seletor == "botao-representar":
            self._portal.representar_clicado += 1
            if callable(self._portal.ao_representar):
                self._portal.ao_representar(self._portal)

    def fill(self, valor):
        self._portal.documento_digitado = valor


class _Teclado:
    def press(self, _tecla):
        pass


class Pagina:
    """Pagina falsa. Resolve apenas os seletores que o fluxo usa de verdade."""

    def __init__(self, portal):
        self.portal = portal
        self.keyboard = _Teclado()

    @property
    def frames(self):
        # Nenhum `frame=challenge` ATIVO: o desafio da representacao aparece
        # como widget "Sou humano" fechado, que e o que o detector ve primeiro.
        return []

    def locator(self, seletor):
        p = self.portal

        if seletor == solver.CHECKBOX_SEL:
            return _Locator(p, seletor, existe=p.captcha)

        if seletor == login.SEL_DOCUMENTO_REPRESENTADO:
            # So existe quando ha representacao ativa — o coracao da evidencia.
            if p.perfil is None:
                return _Locator(p, seletor, existe=False)
            return _Locator(p, seletor, texto=p.perfil.documento)

        if seletor == login.SEL_PAPEL_REPRESENTACAO:
            if p.perfil is None:
                return _Locator(p, seletor, existe=False)
            return _Locator(p, seletor, texto=p.perfil.papel)

        if seletor == "#avatar-dropdown-trigger":
            return _Locator(p, seletor)          # existe SEMPRE, desde o login

        if seletor == "#input-representar-cpfcnpj":
            return _Locator(p, seletor)

        if seletor.startswith("xpath=") and seletor.endswith("/button"):
            return _Locator(p, "botao-representar")

        if seletor.startswith("xpath="):
            return _Locator(p, seletor)          # ng-select

        return _Locator(p, seletor, existe=False)

    def get_by_role(self, _papel, name=None):
        return _Locator(self.portal, f"role:{name}")
