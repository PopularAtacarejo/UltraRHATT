from pathlib import Path
text = Path('scripts/cadastro-empresas.js').read_text(encoding='utf-8')
def find_non_ascii(s):
    return [(i,c) for i,c in enumerate(s) if ord(c)>127]

chars = find_non_ascii(text)
print('count', len(chars))
print(chars[:20])
