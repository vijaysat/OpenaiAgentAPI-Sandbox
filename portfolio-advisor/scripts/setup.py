"""Install the public SDK and application dependencies from the hashed lock."""
from pathlib import Path
import subprocess
import sys

app=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,'-m','venv',str(app/'.venv')],check=True)
python=str(app/'.venv/bin/python')
subprocess.run([python,'-m','pip','install','--require-hashes','-r',str(app/'requirements.lock')],check=True)
subprocess.run([python,str(app/'scripts/check_oci_capabilities.py')],check=True)
subprocess.run(['npm','ci'],cwd=app/'frontend',check=True)
subprocess.run(['npm','run','build'],cwd=app/'frontend',check=True)
