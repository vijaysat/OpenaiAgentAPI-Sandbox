"""Run the API and two supervised workers; --rehearsal uses explicit synthetic data."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearsal", action="store_true")
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(APP), str(APP.parent)]))
    if args.rehearsal:
        local = APP / ".local"
        local.mkdir(exist_ok=True)
        env.update(PORTFOLIO_REHEARSAL="1", APP_ENV="local", APP_HOST="127.0.0.1", APP_BASE_PATH="",
                   APP_ORIGIN=f"http://127.0.0.1:{args.port}", LOCAL_ARTIFACT_DIR=str(local / "artifacts"),
                   AUTH_SESSION_SECRET="local-rehearsal-only-session-key-00000000",
                   DATABASE_URL=env.get("REHEARSAL_DATABASE_URL", "postgresql+psycopg://portfolio:local-rehearsal-only@127.0.0.1:55439/portfolio"))
    os.environ.update(env)
    from portfolio_advisor.config import Settings
    from portfolio_advisor.db import Repository
    settings = Settings.load()
    Repository(settings.get("DATABASE_URL")).initialize()
    commands = [[sys.executable, "-m", "uvicorn", "portfolio_advisor.api:create_app", "--factory", "--host", "127.0.0.1", "--port", str(args.port), "--no-access-log"],
                *[[sys.executable, "-m", "portfolio_advisor.worker"] for _ in range(2)]]
    processes = [subprocess.Popen(command, cwd=APP, env=env) for command in commands]
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Portfolio Advisor: http://127.0.0.1:{args.port} ({'SYNTHETIC REHEARSAL' if args.rehearsal else 'live configuration'})", flush=True)
    try:
        while not stopping and all(p.poll() is None for p in processes):
            time.sleep(.5)
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
        if not stopping:
            raise SystemExit("A child process exited; all services stopped.")


if __name__ == "__main__":
    main()
