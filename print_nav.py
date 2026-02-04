from pathlib import Path
text = Path('cadastro-funcionarios.html').read_text(encoding='utf-8')
for line in text.splitlines():
    if 'Cadastro' in line or 'Configura' in line:
        print(repr(line))
