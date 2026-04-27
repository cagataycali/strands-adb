"""Integration tests for Frontier #5 — physical camera.

Require a physical Android device connected via adb. Skip cleanly otherwise.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device() -> bool:
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def test_camera_photo_back():
    """Take a back-camera photo and verify image block + JPEG file."""
    if not _has_device():
        print("⏭  skip: no adb device")
        return
    print("📸 test_camera_photo_back ...")
    r = adb(action="camera_photo", facing="back", camera_timeout=20)
    assert r.get("status") == "success", f"status != success: {r}"
    assert r.get("size_bytes", 0) > 10_000, "file too small — probably truncated"
    path = r.get("path")
    assert path and Path(path).exists(), f"local file missing: {path}"
    with open(path, "rb") as f:
        magic = f.read(3)
    assert magic == b"\xff\xd8\xff", f"not a JPEG: magic={magic.hex()}"
    blocks = r.get("content", [])
    assert any("image" in b for b in blocks), "no image block returned"
    img_block = next(b for b in blocks if "image" in b)
    assert img_block["image"]["format"] == "jpeg"
    assert len(img_block["image"]["source"]["bytes"]) == r["size_bytes"]
    print(f"   ✅ {path} ({r['size_bytes']} bytes)")


def test_camera_photo_no_pull():
    """With auto_pull=False we should get device_path only."""
    if not _has_device():
        print("⏭  skip: no adb device")
        return
    print("📸 test_camera_photo_no_pull ...")
    r = adb(action="camera_photo", facing="back",
            auto_pull=False, camera_timeout=20)
    assert r.get("status") == "success", r
    assert "device_path" in r
    assert "path" not in r  # not pulled
    print(f"   ✅ device_path={r['device_path']}")


def test_camera_front():
    """Front-facing camera toggle works."""
    if not _has_device():
        print("⏭  skip: no adb device")
        return
    print("📸 test_camera_front ...")
    r = adb(action="camera_photo", facing="front", camera_timeout=20)
    assert r.get("status") == "success", r
    assert r.get("facing") == "front"
    print(f"   ✅ front photo: {r.get('path')}")


if __name__ == "__main__":
    tests = [test_camera_photo_back, test_camera_photo_no_pull, test_camera_front]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} camera tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
