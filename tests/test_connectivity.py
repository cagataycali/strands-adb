"""Integration tests for Frontier #7 — Wi-Fi, Bluetooth, Airplane Mode."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def _wifi_enabled():
    r = adb(action="wifi_status")
    return r.get("status") == "success" and r.get("enabled")


# ── wifi_status ─────────────────────────────────────────

def test_wifi_status_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="wifi_status")
    assert r["status"] == "success"
    # These keys always present
    for k in ("enabled", "connected", "ssid", "bssid", "rssi", "frequency"):
        assert k in r, f"missing {k}"
    assert isinstance(r["enabled"], bool)
    assert isinstance(r["connected"], bool)
    print(f"   ✅ status: enabled={r['enabled']}, connected={r['connected']}, ssid={r['ssid']}")


def test_wifi_status_connected_parsing():
    """If connected, SSID must not have embedded quotes and RSSI must be int."""
    if not _wifi_enabled(): print("⏭ wifi disabled"); return
    r = adb(action="wifi_status")
    if r["connected"]:
        assert r["ssid"], "connected but no ssid"
        assert not r["ssid"].startswith('"'), f"quoted ssid leaked: {r['ssid']!r}"
        assert not r["ssid"].endswith('"')
        if r["rssi"] is not None:
            assert isinstance(r["rssi"], int)
            assert -100 < r["rssi"] < 0, f"rssi out of range: {r['rssi']}"
        if r["frequency"] is not None:
            assert isinstance(r["frequency"], int)
            assert 2400 <= r["frequency"] <= 7100, f"freq: {r['frequency']}"
    print(f"   ✅ connected state correctly parsed")


# ── wifi_scan ──────────────────────────────────────────

def test_wifi_scan_structure():
    if not _wifi_enabled(): print("⏭ wifi disabled"); return
    r = adb(action="wifi_scan", wifi_scan_wait_sec=4.0)
    assert r["status"] == "success"
    assert isinstance(r["networks"], list)
    assert "count" in r
    for n in r["networks"]:
        for k in ("bssid", "frequency", "rssi", "ssid", "flags",
                  "security", "band"):
            assert k in n, f"missing {k} in {n}"
        assert n["band"] in ("2.4GHz", "5GHz", "6GHz")
        assert n["security"] in (
            "open", "owe", "wep", "wpa", "wpa2", "wpa3", "unknown"
        )
        # BSSID format
        assert n["bssid"].count(":") == 5, f"bad bssid: {n['bssid']}"
    print(f"   ✅ {r['count']} networks, all structured")


def test_wifi_scan_sorted_by_rssi():
    if not _wifi_enabled(): print("⏭ wifi disabled"); return
    r = adb(action="wifi_scan", wifi_scan_wait_sec=4.0)
    nets = r["networks"]
    for i in range(1, len(nets)):
        assert nets[i - 1]["rssi"] >= nets[i]["rssi"], (
            f"not sorted: {nets[i-1]['rssi']} < {nets[i]['rssi']}"
        )
    print(f"   ✅ sorted strongest-first")


def test_wifi_scan_hidden_network_handling():
    """Ensure hidden (empty SSID) networks don't pick up flags as SSID."""
    if not _wifi_enabled(): print("⏭ wifi disabled"); return
    r = adb(action="wifi_scan", wifi_scan_wait_sec=4.0)
    for n in r["networks"]:
        # SSID must never start with '[' (would mean flags leaked)
        assert not n["ssid"].startswith("["), (
            f"flags leaked as SSID: {n['ssid']!r}"
        )
    hidden = [n for n in r["networks"] if not n["ssid"]]
    print(f"   ✅ no flag-leak ({len(hidden)} hidden networks)")


def test_wifi_scan_when_disabled():
    """Scanning while wifi off should error cleanly."""
    if not _has_device(): print("⏭ skip"); return
    # Save state
    was_enabled = _wifi_enabled()
    if was_enabled:
        adb(action="wifi_enable", wifi_enabled=False)
        time.sleep(1.5)
    try:
        r = adb(action="wifi_scan", wifi_scan_wait_sec=1.0)
        assert r["status"] == "error"
        assert "disabled" in r["content"][0]["text"].lower()
    finally:
        if was_enabled:
            adb(action="wifi_enable", wifi_enabled=True)
            time.sleep(2.0)
    print(f"   ✅ scan-while-off fails cleanly")


# ── wifi_list_saved ─────────────────────────────────────

def test_wifi_list_saved_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="wifi_list_saved")
    assert r["status"] == "success"
    assert isinstance(r["saved"], list)
    for n in r["saved"]:
        assert "network_id" in n
        assert "ssid" in n
        assert "security_types" in n
        assert isinstance(n["network_id"], int)
        assert isinstance(n["security_types"], list)
    print(f"   ✅ {r['count']} saved networks, all structured")


