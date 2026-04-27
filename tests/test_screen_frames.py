"""Integration tests for Frontier #4 — Screen frames → CV."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strands_adb.adb_tool import adb


def _has_device():
    r = adb(action="list_devices")
    return r.get("status") == "success" and bool(r.get("devices"))


def _has_ffmpeg():
    import shutil
    return shutil.which("ffmpeg") is not None


# ── screen_frames (live capture) ─────────────────────────────

def test_screen_frames_basic():
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="screen_frames", frames_n=3, frames_interval=0.2,
            include_image=False)
    assert r["status"] == "success", r
    assert r["count"] == 3
    assert all(Path(f["path"]).exists() for f in r["frames"])
    assert all(f["size_bytes"] > 1000 for f in r["frames"])
    print(f"   ✅ captured 3 frames in {r['total_time_sec']:.1f}s")


def test_screen_frames_image_blocks():
    """Verify Converse-API image blocks are built correctly."""
    if not _has_device(): print("⏭ skip"); return
    r = adb(action="screen_frames", frames_n=2, frames_interval=0.1,
            include_image=True)
    assert r["status"] == "success"
    # Expected content: summary + (text + image) per frame = 1 + 2*2 = 5
    image_blocks = [c for c in r["content"] if "image" in c]
    assert len(image_blocks) == 2
    for b in image_blocks:
        img = b["image"]
        assert img["format"] == "png"
        assert isinstance(img["source"]["bytes"], bytes)
        assert len(img["source"]["bytes"]) > 1000
    print(f"   ✅ 2 image blocks w/ proper shape, sizes "
          f"{[len(b['image']['source']['bytes']) for b in image_blocks]}")


def test_screen_frames_output_dir():
    """Custom output directory is respected."""
    if not _has_device(): print("⏭ skip"); return
    import tempfile
    with tempfile.TemporaryDirectory() as tmpd:
        r = adb(action="screen_frames", frames_n=2, frames_interval=0.1,
                include_image=False, output_path=tmpd)
        assert r["status"] == "success"
        assert r["output_dir"] == tmpd
        files = sorted(Path(tmpd).glob("*.png"))
        assert len(files) == 2
    print(f"   ✅ custom dir honored, 2 PNGs created")


def test_screen_frames_invalid_n():
    r = adb(action="screen_frames", frames_n=0)
    assert r["status"] == "error"
    r = adb(action="screen_frames", frames_n=50)
    assert r["status"] == "error"
    print(f"   ✅ n=0 / n=50 rejected")


def test_screen_frames_invalid_interval():
    r = adb(action="screen_frames", frames_n=2, frames_interval=20)
    assert r["status"] == "error"
    print(f"   ✅ interval out of range rejected")


# ── video_frames (ffmpeg extraction) ─────────────────────────

def test_video_frames_ffmpeg_missing():
    """If ffmpeg missing, should report clearly."""
    if _has_ffmpeg(): print("⏭ skip: ffmpeg present"); return
    r = adb(action="video_frames", output_path="/tmp/whatever.mp4", frames_n=3)
    assert r["status"] == "error"
    assert "ffmpeg" in r["content"][0]["text"].lower()
    print(f"   ✅ ffmpeg-missing handled")


def test_video_frames_missing_file():
    if not _has_ffmpeg(): print("⏭ skip"); return
    r = adb(action="video_frames", output_path="/tmp/definitely_not_there.mp4",
            frames_n=3)
    assert r["status"] == "error"
    assert "not found" in r["content"][0]["text"]
    print(f"   ✅ missing file handled")


def test_video_frames_needs_output_path():
    r = adb(action="video_frames", frames_n=3)
    assert r["status"] == "error"
    print(f"   ✅ missing output_path rejected")


def test_video_frames_end_to_end():
    """Record 6s → extract 5 frames → verify all land."""
    if not _has_device(): print("⏭ skip"); return
    if not _has_ffmpeg(): print("⏭ skip: no ffmpeg"); return

    # Simulate motion so the video isn't trivial
    import threading, subprocess
    def motion():
        for _ in range(3):
            subprocess.run(["adb", "shell", "input", "swipe",
                            "500", "1500", "500", "500", "300"])
            time.sleep(1)
    t = threading.Thread(target=motion, daemon=True)
    t.start()

    r = adb(action="screen_record", duration_sec=6)
    t.join()
    assert r["status"] == "success"
    video = r["path"]

    r = adb(action="video_frames", output_path=video, frames_n=5,
            include_image=False)
    assert r["status"] == "success"
    assert r["count"] == 5, f"expected 5 frames, got {r['count']}: {r.get('errors')}"
    assert r["video_duration_sec"] > 0
    for f in r["frames"]:
        assert Path(f["path"]).exists()
        assert f["size_bytes"] > 0
    print(f"   ✅ 5/5 frames extracted from {r['video_duration_sec']:.1f}s video")


def test_video_frames_image_blocks():
    """Verify extracted frames come back as proper image blocks."""
    if not _has_device(): print("⏭ skip"); return
    if not _has_ffmpeg(): print("⏭ skip: no ffmpeg"); return

    # Find an existing mp4 from previous test run, else skip
    mp4s = sorted(Path("/tmp").glob("adb_rec_*.mp4"),
                  key=lambda p: p.stat().st_size, reverse=True)
    if not mp4s or mp4s[0].stat().st_size < 100_000:
        print("⏭ skip: no prior recording available"); return

    r = adb(action="video_frames", output_path=str(mp4s[0]),
            frames_n=3, include_image=True)
    assert r["status"] == "success", r
    image_blocks = [c for c in r["content"] if "image" in c]
    assert len(image_blocks) == r["count"]
    for b in image_blocks:
        assert b["image"]["format"] == "png"
        assert len(b["image"]["source"]["bytes"]) > 1000
    print(f"   ✅ {len(image_blocks)} image blocks from existing mp4")


if __name__ == "__main__":
    tests = [
        test_screen_frames_basic,
        test_screen_frames_image_blocks,
        test_screen_frames_output_dir,
        test_screen_frames_invalid_n,
        test_screen_frames_invalid_interval,
        test_video_frames_ffmpeg_missing,
        test_video_frames_missing_file,
        test_video_frames_needs_output_path,
        test_video_frames_end_to_end,
        test_video_frames_image_blocks,
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
    print(f"\n{passed}/{len(tests)} frames tests passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
