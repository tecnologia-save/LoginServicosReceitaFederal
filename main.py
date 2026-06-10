"""Script de exemplo — Login nos Serviços da Receita Federal.

Edite CERT_NAME e CNPJ abaixo (ou configure o .env) e execute:
    .venv\Scripts\python main.py
"""
from servicos_rf_login import fazer_login

# ── Login simples (sem representar CNPJ) ──────────────────────────────────
resultado = fazer_login(cert_name="DSR")

# ── Login + representação de CNPJ como Procurador ─────────────────────────
# resultado = fazer_login(cert_name="DSR", cnpj="12345678000190")

# ── Via .env (CERT_NAME configurado no .env) ─────────────────────────────
# resultado = fazer_login(cnpj="12345678000190")

# ── Via caminho explícito ─────────────────────────────────────────────────
# resultado = fazer_login(
#     cert_pfx_path=r"C:\Certificados\DSR.pfx",
#     cert_pfx_passphrase="123456",
#     cnpj="12345678000190",
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
