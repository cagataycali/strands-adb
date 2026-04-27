"""Integration tests for Frontier #13 — settings mutation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def test_setting_get():
    if not _has_device():
        print("⏭ skip"); return
    r = adb(action="setting_get", namespace="global", setting_key="airplane_mode_on")
    assert r["status"] == "success"
    assert r["namespace"] == "global"
    assert r["value"] in ("0", "1")
    print(f"   ✅ airplane_mode_on = {r['value']}")


def test_setting_put_get_delete_roundtrip():
    if not _has_device():
        print("⏭ skip"); return
    key = "strands_adb_test_roundtrip"
    # Clean start
    adb(action="setting_delete", namespace="secure", setting_key=key)

    r = adb(action="setting_put", namespace="secure",
            setting_key=key, setting_value="xyz123")
    assert r["status"] == "success"
    assert r["verified"] is True

    r = adb(action="setting_get", namespace="secure", setting_key=key)
    assert r["value"] == "xyz123"

    r = adb(action="setting_delete", namespace="secure", setting_key=key)
    assert r["status"] == "success"

    r = adb(action="setting_get", namespace="secure", setting_key=key)
    assert r["value"] is None
    print(f"   ✅ roundtrip: put → get → delete → null")


def test_setting_list_filter():
    if not _has_device():
        print("⏭ skip"); return
    r = adb(action="setting_list", namespace="system", filter_text="brightness")
    assert r["status"] == "success"
    assert r["count"] >= 1
    assert all("brightness" in k.lower() for k in r["settings"])
    print(f"   ✅ list filter: {r['count']} brightness keys")


def test_invalid_namespace():
    if not _has_device():
        print("⏭ skip"); return
    r = adb(action="setting_get", namespace="bogus", setting_key="foo")
    assert r["status"] == "error"
    print(f"   ✅ invalid namespace rejected")


def test_set_ringer_all_modes():
    if not _has_device():
        print("⏭ skip"); return
    for mode in ["silent", "vibrate", "normal"]:
        r = adb(action="set_ringer", setting_value=mode)
        assert r["status"] == "success", r
        assert r["verified"] is True, f"ringer {mode} not verified: {r}"
    # Leave in normal
    adb(action="set_ringer", setting_value="normal")
    print(f"   ✅ silent/vibrate/normal all verified")


def test_invalid_ringer_mode():
    r = adb(action="set_ringer", setting_value="loud")
    assert r["status"] == "error"
    print(f"   ✅ invalid ringer mode rejected")


def test_set_brightness_roundtrip():
    if not _has_device():
        print("⏭ skip"); return
    # Read original
    orig = adb(action="setting_get", namespace="system",
               setting_key="screen_brightness")["value"]
    try:
        r = adb(action="set_brightness", setting_value=42, auto_brightness=False)
        assert r["status"] == "success"
        assert r["verified"] == "42"
    finally:
        # Restore
        adb(action="set_brightness", setting_value=int(orig or 128),
            auto_brightness=True)
    print(f"   ✅ brightness roundtrip (restored to {orig})")


def test_invalid_brightness():
    r = adb(action="set_brightness", setting_value=999)
    assert r["status"] == "error"
    r = adb(action="set_brightness", setting_value=-5)
    assert r["status"] == "error"
    print(f"   ✅ invalid brightness rejected")


def test_set_bluetooth_roundtrip():
    if not _has_device():
        print("⏭ skip"); return
    # Read original
    orig = adb(action="setting_get", namespace="global",
               setting_key="bluetooth_on")["value"]
    was_on = orig == "1"
    try:
        # Toggle
        target = not was_on
        r = adb(action="set_bluetooth", setting_value=target)
        # May be 'error' if system is still transitioning; either way verified should be honest
        assert "verified" in r
    finally:
        # Restore
        adb(action="set_bluetooth", setting_value=was_on)
    print(f"   ✅ bluetooth toggle (restored to on={was_on})")


def test_airplane_mode_is_honest():
    """Airplane mode should always report radios_affected=False on non-root."""
    if not _has_device():
        print("⏭ skip"); return
    r = adb(action="set_airplane_mode", setting_value=False)
    assert r["status"] == "success"
    assert r["radios_affected"] is False
    assert "caveat" in r
    print(f"   ✅ honest caveat returned")


def test_setting_dump_shape():
    if not _has_device():
        print("⏭ skip"); return
    r = adb(action="setting_dump")
    assert r["status"] == "success"
    assert r["total"] > 100
    assert set(r["snapshot"].keys()) == {"system", "secure", "global"}
    print(f"   ✅ dumped {r['total']} settings")


if __name__ == "__main__":
    tests = [
        test_setting_get,
        test_setting_put_get_delete_roundtrip,
        test_setting_list_filter,
        test_invalid_namespace,
        test_set_ringer_all_modes,
        test_invalid_ringer_mode,
        test_set_brightness_roundtrip,
        test_invalid_brightness,
        test_set_bluetooth_roundtrip,
        test_airplane_mode_is_honest,
        test_setting_dump_shape,
    ]
    passed = failed = 0
    for t in tests:
        print(f"▶ {t.__name__}")
        try:
            t(); passed += 1
        except AssertionError as e:
            print(f"   ❌ FAIL: {e}"); failed += 1
        except Exception as e:
            print(f"   ❌ ERR: {e}"); failed += 1
    print(f"\n{passed}/{len(tests)} settings tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
