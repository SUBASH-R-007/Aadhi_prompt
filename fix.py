import os
d = 'final_videos'
if os.path.exists(d):
    for f in os.listdir(d):
        if f.endswith('.html'):
            p = os.path.join(d, f)
            with open(p, 'r', encoding='utf-8') as file:
                content = file.read()
            if '<base href="/">' not in content:
                content = content.replace('<head>', '<head>\n    <base href="/">')
                with open(p, 'w', encoding='utf-8') as file:
                    file.write(content)
                print('Fixed', f)
