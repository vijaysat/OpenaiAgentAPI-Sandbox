import io
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_image_repackage import inventory


def archive(content=b"verified", mode=0o640, link="file", reverse=False):
    entries = [("file", content), ("link", None)]
    if reverse:
        entries.reverse()
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as target:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.mode, info.uid, info.gid = mode, 10001, 10001
            if data is None:
                info.type, info.linkname = tarfile.SYMTYPE, link
                target.addfile(info)
            else:
                info.size = len(data)
                target.addfile(info, io.BytesIO(data))
    result.seek(0)
    return result


def test_inventory_is_independent_of_archive_entry_order():
    assert inventory(archive()) == inventory(archive(reverse=True))


def test_inventory_detects_content_permissions_and_link_changes():
    original = inventory(archive())
    assert original != inventory(archive(content=b"modified"))
    assert original != inventory(archive(mode=0o777))
    assert original != inventory(archive(link="different"))
