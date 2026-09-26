"""Publish a verified application-only layer over the immutable activated image.

Uses the existing private registry credentials. No dependency, IAM, runtime
configuration or base-package change. Activation still uses rollout's scan gate.
"""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from rollout import APP, Rollout, private_json
from verify_image_repackage import config, exported_inventory


def command(args):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError("Image operation failed; provider output suppressed. Reconcile the saved target before retrying.")
    return result.stdout.strip()


def prepare(r):
    current = r.status()
    if not current.get("verifiedDigest"):
        raise ValueError("The base must be the verified active image.")
    import os
    result = subprocess.run(["npm", "run", "build"], cwd=APP / "frontend",
                            env={**os.environ, "APP_BASE_PATH": urlsplit(r.j["applicationURL"]).path})
    if result.returncode:
        raise ValueError("Frontend build failed.")
    files = {"app/" + name: APP.parent / name for name in (
        "geniagent/transport.py", "geniagent/oci_runtime.py",
        "portfolio-advisor/portfolio_advisor/remote.py", "portfolio-advisor/portfolio_advisor/worker.py",
        "portfolio-advisor/portfolio_advisor/api.py", "portfolio-advisor/portfolio_advisor/auth.py",
        "portfolio-advisor/portfolio_advisor/access.py", "portfolio-advisor/portfolio_advisor/db.py")}
    dist = APP / "frontend/dist"
    files.update({"app/portfolio-advisor/frontend/dist/" + str(p.relative_to(dist)): p
                  for p in dist.rglob("*") if p.is_file()})
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(files.items())}
    base = r.j["expectedImage"]["containerUri"] + "@" + current["verifiedDigest"]
    identity = hashlib.sha256(json.dumps([base, hashes], sort_keys=True).encode()).hexdigest()
    folder = r.path.parent / ("patch-" + identity[:16])
    folder.mkdir(exist_ok=True, mode=0o700)
    archive = folder / "application.tar"
    with tarfile.open(archive, "w") as tar:
        # Hide superseded bundled assets while retaining all unchanged runtime files.
        marker = tarfile.TarInfo("app/portfolio-advisor/frontend/dist/.wh..wh..opq")
        marker.mode, marker.uid, marker.gid = 0o644, 10001, 10001
        tar.addfile(marker, io.BytesIO())
        for name, path in sorted(files.items()):
            body = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid = len(body), 0o644, 10001, 10001
            tar.addfile(info, io.BytesIO(body))
    record = {"base": base, "target": r.j["expectedImage"]["containerUri"] + ":fix-" + identity[:16],
              "files": hashes, "archiveSha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "state": "prepared", "created": time.time()}
    path = folder / "journal.json"
    if path.exists():
        old = json.loads(path.read_text())
        if any(old[k] != record[k] for k in ("base", "target", "files", "archiveSha256")):
            raise ValueError("Patch identity differs from its saved journal.")
    else:
        private_json(path, record)
    return path


def publish(path):
    action = json.loads(path.read_text())
    archive, crane = path.parent / "application.tar", APP / "deploy/private/tools/crane"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != action["archiveSha256"]:
        raise ValueError("Patch archive changed.")
    if action["state"] == "prepared":
        action.update(state="publishing", started=time.time()); private_json(path, action)
        command([crane, "append", "--base", action["base"], "--new_layer", archive, "--new_tag", action["target"]])
    # An uncertain push is inspected, never republished under another tag.
    digest = command([crane, "digest", action["target"]])
    action.update(digest=digest, state="published"); private_json(path, action)
    return action


def verify(path):
    action = json.loads(path.read_text()); crane = APP / "deploy/private/tools/crane"
    target = action["target"].rsplit(":", 1)[0] + "@" + action["digest"]
    before, after = config(crane, action["base"]), config(crane, target)
    if before["config"] != after["config"] or any(before.get(k) != after.get(k) for k in ("os", "architecture", "variant")):
        raise ValueError("Runtime configuration or platform changed.")
    a, b = exported_inventory(crane, action["base"]), exported_inventory(crane, target)
    prefix = "app/portfolio-advisor/frontend/dist/"
    for name in set(a["files"]) | set(b["files"]):
        if name in action["files"]:
            item = b["files"].get(name, {})
            if item.get("sha256") != action["files"][name] or item.get("uid") != 10001 or item.get("mode") != 0o644:
                raise ValueError("Published application file differs from the reviewed patch.")
        elif name.startswith(prefix) and name not in b["files"]:
            continue  # Superseded frontend asset removed by the opaque directory.
        elif a["files"].get(name) != b["files"].get(name):
            raise ValueError("Unexpected filesystem change outside the reviewed patch: " + name)
    if a["packages"] != b["packages"] or after["config"].get("User") != "10001:10001":
        raise ValueError("Base package inventory or runtime user changed.")
    action.update(state="verified", verified=time.time(), unchangedRuntime=True, unchangedPackages=True)
    private_json(path, action)
    return {k: action[k] for k in ("base", "target", "digest", "state", "unchangedRuntime", "unchangedPackages")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "publish", "verify"))
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        print(prepare(Rollout(APP / "deploy/private/vault-bootstrap.json", "Sandbox")))
    else:
        if not args.journal or not args.journal.resolve().is_relative_to(APP / "deploy/private"):
            raise SystemExit("Use the prepared private patch journal.")
        print(json.dumps(globals()[args.action](args.journal), indent=2))
