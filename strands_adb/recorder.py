"""
strands_adb.recorder — Record & replay ADB action sequences.

Captures adb() tool calls via an in-function hook (see adb_tool.py),
writes a JSON script, and replays it deterministically.

Modes:
  1. "agent"   — hook in adb() logs every non-passive action call
  2. "device"  — getevent -lt: raw HID events from the phone (for human touches)

Usage:
    recorder(action="start", name="ig_scroll", mode="agent")
    # ... run adb() tool calls ...
    recorder(action="stop")
    recorder(action="replay", name="ig_scroll", speed=1.5)
    recorder(action="list")
"""
from __future__ import annotations
import inspect
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from strands import tool

RECORDINGS_DIR = Path(os.getenv("STRANDS_ADB_RECORDINGS", Path.home() / ".strands-adb" / "recordings"))
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

# Global recorder state (read by the hook inside adb_tool.adb)
_STATE: Dict[str, Any] = {
    "recording": False,
    "name": None,
    "mode": None,
    "events": [],
    "started_at": None,
    "device_proc": None,
    "device_thread": None,
    "device_serial": None,
}

# ─────────── default-value cache for filtering the hook payload ───────────
_ADB_DEFAULTS: Optional[Dict[str, Any]] = None

def _adb_defaults() -> Dict[str, Any]:
    global _ADB_DEFAULTS
    if _ADB_DEFAULTS is None:
        try:
            from strands_adb.adb_tool import adb as _adb
            fn = getattr(_adb, "_tool_func", _adb)
            sig = inspect.signature(fn)
            _ADB_DEFAULTS = {
                n: p.default
                for n, p in sig.parameters.items()
                if p.default is not inspect.Parameter.empty
            }
        except Exception:
            _ADB_DEFAULTS = {}
    return _ADB_DEFAULTS


def _json_safe(v: Any) -> bool:
    try:
        json.dumps(v)
        return True
    except Exception:
        return False


def _filter_kwargs(kw: Dict[str, Any]) -> Dict[str, Any]:
    """Strip default-valued params so the event payload is minimal."""
    defaults = _adb_defaults()
    out = {}
    for k, v in kw.items():
        if v is None:
            continue
        if k in defaults and defaults[k] == v:
            continue
        if not _json_safe(v):
            continue
        out[k] = v
    return out


# ─────────── device-mode (getevent) ───────────

def _device_record_loop(serial: Optional[str]):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += ["shell", "getevent", "-lt"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    _STATE["device_proc"] = proc
    t0 = time.time()
    for line in proc.stdout:
        if not _STATE["recording"]:
            break
        line = line.strip()
        if line:
            _STATE["events"].append({"ts": time.time() - t0, "raw": line})
    proc.terminate()


# ─────────── save / load ───────────

def _save(name: str) -> Path:
    path = RECORDINGS_DIR / f"{name}.json"
    # Post-filter events to trim defaults (for the agent-mode hook which logs full locals())
    events = _STATE["events"]
    if _STATE["mode"] == "agent":
        events = [
            {
                "ts": e["ts"],
                "action": e["action"],
                "kwargs": _filter_kwargs(e.get("kwargs", {})),
            }
            for e in events
        ]
    path.write_text(json.dumps({
        "name": name,
        "mode": _STATE["mode"],
        "started_at": _STATE["started_at"],
        "stopped_at": time.time(),
        "device_serial": _STATE["device_serial"],
        "count": len(events),
        "events": events,
    }, indent=2))
    return path


def _load(name: str) -> Dict[str, Any]:
    path = RECORDINGS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"No recording named '{name}' at {path}")
    return json.loads(path.read_text())


# ─────────── replay ───────────

def _replay_agent(events: List[Dict], speed: float, dry_run: bool) -> List[Dict]:
    from strands_adb.adb_tool import adb as _adb_tool
    adb_fn = getattr(_adb_tool, "_tool_func", _adb_tool)

    results = []
    last_ts = None
    for i, evt in enumerate(events):
        ts = evt.get("ts")
        if last_ts is not None and ts is not None and speed > 0:
            gap = max(0.0, (ts - last_ts) / speed)
            if gap > 0:
                time.sleep(min(gap, 30.0))
        last_ts = ts

        action = evt["action"]
        kw = evt.get("kwargs", {})
        entry = {"step": i, "action": action, "kwargs": kw}
        if dry_run:
            entry["status"] = "dry_run"
        else:
            try:
                r = adb_fn(action=action, **kw)
                entry["status"] = r.get("status") if isinstance(r, dict) else "ok"
                if isinstance(r, dict) and r.get("status") == "error":
                    entry["error"] = str(r.get("content", ""))[:200]
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)
        results.append(entry)
    return results


