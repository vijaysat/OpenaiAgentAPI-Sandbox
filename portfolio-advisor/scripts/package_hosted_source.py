"""Export only this application's reviewed sources for its private OCI build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SKIP = {'.git', '.venv', '.venv-oci', '__pycache__', 'node_modules', 'dist',
        'private', '.local', '.pytest_cache', '.terraform', 'vendor'}
SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.json', '.toml', '.lock', '.yaml',
            '.yml', '.css', '.svg', '.html', '.md', '.txt', '.sql', '.sh', '.tf'}


def entries(root):
    yield 'LICENSE', (root / 'LICENSE').read_bytes()
    yield 'scripts/api.sh', (root / 'scripts/api.sh').read_bytes()
    for selected in ('geniagent', 'portfolio-advisor'):
        for directory, names, files in os.walk(root / selected, followlinks=False):
            names[:] = [name for name in names if name not in SKIP and not name.endswith('.egg-info')]
            for name in sorted(files):
                path = Path(directory) / name; relative = path.relative_to(root)
                if name.startswith('.env') or name.endswith(('.tfvars', '.tfvars.json', '.tfstate', '.tsbuildinfo')):
                    continue
                if path.suffix not in SUFFIXES and name not in ('Dockerfile', 'Dockerfile.dockerignore', 'postgres-ca.pem'):
                    continue
                if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
                    raise ValueError('Source export refuses symbolic links.')
                data = path.read_bytes()
                if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', data):
                    raise ValueError('Private key material found; source export stopped.')
                yield relative.as_posix(), data


def package(root, output):
    files = sorted(entries(root))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED; info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    output.chmod(0o600)
    return {'files': len(files), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'portfolio-advisor/deploy/private/source.zip')
    args = parser.parse_args()
    print(json.dumps(package(ROOT, args.output)))
