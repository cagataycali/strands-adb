"""Integration tests for Frontier #3 — logcat event stream."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device() -> bool:
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def test_stream_start_stop():
    """Start + stop cleanly."""
    if not _has_device():
        print("⏭  skip: no adb device")
        return
    r = adb(action="log_stream_start")
    assert r.get("status") == "success", r
    time.sleep(0.5)
    r = adb(action="log_stream_status")
    assert r.get("running") is True, r
    r = adb(action="log_stream_stop")
    assert r.get("status") == "success", r
    # Idempotent stop
    r = adb(action="log_stream_stop")
    assert r.get("status") == "success", r
    print("   ✅ start/stop idempotent")


def test_stream_idempotent_start():
    """Starting while running returns success + already_running flag."""
    if not _has_device():
        print("⏭  skip: no adb device")
        return
    adb(action="log_stream_start")
    r = adb(action="log_stream_start")
    assert r.get("status") == "success"
    assert r.get("already_running") is True
    adb(action="log_stream_stop")
    print("   ✅ double-start is safe")


def test_stream_captures_app_launch():
    """Start stream, launch a known app, verify event lands on event_bus."""
    if not _has_device():
        print("⏭  skip: no adb device")
        return
    try:
        from devduck.tools.event_bus import bus
    except ImportError:
        print("⏭  skip: devduck not installed")
        return

    adb(action="log_stream_start")
    time.sleep(1.5)  # warmup

    baseline = len([e for e in bus.recent(200)
                    if e.event_type == "phone.log.app_launch"])

    # Launch something distinctive
    adb(action="home")
    time.sleep(0.5)
    adb(action="launch", package="com.google.android.calculator")
    time.sleep(2.5)

    events = [e for e in bus.recent(200)
              if e.event_type == "phone.log.app_launch"]
    new_events = len(events) - baseline

    adb(action="log_stream_stop")

    assert new_events > 0, f"no new app_launch events captured (baseline={baseline})"
    # Check one of them mentions calculator
    calc_launches = [e for e in events[-new_events:]
                     if "calculator" in e.summary.lower()]
    assert calc_launches, f"calculator not in events: {[e.summary for e in events[-new_events:]]}"
    print(f"   ✅ captured {new_events} app_launch events; calculator seen")


def test_classifier_unit():
    """Unit-test the classifier on synthetic lines."""
    from strands_adb.adb_tool import _classify_logcat

    # App launch
    evt = _classify_logcat({
        "tag": "ActivityTaskManager", "msg": "START u0 {cmp=com.example.foo/.MainActivity} from ...",
        "level": "I",
    })
    assert evt and evt["category"] == "app_launch"
    assert evt.get("package") == "com.example.foo"

    # Crash
    evt = _classify_logcat({
        "tag": "AndroidRuntime", "msg": "FATAL EXCEPTION: main",
        "level": "E",
    })
    assert evt and evt["category"] == "crash"
    assert evt["severity"] == "error"

    # Ignore junk
    evt = _classify_logcat({
        "tag": "SomeRandomTag", "msg": "blah blah",
        "level": "W",
    })
    assert evt is None

    # Call ringing
    evt = _classify_logcat({
        "tag": "TelephonyManager", "msg": "onCallStateChanged state=RINGING number=+1...",
        "level": "I",
    })
    assert evt and evt["category"] == "call_ringing"

    print("   ✅ classifier unit tests pass")


if __name__ == "__main__":
    tests = [
        test_classifier_unit,
        test_stream_start_stop,
        test_stream_idempotent_start,
        test_stream_captures_app_launch,
    ]
    passed = 0
    failed = 0
    for t in tests:
        print(f"▶ {t.__name__}")
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} logcat-stream tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
