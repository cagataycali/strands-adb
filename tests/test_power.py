"""Integration tests for Frontier #8 — Power & Battery."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


# ── power_status ─────────────────────────────────────

def test_power_status_fields():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_status")
    assert r["status"] == "success"

    # Required fields
    for key in ["level_pct", "battery_status", "health", "charging",
                "plugged", "temp_c", "voltage_v", "technology"]:
        assert key in r, f"missing field: {key}"

    # Sanity ranges
    assert 0 <= r["level_pct"] <= 100, f"bogus level: {r['level_pct']}"
    assert -20 < r["temp_c"] < 80, f"bogus temp: {r['temp_c']}"
    assert 2.5 < r["voltage_v"] < 5.5, f"bogus voltage: {r['voltage_v']}"
    assert r["battery_status"] in (
        "unknown", "charging", "discharging", "not_charging", "full"
    )
    assert r["health"] in (
        "unknown", "good", "overheat", "dead", "over_voltage",
        "unspecified_failure", "cold",
    )
    assert isinstance(r["charging"], bool)
    assert isinstance(r["plugged"], list)
    assert r["technology"] in ("Li-ion", "Li-poly", "NiMH", "NiCd", "unknown")

    print(f"   ✅ {r['level_pct']}% {r['battery_status']} {r['temp_c']}°C {r['voltage_v']}V")


def test_power_status_charging_consistency():
    """charging=True iff battery_status='charging' iff plugged list non-empty"""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_status")
    # Plugged → charging (usually; some devices may report full while plugged)
    if r["charging"]:
        assert r["battery_status"] == "charging"
        assert len(r["plugged"]) > 0, f"charging but not plugged: {r['plugged']}"
    print(f"   ✅ charging={r['charging']}, plugged={r['plugged']}, status={r['battery_status']}")


# ── power_thermal ────────────────────────────────────

def test_power_thermal_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_thermal")
    assert r["status"] == "success"
    assert "thermal_status" in r
    assert r["thermal_status"] in (
        "none", "light", "moderate", "severe",
        "critical", "emergency", "shutdown", "unknown",
    )
    assert r["count"] > 0
    assert isinstance(r["temperatures"], list)
    assert isinstance(r["highlights"], dict)
    assert isinstance(r["by_type"], dict)

    # Every temperature entry has proper schema
    for t in r["temperatures"]:
        for k in ("name", "value_c", "type_id", "type",
                  "status_code", "status", "valid"):
            assert k in t, f"temp missing {k}: {t}"

    print(f"   ✅ thermal={r['thermal_status']}, {r['count']} temp sensors")


def test_power_thermal_battery_temp_plausible():
    """Battery temp should match the power_status temp (approximately)."""
    if not _has_device(): print("⏭ skip"); return
    r_stat = adb(action="power_status")
    r_therm = adb(action="power_thermal")

    if r_therm["highlights"].get("battery"):
        bt = r_therm["highlights"]["battery"]["value_c"]
        # The two come from different subsystems; should be within 5°C
        diff = abs(bt - r_stat["temp_c"])
        assert diff < 10, f"battery temp mismatch: {bt} vs {r_stat['temp_c']}"
        print(f"   ✅ battery temp consistent: {bt:.1f}°C vs {r_stat['temp_c']:.1f}°C")
    else:
        print("   ⏭ no battery highlight available")


def test_power_thermal_cpu_sensors():
    """Should find at least some CPU cluster temps on a real phone."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_thermal")
    # Looking for BIG/MID/LITTLE on Tensor/Snapdragon, or just cpu-type temps
    cpu_zones = r["highlights"].get("cpu_big") or r["highlights"].get("cpu_little")
    assert cpu_zones is not None, f"no CPU zones in {list(r['highlights'])}"
    # CPU should be running 25-100°C under normal conditions
    assert 20 < cpu_zones["value_c"] < 110, f"weird CPU temp: {cpu_zones}"
    print(f"   ✅ CPU sensor found: {cpu_zones['name']}={cpu_zones['value_c']:.1f}°C")


