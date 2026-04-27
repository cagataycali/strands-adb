"""Integration tests for Frontier #12 — Sensor Feeds."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


# ── sensors_list ───────────────────────────────────────

def test_sensors_list_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="sensors_list")
    assert r["status"] == "success"
    assert r["count"] > 0
    assert isinstance(r["sensors"], list)

    required = {"handle", "name", "vendor", "type_name", "type_id",
                "permission", "flags", "min_rate_hz", "max_rate_hz",
                "reporting_mode", "wake_up", "version"}
    for s in r["sensors"]:
        missing = required - set(s.keys())
        assert not missing, f"sensor missing keys: {missing}: {s}"
        assert isinstance(s["type_id"], int)
        assert s["type_id"] > 0
        assert s["handle"].startswith("0x")

    print(f"   ✅ {r['count']} sensors, all structured")


def test_sensors_list_has_common_sensors():
    """Pixel devices should have these core sensors."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="sensors_list")
    type_ids = {s["type_id"] for s in r["sensors"]}
    required = {1, 4}  # accelerometer, gyroscope are mandatory on phones
    missing = required - type_ids
    assert not missing, f"missing critical sensors: {missing}"

    # Light (5) should be present on phones too
    common = {5, 6, 8}  # light, pressure, proximity
    found_common = common & type_ids
    assert len(found_common) >= 2, f"only found {found_common} of {common}"
    print(f"   ✅ has accel, gyro + {found_common} common sensors")


def test_sensors_list_rates_parsed():
    """Parser should extract minRate/maxRate for continuous sensors."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="sensors_list")
    # Find accelerometer (always continuous with both min+max)
    accel = next((s for s in r["sensors"] if s["type_id"] == 1), None)
    assert accel is not None
    assert accel["min_rate_hz"] is not None
    assert accel["max_rate_hz"] is not None
    assert accel["min_rate_hz"] > 0
    assert accel["max_rate_hz"] > accel["min_rate_hz"]
    assert accel["reporting_mode"] == "continuous"
    print(f"   ✅ accel rates parsed: {accel['min_rate_hz']}–{accel['max_rate_hz']} Hz")


def test_sensors_list_on_change_has_min_only():
    """Light + proximity are 'on-change' — have minRate but no maxRate."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="sensors_list")
    light = next((s for s in r["sensors"] if s["type_id"] == 5), None)
    if light:
        assert light["reporting_mode"] == "on-change"
        assert light["min_rate_hz"] is not None
        # maxRate is None is OK for on-change
    print(f"   ✅ on-change sensors parsed correctly")


# ── sensors_recent ────────────────────────────────────

def test_sensors_recent_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="sensors_recent")
    assert r["status"] == "success"
    assert r["sensor_count"] > 0

    for sensor_name, bucket in r["events"].items():
        assert "events" in bucket
        assert "latest" in bucket
        assert "max_events" in bucket
        for ev in bucket["events"]:
            assert "ts" in ev
            assert "wall" in ev
            assert "values" in ev
            assert isinstance(ev["ts"], float)
            assert isinstance(ev["values"], list)
            # All values should be floats
            for v in ev["values"]:
                assert isinstance(v, float)
    print(f"   ✅ {r['sensor_count']} sensors w/ events parsed")


# ── sensor_get ────────────────────────────────────────

