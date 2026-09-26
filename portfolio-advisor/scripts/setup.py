"""Install locked application dependencies using the user-provided preview wheel."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

app=Path(__file__).resolve().parents[1]
manifest=json.loads((app/'vendor/manifest.json').read_text())['sdk']
target=app/'vendor'/manifest['filename']
source=app.parent/manifest['filename']
if source.exists():
    if hashlib.sha256(source.read_bytes()).hexdigest()!=manifest['sha256']:
        raise SystemExit('Repository SDK wheel differs from the reviewed manifest. Review it before updating the lock.')
    shutil.copyfile(source,target)
if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=manifest['sha256']:
    raise SystemExit('Add the approved Oracle preview wheel named in vendor/manifest.json to the repository root.')
subprocess.run([sys.executable,'-m','venv',str(app/'.venv')],check=True)
python=str(app/'.venv/bin/python')
subprocess.run([python,'-m','pip','install','--require-hashes','--find-links',str(app/'vendor'),'-r',str(app/'requirements.lock')],check=True)
subprocess.run([python,str(app/'scripts/check_oci_capabilities.py')],check=True)
subprocess.run(['npm','ci'],cwd=app/'frontend',check=True)
subprocess.run(['npm','run','build'],cwd=app/'frontend',check=True)
