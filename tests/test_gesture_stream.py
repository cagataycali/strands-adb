"""Integration tests for Frontier #2 — Touch/Gesture Streaming."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def _wake():
    adb(action="key", key="wakeup")
    time.sleep(0.3)
    adb(action="key", key="menu")
    time.sleep(0.3)
    adb(action="key", key="home")
    time.sleep(0.5)


# ── gesture_stream ───────────────────────────────────────────

def test_gesture_stream_basic():
    if not _has_device(): print("⏭ skip"); return
    _wake()
    r = adb(action="gesture_stream",
            gesture_points=[[540, 1800], [540, 1500], [540, 1200]],
            gesture_step_delay_ms=30)
    assert r["status"] == "success", r
    assert r["steps"] == 3
    assert r["points_count"] == 3
    print(f"   ✅ 3-point stream in {r['elapsed_sec']:.2f}s")


def test_gesture_stream_tuple_points():
    """Accept tuples, not just lists."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="gesture_stream",
            gesture_points=[(300, 500), (300, 600)],
            gesture_step_delay_ms=10)
    assert r["status"] == "success"
    print("   ✅ tuple points accepted")


def test_gesture_stream_validation_min_points():
    r = adb(action="gesture_stream", gesture_points=[[100, 100]])
    assert r["status"] == "error"
    assert "2 points" in r["content"][0]["text"]
    print("   ✅ single point rejected")


def test_gesture_stream_validation_bad_shape():
    r = adb(action="gesture_stream",
            gesture_points=[[100, 100], [200]])
    assert r["status"] == "error"
    print("   ✅ malformed point rejected")


def test_gesture_stream_validation_non_numeric():
    r = adb(action="gesture_stream",
            gesture_points=[[0, 0], ["a", "b"]])
    assert r["status"] == "error"
    assert "numeric" in r["content"][0]["text"]
    print("   ✅ non-numeric coords rejected")


def test_gesture_stream_validation_delay():
    r = adb(action="gesture_stream",
            gesture_points=[[0, 0], [1, 1]],
            gesture_step_delay_ms=5000)
    assert r["status"] == "error"
    print("   ✅ delay out of range rejected")


# ── gesture_long_press ───────────────────────────────────────

def test_long_press_basic():
    if not _has_device(): print("⏭ skip"); return
    _wake()
    r = adb(action="gesture_long_press", x=540, y=1200, gesture_hold_ms=300)
    assert r["status"] == "success"
    # Actual elapsed includes adb RTT so should be > hold_ms but in same ballpark
    assert r["actual_elapsed_ms"] >= 300
    assert r["actual_elapsed_ms"] < 2000  # not crazy high
    print(f"   ✅ hold_ms=300, actual={r['actual_elapsed_ms']}ms")
    adb(action="key", key="back")


def test_long_press_missing_coords():
    r = adb(action="gesture_long_press", gesture_hold_ms=500)
    assert r["status"] == "error"
    print("   ✅ missing x,y rejected")


def test_long_press_hold_too_short():
    r = adb(action="gesture_long_press", x=100, y=100, gesture_hold_ms=50)
    assert r["status"] == "error"
    print("   ✅ hold_ms=50 rejected (min 100)")


def test_long_press_hold_too_long():
    r = adb(action="gesture_long_press", x=100, y=100, gesture_hold_ms=20000)
    assert r["status"] == "error"
    print("   ✅ hold_ms=20000 rejected (max 10000)")


# ── gesture_path (DSL) ───────────────────────────────────────

def test_path_line_horizontal():
    if not _has_device(): print("⏭ skip"); return
    _wake()
    r = adb(action="gesture_path", x=200, y=1200, gesture_shape="line_h",
            gesture_size=400, gesture_steps=10, gesture_step_delay_ms=20)
    assert r["status"] == "success", r
    assert r["points_count"] == 10
    print(f"   ✅ line_h 10 pts in {r['elapsed_sec']:.2f}s")


def test_path_line_vertical():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="gesture_path", x=540, y=800, gesture_shape="line_v",
            gesture_size=400, gesture_steps=10, gesture_step_delay_ms=20)
    assert r["status"] == "success"
    print(f"   ✅ line_v 10 pts")


def test_path_circle():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="gesture_path", x=540, y=1500, gesture_shape="circle",
            gesture_size=150, gesture_steps=16, gesture_step_delay_ms=20)
    assert r["status"] == "success"
    assert r["points_count"] == 16
    print(f"   ✅ circle 16 pts in {r['elapsed_sec']:.2f}s")