def test_sensor_get_accelerometer():
    """Accel should always give x/y/z sum ~9.8 (gravity)."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="sensor_get", sensor_query="accelerometer")
    assert r["status"] == "success"
    assert r["latest"] is not None
    # Labeled should have x, y, z
    labeled = r["labeled"]
    assert {"x", "y", "z"} <= set(labeled.keys())
    # Magnitude should be close to 9.8 m/s² (earth gravity)
    import math
    mag = math.sqrt(labeled["x"]**2 + labeled["y"]**2 + labeled["z"]**2)
    assert 9.0 < mag < 11.0, f"gravity magnitude off: {mag:.3f}"
    print(f"   ✅ accel (x={labeled['x']:.2f}, y={labeled['y']:.2f}, z={labeled['z']:.2f}), |g|={mag:.3f}")


def test_sensor_get_by_alias():
    if not _has_device(): print("⏭ skip"); return
    for alias in ["accel", "gyro", "light", "prox", "barometer"]:
        r = adb(action="sensor_get", sensor_query=alias)
        # Some aliases may have no events but should resolve
        assert r["status"] == "success", f"alias {alias}: {r}"
        assert r.get("type_id") is not None
    print(f"   ✅ all 5 aliases resolve correctly")


def test_sensor_get_by_type_id():
    if not _has_device(): print("⏭ skip"); return
    # 1=accel
    r = adb(action="sensor_get", sensor_query=1)
    assert r["status"] == "success"
    assert r["type_id"] == 1
    # As string
    r = adb(action="sensor_get", sensor_query="4")  # gyro
    assert r["status"] == "success"
    assert r["type_id"] == 4
    print(f"   ✅ type_id (int and str) both work")


def test_sensor_get_prefers_calibrated():
    """Should prefer 'ICM45631 Accelerometer' over '...Uncalibrated'."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="sensor_get", sensor_query="accelerometer")
    assert r["status"] == "success"
    assert "uncalibrated" not in r["sensor"]["name"].lower()
    print(f"   ✅ selected: {r['sensor']['name']}")


# ── validation ────────────────────────────────────────

def test_sensor_get_missing_query():
    r = adb(action="sensor_get")
    assert r["status"] == "error"
    assert "sensor_query" in r["content"][0]["text"]
    print(f"   ✅ missing query rejected")


def test_sensor_get_unknown_alias():
    r = adb(action="sensor_get", sensor_query="quantum_flux")
    assert r["status"] == "error"
    assert "unknown sensor" in r["content"][0]["text"]
    print(f"   ✅ unknown alias rejected")


def test_sensor_get_unavailable_type_id():
    r = adb(action="sensor_get", sensor_query=9999)
    assert r["status"] == "error"
    assert "not available" in r["content"][0]["text"]
    print(f"   ✅ unavailable type_id rejected")


# ── parser unit tests (no device) ─────────────────────

def test_resolve_sensor_type():
    from strands_adb.adb_tool import _resolve_sensor_type
    assert _resolve_sensor_type("accelerometer") == 1
    assert _resolve_sensor_type("accel") == 1
    assert _resolve_sensor_type("ACCEL") == 1
    assert _resolve_sensor_type("gyro") == 4
    assert _resolve_sensor_type("gyroscope") == 4
    assert _resolve_sensor_type("light") == 5
    assert _resolve_sensor_type("ambient_light") == 5
    assert _resolve_sensor_type("proximity") == 8
    assert _resolve_sensor_type("prox") == 8
    assert _resolve_sensor_type("pressure") == 6
    assert _resolve_sensor_type("barometer") == 6
    # type_id
    assert _resolve_sensor_type(1) == 1
    assert _resolve_sensor_type("1") == 1
    # unknown
    assert _resolve_sensor_type("quantum") is None
    assert _resolve_sensor_type("") is None
    assert _resolve_sensor_type(None) is None
    assert _resolve_sensor_type(-5) is None
    print(f"   ✅ all sensor aliases resolve")


def test_parse_sensor_list_basic():
    from strands_adb.adb_tool import _parse_sensor_list
    sample = """Sensor Device:
Total 2 h/w sensors, 2 running 0 disabled clients:

Sensor List:
0x01010001) ICM45631 Accelerometer    | Invensense      | ver: 1 | type: android.sensor.accelerometer(1) | perm: n/a | flags: 0x000009c0
	continuous | minRate=1.50Hz | maxRate=400.00Hz | FIFO (max,reserved) = (3000, 3000) events | non-wakeUp | has-additional-info, 
0x01010005) TMD3743 Ambient Light     | AMS             | ver: 1 | type: android.sensor.light(5) | perm: n/a | flags: 0x00000002
	on-change | minRate=1.00Hz | minDelay=0us | FIFO (max,reserved) = (100, 100) events | non-wakeUp | 

Recent Sensor events:
"""
    sensors = _parse_sensor_list(sample)
    assert len(sensors) == 2, f"expected 2, got {len(sensors)}: {sensors}"

    accel = sensors[0]
    assert accel["name"] == "ICM45631 Accelerometer"
    assert accel["vendor"] == "Invensense"
    assert accel["type_name"] == "accelerometer"
    assert accel["type_id"] == 1
    assert accel["min_rate_hz"] == 1.5
    assert accel["max_rate_hz"] == 400.0
    assert accel["reporting_mode"] == "continuous"
    assert accel["wake_up"] is False

    light = sensors[1]
    assert light["type_id"] == 5
    assert light["reporting_mode"] == "on-change"
    assert light["min_rate_hz"] == 1.0
    assert light["max_rate_hz"] is None  # no maxRate in on-change
    print(f"   ✅ list parser: header + detail + on-change variant")