# ── bt_status / bt_enable ───────────────────────────────

def test_bt_status_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="bt_status")
    assert r["status"] == "success"
    for k in ("enabled", "name", "address", "state", "bonded_count"):
        assert k in r
    assert isinstance(r["enabled"], bool)
    assert isinstance(r["bonded_count"], int)
    if r["enabled"]:
        assert r["state"] == "ON"
    print(f"   ✅ BT: enabled={r['enabled']}, state={r['state']}, bonded={r['bonded_count']}")


# ── airplane mode round trip ────────────────────────────

def test_airplane_mode_round_trip():
    """Toggle airplane mode on and off. WILL affect connectivity briefly."""
    if not _has_device(): print("⏭ skip"); return
    initial = adb(action="airplane_mode_get")
    assert initial["status"] == "success"
    was_enabled = initial["enabled"]

    try:
        # Set to the OPPOSITE state
        target = not was_enabled
        r = adb(action="airplane_mode_set", airplane_enabled=target)
        assert r["status"] == "success"
        assert r["requested"] == target
        # Verify via separate call
        verify = adb(action="airplane_mode_get")
        assert verify["enabled"] == target, (
            f"expected {target}, got {verify['enabled']}"
        )
    finally:
        # Restore
        adb(action="airplane_mode_set", airplane_enabled=was_enabled)
        time.sleep(1.5)
    print(f"   ✅ airplane mode round trip (started={was_enabled})")


# ── wifi_connect / wifi_forget validation ───────────────

def test_wifi_connect_missing_ssid():
    r = adb(action="wifi_connect")
    assert r["status"] == "error"
    assert "ssid" in r["content"][0]["text"].lower()
    print(f"   ✅ missing ssid rejected")


def test_wifi_connect_bad_security():
    r = adb(action="wifi_connect",
            wifi_ssid="test", wifi_security="wpa4")
    assert r["status"] == "error"
    assert "security must be" in r["content"][0]["text"]
    print(f"   ✅ bad security type rejected")


def test_wifi_connect_wpa2_requires_pass():
    r = adb(action="wifi_connect",
            wifi_ssid="test", wifi_security="wpa2")
    assert r["status"] == "error"
    assert "passphrase" in r["content"][0]["text"].lower()
    print(f"   ✅ wpa2 without passphrase rejected")


def test_wifi_connect_open_rejects_pass():
    r = adb(action="wifi_connect",
            wifi_ssid="test", wifi_security="open",
            wifi_passphrase="should-not-be-here")
    assert r["status"] == "error"
    assert "does not take a passphrase" in r["content"][0]["text"]
    print(f"   ✅ open with passphrase rejected")


def test_wifi_forget_negative_id():
    r = adb(action="wifi_forget", wifi_network_id=-1)
    assert r["status"] == "error"
    assert "non-negative" in r["content"][0]["text"]
    print(f"   ✅ negative network_id rejected")


# ── parser unit tests (no device needed) ────────────────

def test_parse_wifi_status_connected():
    from strands_adb.adb_tool import _parse_wifi_status
    out = '''Wifi is enabled
Wifi is connected to "MyNetwork"
WifiInfo: SSID: "MyNetwork", BSSID: aa:bb:cc:dd:ee:ff, MAC: 00:11:22:33:44:55, IP: /10.0.0.5, Security type: 4, RSSI: -45, Link speed: 1200Mbps, Frequency: 5180MHz, Net ID: 0
'''
    info = _parse_wifi_status(out)
    assert info["enabled"] is True
    assert info["connected"] is True
    assert info["ssid"] == "MyNetwork", f"ssid: {info['ssid']!r}"
    assert info["bssid"] == "aa:bb:cc:dd:ee:ff"
    assert info["rssi"] == -45
    assert info["link_speed_mbps"] == 1200
    assert info["frequency"] == 5180
    print(f"   ✅ parser: connected case clean")


def test_parse_wifi_status_disabled():
    from strands_adb.adb_tool import _parse_wifi_status
    info = _parse_wifi_status("Wifi is disabled\nWifi scanning is only available when wifi is enabled\n")
    assert info["enabled"] is False
    assert info["connected"] is False
    assert info["ssid"] is None
    print(f"   ✅ parser: disabled case clean")


def test_parse_scan_results_empty():
    from strands_adb.adb_tool import _parse_scan_results
    assert _parse_scan_results("") == []
    assert _parse_scan_results("No scan results") == []
    print(f"   ✅ parser: empty scan handled")


