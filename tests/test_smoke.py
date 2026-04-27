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
