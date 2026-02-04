from pathlib import Path
lines = Path('scripts/atualizar-funcionario.js').read_text().splitlines()
start = max(0, len(lines) - 80)
for i,line in enumerate(lines[start:], start+1):
    print(f"{i:03d}: {line}")
