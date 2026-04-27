"""Integration tests for Frontier #11 — Notification Pipeline."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


# ── list / get / stats ────────────────────────────────────

def test_notifications_list():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="notifications_list")
    assert r["status"] == "success"
    # Even a fresh phone has at least a few system notifications
    # Just confirm the structure is correct
    for n in r["notifications"]:
        assert "raw" in n
        if "|" in n["raw"]:
            assert "package" in n
            assert "id" in n
            assert n["raw"].startswith(f"{n['user_id']}|{n['package']}")
    print(f"   ✅ {r['count']} notifications listed, all properly parsed")


def test_notifications_stats():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="notifications_stats")
    assert r["status"] == "success"
    assert "active_count" in r
    assert "zen_mode" in r
    assert r["zen_mode_name"] in ("off", "priority", "none", "alarms")
    print(f"   ✅ stats: zen={r['zen_mode_name']} active={r['active_count']}")


# ── post ────────────────────────────────────────────────────

def test_post_notification():
    if not _has_device(): print("⏭ skip"); return
    tag = f"test_post_{int(time.time())}"
    r = adb(action="notifications_post",
            notification_title="Test Title",
            notification_text="Test body text",
            notification_tag=tag)
    assert r["status"] == "success"
    assert r.get("tag") == tag

    time.sleep(0.5)
    # Find it in list
    lr = adb(action="notifications_list")
    tags = [n.get("tag") for n in lr["notifications"]]
    assert tag in tags, f"Posted tag {tag} not found in {tags}"
    print(f"   ✅ posted and verified tag={tag}")


def test_post_with_style():
    if not _has_device(): print("⏭ skip"); return
    tag = f"test_style_{int(time.time())}"
    r = adb(action="notifications_post",
            notification_title="Big Text",
            notification_text="Long body content " * 5,
            notification_tag=tag,
            notification_style="bigtext")
    assert r["status"] == "success"
    print(f"   ✅ posted bigtext style notification")


def test_post_missing_text():
    r = adb(action="notifications_post")
    assert r["status"] == "error"
    assert "text required" in r["content"][0]["text"]
    print(f"   ✅ missing text rejected")


def test_post_bad_style():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="notifications_post",
            notification_text="hi",
            notification_style="not_a_style")
    assert r["status"] == "error"
    assert "unknown style" in r["content"][0]["text"]
    print(f"   ✅ bad style rejected")


# ── get ─────────────────────────────────────────────────────

def test_get_existing_notification():
    if not _has_device(): print("⏭ skip"); return
    # Post one first
    tag = f"test_get_{int(time.time())}"
    adb(action="notifications_post",
        notification_title="Get Test",
        notification_text="body",
        notification_tag=tag)
    time.sleep(0.5)

    lr = adb(action="notifications_list")
    our_key = None
    for n in lr["notifications"]:
        if n.get("tag") == tag:
            our_key = n["raw"]
            break

    assert our_key, "Could not find posted notification"

    r = adb(action="notifications_get", notification_key=our_key)
    assert r["status"] == "success"
    assert r.get("package") == "com.android.shell"
    # channel should be shell_cmd
    assert r.get("channel") == "shell_cmd"
    print(f"   ✅ get returned full details (channel={r.get('channel')})")


def test_get_missing_key():
    r = adb(action="notifications_get")
    assert r["status"] == "error"
    assert "notification_key required" in r["content"][0]["text"]
    print(f"   ✅ missing key rejected")


def test_get_bad_key():
    r = adb(action="notifications_get", notification_key="malformed")
    assert r["status"] == "error"
    assert "invalid notification key" in r["content"][0]["text"]
    print(f"   ✅ malformed key rejected")


# ── snooze / unsnooze ──────────────────────────────────────

def test_snooze_and_unsnooze():
    if not _has_device(): print("⏭ skip"); return
    # Post first
    tag = f"test_snooze_{int(time.time())}"
    adb(action="notifications_post",
        notification_text="snooze me",
        notification_tag=tag)
    time.sleep(0.5)

    lr = adb(action="notifications_list")
    our_key = None
    for n in lr["notifications"]:
        if n.get("tag") == tag:
            our_key = n["raw"]; break
    assert our_key

    # Snooze
    r = adb(action="notifications_snooze",
            notification_key=our_key,
            notification_duration_ms=3000)
    assert r["status"] == "success"
    assert r.get("duration_ms") == 3000

    # Unsnooze immediately
    r = adb(action="notifications_unsnooze", notification_key=our_key)
    assert r["status"] == "success"
    print(f"   ✅ snooze → unsnooze round trip")


def test_snooze_invalid_duration():
    r = adb(action="notifications_snooze",
            notification_key="0|a|1|x|2",
            notification_duration_ms=50)  # too short
    assert r["status"] == "error"
    assert "duration_ms must be" in r["content"][0]["text"]

    r = adb(action="notifications_snooze",
            notification_key="0|a|1|x|2",
            notification_duration_ms=999_999_999_999)  # too long
    assert r["status"] == "error"
    print(f"   ✅ bad durations rejected")


def test_snooze_bad_key():
    r = adb(action="notifications_snooze",
            notification_key="not_a_key",
            notification_duration_ms=1000)
    assert r["status"] == "error"
    print(f"   ✅ bad key rejected")


# ── DND ────────────────────────────────────────────────────

def test_dnd_modes_all_accepted():
    if not _has_device(): print("⏭ skip"); return
    # Cycle through all modes, restore to off
    for mode in ("priority", "alarms", "none", "off"):
        r = adb(action="notifications_set_dnd", notification_dnd_mode=mode)
        assert r["status"] == "success", f"mode={mode}: {r}"
    # Verify we're back to off
    r = adb(action="notifications_stats")
    assert r["zen_mode"] == "0"
    print(f"   ✅ all 4 DND modes cycle + restore to off")


def test_dnd_aliases():
    if not _has_device(): print("⏭ skip"); return
    # "on" and "all" are aliases
    r = adb(action="notifications_set_dnd", notification_dnd_mode="on")
    assert r["status"] == "success"
    r = adb(action="notifications_set_dnd", notification_dnd_mode="all")
    assert r["status"] == "success"
    # Restore
    adb(action="notifications_set_dnd", notification_dnd_mode="off")
    print(f"   ✅ DND aliases (on, all) accepted")


def test_dnd_bad_mode():
    r = adb(action="notifications_set_dnd", notification_dnd_mode="invalid")
    assert r["status"] == "error"
    assert "unknown DND mode" in r["content"][0]["text"]
    print(f"   ✅ bad DND mode rejected")


def test_dnd_package_bypass():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="notifications_dnd_package",
            notification_package="com.android.shell",
            notification_allow=True)
    assert r["status"] == "success"
    assert r.get("allow") is True

    r = adb(action="notifications_dnd_package",
            notification_package="com.android.shell",
            notification_allow=False)
    assert r["status"] == "success"
    assert r.get("allow") is False
    print(f"   ✅ DND package bypass allow/disallow")


def test_dnd_package_missing():
    r = adb(action="notifications_dnd_package")
    assert r["status"] == "error"
    print(f"   ✅ missing package rejected")


# ── parser ─────────────────────────────────────────────────

def test_parse_notification_key():
    """Direct unit test of the key parser."""
    from strands_adb.adb_tool import _parse_notif_key
    parsed = _parse_notif_key("0|com.google.android.gm|12345|inbox_tag|10123")
    assert parsed["user_id"] == "0"
    assert parsed["package"] == "com.google.android.gm"
    assert parsed["id"] == "12345"
    assert parsed["tag"] == "inbox_tag"
    assert parsed["uid"] == "10123"

    # null tag
    parsed = _parse_notif_key("0|pkg|1|null|5")
    assert parsed["tag"] is None

    # Malformed
    parsed = _parse_notif_key("garbage")
    assert parsed == {"raw": "garbage"}
    print(f"   ✅ key parser handles all formats")


def test_shell_quote_pipes():
    """Verify our _shq handles shell metachars correctly."""
    from strands_adb.adb_tool import _shq
    assert _shq("hello") == "'hello'"
    assert _shq("a|b") == "'a|b'"
    # Inner single-quote escape
    assert "'" in _shq("it's")
    print(f"   ✅ shell quoting handles pipes and quotes")


if __name__ == "__main__":
    tests = [
        test_notifications_list,
        test_notifications_stats,
        test_post_notification,
        test_post_with_style,
        test_post_missing_text,
        test_post_bad_style,
        test_get_existing_notification,
        test_get_missing_key,
        test_get_bad_key,
        test_snooze_and_unsnooze,
        test_snooze_invalid_duration,
        test_snooze_bad_key,
        test_dnd_modes_all_accepted,
        test_dnd_aliases,
        test_dnd_bad_mode,
        test_dnd_package_bypass,
        test_dnd_package_missing,
        test_parse_notification_key,
        test_shell_quote_pipes,
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
    # Cleanup: make sure DND is off
    adb(action="notifications_set_dnd", notification_dnd_mode="off")
    print(f"\n{passed}/{len(tests)} notification tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
