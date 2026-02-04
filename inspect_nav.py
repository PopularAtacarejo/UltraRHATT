from pathlib import Path
text = Path('cadastro-funcionarios.html').read_text(encoding='utf-8', errors='replace')
for idx,line in enumerate(text.splitlines(),1):
    if 'Cadastro de' in line or 'Configura' in line:
        print(idx, repr(line))
