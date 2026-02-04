from pathlib import Path
text = Path('scripts/cadastro-empresas.js').read_text(encoding='utf-8', errors='replace')
lines = text.splitlines()
for i in range(40,70):
    print(i+1,repr(lines[i]))
