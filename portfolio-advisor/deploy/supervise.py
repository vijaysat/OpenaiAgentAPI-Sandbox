"""Container PID 1: validate once, supervise one API and two durable workers."""
import os
import signal
import subprocess
import sys
import time
from portfolio_advisor.config import Settings, ConfigurationError
from portfolio_advisor.db import Repository

def main():
    try:
        settings = Settings.load()
        Repository(settings.get('DATABASE_URL')).initialize()
    except Exception as exc:
        print(str(exc) if isinstance(exc, ConfigurationError) else 'Startup failed: check database connectivity and Vault permissions.', file=sys.stderr)
        return 1
    processes = [subprocess.Popen([sys.executable, '-m', 'uvicorn', 'portfolio_advisor.api:create_app', '--factory',
                    '--host', '0.0.0.0', '--port', os.getenv('PORT', '8080'), '--no-access-log']),
                 *[subprocess.Popen([sys.executable, '-m', 'portfolio_advisor.worker']) for _ in range(2)]]
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping and all(p.poll() is None for p in processes): time.sleep(.5)
    finally:
        for p in processes:
            if p.poll() is None: p.terminate()
        deadline = time.monotonic() + 25
        for p in processes:
            try: p.wait(timeout=max(.1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired: p.kill(); p.wait()
    return 0 if stopping else 1

if __name__ == '__main__': sys.exit(main())
