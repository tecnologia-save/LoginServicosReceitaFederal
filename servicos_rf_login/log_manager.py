"""Gerenciador de log. Salva erros em logs/DD-MM-YYYY_servicos_rf.txt no diretório do projeto."""
from datetime import datetime
from pathlib import Path


def registrar_erro(mensagem: str, log_dir: Path = None) -> None:
    if log_dir is None:
        log_dir = Path.cwd() / "logs"
    log_dir.mkdir(exist_ok=True)
    agora = datetime.now()
    nome_arquivo = agora.strftime("%d-%m-%Y") + "_servicos_rf.txt"
    entrada = f"[{agora.strftime('%d/%m/%Y %H:%M:%S')}] ERRO: {mensagem}\n"
    print(f"[LOG] {entrada.strip()}")
    with open(log_dir / nome_arquivo, "a", encoding="utf-8") as f:
        f.write(entrada)