@tool
def recorder(
    action: str,
    name: Optional[str] = None,
    mode: str = "agent",
    speed: float = 1.0,
    dry_run: bool = False,
    device_serial: Optional[str] = None,
) -> Dict[str, Any]:
    """
    🎬 Record & replay ADB action sequences for fast, repeatable automation.

    Args:
        action:        start | stop | replay | list | show | delete | status
        name:          recording name (required for start/replay/show/delete)
        mode:          "agent" (default, wraps adb tool) or "device" (raw getevent)
        speed:         replay speed multiplier (1.0 = real-time, 2.0 = 2x faster, 0 = no delay)
        dry_run:       if True during replay, just print steps without executing
        device_serial: adb device serial (optional)

    Returns:
        dict with status and content list
    """
    try:
        if action == "start":
            if not name:
                return {"status": "error", "content": [{"text": "name required"}]}
            if _STATE["recording"]:
                return {"status": "error", "content": [{"text": f"Already recording '{_STATE['name']}'. Stop first."}]}
            _STATE.update({
                "recording": True, "name": name, "mode": mode,
                "events": [], "started_at": time.time(),
                "device_serial": device_serial,
            })
            if mode == "agent":
                msg = f"🎬 Recording '{name}' (agent mode). Every non-passive adb() call will be logged."
            elif mode == "device":
                t = threading.Thread(target=_device_record_loop, args=(device_serial,), daemon=True)
                _STATE["device_thread"] = t
                t.start()
                msg = f"🎬 Recording '{name}' (device mode / getevent). Touch the phone."
            else:
                _STATE["recording"] = False
                return {"status": "error", "content": [{"text": f"unknown mode: {mode}"}]}
            return {"status": "success", "content": [{"text": msg}]}

        elif action == "stop":
            if not _STATE["recording"]:
                return {"status": "error", "content": [{"text": "Not recording."}]}
            _STATE["recording"] = False
            if _STATE["mode"] == "device" and _STATE["device_proc"]:
                _STATE["device_proc"].terminate()
            nm = _STATE["name"]
            path = _save(nm)
            count = len(_STATE["events"])
            _STATE.update({"name": None, "mode": None, "events": [], "started_at": None})
            return {"status": "success", "content": [{"text": f"✅ Saved '{nm}' → {path} ({count} events)"}]}

        elif action == "replay":
            if not name:
                return {"status": "error", "content": [{"text": "name required"}]}
            data = _load(name)
            if data["mode"] != "agent":
                return {"status": "error", "content": [{"text": f"Replay only supports agent mode (got {data['mode']})."}]}
            results = _replay_agent(data["events"], speed=speed, dry_run=dry_run)
            ok = sum(1 for r in results if r.get("status") in ("success", "ok", "dry_run"))
            return {
                "status": "success",
                "content": [{"text": f"▶️ Replayed '{name}': {ok}/{len(results)} ok (speed={speed}x, dry_run={dry_run})\n\n" + json.dumps(results[-10:], indent=2)}],
            }

        elif action == "list":
            files = sorted(RECORDINGS_DIR.glob("*.json"))
            if not files:
                return {"status": "success", "content": [{"text": f"No recordings in {RECORDINGS_DIR}"}]}
            lines = [f"📼 Recordings in {RECORDINGS_DIR}:"]
            for f in files:
                try:
                    d = json.loads(f.read_text())
                    lines.append(f"  • {d['name']:<30} {d['mode']:<8} {d.get('count', 0):>4} events")
                except Exception:
                    lines.append(f"  • {f.stem} (corrupt)")
            return {"status": "success", "content": [{"text": "\n".join(lines)}]}

        elif action == "show":
            if not name:
                return {"status": "error", "content": [{"text": "name required"}]}
            data = _load(name)
            return {"status": "success", "content": [{"text": json.dumps(data, indent=2)[:6000]}]}

        elif action == "delete":
            if not name:
                return {"status": "error", "content": [{"text": "name required"}]}
            p = RECORDINGS_DIR / f"{name}.json"
            if p.exists():
                p.unlink()
                return {"status": "success", "content": [{"text": f"🗑️  Deleted {name}"}]}
            return {"status": "error", "content": [{"text": f"Not found: {name}"}]}

        elif action == "status":
            if _STATE["recording"]:
                return {"status": "success", "content": [{"text": f"🔴 Recording '{_STATE['name']}' ({_STATE['mode']}) — {len(_STATE['events'])} events captured"}]}
            return {"status": "success", "content": [{"text": "⏹️  Idle"}]}

        else:
            return {"status": "error", "content": [{"text": f"Unknown action: {action}. Use start|stop|replay|list|show|delete|status"}]}

    except Exception as e:
        return {"status": "error", "content": [{"text": f"recorder error: {e}"}]}
