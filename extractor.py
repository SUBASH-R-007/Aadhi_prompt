import json

with open('index.html', 'r', encoding='utf-8') as f:
    html = f.read()

start_str = 'let currentSlide = 0;'
start_idx = html.find(start_str)
end_idx = html.find('</script>', start_idx)

script = html[start_idx:end_idx]

with open('app_test.js', 'w', encoding='utf-8') as f:
    f.write('const window = {};\n')
    f.write(script)