def test_path_arc():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="gesture_path", x=540, y=1500, gesture_shape="arc",
            gesture_size=200, gesture_steps=12, gesture_step_delay_ms=20)
    assert r["status"] == "success"
    print(f"   ✅ arc 12 pts")


def test_path_zigzag():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="gesture_path", x=200, y=1500, gesture_shape="zigzag",
            gesture_size=400, gesture_steps=20, gesture_step_delay_ms=15)
    assert r["status"] == "success"
    assert r["points_count"] == 20
    print(f"   ✅ zigzag 20 pts")


def test_path_square():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="gesture_path", x=300, y=1200, gesture_shape="square",
            gesture_size=300, gesture_steps=16, gesture_step_delay_ms=15)
    assert r["status"] == "success"
    assert r["points_count"] >= 12  # per-side * 4 ≈ steps
    print(f"   ✅ square {r['points_count']} pts")


def test_path_invalid_shape():
    r = adb(action="gesture_path", x=100, y=100, gesture_shape="spiral")
    assert r["status"] == "error"
    assert "unknown shape" in r["content"][0]["text"]
    print("   ✅ bad shape rejected with helpful message")


def test_path_steps_out_of_range():
    r = adb(action="gesture_path", x=100, y=100, gesture_shape="line_h",
            gesture_steps=500)
    assert r["status"] == "error"
    print("   ✅ steps>200 rejected")


def test_path_missing_anchor():
    r = adb(action="gesture_path", gesture_shape="line_h")
    assert r["status"] == "error"
    assert "anchor" in r["content"][0]["text"] or "requires" in r["content"][0]["text"]
    print("   ✅ missing anchor rejected")


# ── gesture_pinch (documented stub) ──────────────────────────

def test_pinch_explains_limitation():
    r = adb(action="gesture_pinch", x=540, y=1200, gesture_size=300)
    assert r["status"] == "error"
    txt = r["content"][0]["text"].lower()
    assert "root" in txt
    assert "sendevent" in txt or "accessibility" in txt
    print("   ✅ pinch reports clear explanation of limitation")


def test_pinch_missing_center():
    r = adb(action="gesture_pinch", gesture_size=100)
    assert r["status"] == "error"
    print("   ✅ pinch missing center rejected")


# ── visual smoke test: long-press changes the screen ─────────

def test_long_press_affects_ui():
    """Long-press a home icon and verify screen contents change."""
    if not _has_device(): print("⏭ skip"); return
    _wake()
    import tempfile, os
    before = tempfile.mktemp(suffix=".png")
    after = tempfile.mktemp(suffix=".png")
    adb(action="screenshot", output_path=before, include_image=False)
    time.sleep(0.3)
    adb(action="gesture_long_press", x=540, y=2100, gesture_hold_ms=700)
    time.sleep(0.5)
    adb(action="screenshot", output_path=after, include_image=False)
    b = os.path.getsize(before)
    a = os.path.getsize(after)
    adb(action="key", key="back")
    os.unlink(before); os.unlink(after)
    # Screenshots should differ meaningfully (popup appeared)
    assert abs(a - b) > 500, (
        f"expected visible UI change, but screenshots differ by only "
        f"{abs(a - b)} bytes — gesture may not have landed"
    )
    print(f"   ✅ long-press caused UI change ({abs(a-b)} byte diff)")


if __name__ == "__main__":
    tests = [
        test_gesture_stream_basic,
        test_gesture_stream_tuple_points,
        test_gesture_stream_validation_min_points,
        test_gesture_stream_validation_bad_shape,
        test_gesture_stream_validation_non_numeric,
        test_gesture_stream_validation_delay,
        test_long_press_basic,
        test_long_press_missing_coords,
        test_long_press_hold_too_short,
        test_long_press_hold_too_long,
        test_path_line_horizontal,
        test_path_line_vertical,
        test_path_circle,
        test_path_arc,
        test_path_zigzag,
        test_path_square,
        test_path_invalid_shape,
        test_path_steps_out_of_range,
        test_path_missing_anchor,
        test_pinch_explains_limitation,
        test_pinch_missing_center,
        test_long_press_affects_ui,
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
    print(f"\n{passed}/{len(tests)} gesture tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
