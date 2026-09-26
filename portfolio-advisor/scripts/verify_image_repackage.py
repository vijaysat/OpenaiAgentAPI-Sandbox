"""Compare a repackaged image with its source without running or extracting it."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from rollout import Rollout, private_json

APP = Path(__file__).resolve().parents[1]


def inventory(stream):
    files, packages = {}, {}
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            name = member.name.removeprefix("./").lstrip("/")
            if name in files:
                raise ValueError("Duplicate path in merged filesystem export")
            entry = {"type": member.type.decode("ascii"), "mode": member.mode,
                     "uid": member.uid, "gid": member.gid, "size": member.size,
                     "link": member.linkname, "mtime": member.mtime,
                     "pax": member.pax_headers}
            if member.isfile():
                digest = hashlib.sha256()
                source = archive.extractfile(member)
                package_data = bytearray() if name == "var/lib/dpkg/status" else None
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    if package_data is not None:
                        package_data.extend(chunk)
                entry["sha256"] = digest.hexdigest()
                if package_data is not None:
                    for stanza in package_data.decode().split("\n\n"):
                        values = dict(line.split(": ", 1) for line in stanza.splitlines()
                                      if ": " in line and not line.startswith(" "))
                        if values.get("Package") in ("openssl", "libssl3"):
                            packages[values["Package"]] = {"version": values.get("Version"),
                                                          "status": values.get("Status")}
            files[name] = entry
    return {"files": files, "packages": packages}


def exported_inventory(crane, reference):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(crane), "export", reference, "-"],
                                   stdout=subprocess.PIPE, stderr=errors)
        try:
            result = inventory(process.stdout)
            if process.wait(timeout=30) != 0:
                raise ValueError("Image export failed; provider output suppressed")
            return result
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()


def config(crane, reference):
    result = subprocess.run([str(crane), "config", reference], capture_output=True, text=True)
    if result.returncode:
        raise ValueError("Image configuration read failed; provider output suppressed")
    return json.loads(result.stdout)


def verify(r):
    action = r.j["image_repackage"]
    source = action["source"]
    target = source.split("@")[0] + "@" + action["target_digest"]
    crane = APP / "deploy/private/tools/crane"
    before, after = config(crane, source), config(crane, target)
    if before["config"] != after["config"]:
        raise ValueError("Image runtime configuration changed")
    if any(before.get(k) != after.get(k) for k in ("os", "architecture", "variant")):
        raise ValueError("Image platform changed")
    if len(after["rootfs"]["diff_ids"]) != 1 or after["config"]["User"] != "10001:10001":
        raise ValueError("Single-layer or non-root constraint failed")
    a, b = exported_inventory(crane, source), exported_inventory(crane, target)
    private_json(r.path.parent / "repackage-source-files.json", a)
    private_json(r.path.parent / "repackage-target-files.json", b)
    if a != b:
        raise ValueError("Merged files, ownership, permissions or package inventory changed")
    for package in ("openssl", "libssl3"):
        if b["packages"].get(package) != {"version": "3.0.22-1~deb12u1", "status": "install ok installed"}:
            raise ValueError("Expected patched package inventory was not found")
    result = {"source": source, "target": target, "filesystem_identical": True,
              "runtime_configuration_identical": True, "layer_count": 1,
              "file_count": len(b["files"]), "packages": b["packages"]}
    private_json(r.path.parent / "repackage-verification.json", result)
    r.j["image_repackage"].update(state="VERIFIED", verification=result)
    r.save()
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(verify(Rollout(APP / "deploy/private/vault-bootstrap.json", "Sandbox")), indent=2))
    except Exception as exc:
        raise SystemExit(f"Repackage verification failed ({type(exc).__name__}); activation remains blocked.") from None
