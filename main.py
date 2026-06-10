"""Script de teste — Login nos Serviços da Receita Federal.

Edite CERT_NAME abaixo (ou configure o .env) e execute:
    .venv\Scripts\python main.py
"""
from servicos_rf_login import fazer_login

# ── Opção A: nome do certificado (recomendado) ─────────────────────────────
# O código busca em C:\Certificados e lê a senha do senhas.json.
# Exemplos: "DSR", "Save Tecnologia", "Cristiano", "GSH"
resultado = fazer_login(cert_name="DSR")

# ── Opção B: lê CERT_NAME do .env (descomente e comente a opção A) ─────────
# resultado = fazer_login()

# ── Opção C: caminho e senha explícitos ────────────────────────────────────
# resultado = fazer_login(
#     cert_pfx_path=r"C:\Certificados\DSR.pfx",
#     cert_pfx_passphrase="123456",
# )

# ── Resultado ──────────────────────────────────────────────────────────────
if resultado is None:
    print("\n[FALHA] Login não concluído.")
else:
    p, context, page = resultado
    print(f"\n[OK] Login concluído! URL: {page.url}")

    input("\nPressione ENTER para fechar o navegador...")
    context.close()
    p.stop()
