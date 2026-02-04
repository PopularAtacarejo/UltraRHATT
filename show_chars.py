from pathlib import Path
text = Path('scripts/cadastro-empresas.js').read_text(encoding='utf-8', errors='replace')
snippet = text.split('function updateResultPanel')[1]
print(snippet[:600])
