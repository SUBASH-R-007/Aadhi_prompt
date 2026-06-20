import sys

with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

start_str = 'let currentSlide = 0;'
start_idx = content.find(start_str)

# Find the REAL end of the inline script, which is right before the slide-scrubber-container
end_str = '<div id="slide-scrubber-container"'
end_idx = content.rfind(end_str)

if start_idx == -1 or end_idx == -1:
    print("Could not find bounds")
    sys.exit(1)

# Extract the inline script
inline_script = content[start_idx:end_idx]

# Replace any </script> with <\/script>
new_inline_script = inline_script.replace('</script>', '<\\/script>')

if new_inline_script != inline_script:
    new_content = content[:start_idx] + new_inline_script + content[end_idx:]
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Fixed multiple unescaped script tags!")
else:
    print("No unescaped tags found.")