def test_parse_scan_results_table():
    from strands_adb.adb_tool import _parse_scan_results
    out = """BSSID              Frequency      RSSI           Age(sec)     SSID                                 Flags
  aa:bb:cc:dd:ee:f1       2462        -40              1.0    HomeWiFi                        [WPA2-PSK-CCMP-128][RSN-PSK-CCMP-128][ESS]
  aa:bb:cc:dd:ee:f2       5180        -55              1.0                                   [RSN-SAE-CCMP-128][ESS][MFPR][MFPC]
  aa:bb:cc:dd:ee:f3       6000        -60              1.0    SixGig                          [RSN-SAE-CCMP-128][ESS]
"""
    r = _parse_scan_results(out)
    assert len(r) == 3, f"expected 3, got {len(r)}: {r}"
    # First: normal
    assert r[0]["ssid"] == "HomeWiFi"
    assert r[0]["rssi"] == -40
    assert r[0]["band"] == "2.4GHz"
    assert r[0]["security"] == "wpa2"
    # Second: HIDDEN — empty SSID, flags must not leak as SSID
    assert r[1]["ssid"] == "", f"hidden SSID not empty: {r[1]['ssid']!r}"
    assert r[1]["security"] == "wpa3"  # SAE
    assert r[1]["band"] == "5GHz"
    assert len(r[1]["flags"]) >= 2
    # Third: 6GHz
    assert r[2]["band"] == "6GHz"
    assert r[2]["ssid"] == "SixGig"
    print(f"   ✅ parser: table with hidden networks correct")


def test_parse_saved_networks_collapse():
    from strands_adb.adb_tool import _parse_saved_networks
    out = """Network Id      SSID                         Security type
0            HomeWiFi                         wpa2-psk
0            HomeWiFi                         wpa3-sae^
1            cafe                             open
"""
    r = _parse_saved_networks(out)
    assert len(r) == 2, f"should collapse dup ids, got {r}"
    home = next((n for n in r if n["network_id"] == 0), None)
    assert home is not None
    assert "wpa2-psk" in home["security_types"]
    assert "wpa3-sae" in home["security_types"]
    cafe = next((n for n in r if n["network_id"] == 1), None)
    assert cafe is not None
    assert "open" in cafe["security_types"]
    print(f"   ✅ parser: collapses multi-security-type rows")


def test_parse_bt_status():
    from strands_adb.adb_tool import _parse_bt_status
    out = """Bluetooth Status:
  State:         ON
  Address:       aa:bb:cc:dd:ee:ff
  Name:          Test Device
BluetoothAdapterProperties
  Name: Test Device
  ConnectionState: STATE_DISCONNECTED
  State: ON
  Discovering: false
BluetoothRemoteDevices
  Bonded devices: 3
"""
    info = _parse_bt_status(out)
    assert info["enabled"] is True
    assert info["state"] == "ON"
    assert info["name"] == "Test Device"
    assert info["address"] == "aa:bb:cc:dd:ee:ff"
    assert info["bonded_count"] == 3
    assert info["discovering"] is False
    assert info["connection_state"] == "STATE_DISCONNECTED"
    print(f"   ✅ parser: BT status clean")


def test_security_from_flags():
    from strands_adb.adb_tool import _security_from_flags
    assert _security_from_flags(["[RSN-SAE-CCMP-128]"]) == "wpa3"
    assert _security_from_flags(["[WPA2-PSK-CCMP-128]"]) == "wpa2"
    assert _security_from_flags(["[WEP]"]) == "wep"
    assert _security_from_flags(["[OWE]"]) == "owe"
    assert _security_from_flags([]) == "open"
    assert _security_from_flags(["[ESS]"]) == "open"
    print(f"   ✅ security classifier correct")


def test_extract_leading_int():
    from strands_adb.adb_tool import _extract_leading_int
    assert _extract_leading_int("2401Mbps") == 2401
    assert _extract_leading_int("6135MHz") == 6135
    assert _extract_leading_int("-57") == -57
    assert _extract_leading_int("-57 ") == -57
    assert _extract_leading_int("abc") is None
    assert _extract_leading_int("") is None
    assert _extract_leading_int(None) is None
    print(f"   ✅ _extract_leading_int correct")


if __name__ == "__main__":
    tests = [
        test_wifi_status_structure,
        test_wifi_status_connected_parsing,
        test_wifi_scan_structure,
        test_wifi_scan_sorted_by_rssi,
        test_wifi_scan_hidden_network_handling,
        test_wifi_scan_when_disabled,
        test_wifi_list_saved_structure,
        test_bt_status_structure,
        test_airplane_mode_round_trip,
        test_wifi_connect_missing_ssid,
        test_wifi_connect_bad_security,
        test_wifi_connect_wpa2_requires_pass,
        test_wifi_connect_open_rejects_pass,
        test_wifi_forget_negative_id,
        test_parse_wifi_status_connected,
        test_parse_wifi_status_disabled,
        test_parse_scan_results_empty,
        test_parse_scan_results_table,
        test_parse_saved_networks_collapse,
        test_parse_bt_status,
        test_security_from_flags,
        test_extract_leading_int,
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
    print(f"\n{passed}/{len(tests)} connectivity tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