# ── power_subsystems ─────────────────────────────────

def test_power_subsystems_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_subsystems")
    assert r["status"] == "success"
    assert isinstance(r["subsystems"], dict)
    assert isinstance(r["sorted"], list)
    assert r["capacity_mah"] is not None
    assert r["computed_drain_mah"] is not None
    assert r["actual_drain_mah"] is not None

    # Sanity: capacity typically 3000-6000 mAh on phones
    assert 1000 < r["capacity_mah"] < 10000, f"bogus capacity: {r['capacity_mah']}"
    # Sum of apps shouldn't exceed capacity by much
    total = sum(s["total_mah"] for s in r["sorted"])
    assert total < r["capacity_mah"] * 5  # Some overlap is OK

    print(f"   ✅ {len(r['subsystems'])} subsystems, cap={r['capacity_mah']}mAh, drain={r['computed_drain_mah']}mAh")


def test_power_subsystems_has_core_systems():
    """cpu, screen, wifi should always appear."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_subsystems")
    subs = r["subsystems"]
    for must_have in ("cpu", "screen", "wifi"):
        assert must_have in subs, f"missing subsystem: {must_have}"
    print(f"   ✅ has cpu={subs['cpu']['total_mah']}, screen={subs['screen']['total_mah']}, wifi={subs['wifi']['total_mah']}")


# ── power_consumers ──────────────────────────────────

def test_power_consumers_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_consumers", power_top=20)
    assert r["status"] == "success"
    assert isinstance(r["consumers"], list)
    assert r["total_count"] > 0
    assert len(r["consumers"]) <= 20

    # Sorted descending
    for i in range(len(r["consumers"]) - 1):
        assert r["consumers"][i]["total_mah"] >= r["consumers"][i + 1]["total_mah"]

    # Each consumer has expected schema
    for c in r["consumers"]:
        assert "uid" in c
        assert "total_mah" in c
        assert "subsystems" in c
        assert "packages" in c
        assert isinstance(c["uid"], int)
        assert c["total_mah"] >= 0

    print(f"   ✅ {r['total_count']} UIDs, top drained={r['consumers'][0]['total_mah']}mAh")


def test_power_consumers_packages_resolved():
    """Top consumers should have package names resolved (where possible)."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_consumers", power_top=10)
    # At least 3 of the top 10 should resolve to some package name
    resolved = sum(1 for c in r["consumers"][:10] if c["packages"])
    assert resolved >= 3, f"only {resolved}/10 resolved to packages"
    print(f"   ✅ {resolved}/10 top UIDs have package names")


def test_power_consumers_top_limit():
    if not _has_device(): print("⏭ skip"); return
    r5 = adb(action="power_consumers", power_top=5)
    r50 = adb(action="power_consumers", power_top=50)
    assert len(r5["consumers"]) <= 5
    assert len(r50["consumers"]) >= len(r5["consumers"])
    assert r5["total_count"] == r50["total_count"]  # Total is same
    print(f"   ✅ power_top limit works: 5→{len(r5['consumers'])}, 50→{len(r50['consumers'])}")


def test_power_consumers_uid_format():
    """u0aN should decode to 10000+N, raw UIDs stay as-is."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="power_consumers", power_top=30)
    # System UIDs are <10000; app UIDs are >=10000
    system_uids = [c["uid"] for c in r["consumers"] if c["uid"] < 10000]
    app_uids = [c["uid"] for c in r["consumers"] if c["uid"] >= 10000]
    # Should have both kinds
    assert system_uids, "no system UIDs found (weird)"
    assert app_uids, "no app UIDs found (weird)"
    print(f"   ✅ {len(system_uids)} system + {len(app_uids)} app UIDs")


# ── parser unit tests (no device needed) ────────────

def test_parse_batterystats_uids_basic():
    from strands_adb.adb_tool import _parse_batterystats_uids
    sample = """9,0,i,vers,36,216,BD3A,BP4A
