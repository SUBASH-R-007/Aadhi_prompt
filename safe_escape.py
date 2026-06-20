import re

with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Find the inline script block
start_str = 'let currentSlide = 0;'
end_str = '<!-- History Modal HTML -->'

start_idx = content.find(start_str)
end_idx = content.find(end_str)

if start_idx != -1 and end_idx != -1:
    # Get the portion to process
    script_block = content[start_idx:end_idx]
    
    # We want to replace all </script> EXCEPT the very last one which is the actual closing tag!
    # Let's just find the very last </script> in this block
    last_script_tag = script_block.rfind('</script>')
    
    if last_script_tag != -1:
        # Split into before the final tag and after the final tag
        before_final = script_block[:last_script_tag]
        after_final = script_block[last_script_tag:]
        
        # Escape all </script> in before_final
        escaped_before = before_final.replace('</script>', '<\\/script>')
        
        # Reconstruct
        new_script_block = escaped_before + after_final
        
        new_content = content[:start_idx] + new_script_block + content[end_idx:]
        
        with open('index.html', 'w', encoding='utf-8') as f:
            f.write(new_content)
        print('Escaped all internal script tags successfully!')
    else:
        print('No closing script tag found in block?!')
else:
    print('Could not find bounds.')
