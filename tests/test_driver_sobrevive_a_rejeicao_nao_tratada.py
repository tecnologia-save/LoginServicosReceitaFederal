"""O driver do patchright não morre por uma aba que fechou antes de ser configurada.

RUN-5d92fe06 (15/09/2026), ALEX ROCHA, e RUN-42bd82ab (14/09/2026), BAUMGARTEN:

    ProtocolError: Protocol error (Network.setCacheDisabled): Internal server
    error, session closed.  at CRNetworkManager._forEachSession

Rejeição não tratada dentro do driver; o Node encerra o processo e o Chrome vai
junto. Ver `login._driver_sobrevive_a_rejeicao_nao_tratada`.
"""
import inspect
import os
import subprocess
from pathlib import Path

import pytest

from servicos_rf_login import login

REJEICAO = ('Promise.reject(new Error("Protocol error (Network.setCacheDisabled): '
            'Internal server error, session closed.")); '
            'setTimeout(() => console.log("DRIVER VIVO"), 300);')


def test_acrescenta_a_opcao(monkeypatch):
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    login._driver_sobrevive_a_rejeicao_nao_tratada()
    assert os.environ["NODE_OPTIONS"] == "--unhandled-rejections=warn"


def test_preserva_o_que_ja_havia(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=4096")
    login._driver_sobrevive_a_rejeicao_nao_tratada()
    assert os.environ["NODE_OPTIONS"] == "--max-old-space-size=4096 --unhandled-rejections=warn"


def test_nao_duplica_nem_sobrescreve_escolha_explicita(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--unhandled-rejections=strict")
    login._driver_sobrevive_a_rejeicao_nao_tratada()
    login._driver_sobrevive_a_rejeicao_nao_tratada()
    assert os.environ["NODE_OPTIONS"] == "--unhandled-rejections=strict"


def test_vale_antes_de_subir_o_driver():
    fonte = inspect.getsource(login.main)
    assert (fonte.index("_driver_sobrevive_a_rejeicao_nao_tratada()")
            < fonte.index("sync_playwright().start()"))


def test_o_node_do_patchright_morre_sem_a_opcao_e_sobrevive_com_ela():
    """No binário de verdade que o patchright usa, e não numa suposição sobre o Node."""
    from patchright._impl._driver import compute_driver_executable

    node = compute_driver_executable()[0]
    if not Path(node).exists():
        pytest.skip(f"node do patchright não encontrado em {node}")
    sem = {k: v for k, v in os.environ.items() if k != "NODE_OPTIONS"}
    com = {**sem, "NODE_OPTIONS": login.OPCAO_NODE_REJEICAO_NAO_TRATADA}

    morto = subprocess.run([node, "-e", REJEICAO], env=sem, capture_output=True,
                           text=True, timeout=60)
    vivo = subprocess.run([node, "-e", REJEICAO], env=com, capture_output=True,
                          text=True, timeout=60)

    assert "DRIVER VIVO" not in morto.stdout and morto.returncode != 0, \
        "sem a opção o Node precisa morrer — senão este teste não prova nada"
    assert "DRIVER VIVO" in vivo.stdout and vivo.returncode == 0
