import sys

with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

target = '<script src="app.js" defer></script>'
replacement = '<script src="app.js" defer><\\/script>'

if target in content:
    new_content = content.replace(target, replacement)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('Fixed!')
else:
    print('Target not found.')
