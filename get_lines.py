from pathlib import Path
text = Path('scripts/cadastro-empresas.js').read_text(encoding='utf-8')
for i,line in enumerate(text.splitlines(),1):
    if 30 <= i <= 60:
        print(f"{i:03}: {line}")