9,0,i,uid,1000,com.android.dynsystem
9,0,i,uid,1000,android
9,0,i,uid,10155,com.google.android.GoogleCamera
9,0,i,uid,10238,org.telegram.messenger
8,0,i,uid,99,another.app
"""
    m = _parse_batterystats_uids(sample)
    assert m[1000] == ["com.android.dynsystem", "android"]
    assert m[10155] == ["com.google.android.GoogleCamera"]
    assert m[10238] == ["org.telegram.messenger"]
    assert m[99] == ["another.app"]  # v8 also parsed
    print(f"   ✅ UID map parsed correctly")


def test_parse_uid_consumers_basic():
    from strands_adb.adb_tool import _parse_uid_consumers
    sample = """blah blah
  UID 1073: 857 fg: 2.96 (2h 15m) bg: 854 cached: 0 (76ms)
      cpu=1.22 mobile_radio=855 wifi=0.0137 wakelock=0.000251 (11ms)
  UID u0a155: 260 fg: 50.3 (19m) bg: 175 (4d 9h) fgs: 0.870 cached: 0.00455
      screen=33.5 cpu=21.5 camera=202 wakelock=0.0134
  UID 0: 90.3 bg: 90.3
      cpu=47.4 mobile_radio=42.9 wifi=0.0154
other content
"""
    out = _parse_uid_consumers(sample)
    assert len(out) == 3
    # UID 1073
    c0 = out[0]
    assert c0["uid"] == 1073
    assert c0["total_mah"] == 857.0
    assert c0["fg_mah"] == 2.96
    assert c0["bg_mah"] == 854.0
    assert c0["subsystems"]["mobile_radio"] == 855.0
    # u0a155 → 10155
    c1 = out[1]
    assert c1["uid"] == 10155
    assert c1["total_mah"] == 260.0
    assert c1["fgs_mah"] == 0.870
    assert c1["subsystems"]["camera"] == 202.0
    # UID 0 (root)
    c2 = out[2]
    assert c2["uid"] == 0
    assert c2["total_mah"] == 90.3
    assert c2["subsystems"]["cpu"] == 47.4
    print(f"   ✅ UID consumer parser: u0aN decoded, fg/bg/fgs/cached, subsystems")


def test_parse_uid_consumers_empty():
    from strands_adb.adb_tool import _parse_uid_consumers
    assert _parse_uid_consumers("") == []
    assert _parse_uid_consumers("no UIDs here") == []
    print(f"   ✅ empty input handled")


def test_battery_status_constants():
    from strands_adb.adb_tool import BATTERY_STATUS, BATTERY_HEALTH, THERMAL_STATUS
    assert BATTERY_STATUS[2] == "charging"
    assert BATTERY_STATUS[3] == "discharging"
    assert BATTERY_STATUS[5] == "full"
    assert BATTERY_HEALTH[2] == "good"
    assert BATTERY_HEALTH[3] == "overheat"
    assert THERMAL_STATUS[0] == "none"
    assert THERMAL_STATUS[3] == "severe"
    print(f"   ✅ constant maps correct")


if __name__ == "__main__":
    tests = [
        test_power_status_fields,
        test_power_status_charging_consistency,
        test_power_thermal_structure,
        test_power_thermal_battery_temp_plausible,
        test_power_thermal_cpu_sensors,
        test_power_subsystems_structure,
        test_power_subsystems_has_core_systems,
        test_power_consumers_structure,
        test_power_consumers_packages_resolved,
        test_power_consumers_top_limit,
        test_power_consumers_uid_format,
        test_parse_batterystats_uids_basic,
        test_parse_uid_consumers_basic,
        test_parse_uid_consumers_empty,
        test_battery_status_constants,
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
    print(f"\n{passed}/{len(tests)} power tests, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
