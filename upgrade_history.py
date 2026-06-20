import os
import glob

FINAL_VIDEOS_DIR = "final_videos"
TEMPLATE_FILE = "index.html"

# Read the latest, fixed template
with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
    template_content = f.read()

files = glob.glob(os.path.join(FINAL_VIDEOS_DIR, "*.html"))
upgraded_count = 0

start_marker = '<script id="injected-data">window.INJECTED_DATA = '
end_marker = ';</script>'

for file_path in files:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            old_content = f.read()
            
        if start_marker in old_content:
            part2 = old_content.split(start_marker)[1]
            json_data = part2.split(end_marker)[0]
            
            # Inject into new template
            injected_script = f'<base href="/"><script id="injected-data">window.INJECTED_DATA = {json_data};</script>'
            
            if '<script id="injected-data">window.INJECTED_DATA = null;</script>' in template_content:
                new_html = template_content.replace('<script id="injected-data">window.INJECTED_DATA = null;</script>', injected_script)
            else:
                # Fallback if the placeholder doesn't exist, insert after <head>
                new_html = template_content.replace('<head>', f'<head>\n    {injected_script}')
                
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_html)
            upgraded_count += 1
            print(f"Upgraded {file_path}")
        else:
            print(f"Could not find INJECTED_DATA in {file_path}")
    except Exception as e:
        print(f"Failed to process {file_path}: {e}")

print(f"\nSuccessfully upgraded {upgraded_count} out of {len(files)} history videos.")
