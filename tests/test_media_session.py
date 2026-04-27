"""Integration tests for Frontier #5 — Media Session & AVRCP."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


# ── sessions list / now playing ────────────────────────

def test_sessions_list():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="media_sessions_list")
    assert r["status"] == "success"
    assert "sessions" in r
    assert "count" in r
    # Parser must not pick up prose lines
    for sess in r["sessions"]:
        tag = sess["tag"]
        assert tag, f"empty tag in session: {sess}"
        assert not tag.startswith(("Global", "priority", "owner")), (
            f"prose line leaked into sessions: {tag!r}"
        )
        # tag must appear in package component path
        assert "package" in sess
    print(f"   ✅ {r['count']} sessions parsed, no prose leaks")


def test_now_playing():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="media_now_playing")
    assert r["status"] == "success"
    # Either something is playing or "nothing is playing"
    if r.get("playing"):
        assert r.get("package")
        print(f"   ✅ playing: {r.get('package')}")
    else:
        assert "nothing is playing" in r["content"][0]["text"]
        print(f"   ✅ nothing playing (expected)")


# ── volume get/set/adjust ────────────────────────────────

def test_volume_get_music():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="media_volume_get", media_stream="music")
    assert r["status"] == "success"
    assert r["stream"] == "music"
    assert r["stream_id"] == 3
    assert isinstance(r["volume"], int)
    assert r["volume"] >= 0
    assert r["volume_max"] > 0
    print(f"   ✅ music volume: {r['volume']}/{r['volume_max']}")


def test_volume_get_all_streams():
    if not _has_device(): print("⏭ skip"); return
    streams = ["music", "ring", "alarm", "notification", "accessibility"]
    for stream in streams:
        r = adb(action="media_volume_get", media_stream=stream)
        assert r["status"] == "success", f"{stream}: {r}"
        assert r.get("volume") is not None
    print(f"   ✅ all {len(streams)} streams queryable")


def test_volume_set_api_round_trip():
    """Save volume, call set, verify API echoes the request, restore.

    Note: Android may enforce policies (DND, ringer mode, fixed-volume
    BT headsets) that prevent the actual volume from changing. We test
    that the API succeeds and echoes sensible data, not that the
    device obeys — that's outside our control.
    """
    if not _has_device(): print("⏭ skip"); return
    snap = adb(action="media_volume_get", media_stream="ring")
    original = snap["volume"]
    assert isinstance(original, int)

    try:
        r = adb(action="media_volume_set",
                media_stream="ring", media_volume_index=1)
        assert r["status"] == "success"
        assert r["requested"] == 1
        assert r["stream"] == "ring"
        # actual may differ from requested if phone policy prevents change
        assert isinstance(r.get("actual"), int)
    finally:
        adb(action="media_volume_set",
            media_stream="ring", media_volume_index=original)
    print(f"   ✅ set API round-trips cleanly (requested=1, policy may bound actual)")


def test_volume_adjust():
    """Use adjust to bump volume up then down — net zero."""
    if not _has_device(): print("⏭ skip"); return
    snap = adb(action="media_volume_get", media_stream="ring")
    original = snap["volume"]
    mx = snap["volume_max"]

    try:
        # If not at max, bump up
        if original < mx:
            r = adb(action="media_volume_adjust",
                    media_stream="ring", media_volume_direction="up")
            assert r["status"] == "success"
            after_up = adb(action="media_volume_get", media_stream="ring")
            assert after_up["volume"] >= original  # may stick at max
        # Bump down
        r = adb(action="media_volume_adjust",
                media_stream="ring", media_volume_direction="down")
        assert r["status"] == "success"

        # 'same' — should return current
        r = adb(action="media_volume_adjust",
                media_stream="ring", media_volume_direction="same")
        assert r["status"] == "success"
    finally:
        adb(action="media_volume_set",
            media_stream="ring", media_volume_index=original)
    print(f"   ✅ adjust up/down/same all work")


def test_volume_direction_aliases():
    """English aliases should work."""
    if not _has_device(): print("⏭ skip"); return
    snap = adb(action="media_volume_get", media_stream="ring")
    original = snap["volume"]
    try:
        for d in ["louder", "quieter", "keep", "higher", "lower"]:
            r = adb(action="media_volume_adjust",
                    media_stream="ring", media_volume_direction=d)
            assert r["status"] == "success", f"direction={d}: {r}"
    finally:
        adb(action="media_volume_set",
            media_stream="ring", media_volume_index=original)
    print(f"   ✅ all 5 English aliases work")


def test_numeric_stream_id():
    """Passing stream as digit string should resolve."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="media_volume_get", media_stream="3")  # music
    assert r["status"] == "success"
    assert r["stream"] == "music"
    print(f"   ✅ numeric stream id → name")


# ── dispatch ────────────────────────────────────────────

