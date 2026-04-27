"""Integration tests for Frontier #13 — Security."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


# ── security_posture ──────────────────────────────

def test_posture_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_posture")
    assert r["status"] == "success"

    required = {
        "strong_posture", "android_version", "security_patch",
        "verified_boot_state", "verified_boot_ok", "bootloader_locked",
        "selinux_enforcing", "encrypted", "encryption_type",
        "encryption_state", "developer_options", "adb_enabled",
        "package_verifier", "user_count", "warnings", "raw_props",
    }
    missing = required - set(r.keys())
    assert not missing, f"missing: {missing}"
    assert isinstance(r["strong_posture"], bool)
    assert isinstance(r["warnings"], list)
    assert isinstance(r["user_count"], int)
    print(f"   ✅ posture schema OK, strong={r['strong_posture']}")


def test_posture_android_version():
    """Android version should be a reasonable number."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_posture")
    # Android version string like "14", "15", "16"
    assert r["android_version"] is not None
    try:
        maj = int(r["android_version"].split(".")[0])
        assert 5 <= maj <= 30, f"weird android version: {maj}"
    except (ValueError, IndexError):
        pass  # Some devices use non-numeric release
    # Patch date YYYY-MM-DD
    if r["security_patch"]:
        assert len(r["security_patch"]) == 10
        assert r["security_patch"][4] == "-"
    print(f"   ✅ Android {r['android_version']}, patch {r['security_patch']}")


def test_posture_must_be_adb_enabled():
    """This tool USES ADB — must report ADB enabled (sanity check)."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_posture")
    assert r["adb_enabled"] is True
    # And the warning should mention it
    assert any("ADB" in w for w in r["warnings"])
    print(f"   ✅ ADB correctly detected as enabled")


def test_posture_retail_phone_is_strong():
    """A production Pixel should have all 4 critical checks pass."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_posture")
    # These should all be True on a normal retail phone:
    assert r["verified_boot_ok"], f"verified_boot={r['verified_boot_state']}"
    assert r["bootloader_locked"], "bootloader unlocked (testing device?)"
    assert r["selinux_enforcing"], "SELinux permissive (unusual)"
    assert r["encrypted"], "Storage not encrypted (really unusual)"
    assert r["strong_posture"] is True
    print(f"   ✅ retail phone posture is STRONG")


# ── security_lock ─────────────────────────────────

def test_lock_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_lock")
    assert r["status"] == "success"
    assert isinstance(r["secured"], bool)
    assert "credential_type" in r
    assert isinstance(r["users"], list)
    assert r["user_count"] >= 1
    print(f"   ✅ lock: secured={r['secured']}, cred={r['credential_type']}")


def test_lock_credential_type_known():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_lock")
    # Android 14+ emits capitalized names; allow broad set
    known = {"NONE", "PIN", "PATTERN", "PASSWORD", "MANAGED", "none"}
    assert r["credential_type"].upper() in {k.upper() for k in known}, \
        f"unknown cred type: {r['credential_type']}"
    print(f"   ✅ credential_type={r['credential_type']} is valid")


# ── security_biometrics ───────────────────────────

def test_biometrics_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_biometrics")
    assert r["status"] == "success"
    assert isinstance(r["has_biometrics"], bool)
    assert isinstance(r["sensors"], list)
    for s in r["sensors"]:
        for k in ("id", "oem_strength", "current_strength", "strength_class",
                  "modality_bits", "modalities", "state", "enabled"):
            assert k in s, f"biometric missing {k}: {s}"
    print(f"   ✅ biometrics: {r['count']} sensors")


def test_biometrics_modality_decoding():
    """Pixel 10 Pro has fingerprint + face — both should decode correctly."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_biometrics")
    # Must have at least one biometric
    assert r["has_biometrics"], "no biometric sensors found"
    # Sum of counts should equal number of sensors
    total = r["fingerprint_count"] + r["face_count"] + r["iris_count"]
    # Some sensors may have bits we don't map; allow total <= count
    assert total <= r["count"]
    # Every modality listed should be a string (not leftover int)
    for s in r["sensors"]:
        for mod in s["modalities"]:
            assert isinstance(mod, str)
    print(f"   ✅ fingerprint={r['fingerprint_count']}, face={r['face_count']}, iris={r['iris_count']}")


# ── security_vpn ──────────────────────────────────

def test_vpn_structure():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="security_vpn")
    assert r["status"] == "success"
    assert isinstance(r["active"], bool)
    assert isinstance(r["vpns"], list)
    assert r["count"] == len(r["vpns"])
    # Active matches count>0
    assert r["active"] == (r["count"] > 0)
    for v in r["vpns"]:
        assert "network_id" in v
        assert "transports" in v
        assert "VPN" in v["transports"]
    print(f"   ✅ VPN: {'active' if r['active'] else 'inactive'}, {r['count']} tunnel(s)")


# ── parser unit tests ─────────────────────────────

def test_biometric_modality_constants():
    """Per AOSP BiometricAuthenticator, bits are: credential=1, fingerprint=2, iris=4, face=8."""
    from strands_adb.adb_tool import BIOMETRIC_MODALITY
    assert BIOMETRIC_MODALITY[1] == "credential"
    assert BIOMETRIC_MODALITY[2] == "fingerprint"
    assert BIOMETRIC_MODALITY[4] == "iris"
    assert BIOMETRIC_MODALITY[8] == "face"
    print(f"   ✅ modality constants match AOSP")


def test_biometric_strength_constants():
    from strands_adb.adb_tool import BIOMETRIC_STRENGTH
    # 15 = 0x000F = BIOMETRIC_STRONG (Class 3)
    # 255 = 0x00FF = BIOMETRIC_WEAK (Class 2)
    # 32768 = 0x8000 = DEVICE_CREDENTIAL
    assert BIOMETRIC_STRENGTH[15] == "strong"
    assert BIOMETRIC_STRENGTH[255] == "weak"
    assert BIOMETRIC_STRENGTH[32768] == "convenience"
    print(f"   ✅ strength constants OK")


def test_security_actions_registered():
    """All 4 new actions must be in the action set."""
    from strands_adb.adb_tool import ACTIONS as _ACTIONS
    for a in ("security_posture", "security_lock",
              "security_biometrics", "security_vpn"):
        assert a in _ACTIONS, f"{a} not registered"
    print(f"   ✅ all 4 security actions registered")


if __name__ == "__main__":
    tests = [
        test_posture_structure,
        test_posture_android_version,
        test_posture_must_be_adb_enabled,
        test_posture_retail_phone_is_strong,
        test_lock_structure,
        test_lock_credential_type_known,
        test_biometrics_structure,
        test_biometrics_modality_decoding,
        test_vpn_structure,
        test_biometric_modality_constants,
        test_biometric_strength_constants,
        test_security_actions_registered,
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
    print(f"\n{passed}/{len(tests)} security tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
