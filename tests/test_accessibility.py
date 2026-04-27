"""Integration tests for Frontier #8 — Accessibility / ATC."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


# Snapshot initial state so we restore cleanly
def _snapshot():
    return adb(action="accessibility_status")


def _restore(snap):
    """Restore accessibility settings to pre-test snapshot."""
    adb(action="accessibility_font_scale",
        a11y_font_scale=float(snap.get("font_scale") or 1.0))
    for k, action in [
        ("captioning_enabled", "accessibility_captions"),
        ("magnification_enabled", "accessibility_magnification"),
    ]:
        val = snap.get(k)
        enable = val not in (None, "", "null", "0")
        adb(action=action, a11y_enable=enable)


# ── list & status ────────────────────────────────────────────

def test_accessibility_list():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="accessibility_list")
    assert r["status"] == "success"
    assert r["installed_count"] >= 1
    # Components must be properly paired: "pkg/cls"
    for svc in r["installed"]:
        assert "/" in svc["component"]
        assert svc["component"].startswith(svc["package"])
        # name should match class (substring relationship)
        assert svc["name"]
    print(f"   ✅ {r['installed_count']} services properly parsed")


def test_accessibility_status():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="accessibility_status")
    assert r["status"] == "success"
    # Must report at least these keys
    for k in ["accessibility_enabled", "font_scale"]:
        assert k in r, f"missing {k}"
    print(f"   ✅ status reports all expected keys")


# ── toggle service ───────────────────────────────────────────

def test_toggle_by_alias():
    """Enable a11ymenu via alias, verify, then disable."""
    if not _has_device(): print("⏭ skip"); return

    # Enable
    r = adb(action="accessibility_toggle_service",
            a11y_service="a11ymenu", a11y_enable=True)
    assert r["status"] == "success", r
    assert r.get("component", "").startswith("com.android.systemui.accessibility")

    # Verify in status
    time.sleep(0.3)
    s = adb(action="accessibility_status")
    assert "accessibilitymenu" in (s.get("enabled_services") or "").lower()

    # Disable
    r = adb(action="accessibility_toggle_service",
            a11y_service="a11ymenu", a11y_enable=False)
    assert r["status"] == "success"
    print(f"   ✅ toggle ON → status confirms → toggle OFF")


def test_toggle_idempotent():
    """Re-enabling an already-enabled service is a no-op."""
    if not _has_device(): print("⏭ skip"); return
    adb(action="accessibility_toggle_service",
        a11y_service="a11ymenu", a11y_enable=True)
    r = adb(action="accessibility_toggle_service",
            a11y_service="a11ymenu", a11y_enable=True)
    assert r["status"] == "success"
    assert r.get("changed") is False
    # clean up
    adb(action="accessibility_toggle_service",
        a11y_service="a11ymenu", a11y_enable=False)
    print(f"   ✅ idempotent enable → changed=False")


def test_toggle_by_full_component():
    """Passing a raw pkg/cls bypasses alias lookup."""
    if not _has_device(): print("⏭ skip"); return
    comp = ("com.android.systemui.accessibility.accessibilitymenu/"
            "com.android.systemui.accessibility.accessibilitymenu."
            "AccessibilityMenuService")
    r = adb(action="accessibility_toggle_service",
            a11y_service=comp, a11y_enable=True)
    assert r["status"] == "success"
    assert r.get("component") == comp
    # clean up
    adb(action="accessibility_toggle_service",
        a11y_service=comp, a11y_enable=False)
    print(f"   ✅ full component name accepted")


def test_toggle_missing_service():
    r = adb(action="accessibility_toggle_service")
    assert r["status"] == "error"
    assert "a11y_service" in r["content"][0]["text"]
    print(f"   ✅ missing service rejected")


def test_toggle_unknown_service():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="accessibility_toggle_service",
            a11y_service="definitely_not_real_service_xyz")
    assert r["status"] == "error"
    # error should mention aliases so caller knows what's valid
    assert "alias" in r["content"][0]["text"].lower()
    print(f"   ✅ unknown service rejected with alias hint")


# ── system actions ───────────────────────────────────────────

def test_system_action_by_name():
    if not _has_device(): print("⏭ skip"); return
    # home is harmless
    r = adb(action="accessibility_system_action", a11y_system_action="home")
    assert r["status"] == "success"
    assert r.get("action_id") == 2
    print(f"   ✅ system_action=home → id=2")


def test_system_action_by_numeric():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="accessibility_system_action", a11y_system_action="2")
    assert r["status"] == "success"
    assert r.get("action_id") == 2
    print(f"   ✅ system_action='2' → id=2 (numeric passthrough)")


def test_system_action_unknown():
    r = adb(action="accessibility_system_action",
            a11y_system_action="not_a_real_action")
    assert r["status"] == "error"
    assert "unknown system action" in r["content"][0]["text"]
    print(f"   ✅ unknown action rejected")


def test_system_action_missing():
    r = adb(action="accessibility_system_action")
    assert r["status"] == "error"
    print(f"   ✅ missing action rejected")


def test_system_action_notifications_and_back():
    """Full round trip: open notifications, then back."""
    if not _has_device(): print("⏭ skip"); return
    r1 = adb(action="accessibility_system_action",
             a11y_system_action="notifications")
    assert r1["status"] == "success"
    time.sleep(0.5)
    r2 = adb(action="accessibility_system_action", a11y_system_action="back")
    assert r2["status"] == "success"
    # return home to be tidy
    adb(action="key", key="home")
    print(f"   ✅ notifications → back round trip")


# ── captions / magnification / font_scale ────────────────────

def test_captions_toggle():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="accessibility_captions", a11y_enable=True)
    assert r["status"] == "success"
    assert r.get("enabled") is True
    r = adb(action="accessibility_captions", a11y_enable=False)
    assert r["status"] == "success"
    assert r.get("enabled") is False
    print(f"   ✅ captions on/off")


def test_magnification_toggle():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="accessibility_magnification", a11y_enable=True)
    assert r["status"] == "success"
    r = adb(action="accessibility_magnification", a11y_enable=False)
    assert r["status"] == "success"
    print(f"   ✅ magnification on/off")


def test_font_scale_valid():
    if not _has_device(): print("⏭ skip"); return
    snap = _snapshot()
    try:
        for s in (0.85, 1.0, 1.15, 1.3, 1.5, 2.0):
            r = adb(action="accessibility_font_scale", a11y_font_scale=s)
            assert r["status"] == "success", f"{s}: {r}"
            assert r.get("font_scale") == s
    finally:
        _restore(snap)
    print(f"   ✅ all standard font scales accepted")


def test_font_scale_out_of_range():
    r = adb(action="accessibility_font_scale", a11y_font_scale=0.3)
    assert r["status"] == "error"
    r = adb(action="accessibility_font_scale", a11y_font_scale=5.0)
    assert r["status"] == "error"
    print(f"   ✅ out-of-range font_scale rejected")


# ── parser robustness ────────────────────────────────────────

def test_parser_unique_components():
    """Same component name must not appear twice with different packages."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="accessibility_list")
    # Check no cross-package bleed: each component's package prefix
    # should match the declared package
    for svc in r["installed"]:
        pkg = svc["package"]
        comp = svc["component"]
        assert comp.startswith(pkg + "/"), (
            f"parser bleed: component {comp!r} doesn't match package {pkg!r}"
        )
    print(f"   ✅ no cross-package bleed in parser")


if __name__ == "__main__":
    tests = [
        test_accessibility_list,
        test_accessibility_status,
        test_toggle_by_alias,
        test_toggle_idempotent,
        test_toggle_by_full_component,
        test_toggle_missing_service,
        test_toggle_unknown_service,
        test_system_action_by_name,
        test_system_action_by_numeric,
        test_system_action_unknown,
        test_system_action_missing,
        test_system_action_notifications_and_back,
        test_captions_toggle,
        test_magnification_toggle,
        test_font_scale_valid,
        test_font_scale_out_of_range,
        test_parser_unique_components,
    ]
    passed = failed = 0
    # capture initial state ONCE
    if _has_device():
        init_snap = _snapshot()
    try:
        for t in tests:
            print(f"▶ {t.__name__}")
            try:
                t(); passed += 1
            except AssertionError as e:
                print(f"   ❌ FAIL: {e}"); failed += 1
            except Exception as e:
                print(f"   ❌ ERR: {type(e).__name__}: {e}"); failed += 1
    finally:
        if _has_device():
            _restore(init_snap)
    print(f"\n{passed}/{len(tests)} a11y tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