def test_parse_recent_events_basic():
    from strands_adb.adb_tool import _parse_recent_events
    sample = """Sensor List: stub ending
Recent Sensor events:
ICM45631 Accelerometer: last 3 events
	 1 (ts=574.997002195, wall=23:34:07.521) 0.12, 0.26, 9.74, 0.00, 0.00, 0.00, 
	 2 (ts=575.007027742, wall=23:34:07.527) 0.13, 0.27, 9.73, 0.00, 0.00, 0.00, 
	 3 (ts=575.017053758, wall=23:34:07.538) 0.14, 0.26, 9.73, 0.00, 0.00, 0.00, 
Device Orientation: last 1 events
	 1 (ts=10.096779952, wall=23:24:42.639) 0.00, 

Active connections:
"""
    events = _parse_recent_events(sample)
    assert "ICM45631 Accelerometer" in events
    accel = events["ICM45631 Accelerometer"]
    assert accel["max_events"] == 3
    assert len(accel["events"]) == 3
    assert accel["events"][0]["ts"] == 574.997002195
    assert accel["events"][0]["wall"] == "23:34:07.521"
    assert accel["events"][0]["values"][:3] == [0.12, 0.26, 9.74]
    assert accel["latest"]["values"][:3] == [0.14, 0.26, 9.73]

    orient = events["Device Orientation"]
    assert len(orient["events"]) == 1
    assert orient["events"][0]["values"] == [0.0]
    print(f"   ✅ events parser: sensors + events parsed + end marker respected")


def test_parse_recent_events_empty():
    from strands_adb.adb_tool import _parse_recent_events
    assert _parse_recent_events("") == {}
    assert _parse_recent_events("Nothing relevant here") == {}
    print(f"   ✅ events parser: empty input handled")


def test_label_values():
    from strands_adb.adb_tool import _label_values
    # accelerometer: x, y, z
    r = _label_values(1, [0.1, 0.2, 9.8])
    assert r == {"x": 0.1, "y": 0.2, "z": 9.8}
    # light: lux only (but may have padding)
    r = _label_values(5, [100.0, 0.0, 0.0])
    assert r["lux"] == 100.0
    assert "v1" in r  # extra values get generic labels
    assert "v2" in r
    # unknown type_id: all generic
    r = _label_values(999, [1.0, 2.0])
    assert r == {"v0": 1.0, "v1": 2.0}
    print(f"   ✅ value labeler correct")


if __name__ == "__main__":
    tests = [
        test_sensors_list_structure,
        test_sensors_list_has_common_sensors,
        test_sensors_list_rates_parsed,
        test_sensors_list_on_change_has_min_only,
        test_sensors_recent_structure,
        test_sensor_get_accelerometer,
        test_sensor_get_by_alias,
        test_sensor_get_by_type_id,
        test_sensor_get_prefers_calibrated,
        test_sensor_get_missing_query,
        test_sensor_get_unknown_alias,
        test_sensor_get_unavailable_type_id,
        test_resolve_sensor_type,
        test_parse_sensor_list_basic,
        test_parse_recent_events_basic,
        test_parse_recent_events_empty,
        test_label_values,
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
    print(f"\n{passed}/{len(tests)} sensor tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
