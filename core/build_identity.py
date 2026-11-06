"""Identify the loaded application, including trading modules and browser assets."""
import hashlib
from pathlib import Path

def build_identity(root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parents[1]
    paths=[root/'run_console.py']
    for directory in ('core','config','web_ui'):
        paths.extend(p for p in (root/directory).rglob('*') if p.is_file() and p.suffix in ('.py','.js','.html','.css'))
    digest=hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode());digest.update(b'\0');digest.update(path.read_bytes());digest.update(b'\0')
    return digest.hexdigest()[:16]