def test_dispatch_all_keys():
    """Every valid key should dispatch successfully (even if no player)."""
    if not _has_device(): print("⏭ skip"); return
    keys = [
        "play", "pause", "play-pause",
        "next", "previous", "stop",
        "rewind", "fast-forward",
    ]
    for k in keys:
        r = adb(action="media_dispatch", media_key=k)
        assert r["status"] == "success", f"{k}: {r}"
    print(f"   ✅ all {len(keys)} media keys dispatched")


def test_dispatch_aliases():
    if not _has_device(): print("⏭ skip"); return
    # skip → next, back → previous, toggle → play-pause
    for alias, expected in [
        ("skip", "next"),
        ("back", "previous"),
        ("prev", "previous"),
        ("toggle", "play-pause"),
        ("ff", "fast-forward"),
    ]:
        r = adb(action="media_dispatch", media_key=alias)
        assert r["status"] == "success"
        assert r.get("key") == expected, (
            f"alias {alias!r} should resolve to {expected!r}, "
            f"got {r.get('key')!r}"
        )
    print(f"   ✅ 5 media key aliases resolve correctly")


# ── validation ────────────────────────────────────────────

def test_dispatch_missing_key():
    r = adb(action="media_dispatch")
    assert r["status"] == "error"
    assert "media_key" in r["content"][0]["text"]
    print(f"   ✅ missing media_key rejected")


def test_dispatch_unknown_key():
    r = adb(action="media_dispatch", media_key="explode")
    assert r["status"] == "error"
    assert "unknown media key" in r["content"][0]["text"]
    print(f"   ✅ unknown key rejected w/ valid list")


def test_volume_unknown_stream():
    r = adb(action="media_volume_get", media_stream="bogus")
    assert r["status"] == "error"
    assert "unknown stream" in r["content"][0]["text"]
    print(f"   ✅ unknown stream rejected")


def test_volume_set_out_of_range():
    r = adb(action="media_volume_set",
            media_stream="music", media_volume_index=999)
    assert r["status"] == "error"
    assert "0..100" in r["content"][0]["text"]

    r = adb(action="media_volume_set",
            media_stream="music", media_volume_index=-1)
    assert r["status"] == "error"
    print(f"   ✅ out-of-range volume index rejected")


def test_adjust_bad_direction():
    r = adb(action="media_volume_adjust",
            media_stream="music", media_volume_direction="sideways")
    assert r["status"] == "error"
    assert "direction must be" in r["content"][0]["text"]
    print(f"   ✅ bad direction rejected")


# ── parser unit tests ────────────────────────────────────

def test_parse_volume_output():
    from strands_adb.adb_tool import _parse_volume_output
    out = """[V] will control stream=3 (STREAM_MUSIC)
[V] will get volume
[V] Connecting to AudioService
[V] volume is 7 in range [0..25]"""
    p = _parse_volume_output(out)
    assert p["volume"] == 7
    assert p["min"] == 0
    assert p["max"] == 25

    # Garbage
    p = _parse_volume_output("no volume here")
    assert p["volume"] is None
    assert p["max"] is None
    print(f"   ✅ volume output parser handles good + bad input")


def test_resolve_media_key():
    from strands_adb.adb_tool import _resolve_media_key
    assert _resolve_media_key("play") == "play"
    assert _resolve_media_key("PLAY") == "play"
    assert _resolve_media_key("skip") == "next"
    assert _resolve_media_key("toggle") == "play-pause"
    assert _resolve_media_key("play_pause") == "play-pause"
    assert _resolve_media_key("nonsense") is None
    assert _resolve_media_key("") is None
    print(f"   ✅ media key resolver correct")


def test_resolve_stream():
    from strands_adb.adb_tool import _resolve_stream
    assert _resolve_stream("music") == 3
    assert _resolve_stream("MUSIC") == 3
    assert _resolve_stream("media") == 3  # alias
    assert _resolve_stream("3") == 3
    assert _resolve_stream(3) == 3
    assert _resolve_stream("bogus") is None
    assert _resolve_stream("999") is None  # out of known range
    print(f"   ✅ stream resolver handles names/ids/aliases")


if __name__ == "__main__":
    tests = [
        test_sessions_list,
        test_now_playing,
        test_volume_get_music,
        test_volume_get_all_streams,
        test_volume_set_api_round_trip,
        test_volume_adjust,
        test_volume_direction_aliases,
        test_numeric_stream_id,
        test_dispatch_all_keys,
        test_dispatch_aliases,
        test_dispatch_missing_key,
        test_dispatch_unknown_key,
        test_volume_unknown_stream,
        test_volume_set_out_of_range,
        test_adjust_bad_direction,
        test_parse_volume_output,
        test_resolve_media_key,
        test_resolve_stream,
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
    print(f"\n{passed}/{len(tests)} media tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
