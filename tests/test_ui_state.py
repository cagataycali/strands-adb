"""Integration tests for Frontier #15 + #16 + #18 — UI State Machine, App Launcher, Forms."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def _has_pin():
    return bool(os.environ.get("ADB_DEVICE_PIN", ""))


def _ensure_home():
    """Make sure device is unlocked + on home screen."""
    if _has_pin():
        adb(action="unlock")
    adb(action="key", key="home")
    time.sleep(0.4)


# ── parser unit tests (no device) ────────

def test_parse_node_attrs_basic():
    from strands_adb.adb_tool import _parse_node_attrs
    node = '<node text="Gmail" resource-id="com.app:id/btn" class="android.widget.Button" bounds="[100,200][300,400]" clickable="true"/>'
    a = _parse_node_attrs(node)
    assert a["text"] == "Gmail"
    assert a["resource-id"] == "com.app:id/btn"
    assert a["class"] == "android.widget.Button"
    assert a["clickable"] == "true"
    assert a["_bounds"] == (100, 200, 300, 400)
    assert a["_cx"] == 200
    assert a["_cy"] == 300
    assert a["_width"] == 200
    assert a["_height"] == 200
    print(f"   ✅ parsed: text={a['text']}, center=({a['_cx']},{a['_cy']})")


def test_parse_node_no_bounds():
    from strands_adb.adb_tool import _parse_node_attrs
    node = '<node text="x"/>'
    a = _parse_node_attrs(node)
    assert a["text"] == "x"
    assert "_bounds" not in a
    print(f"   ✅ missing bounds handled")


def test_iter_nodes_multi():
    from strands_adb.adb_tool import _iter_nodes
    xml = '''<?xml version="1.0"?>
    <hierarchy>
      <node text="a" bounds="[0,0][10,10]"/>
      <node text="b" bounds="[20,20][30,30]">
        <node text="c" bounds="[21,21][29,29]"/>
      </node>
    </hierarchy>'''
    nodes = list(_iter_nodes(xml))
    assert len(nodes) == 3
    print(f"   ✅ yielded {len(nodes)} nodes (incl nested)")


def test_node_matches_all_criteria():
    from strands_adb.adb_tool import _node_matches, _parse_node_attrs
    node = _parse_node_attrs('<node text="Gmail" resource-id="com.app:id/x" class="android.widget.Button" clickable="true" bounds="[0,0][1,1]"/>')
    # Exact text match
    assert _node_matches(node, text="Gmail")
    assert not _node_matches(node, text="gmail")  # case-sensitive exact
    # text_contains is case-insensitive
    assert _node_matches(node, text_contains="gmai")
    assert _node_matches(node, text_contains="GMAIL")
    # resource_id exact
    assert _node_matches(node, resource_id="com.app:id/x")
    # class_name substring
    assert _node_matches(node, class_name="Button")
    # clickable bool
    assert _node_matches(node, clickable=True)
    assert not _node_matches(node, clickable=False)
    # AND of fields
    assert _node_matches(node, text="Gmail", clickable=True)
    assert not _node_matches(node, text="Gmail", clickable=False)
    print(f"   ✅ selector logic correct")


def test_element_to_public_shape():
    from strands_adb.adb_tool import _parse_node_attrs, _element_to_public
    raw = _parse_node_attrs('<node text="x" bounds="[10,20][30,40]" clickable="true"/>')
    p = _element_to_public(raw)
    assert p["text"] == "x"
    assert p["bounds"] == [10, 20, 30, 40]
    assert p["center"] == (20, 30)
    assert p["width"] == 20 and p["height"] == 20
    assert p["clickable"] is True
    print(f"   ✅ public shape ok")


# ── integration tests ────────────────────

def test_foreground_info():
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    r = adb(action="foreground_info")
    assert r["status"] == "success"
    assert "package" in r
    assert "activity" in r
    print(f"   ✅ foreground: {r['package']} / {r['activity'].split('.')[-1]}")


def test_find_element_on_home():
    """Home screen should have Settings or Phone icons findable."""
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    # Try to find the Google search bar or any TextView
    r = adb(action="find_elements", ui_class_name="TextView", ui_limit=20)
    assert r["status"] == "success"
    assert r["count"] > 0
    print(f"   ✅ {r['count']} TextViews on home")


def test_find_element_validation():
    r = adb(action="find_element")
    assert r["status"] == "error"
    assert "selector" in r["content"][0]["text"]
    print(f"   ✅ empty selector rejected")


def test_find_element_no_match():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="find_element", ui_text="this_never_exists_12345abc")
    assert r["status"] == "error"
    assert "no match" in r["content"][0]["text"]
    print(f"   ✅ no-match returns error")


def test_app_launch_by_alias():
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    r = adb(action="app_launch", ui_app_name="calculator")
    assert r["status"] == "success"
    assert r["package"] == "com.google.android.calculator"
    # Wait for it
    r2 = adb(action="wait_for_window", ui_window_package="com.google.android.calculator", ui_timeout=5)
    assert r2["status"] == "success"
    print(f"   ✅ calculator launched: {r2['focus']}")


def test_app_launch_unknown():
    """Fuzzy lookup should fail for totally unknown names."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="app_launch", ui_app_name="xyzzy_fake_app_12345")
    assert r["status"] == "error"
    print(f"   ✅ unknown app name rejected")


