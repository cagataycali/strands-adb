# Screen Recording

Record what your agent is doing on-device. Critical for debugging UI automation,
demos, and **after-the-fact review** of agent actions.

Three actions, two modes:

| Action | Mode | Use When |
|---|---|---|
| `screen_record` | **Blocking** | You know the duration upfront (≤180s) |
| `screen_record_start` | **Background** | Agent needs to keep acting while recording |
| `screen_record_stop` | **Background** | Stop + pull + optionally merge segments |
| `screen_record_status` | **Background** | Check elapsed time + segments pulled so far |

---

## Quick Start — Record While Acting

The common pattern: start recording, perform actions, stop and get the video.

```python
from strands_adb import adb

# 1. Start recording in the background (returns immediately)
adb(
    action="screen_record_start",
    output_path="/tmp/agent_demo.mp4",
    screenrec_bit_rate_mbps=4,       # 4 Mbps (default)
    screenrec_size="720x1600",       # downscale (None = native)
    screenrec_segment_sec=180,       # max per segment (Android cap)
)

# 2. Do whatever your agent needs to do
adb(action="tap", x=500, y=1000)
adb(action="type", text="hello world")
adb(action="key", key="enter")
# ... minutes of work ...

# 3. Stop, pull segments, optionally merge
result = adb(action="screen_record_stop")
# result["segments"] → list of mp4 paths
# result["merged_path"] → single concatenated mp4 (if ffmpeg available)
```

## Parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `output_path` | `str` | `/tmp/adb_rec_<ts>.mp4` | Where the first / final mp4 lands |
| `screenrec_bit_rate_mbps` | `int` | `4` | 1-20 Mbps. Higher = larger file |
| `screenrec_size` | `str` | `None` | e.g. `"720x1280"`. `None` = device native |
| `screenrec_segment_sec` | `int` | `180` | Per-segment cap. Android hard limit is 180 |

## Long Recordings (>3 minutes)

Android `screenrecord` caps at 180 seconds per invocation. `screen_record_start`
automatically **chains segments** and pulls each one as soon as it finishes,
so `stop` is fast regardless of how long the recording ran.

If `ffmpeg` is on your `$PATH`, segments are concatenated into a single mp4:

```python
# 10-minute recording → 4 segments → auto-merged
adb(action="screen_record_start", output_path="/tmp/long.mp4")
time.sleep(600)
result = adb(action="screen_record_stop")
print(result["merged_path"])    # /tmp/long_merged.mp4
print(len(result["segments"])) # 4
```

## Status Polling

```python
status = adb(action="screen_record_status")
# {"running": True, "elapsed_sec": 47.3, "segments": [...], "output_path": "..."}
```

## Blocking Variant (fixed duration)

If you know exactly how long you want to record and don't need to do anything
else during that window:

```python
adb(action="screen_record", duration_sec=10, output_path="/tmp/ten_seconds.mp4")
# Blocks for 10s, then pulls the mp4
```

## File Size Reference

Typical bit-rates and per-minute file sizes (at 720×1600):

| Bit Rate | ~MB per minute | Use Case |
|---|---|---|
| 1 Mbps | 7 MB | Audit logs, low-bandwidth |
| 2 Mbps | 15 MB | Demo recordings |
| 4 Mbps (default) | 30 MB | General purpose |
| 8 Mbps | 60 MB | High-motion content |

## Known Limitations

- **DRM / Secure windows** (banking apps, Netflix, etc.) record as **black**.
  This is enforced by Android at the kernel level — no workaround.
- **Audio is NOT recorded.** `screenrecord` is video-only.
- **Segment seams** may show a ~1 frame hiccup at the 180s boundary. ffmpeg
  `-c copy` concat preserves streams but doesn't re-encode the seam.
- **Max bit-rate** on most devices caps around 20 Mbps regardless of what
  you pass.

## Patterns

### Record every agent run

Wrap your agent loop:

```python
import time
from strands_adb import adb

rec_path = f"/tmp/agent_{int(time.time())}.mp4"
adb(action="screen_record_start", output_path=rec_path)
try:
    agent("book a haircut appointment")
finally:
    result = adb(action="screen_record_stop")
    print(f"Recording: {result.get('merged_path') or rec_path}")
```

### Record only on failure

```python
adb(action="screen_record_start", output_path="/tmp/pending.mp4")
try:
    agent.do_work()
    # Success — discard recording
    adb(action="screen_record_stop")
    os.remove("/tmp/pending.mp4")
except Exception as e:
    # Failure — keep the evidence
    result = adb(action="screen_record_stop")
    print(f"Failure recorded: {result['merged_path']}")
    raise
```

### Pair with logcat streaming

```python
adb(action="log_stream_start", log_filters=["ActivityTaskManager:I", "*:S"])
adb(action="screen_record_start", output_path="/tmp/combo.mp4")

# agent work ...

adb(action="screen_record_stop")
adb(action="log_stream_stop")
# Now you have video + structured logs for the same window
```
