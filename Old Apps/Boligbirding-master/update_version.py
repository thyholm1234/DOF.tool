import os
import sys
import re
from datetime import datetime

TARGET_EXTENSIONS = {".html", ".css", ".js"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}


def find_target_files(base_dir):
    files = []
    for root, dirs, filenames in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in TARGET_EXTENSIONS:
                continue
            files.append(os.path.join(root, filename))
    return sorted(files)

def get_current_version(sw_path, html_path=None):
    # Prøv først sw.js
    if os.path.exists(sw_path):
        with open(sw_path, encoding='utf-8') as f:
            content = f.read()
        m = re.search(r"CACHE_NAME\s*=\s*['\"][a-zA-Z0-9_-]+-v([\d\.]+)['\"]", content)
        if m:
            return m.group(1)
        m = re.search(r"Version:\s*([\d\.]+)", content)
        if m:
            return m.group(1)
    # Prøv index.html hvis angivet
    if html_path and os.path.exists(html_path):
        with open(html_path, encoding='utf-8') as f:
            content = f.read()
        m = re.search(r"Version:\s*([\d\.]+)", content)
        if m:
            return m.group(1)
    return "1.0.0"

def bump_version(version, mode):
    parts = [int(x) for x in version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    if mode == "large":
        parts[0] += 1
        parts[1] = 0
        parts[2] = 0
    elif mode == "med":
        parts[1] += 1
        parts[2] = 0
    else:  # small/default
        parts[2] += 1
    return ".".join(str(x) for x in parts)

now = datetime.now()
DATE = now.strftime('%Y-%m-%d %H.%M.%S')

def version_line(ext, version):
    copyright_line = "© Christian Vemmelund Helligsø"
    if ext == '.html':
        return f'<!-- Version: {version} - {DATE} -->\n<!-- {copyright_line} -->'
    elif ext == '.webmanifest' or ext == '.json':
        return f'// Version: {version} - {DATE}\n// {copyright_line}'
    elif ext == '.css':
        return f'/* Version: {version} - {DATE} */\n/* {copyright_line} */'
    else:
        return f'// Version: {version} - {DATE}\n// {copyright_line}'

def update_file(filepath, version):
    ext = os.path.splitext(filepath)[1]
    if ext == '.manifest':
        print("Springer over .manifest:", filepath)
        return
    if not os.path.exists(filepath):
        print("Filen findes ikke:", filepath)
        return
    with open(filepath, encoding='utf-8') as f:
        content = f.read()
    # Opdater CACHE_NAME i sw.js med hele versionsnummeret
    if os.path.basename(filepath) == "sw.js":
        content = re.sub(
            r'(const CACHE_NAME\s*=\s*["\'])[^\']*(["\'];\s*)',
            rf'\1boligbirding-v{version}\2',
            content
        )
    if ext == '.webmanifest':
        content = re.sub(r'^(<!-- Version:.*?-->\s*|// Version:.*?\n)+', '', content, flags=re.MULTILINE)
        new_content = version_line(ext, version) + '\n' + content.lstrip()
    elif ext == '.html':
        new_content, n = re.subn(r'^<!-- Version:.*?-->\s*(<!--.*?-->\s*)?', version_line(ext, version) + '\n', content, count=1, flags=re.MULTILINE)
        if n == 0:
            new_content = version_line(ext, version) + '\n' + content
        def add_version_tag(match):
            url = match.group(1)
            if url.endswith('.webmanifest'):
                return f'{url}"'
            url = re.sub(r'\?v=[\d\.]+', '', url)
            return f'{url}?v={version}"'
        new_content = re.sub(
            r'(href="[^"]+\.(css))(?:\?v=[\d\.]+)?"',
            add_version_tag,
            new_content
        )
        new_content = re.sub(
            r'(src="[^"]+\.js)(?:\?v=[\d\.]+)?"',
            add_version_tag,
            new_content
        )
        new_content = re.sub(
            r'(href="[^"]+\.webmanifest)(?:\?v=[\d\.]+)?"',
            r'\1"',
            new_content
        )
        # --- Tilføj denne blok for at opdatere service worker-registrering ---
        new_content = re.sub(
            r"(navigator\.serviceWorker\.register\(\s*['\"]\/sw\.js)(\?v=[\d\.]+)?(['\"])",
            rf"\1?v={version}\3",
            new_content
        )
    elif ext == '.css':
        new_content, n = re.subn(r'^/\* Version:.*?\*/\s*(/\*.*?\*/\s*)?', version_line(ext, version) + '\n', content, count=1, flags=re.MULTILINE)
        if n == 0:
            new_content = version_line(ext, version) + '\n' + content
    else:
        new_content = re.sub(
            r'^(// Version:.*?(\r?\n))+((//.*?(\r?\n))*)',
            '',
            content,
            flags=re.MULTILINE
        )
        new_content = version_line(ext, version) + '\n' + new_content
    new_content = new_content.replace('\x0f', '')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('Opdateret:', filepath)

if __name__ == '__main__':
    base_dir = os.path.dirname(__file__)
    sw_path = os.path.join(base_dir, "web", "sw.js")
    html_path = os.path.join(base_dir, "web", "index.html")
    target_files = find_target_files(base_dir)

    if not target_files:
        print("Ingen .html/.css/.js filer fundet")
        sys.exit(0)

    # Find aktuel version
    current_version = get_current_version(sw_path, html_path)
    # Argument: version eller flag
    arg = sys.argv[1] if len(sys.argv) > 1 else "small"
    if arg in ("small", "med", "large"):
        new_version = bump_version(current_version, arg)
    else:
        new_version = arg
    print(f"Bumper version: {current_version} -> {new_version}")
    print(f"Opdaterer {len(target_files)} filer...")
    for file_path in target_files:
        update_file(file_path, new_version)
    print('Færdig!')