# Login Serviços Receita Federal

Pacote Python para login automatizado no portal **Serviços da Receita Federal** via SSO gov.br, com suporte a certificado digital A1 (`.pfx`) e resolução automática de hCaptcha via Google Gemini.

Retorna uma instância de navegador já autenticada, pronta para uso em outras automações.

---

## Funcionalidades

- Login com certificado digital A1 (`.pfx`) via [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
- **Resolução automática de hCaptcha** com Google Gemini 2.5 Flash
  - Grade 3×3 (imagem única dividida em tiles)
  - Grade fused (imagem única sobreposta)
  - Imagem completa com coordenadas
  - **Cartas com animal** (`cartao_animal`) — grid 2×2 animado
- **Movimento de mouse durante o captcha** para evitar detecção de automação
- **Recuperação automática** quando o site bloqueia o acesso ("acesso automatizado detectado")
- Busca automática do certificado por nome em `C:\Certificados` com match fuzzy
- Leitura automática da senha a partir de `C:\Certificados\senhas.json`
- Suporte a quatro formas de informar o certificado: nome, `.env`, planilha ou formulário
- Detecção de sessão ativa — pula o login se o navegador já estiver autenticado
- Log automático de erros com data e hora em arquivo `.txt` diário

---

## Requisitos

- Python 3.10+
- Google Chrome instalado
- Certificado digital A1 (`.pfx`) em `C:\Certificados`
- Arquivo `C:\Certificados\senhas.json` com as senhas (ver formato abaixo)
- Chave de API do Google Gemini (`GEMINI_API_KEY`)
- Pacote [`captcha_uipath`](https://github.com/tecnologia-save/CaptchaSolver) instalado

---

## Instalação

### 1. Clone o repositório

```bash
git clone https://github.com/tecnologia-save/LoginServicosReceitaFederal.git
cd LoginServicosReceitaFederal
```

### 2. Crie e ative o ambiente virtual

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Instale o pacote e dependências

```bash
pip install -e .
```

> O pacote `captcha_uipath` deve estar instalado no mesmo ambiente. Consulte o repositório [CaptchaSolver](https://github.com/tecnologia-save/CaptchaSolver) para instruções de instalação.

### 4. Instale os binários do Patchright

```bash
patchright install chromium
```

---

## Configuração

### Arquivo `.env`

Crie um `.env` na raiz do projeto (use `.env.example` como base):

```env
# Opção 1 (recomendada): nome do certificado em C:\Certificados
# A senha é lida automaticamente do C:\Certificados\senhas.json
CERT_NAME=Save Tecnologia

# Opção 2: caminho direto para o .pfx
# CERT_PFX_PATH=C:\Certificados\meu_certificado.pfx
# CERT_PFX_PASSPHRASE=senha_do_certificado

# Chave do Google Gemini (necessária para resolver o hCaptcha)
GEMINI_API_KEY=sua_chave_gemini_aqui
```

### Arquivo `senhas.json`

Crie `C:\Certificados\senhas.json` com o mapeamento de `nome_do_arquivo.pfx → senha`:

```json
{
  "DSR.pfx": "senha123",
  "Save Tecnologia.pfx": "outra_senha",
  "Cristiano Vasconcelos.pfx": "senha456"
}
```

---

## Como usar

### Uso básico

```python
from servicos_rf_login import fazer_login

resultado = fazer_login(cert_name="DSR")

if resultado is None:
    print("Login falhou.")
else:
    p, context, page = resultado
    # Use `page` para navegar nos Serviços RF
    page.goto("https://servicos.receita.fazenda.gov.br/...")
    # Ao terminar, feche o navegador
    context.close()
    p.stop()
```

### Por nome (recomendado)

Aceita nome exato, parcial ou com pequenos erros de digitação:

```python
fazer_login(cert_name="DSR")
fazer_login(cert_name="Save")            # bate em "Save Tecnologia.pfx"
fazer_login(cert_name="save tec")        # match parcial
fazer_login(cert_name="Crisitano")       # typo → bate em "Cristiano Vasconcelos.pfx"
```

### Via `.env` (sem parâmetros)

```python
fazer_login()  # lê CERT_NAME do .env
```

### Via planilha

```python
import pandas as pd
from servicos_rf_login import fazer_login

df = pd.read_excel("tarefas.xlsx")
linha = df.iloc[0]

resultado = fazer_login(cert_name=linha["Certificado"])
# ou com caminho explícito:
resultado = fazer_login(
    cert_pfx_path=linha["Caminho_PFX"],
    cert_pfx_passphrase=linha["Senha_PFX"],
)
```

### Via formulário

```python
from servicos_rf_login import fazer_login

resultado = fazer_login(
    cert_pfx_path=formulario.get("cert_path"),
    cert_pfx_passphrase=formulario.get("cert_pass"),
)
```

---

## Parâmetros de `fazer_login()`

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `cert_name` | `str` | Nome (ou parte) do certificado em `C:\Certificados`. Aceita match parcial e fuzzy. A senha é lida do `senhas.json`. |
| `cert_pfx_path` | `str` | Caminho absoluto para o `.pfx`. Requer `cert_pfx_passphrase`. |
| `cert_pfx_passphrase` | `str` | Senha do `.pfx` informado em `cert_pfx_path`. |
| `project_dir` | `Path\|str` | Diretório do projeto. Salva o perfil do Chrome e logs. Padrão: `Path.cwd()`. |

**Prioridade de resolução do certificado:**

```
1. cert_pfx_path + cert_pfx_passphrase  (parâmetros explícitos)
2. cert_name                             (busca em C:\Certificados)
3. CERT_NAME no .env                     (busca em C:\Certificados)
4. CERT_PFX_PATH + CERT_PFX_PASSPHRASE  (caminho direto no .env)
```

### Retorno

- **Sucesso:** tupla `(p, context, page)`
  - `p` — instância do Playwright
  - `context` — contexto persistente do navegador
  - `page` — página autenticada nos Serviços RF, pronta para uso
- **Falha:** `None`

---

## Como funciona — fluxo completo

```
1. Resolve o certificado (parâmetros → cert_name → .env)
2. Abre o Chrome com perfil persistente + certificado A1 embutido
3. Navega para https://sso.acesso.gov.br/login?client_id=p-servicos.receitafederal.gov.br
   │
   ├── Sessão ativa detectada? → retorna imediatamente
   │
   └── Sem sessão:
       4. Clica em "Entrar com gov.br"
       5. Resolve hCaptcha se aparecer
          └── Movimento de mouse em background durante a resolução
       6. Verifica bloqueio por "acesso automatizado"
          └── Se bloqueado: go back → re-clica "Entrar com gov.br" → resolve captcha
       7. Clica em "Seu certificado digital"
       8. Resolve hCaptcha se aparecer (até 3 tentativas)
          └── Movimento de mouse em background durante a resolução
       9. Verifica bloqueio novamente e recupera se necessário
      10. Aguarda redirecionamento para servicos.receita.fazenda.gov.br (até 60s)
      11. Retorna (p, context, page) autenticados
```

---

## Resolução de hCaptcha

O hCaptcha é resolvido automaticamente pelo pacote `captcha_uipath` usando o **Google Gemini 2.5 Flash**. Quatro tipos de desafio são suportados:

### Tipos de captcha

| Tipo | Descrição | Estratégia |
|---|---|---|
| `grade` | Grade 3×3 onde cada tile é parte de uma imagem única | Screenshot do iframe → Gemini identifica os 9 tiles por posição |
| `grade_fused` | Imagem única com grid 3×3 sobreposto | Screenshot + recorte da área de tiles → Gemini retorna índices 0–8 |
| `imagem` | Imagem com coordenadas de clique | Screenshot com grid 20×20 → Gemini retorna posições col/row |
| `cartao_animal` | Grid 2×2 animado — cartas viram revelando animais, uma é diferente | 12 screenshots do iframe a cada 0,5s (6s total) → todos enviados ao Gemini → clique por coordenada |

### Tipo `cartao_animal` em detalhe

Este tipo é o mais complexo: as 4 cartas revelam animais em sequência (uma de cada vez, ~1s cada), então nunca estão todas visíveis ao mesmo tempo.

A estratégia:
1. Captura 12 frames do iframe em 6 segundos (2 frames/segundo)
2. Envia todos os frames ao Gemini como sequência de imagens
3. O Gemini identifica o animal de cada posição e qual é o único diferente
4. O clique usa `page.mouse.click()` com coordenadas absolutas calculadas a partir do `bounding_box()` do iframe + posições percentuais calibradas de cada carta

### Anti-detecção

Durante toda a resolução do captcha, uma thread de background move o mouse em trajetórias suaves e aleatórias pela tela. Isso evita que o mouse fique estático enquanto o captcha é processado — comportamento que o hCaptcha usa como sinal de automação.

---

## Match de certificado por nome

Três estratégias em cascata:

| Estratégia | Exemplo |
|---|---|
| **Exato** (case-insensitive) | `"DSR"` → `DSR.pfx` |
| **Substring bidirecional** | `"save"` → `Save Tecnologia.pfx` |
| **Fuzzy** (difflib ≥ 40%) | `"Crisitano"` → `Cristiano Vasconcelos.pfx` |

Se houver múltiplos matches de substring, escolhe o com tamanho de nome mais próximo ao informado.

---

## Logs de erro

Erros são salvos automaticamente em `logs/DD-MM-AAAA_servicos_rf.txt` no diretório do projeto.

---

## Estrutura do projeto

```
LoginServicosReceitaFederal/
├── servicos_rf_login/
│   ├── __init__.py       # Exporta fazer_login()
│   ├── login.py          # Fluxo principal de autenticação
│   └── log_manager.py    # Registro de erros em arquivo .txt
├── main.py               # Script de exemplo
├── pyproject.toml        # Metadados e dependências do pacote
├── .env.example          # Template de configuração
├── .gitignore
└── README.md
```

---

## Dependências

| Biblioteca | Uso |
|---|---|
| `patchright` | Automação do navegador com suporte a certificados digitais A1 |
| `python-dotenv` | Carregamento das variáveis do `.env` |
| `captcha_uipath` | Resolução de hCaptcha via Google Gemini |

> `difflib`, `threading`, `random` e `json` são da biblioteca padrão do Python.

---

## Observações

- O `senhas.json` em `C:\Certificados` **nunca deve ser commitado** — já está no `.gitignore`.
- O **perfil persistente do Chrome** fica em `chrome_debug_profile/` e mantém cookies e sessões entre execuções — também ignorado pelo git.
- O pacote foi desenvolvido e testado em **Windows** com Google Chrome.
- A `GEMINI_API_KEY` é necessária apenas quando o hCaptcha aparece; se a sessão ainda estiver ativa no perfil do Chrome, o login ocorre sem nenhuma chamada à API.
