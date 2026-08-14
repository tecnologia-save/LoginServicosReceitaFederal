"""Login nos Servicos da Receita Federal.

Alem do login, a API publica expoe o contrato da INTERVENCAO MANUAL: o portal
apresenta um captcha ao representar um CNPJ, e esse desafio nao e automatizado.
Quem integra injeta `on_manual_challenge` e recebe de volta uma das respostas
abaixo; as excecoes distinguem os desfechos que NAO sao sucesso.
"""
from .login import (
    CANCELAR,
    CONTINUAR,
    EXPIRADO,
    FalhaDoResolvedorCaptcha,
    RepresentacaoCancelada,
    RepresentacaoExpirada,
    RepresentacaoNaoConfirmada,
    RepresentacaoRequerIntervencao,
)
from .login import main as fazer_login

__all__ = [
    "CANCELAR",
    "CONTINUAR",
    "EXPIRADO",
    "FalhaDoResolvedorCaptcha",
    "RepresentacaoCancelada",
    "RepresentacaoExpirada",
    "RepresentacaoNaoConfirmada",
    "RepresentacaoRequerIntervencao",
    "fazer_login",
]
