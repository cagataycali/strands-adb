"""Smoke tests — require an adb-connected device."""
import subprocess
import pytest
from strands_adb.adb_tool import adb


def _has_device() -> bool:
    try:
        r = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5
        )
        return any(
            ln.strip().endswith("device")
            for ln in r.stdout.splitlines()[1:]
        )
    except Exception:
        return False


needs_device = pytest.mark.skipif(not _has_device(), reason="no adb device")


def test_unknown_action():
    r = adb(action="nope")
    assert r["status"] == "error"


@needs_device
def test_list_devices():
    r = adb(action="list_devices")
    assert r["status"] == "success"
    assert r["devices"]


@needs_device
def test_device_info():
    r = adb(action="device_info")
    assert r["status"] == "success"
    assert "ro.product.model" in r["info"]


@needs_device
def test_battery():
    r = adb(action="battery")
    assert r["status"] == "success"
    assert "level" in r["content"][0]["text"].lower()


@needs_device
def test_screenshot_returns_image_block():
    """Screenshot must return a Converse API image block so the agent can SEE."""
    r = adb(action="screenshot", output_path="/tmp/_test_shot.png")
    assert r["status"] == "success"
    # Block 0: text summary, Block 1: image block
    assert len(r["content"]) == 2
    img_block = r["content"][1]
    assert "image" in img_block
    assert img_block["image"]["format"] == "png"
    assert img_block["image"]["source"]["bytes"].startswith(b"\x89PNG")


@needs_device
def test_screenshot_no_image_when_disabled():
    r = adb(
        action="screenshot",
        output_path="/tmp/_test_shot2.png",
        include_image=False,
    )
    assert r["status"] == "success"
    assert len(r["content"]) == 1
    assert "text" in r["content"][0]
