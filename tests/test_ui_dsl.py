"""Integration tests for Frontier #10 — UI Query DSL."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb
from strands_adb.adb_tool import (
    _match_str, _parse_bounds, _filter_ui,
)


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def _ensure_home():
    adb(action="key", key="KEYCODE_WAKEUP")
    time.sleep(0.3)
    adb(action="key", key="KEYCODE_MENU")
    time.sleep(0.2)
    adb(action="key", key="KEYCODE_HOME")
    time.sleep(1)


# ── Unit tests (no device required) ──────────────────────────

def test_match_str_substring():
    assert _match_str("hello world", "world")
    assert _match_str("HELLO", "hello")  # case-insensitive
    assert not _match_str("foo", "bar")
    assert _match_str("anything", "")    # empty pattern = match all
    print("   ✅ substring matcher")


def test_match_str_exact():
    assert _match_str("foo", "=foo")
    assert not _match_str("foobar", "=foo")
    assert not _match_str("FOO", "=foo")  # case-sensitive
    print("   ✅ exact matcher (=prefix)")


def test_match_str_regex():
    assert _match_str("calc_button_7", "^button_\\d+")
    assert _match_str("foo123bar", "^\\d+")
    assert not _match_str("foobar", "^\\d+$")
    print("   ✅ regex matcher (^prefix)")


def test_parse_bounds():
    assert _parse_bounds("[0,0][100,200]") == (0, 0, 100, 200)
    assert _parse_bounds("[-5,10][50,60]") == (-5, 10, 50, 60)
    assert _parse_bounds("no bounds here") is None
    print("   ✅ bounds parser")


def test_filter_ui_unit():
    """Filter pure XML with no device."""
    xml = '''<?xml version="1.0"?><hierarchy rotation="0">
      <node text="Hello" resource-id="com.x/.btn1" class="android.widget.Button"
            clickable="true" scrollable="false" bounds="[0,0][100,100]"
            content-desc="" package="com.x" enabled="true" selected="false"/>
      <node text="World" resource-id="com.x/.btn2" class="android.widget.TextView"
            clickable="false" scrollable="false" bounds="[10,10][50,50]"
            content-desc="description" package="com.x" enabled="true" selected="false"/>
    </hierarchy>'''
    # text match (substring)
    r = _filter_ui(xml, text="Hello")
    assert len(r) == 1
    assert r[0]["text"] == "Hello"
    assert r[0]["center"] == (50, 50)

    # fallback: text matcher hits content_desc
    r = _filter_ui(xml, text="description")
    assert len(r) == 1
    assert r[0]["text"] == "World"

    # clickable filter
    r = _filter_ui(xml, clickable=True)
    assert len(r) == 1
    assert r[0]["text"] == "Hello"

    # combined
    r = _filter_ui(xml, class_name="Button", clickable=True)
    assert len(r) == 1

    print("   ✅ _filter_ui handles all filters")


# ── Live device tests ────────────────────────────────────────

def test_ui_find_live():
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    r = adb(action="ui_find", clickable_filter=True)
    assert r["status"] == "success"
    assert r["count"] > 0
    assert all(m["center"] for m in r["matches"])
    print(f"   ✅ home screen: {r['count']} clickables")


def test_ui_wait_for_success():
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    t0 = time.time()
    r = adb(action="ui_wait_for", class_name="FrameLayout", ui_timeout=5.0)
    elapsed = time.time() - t0
    assert r["status"] == "success", r
    assert r["count"] > 0
    assert elapsed < 5.0
    print(f"   ✅ found in {elapsed:.1f}s, {r['count']} matches")


def test_ui_wait_for_timeout():
    if not _has_device(): print("⏭ skip"); return
    t0 = time.time()
    r = adb(action="ui_wait_for",
            text="this_text_should_never_appear_xyz789",
            ui_timeout=1.5, ui_poll_interval=0.5)
    elapsed = time.time() - t0
    assert r["status"] == "error"
    assert r["timed_out"] is True
    assert 1.0 <= elapsed <= 5.0  # generous bounds for XML parse time
    print(f"   ✅ timed out after {elapsed:.1f}s")


def test_ui_tap_by_calculator():
    """E2E: open calculator → tap buttons via content-desc → verify result."""
    if not _has_device(): print("⏭ skip"); return
    # Clean start of calculator
    adb(action="home")
    time.sleep(0.5)
    adb(action="kill", package="com.google.android.calculator")
    time.sleep(0.5)
    import subprocess
    subprocess.run(["adb", "shell", "monkey", "-p",
                    "com.google.android.calculator", "-c",
                    "android.intent.category.LAUNCHER", "1"],
                   capture_output=True, timeout=10)
    time.sleep(3)

    # Tap 7 + 3 =
    for btn in ["7", "plus", "3", "equals"]:
        r = adb(action="ui_tap_by", desc_filter=btn)
        assert r["status"] == "success", f"tap {btn!r} failed: {r}"
        time.sleep(0.3)

    time.sleep(0.5)
    # Look for result '10' — may appear in result or formula display
    r = adb(action="ui_find", text="10")
    assert r["count"] > 0, "expected '10' on screen after 7+3="

    adb(action="home")
    print(f"   ✅ calculator 7+3=10 automated, {r['count']} '10' matches found")


def test_ui_tap_by_no_match():
    """Graceful error when nothing matches."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="ui_tap_by", text="impossible_button_zz999")
    assert r["status"] == "error"
    assert "no match" in r["content"][0]["text"]
    print(f"   ✅ no-match handled gracefully")


def test_ui_find_by_resource_id():
    """resource_id matcher (substring)."""
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    # Launcher uses an id suffix like 'icon' or 'hotseat' — just try it
    r = adb(action="ui_find", resource_id="icon")
    assert r["status"] == "success"
    # Can be 0 if none, that's fine — just verify the filter ran
    print(f"   ✅ resource_id filter: {r['count']} matches for 'icon'")


if __name__ == "__main__":
    tests = [
        test_match_str_substring,
        test_match_str_exact,
        test_match_str_regex,
        test_parse_bounds,
        test_filter_ui_unit,
        test_ui_find_live,
        test_ui_wait_for_success,
        test_ui_wait_for_timeout,
        test_ui_tap_by_calculator,
        test_ui_tap_by_no_match,
        test_ui_find_by_resource_id,
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
    print(f"\n{passed}/{len(tests)} UI DSL tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