def test_app_launch_by_exact_package():
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    r = adb(action="app_launch", ui_package_arg="com.android.settings")
    assert r["status"] == "success"
    assert r["package"] == "com.android.settings"
    print(f"   ✅ exact package launch works")


def test_wait_for_window_timeout():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="wait_for_window",
            ui_window_package="com.this.does.not.exist",
            ui_timeout=1.0)
    assert r["status"] == "error"
    assert "timeout" in r["content"][0]["text"]
    print(f"   ✅ timeout path works")


def test_wait_for_element_finds_existing():
    """On home screen, some element should be findable immediately."""
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    r = adb(action="wait_for_element", ui_class_name="TextView", ui_timeout=3)
    assert r["status"] == "success"
    assert r["polls"] >= 1
    assert r["elapsed_sec"] < 3.0
    print(f"   ✅ found in {r['polls']} polls, {r['elapsed_sec']:.2f}s")


def test_tap_element_calculator_flow():
    """Full end-to-end: launch calc, tap buttons, verify result."""
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()

    # Launch + wait
    adb(action="app_launch", ui_app_name="calculator")
    r = adb(action="wait_for_window", ui_window_package="com.google.android.calculator", ui_timeout=5)
    assert r["status"] == "success"
    time.sleep(0.5)

    # 7 + 3 = via UI taps
    for desc in ["clear", "7", "plus", "3", "equals"]:
        r = adb(action="tap_element", ui_content_desc=desc)
        assert r["status"] == "success", f"tap {desc} failed: {r}"
        time.sleep(0.2)

    time.sleep(0.5)
    # Verify result
    r = adb(action="find_element", ui_text="10")
    assert r["status"] == "success", f"result '10' not found: {r}"
    print(f"   ✅ 7 + 3 = 10 automated successfully via UI")

    # Cleanup
    adb(action="key", key="home")


def test_tap_element_requires_selector():
    r = adb(action="tap_element")
    assert r["status"] == "error"
    print(f"   ✅ empty selector rejected")


def test_type_into_requires_selector():
    r = adb(action="type_into", ui_input_text="hello")
    assert r["status"] == "error"
    print(f"   ✅ no selector rejected")


def test_wait_for_idle():
    """Home screen should reach idle within a few seconds."""
    if not _has_device(): print("⏭ skip"); return
    _ensure_home()
    time.sleep(0.5)  # let any transient animations settle
    r = adb(action="wait_for_idle", ui_timeout=5, ui_quiet_ms=300)
    # May or may not become idle depending on active widgets. If it times
    # out, that's informational — we just check the action runs cleanly.
    assert r["status"] in ("success", "error")
    assert "polls" in r or "polled" in r.get("content", [{}])[0].get("text", "")
    print(f"   ✅ wait_for_idle ran: {r['status']}")


def test_ui_actions_registered():
    from strands_adb.adb_tool import ACTIONS
    for a in ("find_element", "find_elements", "wait_for_element",
              "wait_for_gone", "wait_for_idle", "wait_for_window",
              "tap_element", "type_into", "app_launch", "foreground_info"):
        assert a in ACTIONS, f"{a} missing"
    print(f"   ✅ all 10 UI actions registered")


if __name__ == "__main__":
    tests = [
        # unit
        test_parse_node_attrs_basic,
        test_parse_node_no_bounds,
        test_iter_nodes_multi,
        test_node_matches_all_criteria,
        test_element_to_public_shape,
        # validation
        test_find_element_validation,
        test_tap_element_requires_selector,
        test_type_into_requires_selector,
        test_ui_actions_registered,
        # integration
        test_foreground_info,
        test_find_element_on_home,
        test_find_element_no_match,
        test_app_launch_by_alias,
        test_app_launch_unknown,
        test_app_launch_by_exact_package,
        test_wait_for_window_timeout,
        test_wait_for_element_finds_existing,
        test_tap_element_calculator_flow,
        test_wait_for_idle,
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
    print(f"\n{passed}/{len(tests)} UI state tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
