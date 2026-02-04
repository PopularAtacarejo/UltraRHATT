# -*- coding: utf-8 -*-
from pathlib import Path

path = Path('funcionarios-ativos.html')
text = path.read_text(encoding='utf-8')
text = text.replace('\r\n', '\n')

marker = 'data-action="toggle-edit"'
while marker in text:
    idx = text.index(marker)
    start = text.rfind('<button', 0, idx)
    end = text.find('</button>', idx)
    if start == -1 or end == -1:
        break
    end += len('</button>')
    text = text[:start] + text[end:]

edit_marker = '<div class="detail-edit-column">'
if edit_marker in text:
    start = text.index(edit_marker)
    tail = '                </div>\n            </div>\n        </div>\n    </div>\n'
    end = text.find(tail, start)
    if end != -1:
        text = text[:start] + text[end + len(tail):]

path.write_text(text, encoding='utf-8')
