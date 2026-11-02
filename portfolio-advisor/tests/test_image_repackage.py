import io
from pathlib import Path
import sys
import tarfile
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_image_repackage import inventory
from check_image_layers import EMPTY_LAYER, check_layers


@pytest.mark.parametrize("layers,empty", [
    (["sha256:" + "a" * 64], 0),
    (["sha256:" + "a" * 64, EMPTY_LAYER], 1),
    ([EMPTY_LAYER, "sha256:" + "a" * 64, EMPTY_LAYER], 2),
])
def test_runtime_layer_check_accepts_only_one_filesystem(layers, empty):
    assert check_layers(layers) == empty


@pytest.mark.parametrize("layers", [
    [], [EMPTY_LAYER], ["sha256:" + "a" * 64, "sha256:" + "b" * 64],
    ["sha256:" + "a" * 64, EMPTY_LAYER, "sha256:" + "b" * 64],
    None, "not-a-list", [None], ["invalid-digest"],
])
def test_runtime_layer_check_rejects_missing_or_retained_filesystems(layers):
    with pytest.raises(ValueError):
        check_layers(layers)


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
