"""Integration tests for Frontier #14 — Session Lifecycle & Smart Unlock."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def _has_pin():
    return bool(os.environ.get("ADB_DEVICE_PIN", ""))


# ── is_locked ────────────────────────────

def test_is_locked_schema():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="is_locked")
    assert r["status"] == "success"
    for k in ("locked", "awake", "wakefulness", "trust_state"):
        assert k in r
    assert isinstance(r["locked"], bool) or r["locked"] is None
    assert isinstance(r["awake"], bool) or r["awake"] is None
    print(f"   ✅ locked={r['locked']}  awake={r['awake']}  trust={r['trust_state']}")


# ── wake ─────────────────────────────────

def test_wake_makes_awake():
    if not _has_device(): print("⏭ skip"); return
    # Sleep first
    adb(action="sleep")
    import time; time.sleep(1.5)
    # Verify asleep
    r = adb(action="is_locked")
    assert r["awake"] is False, f"expected asleep, got {r}"
    # Wake it
    r = adb(action="wake")
    assert r["status"] == "success"
    assert r["awake"] is True
    print(f"   ✅ wake: {r['wakefulness']}")


# ── sleep ────────────────────────────────

def test_sleep_makes_not_awake():
    if not _has_device(): print("⏭ skip"); return
    # Wake it
    adb(action="wake")
    import time; time.sleep(0.5)
    r = adb(action="sleep")
    assert r["status"] == "success"
    assert r["awake"] is False
    # Verify via is_locked
    r2 = adb(action="is_locked")
    assert r2["awake"] is False
    print(f"   ✅ sleep: {r['wakefulness']}")


# ── unlock ───────────────────────────────

def test_unlock_end_to_end():
    """Lock device, run unlock, verify."""
    if not _has_device(): print("⏭ skip"); return
    if not _has_pin():
        print("   ⏭ skip (no ADB_DEVICE_PIN env)")
        return

    # Lock the device
    adb(action="sleep")
    import time; time.sleep(2.5)

    # Confirm locked
    r = adb(action="is_locked")
    assert r["locked"] is True, f"expected locked, got {r}"

    # Unlock
    r = adb(action="unlock")
    assert r["status"] == "success", f"unlock failed: {r}"
    assert r["locked"] is False
    assert r["attempts"] >= 1
    assert "dismiss_keyguard" in r["steps"] or "already_unlocked" in r["steps"][0]

    # Verify unlocked
    r2 = adb(action="is_locked")
    assert r2["locked"] is False
    print(f"   ✅ unlock: {r['attempts']} attempt(s), {len(r['steps'])} steps")


def test_unlock_idempotent():
    if not _has_device(): print("⏭ skip"); return
    if not _has_pin():
        print("   ⏭ skip (no ADB_DEVICE_PIN env)")
        return

    # Ensure unlocked first
    adb(action="unlock")
    import time; time.sleep(0.5)
    # Second call should short-circuit
    r = adb(action="unlock")
    assert r["status"] == "success"
    assert r["steps"] == ["already_unlocked"]
    print(f"   ✅ idempotent: short-circuits when already unlocked")


def test_unlock_missing_pin_rejected():
    if not _has_device(): print("⏭ skip"); return
    # Temporarily clear env PIN
    saved = os.environ.pop("ADB_DEVICE_PIN", None)
    try:
        # First lock the device so we don't hit the already_unlocked branch
        adb(action="sleep")
        import time; time.sleep(2)

        r = adb(action="unlock")  # no pin param, no env
        assert r["status"] == "error"
        assert "PIN" in r["content"][0]["text"] or "pin" in r["content"][0]["text"]

        # Cleanup: unlock it back
        if saved:
            os.environ["ADB_DEVICE_PIN"] = saved
            adb(action="unlock")
    finally:
        if saved:
            os.environ["ADB_DEVICE_PIN"] = saved
    print(f"   ✅ missing PIN rejected cleanly")


def test_unlock_non_digit_pin_rejected():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="unlock", pin="abcd")
    assert r["status"] == "error"
    assert "digit" in r["content"][0]["text"].lower()
    print(f"   ✅ non-digit PIN rejected")


# ── keep_awake ───────────────────────────

def test_keep_awake_toggle():
    if not _has_device(): print("⏭ skip"); return
    # Enable
    r_on = adb(action="keep_awake", keep_awake_enabled=True)
    assert r_on["status"] == "success"
    assert r_on["keep_awake"] is True
    assert r_on["raw_value"] == "7"

    # Disable
    r_off = adb(action="keep_awake", keep_awake_enabled=False)
    assert r_off["status"] == "success"
    assert r_off["keep_awake"] is False
    assert r_off["raw_value"] == "0"
    print(f"   ✅ keep_awake 7↔0 toggles Settings.Global.stay_on_while_plugged_in")


# ── action registration ──────────────────

def test_session_actions_registered():
    from strands_adb.adb_tool import ACTIONS
    for a in ("is_locked", "wake", "sleep", "unlock", "keep_awake"):
        assert a in ACTIONS, f"{a} not registered"
    print(f"   ✅ all 5 session actions registered")


if __name__ == "__main__":
    tests = [
        test_is_locked_schema,
        test_wake_makes_awake,
        test_sleep_makes_not_awake,
        test_unlock_end_to_end,
        test_unlock_idempotent,
        test_unlock_missing_pin_rejected,
        test_unlock_non_digit_pin_rejected,
        test_keep_awake_toggle,
        test_session_actions_registered,
    ]
    passed = failed = 0
    for t in tests:
        print(f"▶ {t.__name__}")
        try:
            t(); passed += 1
        except AssertionError as e:
            print(f"   ❌ FAIL: {e}"); failed += 1
        except Exception as e:
            print(f"   ❌ ERR: {type(e).__name__}: {e}"); failed += 1
    print(f"\n{passed}/{len(tests)} session tests, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
