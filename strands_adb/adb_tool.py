"""
🤖 strands-adb — Android device control as a @tool.

One tool, many actions. Dispatches to adb via subprocess with structured
arguments, safety checks, and LLM-friendly responses.

Actions grouped by domain:
  device:  list_devices, select_device, device_info, battery, wake, unlock
  shell:   shell (run arbitrary adb shell command)
  ui:      tap, swipe, type_text, key, back, home, recent
  screen:  screenshot, screenrecord_start, screenrecord_stop, ui_dump
  apps:    list_packages, launch, kill, install, uninstall, clear_data,
           current_app
  files:   push, pull, ls
  intent:  open_url, share_text, start_activity
  notif:   notifications, dismiss_notifications
  logs:    logcat
"""
from __future__ import annotations

import base64
import re
import threading
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strands import tool

# Path-safe import so the tool works both when installed AND when loaded
# as a raw file via manage_tools.add(/path/to/adb_tool.py)
import sys as _sys
_here = str(Path(__file__).resolve().parent.parent)
if _here not in _sys.path:
    _sys.path.insert(0, _here)
from strands_adb import smart  # noqa: E402

logger = logging.getLogger("strands_adb")

# ----------------------------------------------------------------------------
# Core adb runner
# ----------------------------------------------------------------------------

class ADBError(RuntimeError):
    pass


# Per-process selected device (can be overridden per call via `serial`)
_SELECTED_SERIAL: Optional[str] = os.getenv("ADB_SERIAL") or None


def _adb_bin() -> str:
    return os.getenv("ADB_BIN", "adb")


def _run(
    args: List[str],
    serial: Optional[str] = None,
    timeout: int = 30,
    check: bool = False,
    input_data: Optional[str] = None,
) -> Dict[str, Any]:
    """Run an adb command, return {stdout, stderr, returncode}."""
    cmd = [_adb_bin()]
    s = serial or _SELECTED_SERIAL
    if s:
        cmd += ["-s", s]
    cmd += args

    logger.debug("adb: %s", " ".join(shlex.quote(c) for c in cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
        )
    except FileNotFoundError as e:
        raise ADBError(
            f"adb binary not found: {_adb_bin()} — install android-platform-tools"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ADBError(f"adb command timed out after {timeout}s: {args}") from e

    result = {
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "returncode": proc.returncode,
        "cmd": cmd,
    }
    if check and proc.returncode != 0:
        raise ADBError(f"adb failed ({proc.returncode}): {proc.stderr.strip()}")
    return result


def _ok(text: str, **extra) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": "success", "content": [{"text": text}]}
    if extra:
        payload.update(extra)
    return payload


def _err(text: str) -> Dict[str, Any]:
    return {"status": "error", "content": [{"text": text}]}


# ----------------------------------------------------------------------------
# Action handlers
# ----------------------------------------------------------------------------

def _handle_list_devices() -> Dict[str, Any]:
    r = _run(["devices", "-l"])
    if r["returncode"] != 0:
        return _err(r["stderr"] or "adb devices failed")
    lines = [ln for ln in r["stdout"].splitlines() if ln and "List of devices" not in ln]
    devices = []
    for ln in lines:
        parts = ln.split()
        if len(parts) >= 2:
            serial, state = parts[0], parts[1]
            meta = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            devices.append({"serial": serial, "state": state, **meta})
    text = f"{len(devices)} device(s):\n" + "\n".join(
        f"  - {d['serial']} [{d['state']}] {d.get('model', '')}" for d in devices
    )
    return _ok(text, devices=devices)


def _handle_select_device(serial: str) -> Dict[str, Any]:
    global _SELECTED_SERIAL
    if not serial:
        return _err("serial required")
    _SELECTED_SERIAL = serial
    return _ok(f"selected device: {serial}")


def _handle_device_info(serial: Optional[str]) -> Dict[str, Any]:
    props = [
        "ro.product.model",
        "ro.product.manufacturer",
        "ro.build.version.release",
        "ro.build.version.sdk",
        "ro.serialno",
    ]
    info = {}
    for p in props:
        r = _run(["shell", "getprop", p], serial=serial)
        info[p] = r["stdout"]
    return _ok("device info:\n" + "\n".join(f"  {k}: {v}" for k, v in info.items()), info=info)


def _handle_battery(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(["shell", "dumpsys", "battery"], serial=serial)
    return _ok(r["stdout"][:2000])


def _handle_shell(command: str, serial: Optional[str], timeout: int) -> Dict[str, Any]:
    if not command:
        return _err("command required")
    r = _run(["shell", command], serial=serial, timeout=timeout)
    body = r["stdout"]
    if r["stderr"]:
        body += f"\n[stderr] {r['stderr']}"
    return _ok(body or "(no output)")


def _handle_tap(x: int, y: int, serial: Optional[str]) -> Dict[str, Any]:
    _run(["shell", "input", "tap", str(x), str(y)], serial=serial)
    return _ok(f"tapped ({x}, {y})")


def _handle_swipe(
    x1: int, y1: int, x2: int, y2: int, duration_ms: int, serial: Optional[str]
) -> Dict[str, Any]:
    _run(
        ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        serial=serial,
    )
    return _ok(f"swiped ({x1},{y1}) → ({x2},{y2}) in {duration_ms}ms")


def _handle_type(text: str, serial: Optional[str]) -> Dict[str, Any]:
    if text is None:
        return _err("text required")
    # adb input text needs spaces escaped as %s
    escaped = text.replace(" ", "%s")
    _run(["shell", "input", "text", escaped], serial=serial)
    return _ok(f"typed: {text[:80]}")


KEY_ALIASES = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recent": "KEYCODE_APP_SWITCH",
    "enter": "KEYCODE_ENTER",
    "tab": "KEYCODE_TAB",
    "space": "KEYCODE_SPACE",
    "delete": "KEYCODE_DEL",
    "escape": "KEYCODE_ESCAPE",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "power": "KEYCODE_POWER",
    "menu": "KEYCODE_MENU",
    "search": "KEYCODE_SEARCH",
}


def _handle_key(key: str, serial: Optional[str]) -> Dict[str, Any]:
    if not key:
        return _err("key required")
    keycode = KEY_ALIASES.get(key.lower(), key)
    if not keycode.startswith("KEYCODE_"):
        keycode = f"KEYCODE_{keycode.upper()}"
    _run(["shell", "input", "keyevent", keycode], serial=serial)
    return _ok(f"keyevent {keycode}")


def _handle_screenshot(
    output_path: Optional[str],
    serial: Optional[str],
    return_base64: bool,
    include_image: bool,
) -> Dict[str, Any]:
    """Capture PNG from device.

    When ``include_image`` is True, returns a Converse API image block so
    the agent can actually *see* the screen (same pattern as
    strands_tools.image_reader). This is the whole point of the tool —
    close the perception loop.
    """
    out = Path(output_path) if output_path else Path(
        f"/tmp/adb_screenshot_{int(time.time())}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    # Direct binary capture via exec-out (fast path)
    cmd = [_adb_bin()]
    s = serial or _SELECTED_SERIAL
    if s:
        cmd += ["-s", s]
    cmd += ["exec-out", "screencap", "-p"]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        return _err("screenshot timed out")

    png_bytes = proc.stdout or b""
    if proc.returncode != 0 or not png_bytes.startswith(b"\x89PNG"):
        # Fallback: shell + pull (handles devices where exec-out is flaky)
        _run(["shell", "screencap", "-p", "/sdcard/_shot.png"], serial=serial, timeout=20)
        r = _run(["pull", "/sdcard/_shot.png", str(out)], serial=serial, timeout=20)
        _run(["shell", "rm", "/sdcard/_shot.png"], serial=serial)
        if r["returncode"] != 0 or not out.exists():
            return _err(
                f"screenshot failed: {proc.stderr.decode(errors='ignore')[:500]}"
            )
        png_bytes = out.read_bytes()
    else:
        out.write_bytes(png_bytes)

    size = len(png_bytes)
    summary = f"screenshot saved: {out} ({size} bytes)"

    # Build content blocks: text summary + (optional) image for Converse API
    content: List[Dict[str, Any]] = [{"text": summary}]
    if include_image:
        # Converse API image block — identical format to strands_tools.image_reader.
        # The agent will literally see the pixels, not just the path.
        content.append(
            {"image": {"format": "png", "source": {"bytes": png_bytes}}}
        )

    payload: Dict[str, Any] = {
        "status": "success",
        "content": content,
        "path": str(out),
        "size_bytes": size,
    }
    if return_base64:
        payload["base64"] = base64.b64encode(png_bytes).decode()
    return payload




# =============================================================================
# 📸  Physical Camera  (Frontier #5)
# =============================================================================
#
# Strategy: launch GoogleCamera via STILL_IMAGE_CAMERA intent, then tap the
# shutter by resource-id (stable across camera modes / Android versions —
# Google keeps `com.google.android.GoogleCamera:id/shutter_button`).
#
# For "Night Sight" mode the shutter stays pressed ~3s; we poll DCIM for a
# new file with a sensible timeout and pull it back to the host.

_CAMERA_PKG = "com.google.android.GoogleCamera"
_CAMERA_INTENT_STILL = "android.media.action.STILL_IMAGE_CAMERA"
_CAMERA_INTENT_VIDEO = "android.media.action.VIDEO_CAMERA"


def _latest_dcim_file(
    serial: Optional[str], extensions: tuple = (".jpg", ".jpeg", ".png", ".mp4")
) -> Optional[str]:
    """Return absolute path on device of newest file in DCIM/Camera matching ext."""
    r = _run(
        ["shell", "ls", "-t", "/sdcard/DCIM/Camera/"],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return None
    for line in r["stdout"].splitlines():
        name = line.strip()
        if not name:
            continue
        if name.lower().endswith(extensions):
            return f"/sdcard/DCIM/Camera/{name}"
    return None


def _tap_by_resource_id(resource_id: str, serial: Optional[str]) -> bool:
    """Dump UI, find bounds for resource-id, tap center. Returns True on hit."""
    dump = _run(["shell", "uiautomator", "dump", "/sdcard/_cam_ui.xml"],
                serial=serial, timeout=10)
    if dump["returncode"] != 0:
        return False
    pull = _run(["shell", "cat", "/sdcard/_cam_ui.xml"], serial=serial, timeout=10)
    xml = pull["stdout"]
    _run(["shell", "rm", "/sdcard/_cam_ui.xml"], serial=serial, timeout=5)
    m = re.search(
        rf'<node[^>]*resource-id="{re.escape(resource_id)}"[^/]*/>', xml
    )
    if not m:
        return False
    b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', m.group(0))
    if not b:
        return False
    x1, y1, x2, y2 = map(int, b.groups())
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    r = _run(["shell", "input", "tap", str(cx), str(cy)],
             serial=serial, timeout=5)
    return r["returncode"] == 0


def _tap_by_content_desc(desc: str, serial: Optional[str]) -> bool:
    """Tap a UI element by its content-desc attribute."""
    dump = _run(["shell", "uiautomator", "dump", "/sdcard/_cam_ui.xml"],
                serial=serial, timeout=10)
    if dump["returncode"] != 0:
        return False
    pull = _run(["shell", "cat", "/sdcard/_cam_ui.xml"], serial=serial, timeout=10)
    xml = pull["stdout"]
    _run(["shell", "rm", "/sdcard/_cam_ui.xml"], serial=serial, timeout=5)
    m = re.search(
        rf'<node[^>]*content-desc="{re.escape(desc)}"[^/]*/>', xml
    )
    if not m:
        return False
    b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', m.group(0))
    if not b:
        return False
    x1, y1, x2, y2 = map(int, b.groups())
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    r = _run(["shell", "input", "tap", str(cx), str(cy)],
             serial=serial, timeout=5)
    return r["returncode"] == 0


def _handle_camera_photo(
    output_path: Optional[str],
    serial: Optional[str],
    facing: str,
    auto_pull: bool,
    include_image: bool,
    return_base64: bool,
    timeout_sec: int,
) -> Dict[str, Any]:
    """Take a still photo using GoogleCamera. Returns image block for the agent."""
    # 1. Record baseline — what's currently the newest photo?
    baseline = _latest_dcim_file(serial, extensions=(".jpg", ".jpeg", ".png"))

    # 2. Wake the device
    _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial, timeout=5)
    time.sleep(0.8)

    # 3. Launch camera via intent
    r = _run(
        ["shell", "am", "start", "-a", _CAMERA_INTENT_STILL],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"camera intent failed: {r['stderr'][:200]}")

    # Wait for Camera to focus + render viewfinder
    time.sleep(2.0)

    # 4. Optionally toggle to front camera
    if facing == "front":
        if not _tap_by_content_desc("Switch to front camera", serial):
            logger.debug("front-camera toggle not found (maybe already front)")
        time.sleep(2.5)  # front viewfinder + UI relayout

    # 5. Tap shutter by resource-id (robust across modes). Retry a few times
    #    because after a mode switch the UI tree can still be animating.
    tapped = False
    for attempt in range(3):
        if _tap_by_resource_id(
            "com.google.android.GoogleCamera:id/shutter_button", serial
        ):
            tapped = True
            break
        time.sleep(0.8)
    if not tapped:
        # Fallback: try content-desc variants
        for desc in ("Take photo", "Take Night Sight photo",
                     "Take Portrait photo", "Shutter"):
            if _tap_by_content_desc(desc, serial):
                tapped = True
                break
    if not tapped:
        return _err("could not find shutter button in GoogleCamera UI")

    # 6. Poll DCIM for a new file (Night Sight can take 3-5s)
    deadline = time.time() + timeout_sec
    new_file = None
    while time.time() < deadline:
        time.sleep(0.5)
        latest = _latest_dcim_file(serial, extensions=(".jpg", ".jpeg", ".png"))
        if latest and latest != baseline:
            new_file = latest
            break
    if not new_file:
        return _err(
            f"no new photo appeared in /sdcard/DCIM/Camera within "
            f"{timeout_sec}s (baseline was {baseline})"
        )

    # 7. Pull it to host (unless caller opts out)
    payload: Dict[str, Any] = {
        "status": "success",
        "device_path": new_file,
        "facing": facing,
    }

    if not auto_pull:
        payload["content"] = [{"text": f"photo captured on device: {new_file}"}]
        return payload

    name = new_file.rsplit("/", 1)[-1]
    local = Path(output_path) if output_path else Path(f"/tmp/{name}")
    local.parent.mkdir(parents=True, exist_ok=True)
    pull = _run(["pull", new_file, str(local)], serial=serial, timeout=30)
    if pull["returncode"] != 0 or not local.exists():
        return _err(f"pull failed: {pull['stderr'][:200]}")

    img_bytes = local.read_bytes()
    size = len(img_bytes)
    summary = f"📸 photo captured → {local} ({size} bytes, facing={facing})"
    content: List[Dict[str, Any]] = [{"text": summary}]

    if include_image:
        # Converse image block — same pattern as screenshot.
        fmt = "jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "png"
        content.append(
            {"image": {"format": fmt, "source": {"bytes": img_bytes}}}
        )

    payload["content"] = content
    payload["path"] = str(local)
    payload["size_bytes"] = size
    if return_base64:
        payload["base64"] = base64.b64encode(img_bytes).decode()
    return payload


def _handle_camera_video(
    duration_sec: int,
    output_path: Optional[str],
    serial: Optional[str],
    facing: str,
    auto_pull: bool,
) -> Dict[str, Any]:
    """Record a short video via GoogleCamera.

    We launch the Video intent, tap the record button (reuses shutter_button
    resource-id in video mode), wait ``duration_sec``, then tap again to
    stop. Finally pull the mp4.
    """
    baseline = _latest_dcim_file(serial, extensions=(".mp4",))

    _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial, timeout=5)
    time.sleep(0.8)

    r = _run(
        ["shell", "am", "start", "-a", _CAMERA_INTENT_VIDEO],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"video intent failed: {r['stderr'][:200]}")
    time.sleep(2.0)

    if facing == "front":
        _tap_by_content_desc("Switch to front camera", serial)
        time.sleep(1.2)

    # Start recording (video-mode shutter is same resource-id)
    if not _tap_by_resource_id(
        "com.google.android.GoogleCamera:id/shutter_button", serial
    ):
        return _err("couldn't start video recording (shutter not found)")

    time.sleep(max(1, duration_sec))

    # Stop recording
    _tap_by_resource_id(
        "com.google.android.GoogleCamera:id/shutter_button", serial
    )
    time.sleep(2.0)  # let encoder finalise

    # Find new mp4
    deadline = time.time() + 30
    new_file = None
    while time.time() < deadline:
        time.sleep(0.5)
        latest = _latest_dcim_file(serial, extensions=(".mp4",))
        if latest and latest != baseline:
            new_file = latest
            break
    if not new_file:
        return _err("no new video appeared in DCIM")

    payload: Dict[str, Any] = {
        "status": "success",
        "device_path": new_file,
        "duration_sec": duration_sec,
        "facing": facing,
    }
    if not auto_pull:
        payload["content"] = [{"text": f"video recorded on device: {new_file}"}]
        return payload

    name = new_file.rsplit("/", 1)[-1]
    local = Path(output_path) if output_path else Path(f"/tmp/{name}")
    local.parent.mkdir(parents=True, exist_ok=True)
    pull = _run(["pull", new_file, str(local)], serial=serial, timeout=60)
    if pull["returncode"] != 0 or not local.exists():
        return _err(f"video pull failed: {pull['stderr'][:200]}")

    size = local.stat().st_size
    payload["content"] = [{"text": f"🎥 video captured → {local} ({size} bytes)"}]
    payload["path"] = str(local)
    payload["size_bytes"] = size
    return payload


def _handle_ui_dump(serial: Optional[str]) -> Dict[str, Any]:
    _run(["shell", "uiautomator", "dump", "/sdcard/_ui.xml"], serial=serial, timeout=30)
    r = _run(["shell", "cat", "/sdcard/_ui.xml"], serial=serial, timeout=30)
    _run(["shell", "rm", "/sdcard/_ui.xml"], serial=serial)
    xml = r["stdout"]
    return _ok(f"ui hierarchy ({len(xml)} chars):\n{xml[:5000]}", xml=xml)


def _handle_list_packages(
    filter_text: Optional[str], serial: Optional[str]
) -> Dict[str, Any]:
    args = ["shell", "pm", "list", "packages"]
    if filter_text:
        args.append(filter_text)
    r = _run(args, serial=serial)
    pkgs = [ln.replace("package:", "") for ln in r["stdout"].splitlines() if ln]
    return _ok(f"{len(pkgs)} package(s):\n" + "\n".join(f"  {p}" for p in pkgs[:50]), packages=pkgs)


def _handle_launch(package: str, serial: Optional[str]) -> Dict[str, Any]:
    if not package:
        return _err("package required")
    r = _run(
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
        serial=serial,
    )
    if r["returncode"] != 0:
        return _err(f"launch failed: {r['stderr']}")
    return _ok(f"launched {package}")


def _handle_kill(package: str, serial: Optional[str]) -> Dict[str, Any]:
    if not package:
        return _err("package required")
    _run(["shell", "am", "force-stop", package], serial=serial)
    return _ok(f"killed {package}")


def _handle_install(apk_path: str, serial: Optional[str]) -> Dict[str, Any]:
    if not apk_path or not Path(apk_path).exists():
        return _err(f"apk not found: {apk_path}")
    r = _run(["install", "-r", apk_path], serial=serial, timeout=180)
    ok = "Success" in (r["stdout"] + r["stderr"])
    return _ok(r["stdout"] or r["stderr"]) if ok else _err(r["stderr"] or r["stdout"])


def _handle_uninstall(package: str, serial: Optional[str]) -> Dict[str, Any]:
    if not package:
        return _err("package required")
    r = _run(["uninstall", package], serial=serial)
    return _ok(r["stdout"] or "uninstalled")


def _handle_clear_data(package: str, serial: Optional[str]) -> Dict[str, Any]:
    if not package:
        return _err("package required")
    _run(["shell", "pm", "clear", package], serial=serial)
    return _ok(f"cleared data for {package}")


def _handle_current_app(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(
        ["shell", "dumpsys", "activity", "activities"],
        serial=serial,
    )
    pkg = "?"
    for ln in r["stdout"].splitlines():
        if "mResumedActivity" in ln or "topResumedActivity" in ln:
            pkg = ln.strip()
            break
    return _ok(f"current: {pkg}")


def _handle_push(local: str, remote: str, serial: Optional[str]) -> Dict[str, Any]:
    if not local or not remote:
        return _err("local and remote required")
    r = _run(["push", local, remote], serial=serial, timeout=300)
    return _ok(r["stdout"] or "pushed") if r["returncode"] == 0 else _err(r["stderr"])


def _handle_pull(remote: str, local: str, serial: Optional[str]) -> Dict[str, Any]:
    if not local or not remote:
        return _err("local and remote required")
    r = _run(["pull", remote, local], serial=serial, timeout=300)
    return _ok(r["stdout"] or "pulled") if r["returncode"] == 0 else _err(r["stderr"])


def _handle_ls(remote: str, serial: Optional[str]) -> Dict[str, Any]:
    r = _run(["shell", "ls", "-la", remote or "/sdcard"], serial=serial)
    return _ok(r["stdout"] or r["stderr"])


def _handle_open_url(url: str, serial: Optional[str]) -> Dict[str, Any]:
    if not url:
        return _err("url required")
    _run(
        ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", shlex.quote(url)],
        serial=serial,
    )
    return _ok(f"opened {url}")


def _handle_share_text(text: str, serial: Optional[str]) -> Dict[str, Any]:
    if not text:
        return _err("text required")
    _run(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.SEND",
            "-t",
            "text/plain",
            "--es",
            "android.intent.extra.TEXT",
            shlex.quote(text),
        ],
        serial=serial,
    )
    return _ok("share intent sent")


def _handle_start_activity(
    component: str, serial: Optional[str], extras: Optional[Dict[str, str]]
) -> Dict[str, Any]:
    if not component:
        return _err("component required (e.g. com.app/.MainActivity)")
    args = ["shell", "am", "start", "-n", component]
    if extras:
        for k, v in extras.items():
            args += ["--es", k, str(v)]
    r = _run(args, serial=serial)
    return _ok(r["stdout"] or "started") if r["returncode"] == 0 else _err(r["stderr"])


def _handle_notifications(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(["shell", "dumpsys", "notification", "--noredact"], serial=serial, timeout=30)
    return _ok(r["stdout"][:5000])


def _handle_logcat(
    filter_text: Optional[str], lines: int, serial: Optional[str]
) -> Dict[str, Any]:
    args = ["logcat", "-d", "-t", str(lines)]
    if filter_text:
        args += ["*:S", f"{filter_text}:V"]
    r = _run(args, serial=serial, timeout=30)
    return _ok(r["stdout"][-8000:] or "(empty)")


# ----------------------------------------------------------------------------
# Smart handlers (semantic layer)
# ----------------------------------------------------------------------------


def _handle_notifications_parsed(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(["shell", "dumpsys", "notification", "--noredact"], serial=serial, timeout=30)
    parsed = smart.parse_notifications(r["stdout"])
    lines = [
        f"📬 {len(parsed)} notifications:",
        *[
            f"  • [{n['pkg'].split('.')[-1]}] {n['title']}"
            + (f" — {n['text'][:100]}" if n["text"] else "")
            for n in parsed[:30]
        ],
    ]
    return _ok("\n".join(lines), notifications=parsed)


def _handle_ui_find_legacy(
    serial: Optional[str],
    text: Optional[str],
    desc: Optional[str],
    resource_id: Optional[str],
) -> Dict[str, Any]:
    _run(["shell", "uiautomator", "dump", "/sdcard/_ui.xml"], serial=serial, timeout=30)
    r = _run(["shell", "cat", "/sdcard/_ui.xml"], serial=serial, timeout=30)
    _run(["shell", "rm", "/sdcard/_ui.xml"], serial=serial)
    elements = smart.parse_ui_dump(r["stdout"])
    if not any([text, desc, resource_id]):
        return _ok(
            f"{len(elements)} interactable elements on screen",
            elements=elements,
        )
    found = smart.find_element(
        elements, text=text, desc=desc, resource_id=resource_id
    )
    if not found:
        return _err(
            f"no element found matching text={text!r} desc={desc!r} id={resource_id!r}"
        )
    return _ok(
        f"found: {found['text'] or found['desc'] or found['resource_id']}\n"
        f"  class: {found['class']}\n"
        f"  bounds: ({found['bounds']['x1']},{found['bounds']['y1']}) "
        f"→ ({found['bounds']['x2']},{found['bounds']['y2']})\n"
        f"  center: ({found['bounds']['cx']}, {found['bounds']['cy']})",
        element=found,
    )


def _handle_smart_tap_legacy(
    serial: Optional[str],
    text: Optional[str],
    desc: Optional[str],
    resource_id: Optional[str],
) -> Dict[str, Any]:
    if not any([text, desc, resource_id]):
        return _err("smart_tap requires one of text, desc, or resource_id")
    _run(["shell", "uiautomator", "dump", "/sdcard/_ui.xml"], serial=serial, timeout=30)
    r = _run(["shell", "cat", "/sdcard/_ui.xml"], serial=serial, timeout=30)
    _run(["shell", "rm", "/sdcard/_ui.xml"], serial=serial)
    elements = smart.parse_ui_dump(r["stdout"])
    found = smart.find_element(
        elements, text=text, desc=desc, resource_id=resource_id
    )
    if not found:
        return _err(
            f"no clickable element found matching text={text!r} "
            f"desc={desc!r} id={resource_id!r}"
        )
    b = found["bounds"]
    _run(["shell", "input", "tap", str(b["cx"]), str(b["cy"])], serial=serial)
    label = found["text"] or found["desc"] or found["resource_id"]
    return _ok(f"smart-tapped '{label}' at ({b['cx']}, {b['cy']})", element=found)


def _handle_sensors(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(["shell", "dumpsys", "sensorservice"], serial=serial, timeout=30)
    sensors = smart.list_sensors(r["stdout"])
    last_vals = smart.parse_sensor_last_values(r["stdout"])
    # Join: attach last values to sensors by name match
    for s in sensors:
        for k, v in last_vals.items():
            if s["name"].startswith(k) or k.startswith(s["name"]):
                s["last_values"] = v
                break
    lines = [f"🌡️ {len(sensors)} sensors:"]
    for s in sensors[:25]:
        vals = s.get("last_values")
        suffix = f" = {vals}" if vals else ""
        lines.append(f"  • {s['name']} ({s['vendor']}){suffix}")
    return _ok("\n".join(lines), sensors=sensors)


def _handle_thermals(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(["shell", "dumpsys", "thermalservice"], serial=serial, timeout=30)
    temps = smart.parse_thermals(r["stdout"])
    # Sort by value desc
    temps.sort(key=lambda t: t["value"], reverse=True)
    lines = [f"🌡️ {len(temps)} thermal zones (hottest first):"]
    for t in temps[:20]:
        status = " ⚠️" if t["status"] > 0 else ""
        lines.append(f"  • {t['name']}: {t['value']:.1f}°{status}")
    return _ok("\n".join(lines), thermals=temps)


def _handle_wifi_info(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(["shell", "cmd", "wifi", "status"], serial=serial, timeout=10)
    info = smart.parse_wifi(r["stdout"])
    if not info.get("connected"):
        return _ok("wifi: not connected", wifi=info)
    return _ok(
        f"📶 wifi: {info['ssid']} ({info['rssi']} dBm, "
        f"{info['link_speed_mbps']} Mbps @ {info['frequency_mhz']} MHz)\n"
        f"  IP: {info['ip']}   BSSID: {info['bssid']}",
        wifi=info,
    )


def _screenrec_background_loop(
    serial: Optional[str],
    bit_rate_mbps: int,
    size: Optional[str],
    segment_sec: int,
) -> None:
    """Record screen in chained segments until stop_flag is set.

    Each segment is `segment_sec` seconds (capped at 180 by Android).
    Segments are pulled to local on-the-fly so stop() is fast.
    """
    s = serial or _SELECTED_SERIAL
    seg_idx = 0
    while not _SCREENREC_STATE["stop_flag"]:
        seg_idx += 1
        remote = f"/sdcard/_ddrec_{int(time.time())}_{seg_idx:03d}.mp4"
        cmd = [_adb_bin()]
        if s:
            cmd += ["-s", s]
        shell_args = ["shell", f"screenrecord --time-limit {segment_sec} "
                               f"--bit-rate {bit_rate_mbps * 1_000_000}"]
        if size:
            shell_args[-1] += f" --size {size}"
        shell_args[-1] += f" {remote}"
        cmd += shell_args

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with _SCREENREC_STATE["lock"]:
                _SCREENREC_STATE["process"] = proc
            # Poll for stop_flag every 0.5s so we can kill early
            start = time.time()
            while time.time() - start < segment_sec + 5:
                if _SCREENREC_STATE["stop_flag"]:
                    try:
                        # SIGINT to screenrecord finalizes the mp4 properly
                        proc.send_signal(2)  # SIGINT
                        proc.wait(timeout=3)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
            # Give the device a beat to finalize
            time.sleep(0.8)
        except Exception:
            break

        _SCREENREC_STATE["segments_remote"].append(remote)

        # Pull this segment immediately so stop() doesn't have to wait
        local_dir = Path(_SCREENREC_STATE["output_path"] or f"/tmp/adb_rec_{int(time.time())}.mp4").parent
        local_dir.mkdir(parents=True, exist_ok=True)
        base = Path(_SCREENREC_STATE["output_path"] or f"/tmp/adb_rec.mp4")
        if seg_idx == 1 and not _SCREENREC_STATE["stop_flag"]:
            # first segment with segments to come → number them
            local_path = base
        elif seg_idx == 1:
            local_path = base
        else:
            local_path = base.with_name(
                f"{base.stem}_seg{seg_idx:03d}{base.suffix}"
            )
        try:
            _run(["pull", remote, str(local_path)], serial=serial, timeout=60)
            _run(["shell", "rm", remote], serial=serial)
            _SCREENREC_STATE["segments_local"].append(str(local_path))
        except Exception:
            pass

        if _SCREENREC_STATE["stop_flag"]:
            break


def _handle_screen_record_start(
    serial: Optional[str],
    output_path: Optional[str],
    bit_rate_mbps: int = 4,
    size: Optional[str] = None,
    segment_sec: int = 180,
) -> Dict[str, Any]:
    """Start a BACKGROUND screen recording. Returns immediately."""
    if _SCREENREC_STATE["running"]:
        return {
            "status": "success",
            "content": [{"text": f"🎬 screen recording already running "
                                  f"(started {time.time() - (_SCREENREC_STATE['started_at'] or 0):.0f}s ago, "
                                  f"{len(_SCREENREC_STATE['segments_local'])} segments so far)"}],
            "already_running": True,
        }
    if segment_sec > 180:
        segment_sec = 180  # Android hard limit
    if segment_sec < 10:
        segment_sec = 10

    # Reset state
    _SCREENREC_STATE["running"] = True
    _SCREENREC_STATE["stop_flag"] = False
    _SCREENREC_STATE["serial"] = serial or _SELECTED_SERIAL
    _SCREENREC_STATE["started_at"] = time.time()
    _SCREENREC_STATE["segments_remote"] = []
    _SCREENREC_STATE["segments_local"] = []
    _SCREENREC_STATE["output_path"] = output_path or f"/tmp/adb_rec_{int(time.time())}.mp4"
    _SCREENREC_STATE["bit_rate_mbps"] = bit_rate_mbps
    _SCREENREC_STATE["size"] = size
    _SCREENREC_STATE["segment_sec"] = segment_sec

    t = threading.Thread(
        target=_screenrec_background_loop,
        args=(serial, bit_rate_mbps, size, segment_sec),
        daemon=True,
        name="strands-adb-screenrec",
    )
    _SCREENREC_STATE["thread"] = t
    t.start()

    return _ok(
        f"🎬 screen recording started in background "
        f"(bit_rate={bit_rate_mbps}Mbps, size={size or 'native'}, "
        f"segment={segment_sec}s, output={_SCREENREC_STATE['output_path']})",
        output_path=_SCREENREC_STATE["output_path"],
        pid=_SCREENREC_STATE.get("process").pid if _SCREENREC_STATE.get("process") else None,
    )


def _handle_screen_record_stop(serial: Optional[str] = None) -> Dict[str, Any]:
    """Stop background recording, pull remaining segments, return final path(s)."""
    if not _SCREENREC_STATE["running"]:
        return {"status": "success",
                "content": [{"text": "🎬 screen recording is not running"}]}

    _SCREENREC_STATE["stop_flag"] = True
    _SCREENREC_STATE["running"] = False

    # Kill the current segment's adb process if still alive
    proc = _SCREENREC_STATE.get("process")
    if proc and proc.poll() is None:
        try:
            proc.send_signal(2)  # SIGINT
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    # Wait for the background thread to finish pulling
    t = _SCREENREC_STATE.get("thread")
    if t and t.is_alive():
        t.join(timeout=15)

    duration = time.time() - (_SCREENREC_STATE.get("started_at") or time.time())
    locals_ = list(_SCREENREC_STATE["segments_local"])
    total_bytes = 0
    for p in locals_:
        try:
            total_bytes += Path(p).stat().st_size
        except Exception:
            pass

    # If multiple segments and ffmpeg available, try to concat
    final_path = _SCREENREC_STATE["output_path"]
    concat_path = None
    if len(locals_) > 1 and shutil.which("ffmpeg"):
        try:
            base = Path(final_path)
            concat_list = base.with_suffix(".concat.txt")
            concat_list.write_text(
                "\n".join(f"file '{Path(p).absolute()}'" for p in locals_)
            )
            merged = base.with_name(f"{base.stem}_merged{base.suffix}")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat_list), "-c", "copy", str(merged)],
                capture_output=True, timeout=60,
            )
            if merged.exists():
                concat_path = str(merged)
            try:
                concat_list.unlink()
            except Exception:
                pass
        except Exception:
            pass

    _SCREENREC_STATE["thread"] = None
    _SCREENREC_STATE["process"] = None

    msg_parts = [
        f"🎬 recording stopped after {duration:.1f}s",
        f"{len(locals_)} segment(s), {total_bytes:,} bytes total",
    ]
    if concat_path:
        msg_parts.append(f"merged → {concat_path}")
    elif locals_:
        msg_parts.append(f"first segment → {locals_[0]}")

    return _ok(
        " | ".join(msg_parts),
        segments=locals_,
        merged_path=concat_path,
        duration_sec=duration,
        total_bytes=total_bytes,
    )


def _handle_screen_record_status() -> Dict[str, Any]:
    """Check if background screen recording is active."""
    if not _SCREENREC_STATE["running"]:
        return {"status": "success",
                "content": [{"text": "🎬 not recording"}],
                "running": False}
    elapsed = time.time() - (_SCREENREC_STATE.get("started_at") or time.time())
    return {
        "status": "success",
        "content": [{"text": f"🎬 recording for {elapsed:.0f}s, "
                              f"{len(_SCREENREC_STATE['segments_local'])} segment(s) pulled, "
                              f"output={_SCREENREC_STATE['output_path']}"}],
        "running": True,
        "elapsed_sec": elapsed,
        "segments": list(_SCREENREC_STATE["segments_local"]),
        "output_path": _SCREENREC_STATE["output_path"],
    }


def _handle_screen_record(
    duration_sec: int,
    output_path: Optional[str],
    serial: Optional[str],
) -> Dict[str, Any]:
    """Record the screen for duration_sec seconds, pull mp4."""
    if duration_sec > 180:
        return _err("max duration is 180s (Android limit)")
    remote = f"/sdcard/_rec_{int(time.time())}.mp4"
    local = Path(output_path) if output_path else Path(
        f"/tmp/adb_rec_{int(time.time())}.mp4"
    )
    local.parent.mkdir(parents=True, exist_ok=True)
    # Run screenrecord in background
    cmd_bg = [_adb_bin()]
    s = serial or _SELECTED_SERIAL
    if s:
        cmd_bg += ["-s", s]
    cmd_bg += ["shell", f"screenrecord --time-limit {duration_sec} {remote}"]
    try:
        subprocess.run(cmd_bg, capture_output=True, timeout=duration_sec + 10)
    except subprocess.TimeoutExpired:
        return _err("screen record timed out")
    # Small delay so file finalizes
    time.sleep(0.5)
    r = _run(["pull", remote, str(local)], serial=serial, timeout=60)
    _run(["shell", "rm", remote], serial=serial)
    if r["returncode"] != 0 or not local.exists():
        return _err(f"pull failed: {r['stderr']}")
    return _ok(
        f"🎬 recorded {duration_sec}s → {local} ({local.stat().st_size} bytes)",
        path=str(local),
        size_bytes=local.stat().st_size,
    )


def _handle_dial(phone: str, call: bool, serial: Optional[str]) -> Dict[str, Any]:
    """Open dialer with number (call=False) or actually place the call (call=True)."""
    if not phone:
        return _err("phone number required")
    # Sanitize: only digits, +, *, #
    clean = re.sub(r"[^+\d*#]", "", phone)
    if not clean:
        return _err("invalid phone number")
    action = "android.intent.action.CALL" if call else "android.intent.action.DIAL"
    _run(
        ["shell", "am", "start", "-a", action, "-d", f"tel:{clean}"],
        serial=serial,
    )
    verb = "placed call to" if call else "opened dialer for"
    return _ok(f"{verb} {clean}")


def _handle_sms_compose(
    phone: str, text: str, send: bool, serial: Optional[str]
) -> Dict[str, Any]:
    """Open default SMS app with pre-filled message.

    send=True is intentionally NOT supported — Android prevents this
    without the app being the default SMS handler (security by design).
    User must tap Send.
    """
    if send:
        return _err(
            "sending SMS programmatically requires default-SMS-app status; "
            "use send=False to draft and let user tap Send"
        )
    if not phone:
        return _err("phone number required")
    clean = re.sub(r"[^+\d*#]", "", phone)
    args = ["shell", "am", "start", "-a", "android.intent.action.SENDTO",
            "-d", f"sms:{clean}"]
    if text:
        args += ["--es", "sms_body", text]
    _run(args, serial=serial)
    return _ok(f"📩 drafted SMS to {clean}: {text[:60]}")


def _handle_media_control(action: str, serial: Optional[str]) -> Dict[str, Any]:
    """Media key events: play, pause, play_pause, next, previous, stop."""
    keymap = {
        "play": "KEYCODE_MEDIA_PLAY",
        "pause": "KEYCODE_MEDIA_PAUSE",
        "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE",
        "next": "KEYCODE_MEDIA_NEXT",
        "previous": "KEYCODE_MEDIA_PREVIOUS",
        "stop": "KEYCODE_MEDIA_STOP",
        "rewind": "KEYCODE_MEDIA_REWIND",
        "fast_forward": "KEYCODE_MEDIA_FAST_FORWARD",
    }
    kc = keymap.get(action.lower())
    if not kc:
        return _err(f"unknown media action: {action}. Valid: {list(keymap)}")
    _run(["shell", "input", "keyevent", kc], serial=serial)
    return _ok(f"media: {action}")


def _handle_volume(
    stream: str, direction: str, serial: Optional[str]
) -> Dict[str, Any]:
    """Volume control. direction: up, down, mute."""
    keymap = {"up": "KEYCODE_VOLUME_UP", "down": "KEYCODE_VOLUME_DOWN", "mute": "KEYCODE_VOLUME_MUTE"}
    kc = keymap.get(direction.lower())
    if not kc:
        return _err(f"direction must be up|down|mute, got {direction}")
    _run(["shell", "input", "keyevent", kc], serial=serial)
    return _ok(f"volume {direction}")


# ----------------------------------------------------------------------------
# The @tool
# ----------------------------------------------------------------------------



# =============================================================================
# 📜  Logcat Event Stream  (Frontier #3)
# =============================================================================
#
# Instead of polling `adb logcat -d` for a snapshot, keep a background thread
# tailing `adb logcat -v threadtime` forever. Parse each line, classify it
# (notification / crash / battery / package / app-launch / custom), and push
# structured events into devduck's unified `event_bus`.
#
# The agent sees these in its dynamic context automatically (event_bus is
# already wired into get_context_string in devduck's prompt assembler).

_LOGCAT_STATE: Dict[str, Any] = {
    "running": False,
    "process": None,
    "thread": None,
    "serial": None,
    "filters": [],
    "events_parsed": 0,
    "started_at": None,
    "lock": threading.Lock(),
}

# ── Background screen recorder state ─────────────────────────────────────────
# screenrecord on Android has a hard 180s limit per invocation. We spawn it
# as a background adb shell process, and auto-chain new segments if the user
# wants to record longer than 180s. Stop pulls all segments and (optionally)
# concatenates them via ffmpeg if present.
_SCREENREC_STATE: Dict[str, Any] = {
    "running": False,
    "process": None,
    "thread": None,
    "serial": None,
    "started_at": None,
    "stop_flag": False,
    "segments_remote": [],   # list of /sdcard/*.mp4 on device
    "segments_local": [],    # list of pulled local paths
    "output_path": None,     # where the final/first segment ends up
    "bit_rate_mbps": 4,
    "size": None,            # e.g. "720x1280", None = native
    "segment_sec": 180,      # per-segment duration cap (Android limit)
    "lock": threading.Lock(),
}

# threadtime format: "MM-DD HH:MM:SS.mmm PID TID LEVEL TAG: MESSAGE"
_LOGCAT_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<pid>\d+)\s+(?P<tid>\d+)\s+(?P<level>[VDIWEF])\s+(?P<tag>[^:]+?):\s*(?P<msg>.*)$"
)


def _classify_logcat(line_parsed: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Classify a parsed logcat line → high-value event or None."""
    tag = line_parsed["tag"].strip()
    msg = line_parsed["msg"]
    level = line_parsed["level"]

    # 1. App crash / ANR
    if tag == "AndroidRuntime" and level in ("E", "F"):
        if "FATAL EXCEPTION" in msg or "Process:" in msg:
            return {
                "category": "crash",
                "summary": f"💥 App crash: {msg[:100]}",
                "severity": "error",
            }
    if tag in ("ActivityManager", "am_anr") and "ANR" in msg:
        return {"category": "anr", "summary": f"🧊 ANR: {msg[:120]}",
                "severity": "warning"}

    # 2. Low memory
    if tag == "lowmemorykiller" or (tag == "ActivityManager" and "Low on memory" in msg):
        return {"category": "low_memory", "summary": f"🧠 {msg[:120]}",
                "severity": "warning"}

    # 3. Battery state
    if tag == "BatteryService" or (tag == "healthd" and "battery" in msg.lower()):
        return {"category": "battery", "summary": f"🔋 {msg[:120]}"}

    # 4. Package install / uninstall
    if tag in ("PackageManager", "PackageInstaller"):
        lm = msg.lower()
        if "installed package" in lm or "installing" in lm:
            return {"category": "package_install", "summary": f"📦 {msg[:120]}"}
        if "uninstall" in lm or "removed package" in lm:
            return {"category": "package_remove", "summary": f"🗑 {msg[:120]}"}

    # 5. Activity / app focus change
    if tag == "ActivityTaskManager" and "START u0" in msg:
        # Example: "START u0 {act=... cmp=com.whatsapp/.HomeActivity} from ..."
        m = re.search(r"cmp=([\w.]+)/", msg)
        if m:
            return {
                "category": "app_launch",
                "summary": f"🚀 App launched: {m.group(1)}",
                "package": m.group(1),
            }

    # 6. Phone call state
    if tag in ("TelephonyManager", "Telecom", "PhoneStateListener"):
        if "RINGING" in msg:
            return {"category": "call_ringing", "summary": f"📞 Incoming call: {msg[:80]}"}
        if "OFFHOOK" in msg:
            return {"category": "call_active", "summary": f"📞 Call active"}

    # 7. Wifi connection change
    if tag == "WifiStateMachine" or tag == "wpa_supplicant":
        lm = msg.lower()
        if "connected to" in lm or "connecting to" in lm:
            return {"category": "wifi_connect", "summary": f"📶 {msg[:120]}"}
        if "disconnect" in lm:
            return {"category": "wifi_disconnect", "summary": f"📶❌ {msg[:120]}"}

    return None


def _logcat_reader_thread(
    proc: "subprocess.Popen",
    serial: Optional[str],
    source_prefix: str,
) -> None:
    """Consume logcat lines, classify, push to event_bus."""
    try:
        # Lazy import — keep strands-adb usable without devduck present
        from devduck.tools.event_bus import bus  # type: ignore
        has_bus = True
    except Exception:
        bus = None
        has_bus = False

    for raw in iter(proc.stdout.readline, b""):
        if not _LOGCAT_STATE["running"]:
            break
        try:
            line = raw.decode("utf-8", errors="replace").rstrip()
        except Exception:
            continue
        m = _LOGCAT_RE.match(line)
        if not m:
            continue
        parsed = m.groupdict()
        evt = _classify_logcat(parsed)
        if not evt:
            continue

        with _LOGCAT_STATE["lock"]:
            _LOGCAT_STATE["events_parsed"] += 1

        if has_bus:
            bus.emit(
                event_type=f"phone.log.{evt['category']}",
                source=source_prefix,
                summary=evt["summary"],
                detail=line[:500],
                metadata={
                    "category": evt["category"],
                    "severity": evt.get("severity", "info"),
                    "tag": parsed["tag"].strip(),
                    "level": parsed["level"],
                    "serial": serial or "default",
                    **{k: v for k, v in evt.items()
                       if k not in ("category", "summary", "severity")},
                },
            )


def _handle_log_stream_start(
    serial: Optional[str], filters: Optional[List[str]]
) -> Dict[str, Any]:
    """Start a background logcat reader → event_bus pipeline."""
    if _LOGCAT_STATE["running"]:
        return {
            "status": "success",
            "content": [{"text": f"logcat stream already running (events: "
                                  f"{_LOGCAT_STATE['events_parsed']})"}],
            "already_running": True,
        }

    cmd = [_adb_bin()]
    s = serial or _SELECTED_SERIAL
    if s:
        cmd += ["-s", s]
    cmd += ["logcat", "-v", "threadtime"]

    # Custom filters. Default = a curated whitelist of tags that we actually
    # classify in _classify_logcat(). Everything else silenced to keep the
    # parse rate sane — logcat can easily hit thousands of lines/sec.
    spec = filters or [
        "ActivityTaskManager:I",   # app_launch events are level I
        "AndroidRuntime:E",        # crash stacks
        "ActivityManager:W",       # ANR, low memory
        "PackageManager:I",        # installs/removes
        "PackageInstaller:I",
        "BatteryService:*",
        "healthd:*",
        "TelephonyManager:*",
        "Telecom:*",
        "PhoneStateListener:*",
        "WifiStateMachine:*",
        "wpa_supplicant:*",
        "lowmemorykiller:*",
        "*:S",                      # silence everything else
    ]
    cmd += spec

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1,
        )
    except FileNotFoundError:
        return _err(f"adb binary not found: {_adb_bin()}")

    _LOGCAT_STATE["running"] = True
    _LOGCAT_STATE["process"] = proc
    _LOGCAT_STATE["serial"] = s
    _LOGCAT_STATE["filters"] = spec
    _LOGCAT_STATE["started_at"] = time.time()
    _LOGCAT_STATE["events_parsed"] = 0

    t = threading.Thread(
        target=_logcat_reader_thread,
        args=(proc, s, f"phone:{s or 'default'}"),
        daemon=True,
        name="strands-adb-logcat",
    )
    _LOGCAT_STATE["thread"] = t
    t.start()

    return {
        "status": "success",
        "content": [{"text": f"📜 logcat stream started (filters={spec}, "
                              f"serial={s or 'default'}). "
                              f"Events pushed to devduck event_bus under "
                              f"topic `phone.log.*`."}],
        "pid": proc.pid,
        "filters": spec,
    }


def _handle_log_stream_stop() -> Dict[str, Any]:
    """Gracefully stop the background logcat reader."""
    if not _LOGCAT_STATE["running"]:
        return {"status": "success",
                "content": [{"text": "logcat stream is not running"}]}

    _LOGCAT_STATE["running"] = False
    proc = _LOGCAT_STATE.get("process")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    parsed = _LOGCAT_STATE["events_parsed"]
    duration = time.time() - (_LOGCAT_STATE.get("started_at") or time.time())
    _LOGCAT_STATE["process"] = None
    _LOGCAT_STATE["thread"] = None

    return {
        "status": "success",
        "content": [{"text": f"📜 logcat stream stopped. Parsed "
                              f"{parsed} events over {duration:.1f}s."}],
        "events_parsed": parsed,
        "duration_sec": duration,
    }


def _handle_log_stream_status() -> Dict[str, Any]:
    """Report current logcat stream state."""
    running = _LOGCAT_STATE["running"]
    parsed = _LOGCAT_STATE["events_parsed"]
    started = _LOGCAT_STATE.get("started_at")
    duration = (time.time() - started) if started else 0
    return {
        "status": "success",
        "content": [{"text": f"logcat stream: "
                              f"{'🟢 running' if running else '🔴 stopped'} "
                              f"| events={parsed} | duration={duration:.1f}s "
                              f"| filters={_LOGCAT_STATE.get('filters')}"}],
        "running": running,
        "events_parsed": parsed,
        "duration_sec": duration,
        "filters": _LOGCAT_STATE.get("filters"),
    }




# =============================================================================
# ⚙️  Settings Mutation  (Frontier #13)
# =============================================================================
#
# Read/write Android settings via `settings` binary (no root) and convenient
# high-level presets via `svc` / `cmd` binaries. Covers the 90% case the
# agent needs without fighting permission dialogs.
#
# Three tiers:
#   1. Raw — setting_get/put/delete/list        (generic key-value over any namespace)
#   2. Preset — ringer/brightness/bluetooth     (semantic, typed, with verification)
#   3. Diagnostic — setting_dump                (full snapshot of a namespace)

_SETTINGS_NAMESPACES = ("system", "secure", "global")

_RINGER_VALUES = {"normal", "silent", "vibrate"}

def _run_shell_capture(
    args: List[str], serial: Optional[str], timeout: int = 10
) -> Tuple[int, str, str]:
    """Run an adb shell command, capture stdout+stderr. Return (rc, stdout, stderr)."""
    cmd = [_adb_bin()]
    s = serial or _SELECTED_SERIAL
    if s:
        cmd += ["-s", s]
    cmd += ["shell"] + args
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", f"adb binary not found: {_adb_bin()}"


def _handle_setting_get(
    namespace: str, key: str, serial: Optional[str]
) -> Dict[str, Any]:
    """settings get <namespace> <key>"""
    if namespace not in _SETTINGS_NAMESPACES:
        return _err(f"namespace must be one of {_SETTINGS_NAMESPACES}, got {namespace!r}")
    rc, out, errout = _run_shell_capture(
        ["settings", "get", namespace, key], serial
    )
    if rc != 0:
        return _err(f"settings get failed: {errout or out}")
    value = out if out and out != "null" else None
    return {
        "status": "success",
        "content": [{"text": f"{namespace}/{key} = {value!r}"}],
        "namespace": namespace,
        "key": key,
        "value": value,
    }


def _handle_setting_put(
    namespace: str, key: str, value: str, serial: Optional[str]
) -> Dict[str, Any]:
    """settings put <namespace> <key> <value>"""
    if namespace not in _SETTINGS_NAMESPACES:
        return _err(f"namespace must be one of {_SETTINGS_NAMESPACES}, got {namespace!r}")
    rc, out, errout = _run_shell_capture(
        ["settings", "put", namespace, key, str(value)], serial
    )
    if rc != 0:
        return _err(f"settings put failed: {errout or out}")
    # Verify
    rc2, verify, _ = _run_shell_capture(
        ["settings", "get", namespace, key], serial
    )
    return {
        "status": "success",
        "content": [{"text": f"✅ {namespace}/{key} = {verify} (was set to {value!r})"}],
        "namespace": namespace,
        "key": key,
        "requested": str(value),
        "actual": verify,
        "verified": verify == str(value),
    }


def _handle_setting_delete(
    namespace: str, key: str, serial: Optional[str]
) -> Dict[str, Any]:
    """settings delete <namespace> <key>"""
    if namespace not in _SETTINGS_NAMESPACES:
        return _err(f"namespace must be one of {_SETTINGS_NAMESPACES}")
    rc, out, errout = _run_shell_capture(
        ["settings", "delete", namespace, key], serial
    )
    if rc != 0:
        return _err(f"settings delete failed: {errout or out}")
    return {
        "status": "success",
        "content": [{"text": f"🗑  deleted {namespace}/{key} ({out})"}],
        "namespace": namespace,
        "key": key,
    }


def _handle_setting_list(
    namespace: str, filter_text: Optional[str], serial: Optional[str]
) -> Dict[str, Any]:
    """settings list <namespace> — returns parsed dict + summary text."""
    if namespace not in _SETTINGS_NAMESPACES:
        return _err(f"namespace must be one of {_SETTINGS_NAMESPACES}")
    rc, out, errout = _run_shell_capture(
        ["settings", "list", namespace], serial, timeout=20
    )
    if rc != 0:
        return _err(f"settings list failed: {errout or out}")
    parsed: Dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            parsed[k.strip()] = v.strip()
    if filter_text:
        ft = filter_text.lower()
        parsed = {k: v for k, v in parsed.items() if ft in k.lower()}
    preview = "\n".join(f"  {k} = {v[:80]}" for k, v in list(parsed.items())[:30])
    more = f"\n  … and {len(parsed) - 30} more" if len(parsed) > 30 else ""
    return {
        "status": "success",
        "content": [{"text": f"⚙️  {len(parsed)} settings in {namespace}"
                              f"{' matching ' + repr(filter_text) if filter_text else ''}:\n"
                              f"{preview}{more}"}],
        "namespace": namespace,
        "settings": parsed,
        "count": len(parsed),
    }


# ── High-level presets ─────────────────────────────────────────

def _handle_set_ringer(mode: str, serial: Optional[str]) -> Dict[str, Any]:
    """Set ringer mode: normal | silent | vibrate."""
    m = (mode or "").lower()
    if m not in _RINGER_VALUES:
        return _err(f"ringer mode must be one of {sorted(_RINGER_VALUES)}, got {mode!r}")
    rc, out, errout = _run_shell_capture(
        ["cmd", "audio", "set-ringer-mode", m.upper()], serial
    )
    if rc != 0:
        return _err(f"set-ringer-mode failed: {errout or out}")
    # Verify via settings (get-ringer-mode is empty on some OEMs like Pixel)
    time.sleep(0.8)
    _mode_map = {"0": "silent", "1": "vibrate", "2": "normal"}
    rc2, verify, _ = _run_shell_capture(
        ["settings", "get", "global", "mode_ringer"], serial
    )
    verified_mode = _mode_map.get(verify, "unknown")
    verified = verified_mode == m
    return {
        "status": "success",
        "content": [{"text": f"🔔 ringer → {m.upper()} "
                              f"(verified via mode_ringer={verify} → {verified_mode})"}],
        "mode": m,
        "verified_mode": verified_mode,
        "verified": verified,
    }


def _handle_set_brightness(
    level: int, auto: Optional[bool], serial: Optional[str]
) -> Dict[str, Any]:
    """Set screen brightness (0-255). Optionally toggle auto mode."""
    try:
        lv = int(level)
    except (TypeError, ValueError):
        return _err(f"brightness level must be int 0-255, got {level!r}")
    if not 0 <= lv <= 255:
        return _err(f"brightness level must be 0-255, got {lv}")

    # Optionally set auto mode first (0 = manual, 1 = auto)
    if auto is not None:
        mode_val = "1" if auto else "0"
        _run_shell_capture(
            ["settings", "put", "system", "screen_brightness_mode", mode_val],
            serial,
        )

    rc, out, errout = _run_shell_capture(
        ["settings", "put", "system", "screen_brightness", str(lv)], serial
    )
    if rc != 0:
        return _err(f"set brightness failed: {errout or out}")
    # verify
    rc2, verify, _ = _run_shell_capture(
        ["settings", "get", "system", "screen_brightness"], serial
    )
    return {
        "status": "success",
        "content": [{"text": f"☀️  brightness → {lv}/255 "
                              f"(verified: {verify}, auto={auto})"}],
        "level": lv,
        "auto": auto,
        "verified": verify,
    }


def _handle_set_bluetooth(enabled: bool, serial: Optional[str]) -> Dict[str, Any]:
    """Enable/disable bluetooth via `svc bluetooth`."""
    verb = "enable" if enabled else "disable"
    rc, out, errout = _run_shell_capture(
        ["svc", "bluetooth", verb], serial
    )
    # svc prints "Success" but returns 0 even on silent-fail; verify
    time.sleep(1.0)
    rc2, verify, _ = _run_shell_capture(
        ["settings", "get", "global", "bluetooth_on"], serial
    )
    verified = (verify == "1") == enabled
    return {
        "status": "success" if verified else "error",
        "content": [{"text": f"{'📶' if enabled else '📴'} bluetooth → "
                              f"{verb} ({'verified' if verified else 'NOT verified — may need UI'})"}],
        "enabled": enabled,
        "verified": verified,
        "current": verify,
    }


def _handle_set_airplane_mode(enabled: bool, serial: Optional[str]) -> Dict[str, Any]:
    """Toggle airplane mode flag.

    NOTE: flipping the bit works, but the system broadcast is blocked for
    adb shell UID → radios may not actually cycle until user interaction.
    Returned `radios_affected` is False as honest feedback.
    """
    rc, out, errout = _run_shell_capture(
        ["settings", "put", "global", "airplane_mode_on", "1" if enabled else "0"],
        serial,
    )
    if rc != 0:
        return _err(f"set airplane_mode_on failed: {errout or out}")
    # Try the broadcast — may fail with SecurityException, that's ok
    _run_shell_capture(
        [
            "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE",
            "--ez", "state", "true" if enabled else "false",
        ],
        serial,
    )
    rc2, verify, _ = _run_shell_capture(
        ["settings", "get", "global", "airplane_mode_on"], serial
    )
    return {
        "status": "success",
        "content": [{"text": f"✈️  airplane_mode_on = {verify} "
                              f"(flag set; radios may need UI toggle to actually cycle)"}],
        "enabled": enabled,
        "verified_flag": verify,
        "radios_affected": False,
        "caveat": "settings flag set; broadcast intent blocked for shell UID. "
                  "User may need to open Quick Settings for radios to actually change.",
    }


def _handle_setting_dump(
    serial: Optional[str],
) -> Dict[str, Any]:
    """Snapshot all three namespaces for agent context / debugging."""
    out: Dict[str, Dict[str, str]] = {}
    for ns in _SETTINGS_NAMESPACES:
        rc, raw, _ = _run_shell_capture(
            ["settings", "list", ns], serial, timeout=30
        )
        if rc == 0:
            parsed = {}
            for line in raw.splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    parsed[k.strip()] = v.strip()
            out[ns] = parsed
        else:
            out[ns] = {}
    total = sum(len(v) for v in out.values())
    return {
        "status": "success",
        "content": [{"text": f"📊 dumped {total} settings across "
                              f"{list(out.keys())} namespaces"}],
        "snapshot": out,
        "total": total,
    }




# =============================================================================
# 🎯  UI Query DSL  (Frontier #10)
# =============================================================================
#
# Ergonomic layer on top of raw ui_dump XML. Instead of scraping coords by
# hand, agents can say:
#
#   adb("ui_find", text="Settings")                    → list of matches
#   adb("ui_tap_by", text="Settings")                  → finds + taps center
#   adb("ui_wait_for", text="OK", timeout=5)           → polls until visible
#
# Matchers compose: text="Chat", class_="...TextView", clickable=True.
# All string matchers do case-insensitive substring match by default;
# pass an "=..." prefix for exact match, or "^" for regex.

import xml.etree.ElementTree as _ET


_BOUNDS_RE = re.compile(r"\[(\-?\d+),(\-?\d+)\]\[(\-?\d+),(\-?\d+)\]")


def _match_str(value: str, pattern: str) -> bool:
    """Match a UI attribute against a pattern spec.

    Patterns:
      "foo"    → case-insensitive substring
      "=foo"   → exact case-sensitive match
      "^regex" → regex search (case-insensitive)
    """
    if not pattern:
        return True
    if pattern.startswith("="):
        return value == pattern[1:]
    if pattern.startswith("^"):
        try:
            return bool(re.search(pattern[1:], value, re.IGNORECASE))
        except re.error:
            return False
    return pattern.lower() in (value or "").lower()


def _parse_bounds(bounds_str: str) -> Optional[Tuple[int, int, int, int]]:
    """Parse '[x1,y1][x2,y2]' → (x1, y1, x2, y2) or None."""
    m = _BOUNDS_RE.search(bounds_str or "")
    if not m:
        return None
    return tuple(int(g) for g in m.groups())  # type: ignore


def _node_to_match(node: "_ET.Element") -> Dict[str, Any]:
    """Turn a UI XML node → flat dict with center coords."""
    attrs = node.attrib
    bounds = _parse_bounds(attrs.get("bounds", ""))
    cx = cy = None
    if bounds:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    return {
        "text": attrs.get("text", ""),
        "resource_id": attrs.get("resource-id", ""),
        "class_name": attrs.get("class", ""),
        "content_desc": attrs.get("content-desc", ""),
        "package": attrs.get("package", ""),
        "clickable": attrs.get("clickable") == "true",
        "scrollable": attrs.get("scrollable") == "true",
        "enabled": attrs.get("enabled") == "true",
        "selected": attrs.get("selected") == "true",
        "bounds": bounds,
        "center": (cx, cy) if bounds else None,
    }


def _iter_ui_nodes(xml_text: str):
    """Yield all XML nodes from a ui_dump."""
    try:
        root = _ET.fromstring(xml_text)
    except _ET.ParseError:
        return
    # ui_dump nests <hierarchy><node>...<node>...
    for node in root.iter("node"):
        yield node


def _filter_ui(
    xml_text: str,
    *,
    text: Optional[str] = None,
    resource_id: Optional[str] = None,
    class_name: Optional[str] = None,
    content_desc: Optional[str] = None,
    clickable: Optional[bool] = None,
    scrollable: Optional[bool] = None,
    package: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return all nodes matching the given filters."""
    results: List[Dict[str, Any]] = []
    for node in _iter_ui_nodes(xml_text):
        a = node.attrib
        if text is not None and not _match_str(a.get("text", ""), text):
            # also try content-desc as fallback (many icons have desc, no text)
            if not _match_str(a.get("content-desc", ""), text):
                continue
        if resource_id is not None and not _match_str(a.get("resource-id", ""), resource_id):
            continue
        if class_name is not None and not _match_str(a.get("class", ""), class_name):
            continue
        if content_desc is not None and not _match_str(a.get("content-desc", ""), content_desc):
            continue
        if package is not None and not _match_str(a.get("package", ""), package):
            continue
        if clickable is not None and (a.get("clickable") == "true") != clickable:
            continue
        if scrollable is not None and (a.get("scrollable") == "true") != scrollable:
            continue
        m = _node_to_match(node)
        # Drop nodes with no bounds — invisible / degenerate
        if not m["bounds"]:
            continue
        results.append(m)
    return results


def _get_ui_xml(serial: Optional[str]) -> Optional[str]:
    """Fetch a fresh ui_dump XML. Returns None on failure."""
    # Use the existing ui_dump handler but only care about its xml
    try:
        r = _handle_ui_dump(serial)  # noqa: F821 — defined elsewhere
    except Exception:
        return None
    return r.get("xml") if isinstance(r, dict) else None


def _handle_ui_find(
    serial: Optional[str],
    text: Optional[str],
    resource_id: Optional[str],
    class_name: Optional[str],
    content_desc: Optional[str],
    clickable: Optional[bool],
    scrollable: Optional[bool],
    package: Optional[str],
) -> Dict[str, Any]:
    """Find all UI nodes matching the filters."""
    xml_text = _get_ui_xml(serial)
    if not xml_text:
        return _err("ui_find: could not fetch ui_dump")
    matches = _filter_ui(
        xml_text,
        text=text, resource_id=resource_id, class_name=class_name,
        content_desc=content_desc, clickable=clickable, scrollable=scrollable,
        package=package,
    )
    preview_lines = []
    for m in matches[:10]:
        preview_lines.append(
            f"  • {m['text'][:30]!r} (desc={m['content_desc'][:20]!r}) "
            f"@ {m['center']} clickable={m['clickable']} "
            f"id={m['resource_id'].split('/')[-1][:25]}"
        )
    more = f"\n  … and {len(matches) - 10} more" if len(matches) > 10 else ""
    return {
        "status": "success",
        "content": [{"text": f"🎯 ui_find: {len(matches)} match(es)\n"
                              + "\n".join(preview_lines) + more}],
        "matches": matches,
        "count": len(matches),
    }


def _handle_ui_tap_by(
    serial: Optional[str],
    text: Optional[str],
    resource_id: Optional[str],
    class_name: Optional[str],
    content_desc: Optional[str],
    clickable: Optional[bool],
    package: Optional[str],
    index: int,
) -> Dict[str, Any]:
    """Find the Nth matching node and tap its center."""
    # Default: require clickable=True for tap_by to avoid tapping labels
    if clickable is None:
        clickable = True

    xml_text = _get_ui_xml(serial)
    if not xml_text:
        return _err("ui_tap_by: could not fetch ui_dump")
    matches = _filter_ui(
        xml_text,
        text=text, resource_id=resource_id, class_name=class_name,
        content_desc=content_desc, clickable=clickable, package=package,
    )
    if not matches:
        return _err(f"ui_tap_by: no match for "
                    f"text={text!r} resource_id={resource_id!r} "
                    f"class_={class_name!r} desc={content_desc!r} "
                    f"(clickable={clickable})")
    if index >= len(matches):
        return _err(f"ui_tap_by: index {index} out of range ({len(matches)} matches)")

    target = matches[index]
    cx, cy = target["center"]
    # Tap
    rc, out, errout = _run_shell_capture(
        ["input", "tap", str(cx), str(cy)], serial
    )
    if rc != 0:
        return _err(f"ui_tap_by: tap failed: {errout or out}")
    return {
        "status": "success",
        "content": [{"text": f"🎯 tapped {target['text'][:40]!r} "
                              f"(desc={target['content_desc'][:30]!r}) "
                              f"@ ({cx},{cy}) [match {index+1}/{len(matches)}]"}],
        "target": target,
        "tap": {"x": cx, "y": cy},
        "total_matches": len(matches),
    }


def _handle_ui_wait_for(
    serial: Optional[str],
    text: Optional[str],
    resource_id: Optional[str],
    class_name: Optional[str],
    content_desc: Optional[str],
    clickable: Optional[bool],
    package: Optional[str],
    timeout: float,
    poll_interval: float,
) -> Dict[str, Any]:
    """Poll ui_dump until a match appears (or timeout)."""
    deadline = time.time() + timeout
    polls = 0
    while time.time() < deadline:
        polls += 1
        xml_text = _get_ui_xml(serial)
        if xml_text:
            matches = _filter_ui(
                xml_text,
                text=text, resource_id=resource_id, class_name=class_name,
                content_desc=content_desc, clickable=clickable, package=package,
            )
            if matches:
                elapsed = timeout - (deadline - time.time())
                return {
                    "status": "success",
                    "content": [{"text": f"✅ ui_wait_for found {len(matches)} match(es) "
                                          f"after {elapsed:.1f}s ({polls} polls). "
                                          f"First: {matches[0]['text'][:40]!r} "
                                          f"@ {matches[0]['center']}"}],
                    "matches": matches,
                    "count": len(matches),
                    "elapsed_sec": elapsed,
                    "polls": polls,
                }
        time.sleep(poll_interval)

    return {
        "status": "error",
        "content": [{"text": f"⏱  ui_wait_for timeout after {timeout}s "
                              f"({polls} polls) — no match for "
                              f"text={text!r} id={resource_id!r}"}],
        "matches": [],
        "count": 0,
        "elapsed_sec": timeout,
        "polls": polls,
        "timed_out": True,
    }




# =============================================================================
# 🎬  Screen Frames → CV  (Frontier #4)
# =============================================================================
#
# Turn the stream of pixels on the phone into first-class agent vision.
#
# Two complementary strategies:
#
#   A) screen_frames  — Fast live snapshots. Take N screencaps in sequence,
#                       each ~600ms apart. Good for seeing "what's happening
#                       right now" without recording a video.
#
#   B) video_frames   — Extract N evenly-spaced frames from an existing mp4
#                       via ffmpeg. Good for replay / understanding a past
#                       interaction captured with screen_record.
#
# Both actions return Converse-API image blocks so the agent literally sees
# the pixels, not just paths.

import shutil as _shutil


def _which_ffmpeg() -> Optional[str]:
    """Find ffmpeg binary. Returns None if not installed."""
    return _shutil.which("ffmpeg")


def _capture_one_png(serial: Optional[str]) -> Optional[bytes]:
    """Grab a single screencap PNG via `adb exec-out`. Returns raw bytes or None."""
    cmd = [_adb_bin()]
    if serial:
        cmd += ["-s", serial]
    cmd += ["exec-out", "screencap", "-p"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=15)
        if res.returncode != 0 or not res.stdout:
            return None
        return res.stdout
    except Exception:
        return None


def _handle_screen_frames(
    n: int,
    interval_sec: float,
    include_image: bool,
    output_dir: Optional[str],
    serial: Optional[str],
) -> Dict[str, Any]:
    """Capture N screen frames at fixed interval → image blocks + files.

    Each frame is saved to output_dir (or /tmp) as frame_NN.png.
    Image blocks are included so the agent can see each frame inline.
    """
    if n < 1 or n > 30:
        return _err(f"n must be 1..30, got {n}")
    if interval_sec < 0 or interval_sec > 10:
        return _err(f"interval_sec must be 0..10, got {interval_sec}")

    base = Path(output_dir or f"/tmp/adb_frames_{int(time.time())}")
    base.mkdir(parents=True, exist_ok=True)

    s = serial or _SELECTED_SERIAL
    started = time.time()
    captured: List[Dict[str, Any]] = []
    content: List[Dict[str, Any]] = []

    for i in range(n):
        if i > 0 and interval_sec > 0:
            time.sleep(interval_sec)
        t_start = time.time()
        png = _capture_one_png(s)
        if png is None:
            content.append(
                {"text": f"❌ frame {i}: screencap failed, skipping"}
            )
            continue

        path = base / f"frame_{i:02d}.png"
        path.write_bytes(png)
        captured.append({
            "index": i,
            "path": str(path),
            "size_bytes": len(png),
            "capture_time": t_start - started,
        })

    # Build content: summary header then alternating text + image blocks
    total_time = time.time() - started
    summary = (
        f"🎬 captured {len(captured)}/{n} frame(s) over {total_time:.1f}s "
        f"at {interval_sec}s intervals → {base}"
    )
    content.insert(0, {"text": summary})

    if include_image:
        for frame in captured:
            png = Path(frame["path"]).read_bytes()
            content.append({"text": f"Frame {frame['index']} (t={frame['capture_time']:.1f}s):"})
            content.append(
                {"image": {"format": "png", "source": {"bytes": png}}}
            )

    return {
        "status": "success",
        "content": content,
        "frames": captured,
        "count": len(captured),
        "output_dir": str(base),
        "total_time_sec": total_time,
    }


def _handle_video_frames(
    video_path: str,
    n: int,
    include_image: bool,
    output_dir: Optional[str],
) -> Dict[str, Any]:
    """Extract N evenly-spaced frames from an existing video via ffmpeg."""
    if not _which_ffmpeg():
        return _err(
            "ffmpeg not found in PATH — install with `brew install ffmpeg` "
            "or `apt install ffmpeg`. Alternatively, use screen_frames for "
            "live captures without ffmpeg."
        )

    vp = Path(video_path).expanduser()
    if not vp.exists():
        return _err(f"video not found: {vp}")

    if n < 1 or n > 30:
        return _err(f"n must be 1..30, got {n}")

    base = Path(output_dir or f"/tmp/adb_video_frames_{int(time.time())}")
    base.mkdir(parents=True, exist_ok=True)

    # Probe duration. Try multiple strategies because Android screenrecord
    # sometimes writes files without format-level duration metadata.
    def _probe_duration(path: str) -> float:
        strategies = [
            # Format-level (most accurate when present)
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            # Stream-level fallback
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
        ]
        for cmd in strategies:
            try:
                res = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=10
                )
                val = (res.stdout or "").strip()
                if val and val not in ("N/A", "0.000000"):
                    try:
                        d = float(val)
                        if d > 0:
                            return d
                    except ValueError:
                        pass
            except Exception:
                continue
        return 0.0

    duration = _probe_duration(str(vp))
    if duration <= 0:
        return _err(
            f"could not probe duration of {vp} — file may be corrupt / "
            f"too short. screenrecord needs ~3s minimum to produce "
            f"valid mp4."
        )

    # N evenly-spaced timestamps (avoid exact 0 and end to avoid empty/black frames)
    if n == 1:
        timestamps = [duration / 2]
    else:
        margin = duration * 0.05  # skip first/last 5%
        usable = duration - 2 * margin
        timestamps = [margin + usable * i / (n - 1) for i in range(n)]

    extracted: List[Dict[str, Any]] = []
    errors: List[str] = []

    # Single-pass extraction: `-vf fps=N/duration` emits N evenly-spaced
    # frames across the full video in one ffmpeg invocation. Far more
    # robust than per-frame seeking on Android screenrecord outputs which
    # often have broken keyframe indexes past the first few seconds.
    out_pattern = str(base / "frame_%02d.png")
    fps_filter = f"fps={n}/{duration:.3f}"
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-i", str(vp),
        "-vf", fps_filter,
        "-frames:v", str(n),
        "-q:v", "2",
        out_pattern,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            errors.append(f"ffmpeg exit {res.returncode}: {(res.stderr or '')[:200]}")
    except subprocess.TimeoutExpired:
        errors.append("ffmpeg timeout after 60s")
    except Exception as e:
        errors.append(f"ffmpeg exception: {e}")

    # Collect what actually got produced (may be fewer than n if video is short)
    # ffmpeg numbers from 01, not 00
    for i in range(1, n + 1):
        frame_path = base / f"frame_{i:02d}.png"
        if frame_path.exists() and frame_path.stat().st_size > 0:
            # Approximate timestamp: i-th frame of n spread over duration
            ts_approx = (i - 0.5) * (duration / n)
            extracted.append({
                "index": i - 1,  # 0-based external
                "timestamp_sec": ts_approx,
                "path": str(frame_path),
                "size_bytes": frame_path.stat().st_size,
            })

    summary = (
        f"🎬 extracted {len(extracted)}/{n} frame(s) from {vp.name} "
        f"(duration={duration:.1f}s) → {base}"
    )
    if errors:
        summary += f" ({len(errors)} error(s))"
    content: List[Dict[str, Any]] = [{"text": summary}]

    if include_image:
        for f in extracted:
            png = Path(f["path"]).read_bytes()
            # PNG format for Converse API
            content.append({"text": f"Frame {f['index']} @ {f['timestamp_sec']:.1f}s:"})
            content.append(
                {"image": {"format": "png", "source": {"bytes": png}}}
            )

    return {
        "status": "success",
        "content": content,
        "frames": extracted,
        "count": len(extracted),
        "output_dir": str(base),
        "video_duration_sec": duration,
        "errors": errors,
    }




# =============================================================================
# 🎯  Touch / Gesture Streaming  (Frontier #2)
# =============================================================================
#
# Non-rooted reality: /dev/input/event* is owned by root:input with 0660
# perms and SELinux u:r:shell:s0 context denies writes. sendevent is not
# available unless the device is rooted.
#
# The workable equivalent is the `input` binary's full command set:
#
#   input tap <x> <y>                              — simple tap
#   input swipe <x1> <y1> <x2> <y2> [duration_ms]  — linear swipe
#   input draganddrop <x1> <y1> <x2> <y2> [dur_ms] — long-press + drag
#   input motionevent DOWN|MOVE|UP <x> <y>         — ⭐ raw gesture stream
#   input keyevent <code>
#   input text <string>
#
# motionevent is the non-root equivalent of sendevent for gesture streaming:
# we can script arbitrary paths by emitting DOWN, N×MOVE, UP.
#
# This module ships:
#
#   gesture_stream(points, durations)
#       Execute an arbitrary 2-D path. `points` is a list of [x,y] pairs;
#       the first is the DOWN, middle are MOVE, last is UP. Consecutive
#       sleeps between events are controlled by `durations` (seconds).
#
#   gesture_long_press(x, y, hold_ms)
#       DOWN → hold → UP. Useful for context menus.
#
#   gesture_path(path_spec)
#       High-level DSL: "line", "arc", "zigzag" (composes into motionevent).
#
# Multi-finger (pinch/rotate) is NOT available without root on stock
# Android — the `input` binary only supports a single pointer. This is
# documented as a limitation rather than faked.


def _input_cmd(args: List[str], serial: Optional[str]) -> Dict[str, Any]:
    """Helper: run `adb shell input <args>` and surface errors."""
    return _run(["shell", "input"] + args, serial=serial, timeout=30)


def _handle_gesture_stream(
    points: List[List[float]],
    step_delay_ms: int,
    serial: Optional[str],
) -> Dict[str, Any]:
    """Execute a 2-D gesture path via motionevent DOWN/MOVE…/UP.

    Args:
        points:        [[x,y], [x,y], ...] — at least 2 points required.
                       First = DOWN, last = UP, intermediate = MOVE.
        step_delay_ms: Sleep between consecutive events. Smaller = faster
                       fling, larger = smoother drag. 0..1000 ms.

    Returns number of steps executed.
    """
    if len(points) < 2:
        return _err("gesture_stream needs at least 2 points")
    if step_delay_ms < 0 or step_delay_ms > 2000:
        return _err("step_delay_ms must be 0..2000")

    for i, p in enumerate(points):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            return _err(f"point {i} must be [x, y], got {p!r}")
        if not all(isinstance(c, (int, float)) for c in p):
            return _err(f"point {i} coordinates must be numeric, got {p!r}")

    t_start = time.time()
    steps_done = 0
    failures: List[str] = []

    for i, (x, y) in enumerate(points):
        ix, iy = int(x), int(y)
        if i == 0:
            action = "DOWN"
        elif i == len(points) - 1:
            action = "UP"
        else:
            action = "MOVE"

        r = _input_cmd(["motionevent", action, str(ix), str(iy)], serial)
        if r["returncode"] != 0:
            failures.append(f"step {i} ({action}): {r['stderr'][:100]}")
            # Attempt cleanup UP to avoid leaving pointer stuck
            if i > 0 and i < len(points) - 1:
                _input_cmd(["motionevent", "UP", str(ix), str(iy)], serial)
            break
        steps_done += 1

        if i < len(points) - 1 and step_delay_ms > 0:
            time.sleep(step_delay_ms / 1000)

    elapsed = time.time() - t_start
    if failures:
        return _err(
            f"gesture partial: {steps_done}/{len(points)} steps in "
            f"{elapsed:.2f}s — {failures[0]}"
        )
    return _ok(
        f"🎯 gesture: {steps_done} steps ({len(points)-2} moves) in "
        f"{elapsed:.2f}s",
        steps=steps_done,
        elapsed_sec=elapsed,
        points_count=len(points),
    )


def _handle_gesture_long_press(
    x: int,
    y: int,
    hold_ms: int,
    serial: Optional[str],
) -> Dict[str, Any]:
    """DOWN → hold → UP at a single point. Used for context menus."""
    if hold_ms < 100 or hold_ms > 10000:
        return _err("hold_ms must be 100..10000")

    t_start = time.time()
    r1 = _input_cmd(["motionevent", "DOWN", str(x), str(y)], serial)
    if r1["returncode"] != 0:
        return _err(f"DOWN failed: {r1['stderr'][:100]}")
    time.sleep(hold_ms / 1000)
    r2 = _input_cmd(["motionevent", "UP", str(x), str(y)], serial)
    if r2["returncode"] != 0:
        return _err(f"UP failed: {r2['stderr'][:100]}")
    elapsed = time.time() - t_start
    return _ok(
        f"🎯 long-press ({x}, {y}) for {hold_ms}ms (actual: {elapsed*1000:.0f}ms)",
        x=x, y=y, hold_ms=hold_ms, actual_elapsed_ms=int(elapsed*1000),
    )


def _handle_gesture_path(
    shape: str,
    x: int,
    y: int,
    size: int,
    steps: int,
    step_delay_ms: int,
    serial: Optional[str],
) -> Dict[str, Any]:
    """High-level gesture DSL — generates points and delegates to stream.

    Shapes:
        line_h   — horizontal line starting at (x, y), length=size
        line_v   — vertical line starting at (x, y), length=size
        circle   — full circle centered at (x, y), radius=size
        arc      — half-circle at (x, y), radius=size
        zigzag   — 3 bumps at (x, y), amplitude=size/3
        square   — closed square starting at (x, y), side=size
    """
    import math
    if steps < 3 or steps > 200:
        return _err("steps must be 3..200")

    pts: List[List[float]] = []
    if shape == "line_h":
        for i in range(steps):
            t = i / (steps - 1)
            pts.append([x + size * t, y])
    elif shape == "line_v":
        for i in range(steps):
            t = i / (steps - 1)
            pts.append([x, y + size * t])
    elif shape == "circle":
        for i in range(steps):
            theta = 2 * math.pi * i / (steps - 1)
            pts.append([x + size * math.cos(theta), y + size * math.sin(theta)])
    elif shape == "arc":
        for i in range(steps):
            theta = math.pi * i / (steps - 1)
            pts.append([x + size * math.cos(theta), y - size * math.sin(theta)])
    elif shape == "zigzag":
        amp = size / 3
        for i in range(steps):
            t = i / (steps - 1)
            bump_y = amp * math.sin(2 * math.pi * 3 * t)
            pts.append([x + size * t, y + bump_y])
    elif shape == "square":
        # 4 equal-length segments, total steps distributed
        per_side = max(2, steps // 4)
        for seg in range(4):
            dx, dy = [(1, 0), (0, 1), (-1, 0), (0, -1)][seg]
            sx = x + (dx if seg > 0 else 0) * size
            sy = y + (dy if seg > 0 else 0) * size
            # actually compute from previous endpoint
        # Simpler: corner walk
        corners = [
            [x, y], [x + size, y], [x + size, y + size],
            [x, y + size], [x, y],
        ]
        for i in range(4):
            start = corners[i]
            end = corners[i + 1]
            for j in range(per_side):
                t = j / (per_side - 1) if per_side > 1 else 0
                pts.append([
                    start[0] + (end[0] - start[0]) * t,
                    start[1] + (end[1] - start[1]) * t,
                ])
    else:
        return _err(f"unknown shape: {shape}. "
                    f"valid: line_h, line_v, circle, arc, zigzag, square")

    return _handle_gesture_stream(pts, step_delay_ms, serial)


def _handle_gesture_pinch(
    cx: int,
    cy: int,
    size: int,
    direction: str,
    serial: Optional[str],
) -> Dict[str, Any]:
    """Pinch simulation — since multi-touch requires root/sendevent, we
    use the Accessibility Service's dispatchGesture via `cmd` as a best
    effort. Falls back to a clear error explaining the limitation.

    Current behavior: reports that true two-finger pinch requires root.
    Provided for API completeness so consumers get a clear explanation
    rather than a cryptic failure.
    """
    return _err(
        "true two-finger pinch requires root (sendevent to /dev/input/event*) "
        "or a companion accessibility service app. Use pinch_via_magnification "
        "action once implemented, or install Shizuku for non-root injection. "
        "For single-finger alternatives: use gesture_path with shape='circle' "
        "to simulate rotate-like motions, or use system zoom via "
        "'settings put secure accessibility_display_magnification_enabled 1'."
    )




# =============================================================================
# ♿  Accessibility / ATC  (Frontier #8)
# =============================================================================
#
# Accessibility is a first-class Android subsystem that agents can control:
# TalkBack, captions, magnification, font scale, high contrast, select-to-
# speak, switch access, and system actions (back/home/notifications/…) all
# sit behind the Accessibility Manager.
#
# APIs used (no root needed):
#
#   settings put secure enabled_accessibility_services <comp1:comp2>
#   settings put secure accessibility_enabled 0|1
#   settings put secure accessibility_captioning_enabled 0|1
#   settings put secure accessibility_display_magnification_enabled 0|1
#   settings put system font_scale <float>
#   cmd package query-services -a android.accessibilityservice.AccessibilityService
#   cmd accessibility call-system-action <int>
#   dumpsys accessibility
#
# Known accessibility service packages (Pixel defaults):
#   com.google.android.marvin.talkback/.TalkBackService         — screen reader
#   com.google.android.accessibility.switchaccess/…             — switch access
#   com.google.android.apps.accessibility.voiceaccess/.JustSpeakService — voice
#   com.google.android.accessibility.soundamplifier/…           — amp
#   com.android.systemui.accessibility.accessibilitymenu/…      — a11y menu
#   com.google.audio.hearing.visualization.accessibility.scribe/… — live caption
#
# Design: short names → full component paths so callers can say
#   accessibility_toggle_service(service="talkback", enable=True)
# and we look up the right ComponentName.

# Short-name → component shortcuts for the common Pixel accessibility apps.
# The actual full component name is looked up at runtime because some OEM
# ROMs use slightly different class names.
A11Y_SERVICE_ALIASES = {
    "talkback":      "com.google.android.marvin.talkback",
    "switchaccess":  "com.google.android.accessibility.switchaccess",
    "voiceaccess":   "com.google.android.apps.accessibility.voiceaccess",
    "soundamp":      "com.google.android.accessibility.soundamplifier",
    "a11ymenu":      "com.android.systemui.accessibility.accessibilitymenu",
    "selecttospeak": "com.google.android.marvin.talkback",  # bundled in TB pkg
    "livecaption":   "com.google.audio.hearing.visualization.accessibility.scribe",
    "scribe":        "com.google.audio.hearing.visualization.accessibility.scribe",
}

# Global system actions — these IDs are defined in
# android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_*
# (public API, stable across Android versions since API 16-31).
A11Y_SYSTEM_ACTIONS = {
    "back":               1,
    "home":               2,
    "notifications":      3,
    "recents":            4,
    "quick_settings":     5,
    "power_dialog":       6,
    "toggle_split_screen": 7,
    "lock_screen":        8,
    "screenshot":         9,
    "accessibility_button": 11,
    "accessibility_button_chooser": 12,
    "accessibility_shortcut": 13,
    "dpad_up":            16,
    "dpad_down":          17,
    "dpad_left":          18,
    "dpad_right":         19,
    "dpad_center":        20,
    "menu":               21,
    "media_play_pause":   22,
}


def _a11y_list_installed_services(serial: Optional[str]) -> List[Dict[str, str]]:
    """Scan installed accessibility services via `cmd package query-services`.

    The output groups info as `Service #N:` blocks, each containing a
    `ServiceInfo:` section followed by an `ApplicationInfo:` section.
    Both sections have `name=` and `packageName=` fields, so we must:
      1. anchor on `Service #` as block boundary
      2. only capture the FIRST name= / packageName= after each block header
         (which live in the ServiceInfo section)

    Returns list of {package, component, name} dicts.
    """
    r = _run(
        ["shell", "cmd", "package", "query-services", "-a",
         "android.accessibilityservice.AccessibilityService"],
        serial=serial, timeout=15,
    )
    if r["returncode"] != 0:
        return []

    services: List[Dict[str, str]] = []
    cur_pkg: Optional[str] = None
    cur_name: Optional[str] = None
    in_service_info = False

    for raw in (r["stdout"] or "").splitlines():
        line = raw.strip()

        # Start of a new service block — flush previous, reset state
        if line.startswith("Service #"):
            if cur_pkg and cur_name:
                services.append({
                    "package": cur_pkg,
                    "name": cur_name,
                    "component": f"{cur_pkg}/{cur_name}",
                })
            cur_pkg = cur_name = None
            in_service_info = False
            continue

        if line == "ServiceInfo:":
            in_service_info = True
            continue

        # Once we hit ApplicationInfo, stop capturing (its name/pkg overrides)
        if line == "ApplicationInfo:":
            in_service_info = False
            continue

        if in_service_info:
            if cur_name is None and line.startswith("name="):
                cur_name = line.split("=", 1)[1].strip()
            elif cur_pkg is None and line.startswith("packageName="):
                cur_pkg = line.split("=", 1)[1].strip()

    # Flush final block
    if cur_pkg and cur_name:
        services.append({
            "package": cur_pkg,
            "name": cur_name,
            "component": f"{cur_pkg}/{cur_name}",
        })

    return services


def _a11y_resolve_component(
    service: str, serial: Optional[str]
) -> Optional[str]:
    """Resolve a short name or package to full `pkg/cls` ComponentName.

    Accepts:
      - full component "pkg/cls" → returned verbatim
      - package name "com.foo.bar" → looked up in installed services
      - short alias "talkback" → resolved via A11Y_SERVICE_ALIASES then lookup
    """
    if "/" in service:
        return service

    pkg = A11Y_SERVICE_ALIASES.get(service.lower(), service)
    installed = _a11y_list_installed_services(serial)
    # Prefer exact package match
    for svc in installed:
        if svc["package"] == pkg:
            return svc["component"]
    # Fallback: substring match
    for svc in installed:
        if service.lower() in svc["package"].lower():
            return svc["component"]
    return None


def _a11y_get_enabled(serial: Optional[str]) -> List[str]:
    """Current enabled_accessibility_services as list of components."""
    r = _run(
        ["shell", "settings", "get", "secure", "enabled_accessibility_services"],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return []
    raw = (r["stdout"] or "").strip()
    if raw in ("", "null"):
        return []
    return [c for c in raw.split(":") if c]


def _a11y_set_enabled(
    components: List[str], serial: Optional[str]
) -> Dict[str, Any]:
    """Write the full enabled_accessibility_services list atomically."""
    value = ":".join(components) if components else '""'
    r = _run(
        ["shell", "settings", "put", "secure",
         "enabled_accessibility_services", value],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return {"ok": False, "error": r["stderr"] or "put failed"}

    # Also toggle master accessibility_enabled flag to match
    master = "1" if components else "0"
    _run(
        ["shell", "settings", "put", "secure", "accessibility_enabled", master],
        serial=serial, timeout=5,
    )
    return {"ok": True}


def _handle_accessibility_list(serial: Optional[str]) -> Dict[str, Any]:
    """List all installed accessibility services + enabled state."""
    installed = _a11y_list_installed_services(serial)
    enabled = set(_a11y_get_enabled(serial))

    lines = [f"♿ {len(installed)} installed, {len(enabled)} enabled:"]
    for svc in installed:
        mark = "✅" if svc["component"] in enabled else "  "
        lines.append(f"  {mark} {svc['component']}")

    if enabled:
        lines.append("")
        lines.append("Enabled components not in installed list:")
        unknown = enabled - {s["component"] for s in installed}
        for u in unknown:
            lines.append(f"     ⚠ {u}")

    return _ok(
        "\n".join(lines),
        installed=installed,
        enabled=sorted(enabled),
        installed_count=len(installed),
        enabled_count=len(enabled),
    )


def _handle_accessibility_toggle_service(
    service: str, enable: bool, serial: Optional[str]
) -> Dict[str, Any]:
    """Enable/disable a single accessibility service by name or component."""
    if not service:
        return _err("service name required (e.g. 'talkback' or 'pkg/cls')")

    component = _a11y_resolve_component(service, serial)
    if component is None:
        aliases = ", ".join(sorted(A11Y_SERVICE_ALIASES.keys()))
        return _err(
            f"could not resolve accessibility service '{service}'. "
            f"Known aliases: {aliases}. "
            f"Or pass a full 'package/class' component name. "
            f"Use action='accessibility_list' to see installed services."
        )

    enabled = _a11y_get_enabled(serial)
    changed = False
    if enable:
        if component not in enabled:
            enabled.append(component)
            changed = True
    else:
        if component in enabled:
            enabled.remove(component)
            changed = True

    if not changed:
        state = "already enabled" if enable else "already disabled"
        return _ok(f"♿ {component} {state}", component=component, changed=False)

    result = _a11y_set_enabled(enabled, serial)
    if not result["ok"]:
        return _err(f"failed to update enabled services: {result.get('error')}")

    verb = "enabled" if enable else "disabled"
    return _ok(
        f"♿ {verb} {component}",
        component=component,
        changed=True,
        enabled_services=enabled,
    )


def _handle_accessibility_system_action(
    action: str, serial: Optional[str]
) -> Dict[str, Any]:
    """Call a global system accessibility action by name or numeric id."""
    if not action:
        names = ", ".join(sorted(A11Y_SYSTEM_ACTIONS.keys()))
        return _err(f"system_action name required. Options: {names}")

    # Support either name or numeric id
    if action.isdigit():
        action_id = int(action)
        action_name = f"id_{action_id}"
    else:
        key = action.lower()
        if key not in A11Y_SYSTEM_ACTIONS:
            names = ", ".join(sorted(A11Y_SYSTEM_ACTIONS.keys()))
            return _err(f"unknown system action '{action}'. Valid: {names}")
        action_id = A11Y_SYSTEM_ACTIONS[key]
        action_name = key

    r = _run(
        ["shell", "cmd", "accessibility", "call-system-action", str(action_id)],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"call-system-action failed: {r['stderr'][:100]}")

    return _ok(
        f"♿ system action '{action_name}' (id={action_id}) invoked",
        action=action_name,
        action_id=action_id,
    )


def _handle_accessibility_captions(
    enable: bool, serial: Optional[str]
) -> Dict[str, Any]:
    """Toggle system captioning (shows captions in media apps that opt in)."""
    value = "1" if enable else "0"
    r = _run(
        ["shell", "settings", "put", "secure",
         "accessibility_captioning_enabled", value],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"failed: {r['stderr'][:100]}")
    verb = "enabled" if enable else "disabled"
    return _ok(f"♿ system captions {verb}", enabled=enable)


def _handle_accessibility_magnification(
    enable: bool, serial: Optional[str]
) -> Dict[str, Any]:
    """Toggle display magnification."""
    value = "1" if enable else "0"
    r = _run(
        ["shell", "settings", "put", "secure",
         "accessibility_display_magnification_enabled", value],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"failed: {r['stderr'][:100]}")
    verb = "enabled" if enable else "disabled"
    return _ok(f"♿ display magnification {verb}", enabled=enable)


def _handle_accessibility_font_scale(
    scale: float, serial: Optional[str]
) -> Dict[str, Any]:
    """Set system font scale. 1.0=normal; 0.85=small; 1.15=large; 1.3=larger;
    1.5=largest; 2.0=massive. Non-integer multiples allowed."""
    if scale < 0.5 or scale > 3.0:
        return _err("font_scale must be 0.5..3.0")
    r = _run(
        ["shell", "settings", "put", "system", "font_scale", str(scale)],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"failed: {r['stderr'][:100]}")
    return _ok(f"♿ font_scale → {scale}", font_scale=scale)


def _handle_accessibility_status(serial: Optional[str]) -> Dict[str, Any]:
    """Snapshot of the accessibility subsystem for agent overview."""
    def _get(ns, key):
        r = _run(["shell", "settings", "get", ns, key],
                 serial=serial, timeout=5)
        return (r["stdout"] or "").strip() if r["returncode"] == 0 else None

    status = {
        "accessibility_enabled":     _get("secure", "accessibility_enabled"),
        "enabled_services":          _get("secure", "enabled_accessibility_services"),
        "captioning_enabled":        _get("secure", "accessibility_captioning_enabled"),
        "magnification_enabled":     _get("secure", "accessibility_display_magnification_enabled"),
        "font_scale":                _get("system", "font_scale"),
        "touch_exploration":         _get("secure", "touch_exploration_enabled"),
        "high_text_contrast":        _get("secure", "high_text_contrast_enabled"),
        "color_inversion":           _get("secure", "accessibility_display_inversion_enabled"),
    }
    lines = ["♿ Accessibility status:"]
    for k, v in status.items():
        display = "—" if v in (None, "", "null") else v
        lines.append(f"  {k:28s}: {display}")
    return _ok("\n".join(lines), **{k: v for k, v in status.items()})



# =============================================================================
# 📐  Sensor Feeds  (Frontier #12)
# =============================================================================
#
# Agents read phone sensors — accelerometer, gyro, magnetometer, proximity,
# ambient light, barometer, gravity, rotation — from `dumpsys sensorservice`.
# No root, no companion app, no JNI. Just parse the text dump.
#
# Output structure of `dumpsys sensorservice`:
#
#   Sensor Device:
#   Total N h/w sensors, N running 0 disabled clients:
#   0xHANDLE) active-count = M; sampling_period(ms) = {...}, ...
#
#   Sensor List:
#   0xHANDLE) NAME                    | VENDOR | ver: V |
#            type: android.sensor.X(TYPE_ID) | perm: ... | flags: 0xNNNN
#            continuous | minRate=... | maxRate=... | ...
#
#   Recent Sensor events:
#   SENSOR NAME: last N events
#       i (ts=SECONDS.NS, wall=HH:MM:SS.mmm) v1, v2, v3, ...,
#
# The sensor TYPE_ID after android.sensor.X is the canonical Android
# Sensor.TYPE_* constant (Sensor.TYPE_ACCELEROMETER=1, TYPE_GYROSCOPE=4
# etc). We use that for aliasing.

# Canonical Android Sensor.TYPE_* constants (from Android source)
# https://developer.android.com/reference/android/hardware/Sensor#TYPE_ACCELEROMETER
SENSOR_TYPE_ALIASES: Dict[str, int] = {
    # Most-used motion sensors
    "accelerometer":           1,
    "accel":                   1,
    "magnetic_field":          2,
    "magnetometer":            2,
    "mag":                     2,
    "orientation":             3,
    "gyroscope":               4,
    "gyro":                    4,
    "light":                   5,
    "lux":                     5,
    "ambient_light":           5,
    "pressure":                6,
    "barometer":               6,
    "proximity":               8,
    "prox":                    8,
    "gravity":                 9,
    "linear_acceleration":    10,
    "linear_accel":           10,
    "rotation_vector":        11,
    "rotation":               11,
    "relative_humidity":      12,
    "humidity":               12,
    "ambient_temperature":    13,
    "temperature":            13,
    "magnetic_field_uncalibrated": 14,
    "game_rotation_vector":   15,
    "gyroscope_uncalibrated": 16,
    "gyro_uncal":             16,
    "significant_motion":     17,
    "step_detector":          18,
    "step_counter":           19,
    "geomagnetic_rotation_vector": 20,
    "heart_rate":             21,
    "accelerometer_uncalibrated": 35,
    "accel_uncal":            35,
    "hinge_angle":            36,
    # Values per sample for output formatting (for nice "x, y, z" display)
}

# Sensors whose values are vectors of known dimension (for labels)
SENSOR_VALUE_LABELS: Dict[int, List[str]] = {
    1:  ["x", "y", "z"],                        # accelerometer m/s²
    2:  ["x", "y", "z"],                        # magnetometer μT
    3:  ["azimuth", "pitch", "roll"],           # orientation deg
    4:  ["x", "y", "z"],                        # gyro rad/s
    5:  ["lux"],                                # light
    6:  ["hPa"],                                # barometer
    8:  ["distance"],                           # proximity cm
    9:  ["x", "y", "z"],                        # gravity m/s²
    10: ["x", "y", "z"],                        # linear accel m/s²
    11: ["x", "y", "z", "w"],                   # rotation vector
    12: ["percent"],                            # humidity
    13: ["celsius"],                            # temperature
    14: ["x", "y", "z", "bias_x", "bias_y", "bias_z"],
    15: ["x", "y", "z", "w"],
    16: ["x", "y", "z", "bias_x", "bias_y", "bias_z"],
    19: ["steps"],
    21: ["bpm"],
    35: ["x", "y", "z", "bias_x", "bias_y", "bias_z"],
    36: ["angle"],
}


def _resolve_sensor_type(query: Any) -> Optional[int]:
    """Resolve a sensor name/alias/type_id to an Android Sensor.TYPE_*.

    Accepts: 'accelerometer', 'accel', 1, '1', 'gyro', ...
    """
    if isinstance(query, int):
        return query if query > 0 else None
    if not isinstance(query, str):
        return None
    low = query.lower().strip().replace("-", "_").replace(" ", "_")
    # Direct alias
    if low in SENSOR_TYPE_ALIASES:
        return SENSOR_TYPE_ALIASES[low]
    # Digit string
    if low.isdigit():
        n = int(low)
        return n if n > 0 else None
    return None


def _parse_sensor_list(out: str) -> List[Dict[str, Any]]:
    """Parse the 'Sensor List:' section of `dumpsys sensorservice`.

    Each sensor is 2+ lines:
      0xHANDLE) NAME | VENDOR | ver: V | type: android.sensor.X(TYPE_ID) | ...
         continuous | minRate=Nhz | maxRate=Mhz | ... | (non-)wakeUp |
    """
    import re as _re
    sensors: List[Dict[str, Any]] = []
    lines = (out or "").splitlines()

    # Find "Sensor List:" header
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "Sensor List:":
            start = i + 1
            break
    if start is None:
        return sensors

    # Sensors continue until "Fusion States:", "Recent Sensor events:",
    # or another top-level section header
    end_markers = (
        "Fusion States:", "Recent Sensor events:", "Active connections",
        "Socket Buffer size", "Previous Registrations:",
        "WakeLock Statistics:", "Mode:",
    )

    # Pattern for header line:
    # 0x01010001) ICM45631 Accelerometer    | Invensense | ver: 1 | type: android.sensor.accelerometer(1) | perm: n/a | flags: 0xXXXX
    hdr_re = _re.compile(
        r"^\s*(0x[0-9a-fA-F]+)\)\s+"
        r"(.+?)\s*\|\s*"
        r"(.+?)\s*\|\s*"
        r"ver:\s*(\d+)\s*\|\s*"
        r"type:\s*android\.sensor\.([a-z_]+)\((\d+)\)\s*\|\s*"
        r"perm:\s*([^|]*?)\s*\|\s*"
        r"flags:\s*(0x[0-9a-fA-F]+)"
    )
    # Pattern for detail line. Both of these shapes exist:
    #   "continuous | minRate=1.50Hz | maxRate=400.00Hz | ..."   (has both)
    #   "on-change | minRate=1.00Hz | minDelay=0us | ..."        (minRate only)
    # So parse each field independently.
    min_rate_re = _re.compile(r"minRate=([0-9.]+)Hz")
    max_rate_re = _re.compile(r"maxRate=([0-9.]+)Hz")

    current: Optional[Dict[str, Any]] = None
    for raw in lines[start:]:
        stripped = raw.strip()
        if not stripped:
            continue
        if any(stripped.startswith(m) for m in end_markers):
            break

        m = hdr_re.match(raw)
        if m:
            if current:
                sensors.append(current)
            current = {
                "handle": m.group(1),
                "name": m.group(2).strip(),
                "vendor": m.group(3).strip(),
                "version": int(m.group(4)),
                "type_name": m.group(5),
                "type_id": int(m.group(6)),
                "permission": m.group(7).strip(),
                "flags": m.group(8),
                "min_rate_hz": None,
                "max_rate_hz": None,
                "reporting_mode": None,
                "wake_up": None,
            }
            continue

        if current is None:
            continue

        # Detail line: parse rates (each independently), reporting mode, wake-up
        mn = min_rate_re.search(raw)
        if mn:
            try: current["min_rate_hz"] = float(mn.group(1))
            except ValueError: pass
        mx = max_rate_re.search(raw)
        if mx:
            try: current["max_rate_hz"] = float(mx.group(1))
            except ValueError: pass

        # Reporting mode: first token before |
        toks = [t.strip() for t in stripped.split("|")]
        if toks and toks[0] in ("continuous", "on-change",
                                 "one-shot", "special-trigger"):
            current["reporting_mode"] = toks[0]
        for t in toks:
            if t == "wakeUp":
                current["wake_up"] = True
            elif t == "non-wakeUp":
                current["wake_up"] = False

    if current:
        sensors.append(current)
    return sensors


def _parse_recent_events(out: str) -> Dict[str, Dict[str, Any]]:
    """Parse the 'Recent Sensor events:' section.

    Returns dict mapping sensor_name → {events: [...], latest: {...}}.
    Each event has: {ts, wall, values}.

    Section format:
      Recent Sensor events:
      ICM45631 Accelerometer: last 10 events
           1 (ts=574.997002195, wall=23:34:07.521) 0.12, 0.26, 9.74, 0.00, ...
           2 (ts=575.007027742, wall=23:34:07.527) 0.12, 0.27, 9.73, 0.00, ...
      Device Orientation: last 2 events
           1 (ts=10.096779952, wall=23:24:42.639) 0.00,
    """
    import re as _re
    lines = (out or "").splitlines()

    start = None
    for i, line in enumerate(lines):
        if line.strip() == "Recent Sensor events:":
            start = i + 1
            break
    if start is None:
        return {}

    # End at next top-level section
    end_markers = (
        "Active connections", "Socket Buffer size",
        "Previous Registrations:", "WakeLock Statistics:",
        "Mode:", "Fusion States:",
    )

    # "SENSOR NAME: last N events"
    header_re = _re.compile(r"^([^:]+?):\s*last\s+(\d+)\s+events\s*$")
    # "   1 (ts=X.Y, wall=H:M:S.MS) v1, v2, v3, "
    event_re = _re.compile(
        r"^\s*(\d+)\s+\(ts=([\d.]+),\s*wall=([\d:.]+)\)\s*(.*?)\s*,?\s*$"
    )

    events: Dict[str, Dict[str, Any]] = {}
    current_sensor: Optional[str] = None

    for raw in lines[start:]:
        stripped = raw.strip()
        if not stripped:
            continue
        if any(stripped.startswith(m) for m in end_markers):
            break

        hm = header_re.match(stripped)
        if hm:
            current_sensor = hm.group(1).strip()
            events[current_sensor] = {
                "sensor_name": current_sensor,
                "max_events": int(hm.group(2)),
                "events": [],
                "latest": None,
            }
            continue

        if current_sensor is None:
            continue

        em = event_re.match(raw)
        if em:
            try:
                ts = float(em.group(2))
            except ValueError:
                continue
            wall = em.group(3)
            raw_vals = em.group(4).strip().rstrip(",")
            # values are comma-separated floats; parse them
            values: List[float] = []
            for v in raw_vals.split(","):
                v = v.strip()
                if not v:
                    continue
                try:
                    values.append(float(v))
                except ValueError:
                    pass
            ev = {"ts": ts, "wall": wall, "values": values}
            events[current_sensor]["events"].append(ev)
            events[current_sensor]["latest"] = ev

    return events


def _label_values(type_id: int, values: List[float]) -> Dict[str, float]:
    """Attach semantic labels to value vector (e.g. accel → {x, y, z})."""
    labels = SENSOR_VALUE_LABELS.get(type_id, [])
    result: Dict[str, float] = {}
    for i, v in enumerate(values):
        key = labels[i] if i < len(labels) else f"v{i}"
        result[key] = v
    return result


# --- handlers ----------------------------------------------------------

def _handle_sensors_list(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(
        ["shell", "dumpsys", "sensorservice"],
        serial=serial, timeout=15,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys sensorservice failed: {r['stderr'][:120]}")

    sensors = _parse_sensor_list(r["stdout"] or "")

    # Group by canonical type_name for summary
    motion = [s for s in sensors if s["type_id"] in (1, 2, 4, 9, 10, 11)]
    env = [s for s in sensors if s["type_id"] in (5, 6, 8, 12, 13)]
    composite = [s for s in sensors if s["type_id"] in (3, 15, 20)]

    lines = [f"📐 {len(sensors)} sensors available:"]
    lines.append(f"   • motion: {len(motion)} (accel/gyro/mag/gravity/...)")
    lines.append(f"   • environment: {len(env)} (light/prox/pressure/...)")
    lines.append(f"   • composite: {len(composite)} (orientation/rotation/...)")

    # Highlight common sensors
    for query in ("accelerometer", "gyroscope", "light", "proximity"):
        type_id = SENSOR_TYPE_ALIASES.get(query)
        found = [s for s in sensors if s["type_id"] == type_id]
        if found:
            s = found[0]
            lines.append(
                f"   • {query:15s} → {s['name']!r} ({s['vendor']}, "
                f"{s['min_rate_hz']}–{s['max_rate_hz']} Hz)"
            )

    return _ok(
        "\n".join(lines),
        sensors=sensors,
        count=len(sensors),
    )


def _handle_sensors_recent(serial: Optional[str]) -> Dict[str, Any]:
    """Snapshot of recent events across all active sensors."""
    r = _run(
        ["shell", "dumpsys", "sensorservice"],
        serial=serial, timeout=15,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys sensorservice failed: {r['stderr'][:120]}")

    events_by_sensor = _parse_recent_events(r["stdout"] or "")
    # For nice labels, we also need the sensor list
    sensors = _parse_sensor_list(r["stdout"] or "")
    # Build name → type_id map (use HW sensor names as-is)
    name_to_type: Dict[str, int] = {s["name"]: s["type_id"] for s in sensors}

    # Attach labels
    for sensor_name, bucket in events_by_sensor.items():
        # Find matching type_id (fuzzy — sensorservice uses short names like
        # "ICM45631 Accelerometer" but Sensor List has the same name)
        type_id = name_to_type.get(sensor_name)
        bucket["type_id"] = type_id
        if bucket["latest"] and type_id is not None:
            bucket["latest"]["labeled"] = _label_values(
                type_id, bucket["latest"]["values"]
            )

    lines = [f"📐 {len(events_by_sensor)} sensors with recent events:"]
    # Show latest value of key sensors
    for sensor_name, bucket in list(events_by_sensor.items())[:12]:
        latest = bucket.get("latest")
        if not latest:
            continue
        vals = latest["values"][:4]
        vals_str = ", ".join(f"{v:.3f}" for v in vals)
        if len(latest["values"]) > 4:
            vals_str += ", ..."
        lines.append(
            f"   [{latest['wall']}] {sensor_name[:30]:30s} → {vals_str}"
        )

    return _ok(
        "\n".join(lines),
        events=events_by_sensor,
        sensor_count=len(events_by_sensor),
    )


def _handle_sensor_get(
    sensor_query: Any, serial: Optional[str]
) -> Dict[str, Any]:
    """Get the latest value for a specific sensor by name/alias/type_id."""
    type_id = _resolve_sensor_type(sensor_query)
    if type_id is None:
        return _err(
            f"unknown sensor {sensor_query!r}. Try one of: "
            f"{sorted(SENSOR_TYPE_ALIASES.keys())[:15]}..."
        )

    r = _run(
        ["shell", "dumpsys", "sensorservice"],
        serial=serial, timeout=15,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys sensorservice failed: {r['stderr'][:120]}")

    sensors = _parse_sensor_list(r["stdout"] or "")
    events_by = _parse_recent_events(r["stdout"] or "")

    # Find the sensor with matching type_id
    matches = [s for s in sensors if s["type_id"] == type_id]
    if not matches:
        return _err(
            f"sensor type_id={type_id} not available on this device"
        )

    # Prefer non-uncalibrated variant and non-wakeUp (primary)
    matches.sort(key=lambda s: (
        "uncalibrated" in s["name"].lower(),  # uncal last
        bool(s.get("wake_up")),               # wake-up last
    ))
    sensor = matches[0]
    name = sensor["name"]

    # Find events for this sensor
    bucket = events_by.get(name)
    if not bucket or not bucket["events"]:
        # Fallback: search by type_id in all events
        for ename, ebucket in events_by.items():
            # Match by canonical short name ("accelerometer" in "ICM… Accel…")
            if sensor["type_name"].lower().replace("_", " ") in ename.lower():
                bucket = ebucket
                break

    if not bucket or not bucket["events"]:
        return _ok(
            f"📐 {name}: no recent events (sensor may be inactive)",
            sensor=sensor,
            type_id=type_id,
            latest=None,
            events=[],
        )

    latest = bucket["events"][-1]
    labeled = _label_values(type_id, latest["values"])
    labels = SENSOR_VALUE_LABELS.get(type_id, [])

    # Pretty format
    if labels:
        val_str = "  ".join(
            f"{k}={v:+.3f}" for k, v in labeled.items()
        )
    else:
        val_str = ", ".join(f"{v:.3f}" for v in latest["values"])

    return _ok(
        f"📐 {name} [{latest['wall']}] → {val_str}",
        sensor=sensor,
        type_id=type_id,
        latest=latest,
        labeled=labeled,
        events=bucket["events"],
        event_count=len(bucket["events"]),
    )



# =============================================================================
# 🔋  Power & Battery  (Frontier #8)
# =============================================================================
#
# Deep insight into battery state, thermal health, and per-app power use:
#
#   dumpsys battery           → current state (level, voltage, temp, charging)
#   dumpsys thermalservice    → thermal throttling + skin/CPU/GPU temps
#   dumpsys batterystats      → per-UID + per-subsystem power drain (mAh)
#
# We parse the plain dump (human format) for `power_status`, `power_thermal`,
# and `power_consumers` / `power_subsystems`. batterystats data accumulates
# since last unplug (or since --reset), so returned values are deltas.

# -- battery state ------------------------------------------------------

# Android BatteryManager constants for status + health + plug
BATTERY_STATUS = {
    1: "unknown", 2: "charging", 3: "discharging",
    4: "not_charging", 5: "full",
}
BATTERY_HEALTH = {
    1: "unknown", 2: "good", 3: "overheat", 4: "dead",
    5: "over_voltage", 6: "unspecified_failure", 7: "cold",
}


def _handle_power_status(serial: Optional[str]) -> Dict[str, Any]:
    """Current battery state: level, voltage, temperature, charging."""
    r = _run(["shell", "dumpsys", "battery"], serial=serial, timeout=10)
    if r["returncode"] != 0:
        return _err(f"dumpsys battery failed: {r['stderr'][:120]}")

    state: Dict[str, Any] = {}
    plug_sources: List[str] = []

    for line in (r["stdout"] or "").splitlines():
        stripped = line.strip()
        # "AC powered: true" → plug sources
        if stripped.startswith("AC powered:") and "true" in stripped:
            plug_sources.append("ac")
        elif stripped.startswith("USB powered:") and "true" in stripped:
            plug_sources.append("usb")
        elif stripped.startswith("Wireless powered:") and "true" in stripped:
            plug_sources.append("wireless")
        elif stripped.startswith("Dock powered:") and "true" in stripped:
            plug_sources.append("dock")

        # "key: value" format
        if ":" in stripped and not stripped.startswith(("Time ", "The ")):
            key, _, val = stripped.partition(":")
            key = key.strip().lower().replace(" ", "_")
            val = val.strip()
            # Numeric fields
            if key in ("level", "scale", "status", "health", "voltage",
                      "temperature", "charge_counter", "max_charging_current",
                      "max_charging_voltage", "charging_policy",
                      "capacity_level", "battery_cycle_count"):
                try:
                    state[key] = int(val)
                except ValueError:
                    pass
            elif key in ("technology",):
                state[key] = val
            elif val in ("true", "false"):
                state[key] = (val == "true")

    # Derived / humanized fields
    level_pct = state.get("level")
    temp_c = None
    if "temperature" in state:
        # Raw temp is in deciDegrees Celsius (329 → 32.9°C)
        temp_c = state["temperature"] / 10.0
    voltage_v = None
    if "voltage" in state:
        # Raw voltage is millivolts (4201 → 4.201 V)
        voltage_v = state["voltage"] / 1000.0
    status_text = BATTERY_STATUS.get(state.get("status", 1), "unknown")
    health_text = BATTERY_HEALTH.get(state.get("health", 1), "unknown")
    charging = status_text == "charging"

    summary = (
        f"🔋 {level_pct}% ({status_text}) "
        f"{temp_c:.1f}°C "
        f"{voltage_v:.3f}V"
    ) if all(x is not None for x in (level_pct, temp_c, voltage_v)) else "🔋 (partial)"

    if plug_sources:
        summary += f" [plugged: {','.join(plug_sources)}]"

    return _ok(
        summary,
        level_pct=level_pct,
        battery_status=status_text,
        battery_status_code=state.get("status"),
        health=health_text,
        health_code=state.get("health"),
        charging=charging,
        plugged=plug_sources,
        temp_c=temp_c,
        voltage_v=voltage_v,
        charge_counter_uah=state.get("charge_counter"),
        technology=state.get("technology"),
        max_charging_current_ua=state.get("max_charging_current"),
        max_charging_voltage_uv=state.get("max_charging_voltage"),
        present=state.get("present"),
        raw=state,
    )


# -- thermal ------------------------------------------------------------

# Thermal status constants (Android PowerManager.THERMAL_STATUS_*)
THERMAL_STATUS = {
    0: "none", 1: "light", 2: "moderate", 3: "severe",
    4: "critical", 5: "emergency", 6: "shutdown",
}

# Thermal sensor types (from android.os.Temperature.Type)
THERMAL_TYPE = {
    -1: "unknown", 0: "cpu", 1: "gpu", 2: "battery", 3: "skin",
    4: "usb_port", 5: "powerable", 6: "ambient", 7: "bcl_voltage",
    8: "bcl_current", 9: "npu", 10: "tpu",
}


def _handle_power_thermal(serial: Optional[str]) -> Dict[str, Any]:
    """Thermal throttling state + per-zone temperatures."""
    r = _run(
        ["shell", "dumpsys", "thermalservice"],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys thermalservice failed: {r['stderr'][:120]}")

    import re as _re
    out = r["stdout"] or ""

    # Overall thermal status — "Thermal Status: N"
    status_code: Optional[int] = None
    m = _re.search(r"Thermal Status:\s*(\d+)", out)
    if m:
        status_code = int(m.group(1))

    # Status override flag
    override = "IsStatusOverride: true" in out

    # Temperature lines:
    # "Temperature{mValue=31.483381, mType=-1, mName=VIRTUAL-SKIN..., mStatus=0}"
    temp_re = _re.compile(
        r"Temperature\{mValue=([-\d.E+]+),\s*mType=(-?\d+),"
        r"\s*mName=([^,]+?),\s*mStatus=(-?\d+)\}"
    )

    temps: List[Dict[str, Any]] = []
    # "Cached temperatures:" appears once; parse all temps after it
    cached_idx = out.find("Cached temperatures:")
    search_from = cached_idx if cached_idx >= 0 else 0

    for tm in temp_re.finditer(out, search_from):
        try:
            value = float(tm.group(1))
        except ValueError:
            continue
        ttype = int(tm.group(2))
        name = tm.group(3).strip()
        tstatus = int(tm.group(4))
        # Skip sentinel values (-3.4e38 = Float.MIN_VALUE, very negative)
        if value < -1000 or value > 1000:
            # Clearly invalid readings; still include but flag
            valid = False
        else:
            valid = True
        temps.append({
            "name": name,
            "value_c": value,
            "type_id": ttype,
            "type": THERMAL_TYPE.get(ttype, "unknown"),
            "status_code": tstatus,
            "status": THERMAL_STATUS.get(tstatus, "unknown"),
            "valid": valid,
        })

    # Group by type for summary
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for t in temps:
        by_type.setdefault(t["type"], []).append(t)

    # Highlight key temps
    def _pick(name_substring: str) -> Optional[Dict[str, Any]]:
        for t in temps:
            if t["valid"] and name_substring.lower() in t["name"].lower():
                return t
        return None

    highlights = {
        "battery": _pick("battery"),
        "skin": _pick("skin-legacy") or _pick("skin"),
        "cpu_big": _pick("BIG"),
        "cpu_mid": _pick("MID"),
        "cpu_little": _pick("LITTLE"),
        "tpu": _pick("TPU"),
        "gpu": _pick("GPU"),
    }

    status_label = THERMAL_STATUS.get(status_code, "unknown")
    summary = (
        f"🌡️  thermal={status_label} "
        f"(battery={highlights['battery']['value_c']:.1f}°C, "
        f"skin={highlights['skin']['value_c']:.1f}°C)"
        if highlights["battery"] and highlights["skin"]
        else f"🌡️  thermal={status_label}"
    )

    return _ok(
        summary,
        thermal_status=status_label,
        thermal_status_code=status_code,
        override=override,
        temperatures=temps,
        highlights=highlights,
        by_type=by_type,
        count=len(temps),
    )


# -- power consumers & subsystems --------------------------------------

# Subsystem codes in `pwi,` lines (batterystats --checkin)
PWI_SUBSYSTEMS = {
    "scrn": "screen",
    "cpu":  "cpu",
    "blue": "bluetooth",
    "camera": "camera",
    "audio": "audio",
    "video": "video",
    "flashlight": "flashlight",
    "cell": "mobile_radio",
    "sensors": "sensors",
    "gnss": "gnss",
    "wifi":  "wifi",
    "memory": "memory",
    "phone": "phone",
    "ambi":  "ambient_display",
    "idle":  "idle",
}


def _parse_batterystats_uids(out: str) -> Dict[int, List[str]]:
    """Extract UID → [package_names] from batterystats --checkin output.

    Format: '9,0,i,uid,10155,com.app.name'
    """
    mapping: Dict[int, List[str]] = {}
    for line in out.splitlines():
        if not line.startswith(("9,0,i,uid,", "8,0,i,uid,", "7,0,i,uid,")):
            continue
        parts = line.split(",", 5)
        if len(parts) < 6:
            continue
        try:
            uid = int(parts[4])
        except ValueError:
            continue
        pkg = parts[5].strip()
        mapping.setdefault(uid, []).append(pkg)
    return mapping


def _parse_uid_consumers(out: str) -> List[Dict[str, Any]]:
    """Parse '  UID nnnn: mAh ...' lines from human dumpsys batterystats.

    Format examples:
      "  UID 1073: 857 fg: 2.96 (2h ...) bg: 854 cached: 0 (76ms)"
      "  UID u0a155: 260 fg: 50.3 (...) bg: 175 (...) fgs: 0.870 (...) cached: 0.00455 (...)"

    `u0a155` = user 0, app 155 = UID 10155
    """
    import re as _re
    consumers: List[Dict[str, Any]] = []

    # "UID u0aN" or "UID NNNN"
    uid_re = _re.compile(
        r"^\s*UID\s+(u(\d+)a(\d+)|(\d+)):\s*([\d.]+)"
    )
    # fg/bg/fgs/cached times + power: "fg: 50.3 (19m 39s 400ms)"
    part_re = _re.compile(
        r"\b(fg|bg|fgs|cached):\s*([\d.eE+-]+)(?:\s*\(([^)]+)\))?"
    )

    lines = out.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = uid_re.match(line)
        if not m:
            i += 1
            continue

        # Resolve UID
        if m.group(2) is not None:
            # u0aN → 10000 + N for user 0
            user_id = int(m.group(2))
            app_id = int(m.group(3))
            uid = user_id * 100000 + 10000 + app_id
        else:
            uid = int(m.group(4))

        try:
            total_mah = float(m.group(5))
        except ValueError:
            i += 1
            continue

        # Parse fg/bg/fgs/cached from the same line
        parts: Dict[str, float] = {}
        for pm in part_re.finditer(line):
            key = pm.group(1)
            try:
                parts[f"{key}_mah"] = float(pm.group(2))
            except ValueError:
                pass

        # Also parse the continuation line for per-subsystem drain, e.g.:
        #   "      cpu=21.5 camera=202 ... wakelock=0.0134 (3s 29ms)"
        subsystems: Dict[str, float] = {}
        if i + 1 < len(lines) and lines[i + 1].startswith("      "):
            for ss in _re.finditer(
                r"(\w+)=([\d.eE+-]+)", lines[i + 1]
            ):
                name = ss.group(1)
                # Skip sub-measurements (cpu:fg etc.) — they use : not =
                try:
                    subsystems[name] = float(ss.group(2))
                except ValueError:
                    pass
            i += 2
        else:
            i += 1

        consumers.append({
            "uid": uid,
            "total_mah": total_mah,
            **parts,
            "subsystems": subsystems,
        })

    return consumers


def _handle_power_consumers(
    top: int, serial: Optional[str]
) -> Dict[str, Any]:
    """Top power consumers by UID, with package names resolved."""
    # Parallel fetch: human dump for UIDs, checkin for UID→pkg mapping
    r = _run(
        ["shell", "dumpsys", "batterystats"],
        serial=serial, timeout=30,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys batterystats failed: {r['stderr'][:120]}")

    consumers = _parse_uid_consumers(r["stdout"] or "")

    # Resolve package names via checkin
    r2 = _run(
        ["shell", "dumpsys", "batterystats", "--checkin"],
        serial=serial, timeout=30,
    )
    uid_to_pkgs: Dict[int, List[str]] = {}
    if r2["returncode"] == 0:
        uid_to_pkgs = _parse_batterystats_uids(r2["stdout"] or "")

    # Attach package names
    for c in consumers:
        c["packages"] = uid_to_pkgs.get(c["uid"], [])

    # Sort by total_mah desc, take top N
    consumers.sort(key=lambda x: x["total_mah"], reverse=True)
    top_n = consumers[:top]

    # Summary
    lines = [f"🔌 Top {min(top, len(consumers))} power consumers (mAh):"]
    for c in top_n[:10]:
        pkgs = c.get("packages", [])
        pkg_str = pkgs[0] if pkgs else f"uid={c['uid']}"
        if len(pkgs) > 1:
            pkg_str += f" (+{len(pkgs) - 1} more)"
        lines.append(f"   {c['total_mah']:8.2f} mAh  {pkg_str}")

    return _ok(
        "\n".join(lines),
        consumers=top_n,
        total_count=len(consumers),
    )


def _handle_power_subsystems(serial: Optional[str]) -> Dict[str, Any]:
    """Global per-subsystem power breakdown (screen/cpu/wifi/cell/gnss/etc)."""
    r = _run(
        ["shell", "dumpsys", "batterystats"],
        serial=serial, timeout=30,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys batterystats failed: {r['stderr'][:120]}")

    out = r["stdout"] or ""

    import re as _re
    # Find "Estimated power use (mAh):"
    idx = out.find("Estimated power use (mAh):")
    if idx < 0:
        return _err("no 'Estimated power use' section found")

    # Parse lines until we hit the first per-condition section
    # (which starts with "    (on battery, screen on)" or a UID)
    subsystems: Dict[str, Dict[str, Any]] = {}
    capacity_mah: Optional[float] = None
    computed_drain_mah: Optional[float] = None
    actual_drain_mah: Optional[float] = None

    section = out[idx:]
    for line in section.splitlines()[:40]:
        stripped = line.strip()
        # "Capacity: 4830, Computed drain: 4042, actual drain: 4042"
        cap_m = _re.match(
            r"Capacity:\s*([\d.]+),\s*Computed drain:\s*([\d.]+),"
            r"\s*actual drain:\s*([\d.]+)",
            stripped
        )
        if cap_m:
            capacity_mah = float(cap_m.group(1))
            computed_drain_mah = float(cap_m.group(2))
            actual_drain_mah = float(cap_m.group(3))
            continue

        # Stop at per-condition or "(on battery, screen on)" section
        if stripped.startswith("(on battery"):
            break
        # Or when we hit UID enumeration
        if stripped.startswith("UID "):
            break

        # "screen: 109 apps: 109"
        # "cpu: 337 apps: 338 duration: 6h 3m 22s 567ms"
        sub_m = _re.match(
            r"^([a-z_]+):\s*([\d.eE+-]+)"
            r"(?:\s+apps:\s*([\d.eE+-]+))?"
            r"(?:\s+duration:\s*(.+?))?$",
            stripped
        )
        if sub_m:
            name = sub_m.group(1)
            try:
                total = float(sub_m.group(2))
            except ValueError:
                continue
            apps = None
            if sub_m.group(3):
                try:
                    apps = float(sub_m.group(3))
                except ValueError:
                    pass
            subsystems[name] = {
                "name": name,
                "total_mah": total,
                "apps_mah": apps,
                "duration": sub_m.group(4) if sub_m.group(4) else None,
            }

    # Sort by total_mah desc for the summary
    sorted_subs = sorted(
        subsystems.values(),
        key=lambda x: x["total_mah"],
        reverse=True,
    )

    lines = [
        f"⚡ Global power breakdown:",
        f"   Capacity: {capacity_mah} mAh  |  "
        f"Computed: {computed_drain_mah} mAh  |  "
        f"Actual: {actual_drain_mah} mAh",
    ]
    for sub in sorted_subs[:10]:
        lines.append(
            f"   {sub['total_mah']:8.2f} mAh  {sub['name']}"
            + (f" (apps: {sub['apps_mah']})" if sub['apps_mah'] is not None else "")
        )

    return _ok(
        "\n".join(lines),
        subsystems=subsystems,
        sorted=sorted_subs,
        capacity_mah=capacity_mah,
        computed_drain_mah=computed_drain_mah,
        actual_drain_mah=actual_drain_mah,
    )



# =============================================================================
# 🎯  UI State Machine: find_element, wait_for_*, tap_element, type_into
#     (Frontier #15 + #18 + parts of #16)
# =============================================================================
#
# The foundation layer for reliable UI automation. Every interactive action
# needs these primitives: "find this element", "wait until X appears",
# "tap X then type Y". Without them, agents are stuck guessing with sleep().
#
# Selector model — pass any combination:
#
#   text="Gmail"               → exact match on node text attribute
#   text_contains="mail"       → substring match on text
#   content_desc="Search settings"  → exact on content-desc
#   resource_id="com.app:id/search_bar"  → exact on resource-id
#   class_name="EditText"      → substring on class attribute
#   package="com.settings"     → exact on package
#   clickable=True             → filter by clickable attribute
#   scrollable=True            → filter by scrollable attribute
#   instance=0                 → Nth match (default: first)
#
# Returns element dict: {x,y,bounds,text,content_desc,resource_id,class,clickable,...}

import re as _re_ui
import time as _time_ui


def _parse_node_attrs(node_str: str) -> Dict[str, Any]:
    """Extract all key="value" pairs from a single uiautomator <node .../> tag."""
    attrs: Dict[str, Any] = {}
    for m in _re_ui.finditer(r'(\w[\w-]*)="([^"]*)"', node_str):
        attrs[m.group(1)] = m.group(2)
    # Parse bounds → (x,y,cx,cy) center
    b = _re_ui.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds", ""))
    if b:
        x1, y1, x2, y2 = map(int, b.groups())
        attrs["_bounds"] = (x1, y1, x2, y2)
        attrs["_cx"] = (x1 + x2) // 2
        attrs["_cy"] = (y1 + y2) // 2
        attrs["_width"] = x2 - x1
        attrs["_height"] = y2 - y1
    return attrs


def _iter_nodes(xml: str):
    """Yield raw <node .../> tag strings from a uiautomator dump."""
    # Each node is a self-closing or opening tag starting with '<node '
    for m in _re_ui.finditer(r'<node [^>]*?/?>', xml):
        yield m.group(0)


def _node_matches(
    attrs: Dict[str, Any],
    text: Optional[str] = None,
    text_contains: Optional[str] = None,
    content_desc: Optional[str] = None,
    content_desc_contains: Optional[str] = None,
    resource_id: Optional[str] = None,
    resource_id_contains: Optional[str] = None,
    class_name: Optional[str] = None,
    package: Optional[str] = None,
    clickable: Optional[bool] = None,
    scrollable: Optional[bool] = None,
    focusable: Optional[bool] = None,
    checked: Optional[bool] = None,
    enabled: Optional[bool] = None,
) -> bool:
    """Return True if node attrs match ALL provided selector criteria."""
    if text is not None and attrs.get("text", "") != text:
        return False
    if text_contains is not None and text_contains.lower() not in attrs.get("text", "").lower():
        return False
    if content_desc is not None and attrs.get("content-desc", "") != content_desc:
        return False
    if (content_desc_contains is not None
        and content_desc_contains.lower() not in attrs.get("content-desc", "").lower()):
        return False
    if resource_id is not None and attrs.get("resource-id", "") != resource_id:
        return False
    if (resource_id_contains is not None
        and resource_id_contains not in attrs.get("resource-id", "")):
        return False
    if class_name is not None and class_name not in attrs.get("class", ""):
        return False
    if package is not None and attrs.get("package", "") != package:
        return False
    if clickable is not None and (attrs.get("clickable") == "true") != clickable:
        return False
    if scrollable is not None and (attrs.get("scrollable") == "true") != scrollable:
        return False
    if focusable is not None and (attrs.get("focusable") == "true") != focusable:
        return False
    if checked is not None and (attrs.get("checked") == "true") != checked:
        return False
    if enabled is not None and (attrs.get("enabled") == "true") != enabled:
        return False
    return True


def _find_elements(
    xml: str, selector: Dict[str, Any], limit: int = 100
) -> List[Dict[str, Any]]:
    """Find all nodes matching a selector dict. Returns list of attr dicts."""
    results = []
    for node_str in _iter_nodes(xml):
        attrs = _parse_node_attrs(node_str)
        if _node_matches(attrs, **selector):
            results.append(attrs)
            if len(results) >= limit:
                break
    return results


def _element_to_public(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Convert internal attr dict to the clean public shape agents consume."""
    return {
        "text": attrs.get("text", ""),
        "content_desc": attrs.get("content-desc", ""),
        "resource_id": attrs.get("resource-id", ""),
        "class": attrs.get("class", ""),
        "package": attrs.get("package", ""),
        "bounds": list(attrs.get("_bounds", ())) or None,
        "center": (attrs.get("_cx"), attrs.get("_cy")) if "_cx" in attrs else None,
        "width": attrs.get("_width"),
        "height": attrs.get("_height"),
        "clickable": attrs.get("clickable") == "true",
        "scrollable": attrs.get("scrollable") == "true",
        "focusable": attrs.get("focusable") == "true",
        "focused": attrs.get("focused") == "true",
        "enabled": attrs.get("enabled") == "true",
        "checked": attrs.get("checked") == "true",
        "selected": attrs.get("selected") == "true",
        "password": attrs.get("password") == "true",
    }


def _build_selector(**kwargs) -> Dict[str, Any]:
    """Filter out None values to produce a clean selector dict."""
    return {k: v for k, v in kwargs.items() if v is not None}


# --- handlers ----------------------------------------------------------

def _handle_find_element(
    serial: Optional[str], instance: int = 0, **selector
) -> Dict[str, Any]:
    """Find first (or instance=Nth) element matching selector."""
    sel = _build_selector(**selector)
    if not sel:
        return _err("find_element requires at least one selector field")

    xml = _ui_dump(serial)
    matches = _find_elements(xml, sel, limit=instance + 10)

    if not matches or instance >= len(matches):
        preview = ", ".join(f"{k}={v!r}" for k, v in sel.items())
        return _err(
            f"no match for selector ({preview}) — "
            f"{len(matches)} found, wanted instance={instance}"
        )

    el = _element_to_public(matches[instance])
    cx, cy = el["center"] or (None, None)
    preview = el["text"] or el["content_desc"] or el["resource_id"] or el["class"]
    summary = f"🎯 found: {preview[:40]} at ({cx},{cy})"

    return _ok(
        summary,
        element=el,
        total_matches=len(matches),
        selector=sel,
    )


def _handle_find_elements(
    serial: Optional[str], limit: int = 50, **selector
) -> Dict[str, Any]:
    """Find ALL elements matching selector (up to limit)."""
    sel = _build_selector(**selector)
    if not sel:
        return _err("find_elements requires at least one selector field")

    xml = _ui_dump(serial)
    matches = _find_elements(xml, sel, limit=limit)
    elements = [_element_to_public(m) for m in matches]

    summary = f"🎯 found {len(elements)} elements"
    return _ok(
        summary,
        elements=elements,
        count=len(elements),
        selector=sel,
    )


def _handle_wait_for_element(
    serial: Optional[str],
    timeout: float = 10.0,
    poll_interval: float = 0.3,
    **selector,
) -> Dict[str, Any]:
    """Block until an element matching selector appears, or timeout."""
    sel = _build_selector(**selector)
    if not sel:
        return _err("wait_for_element requires at least one selector field")

    deadline = _time_ui.monotonic() + timeout
    polls = 0
    while _time_ui.monotonic() < deadline:
        polls += 1
        xml = _ui_dump(serial)
        matches = _find_elements(xml, sel, limit=1)
        if matches:
            el = _element_to_public(matches[0])
            elapsed = timeout - (deadline - _time_ui.monotonic())
            return _ok(
                f"⏳ appeared after {elapsed:.2f}s ({polls} polls)",
                element=el,
                elapsed_sec=elapsed,
                polls=polls,
            )
        _time_ui.sleep(poll_interval)

    preview = ", ".join(f"{k}={v!r}" for k, v in sel.items())
    return _err(
        f"timeout after {timeout}s waiting for ({preview}) — polled {polls}x"
    )


def _handle_wait_for_gone(
    serial: Optional[str],
    timeout: float = 10.0,
    poll_interval: float = 0.3,
    **selector,
) -> Dict[str, Any]:
    """Block until element matching selector DISAPPEARS, or timeout."""
    sel = _build_selector(**selector)
    if not sel:
        return _err("wait_for_gone requires at least one selector field")

    deadline = _time_ui.monotonic() + timeout
    polls = 0
    while _time_ui.monotonic() < deadline:
        polls += 1
        xml = _ui_dump(serial)
        matches = _find_elements(xml, sel, limit=1)
        if not matches:
            elapsed = timeout - (deadline - _time_ui.monotonic())
            return _ok(
                f"⏳ gone after {elapsed:.2f}s ({polls} polls)",
                elapsed_sec=elapsed,
                polls=polls,
            )
        _time_ui.sleep(poll_interval)
    return _err(f"timeout — element still present after {timeout}s")


def _handle_wait_for_idle(
    serial: Optional[str],
    timeout: float = 5.0,
    quiet_ms: int = 500,
    poll_interval: float = 0.25,
) -> Dict[str, Any]:
    """Block until UI stops changing for `quiet_ms` milliseconds.

    Diffs consecutive UI dumps. When N consecutive dumps are identical
    (covering at least quiet_ms of time), we call the UI 'idle'.
    """
    deadline = _time_ui.monotonic() + timeout
    last_hash = None
    stable_since = None
    polls = 0

    while _time_ui.monotonic() < deadline:
        polls += 1
        xml = _ui_dump(serial)
        h = hash(xml)
        now = _time_ui.monotonic()

        if h == last_hash:
            if stable_since is None:
                stable_since = now
            elif (now - stable_since) * 1000 >= quiet_ms:
                return _ok(
                    f"⏳ idle for {quiet_ms}ms after {polls} polls",
                    polls=polls,
                    stable_ms=int((now - stable_since) * 1000),
                )
        else:
            stable_since = None
            last_hash = h

        _time_ui.sleep(poll_interval)

    return _err(
        f"UI not idle within {timeout}s — polled {polls}x"
    )


def _handle_wait_for_window(
    serial: Optional[str],
    window_package: Optional[str] = None,
    window_activity: Optional[str] = None,
    window_contains: Optional[str] = None,
    timeout: float = 10.0,
    poll_interval: float = 0.3,
) -> Dict[str, Any]:
    """Block until mCurrentFocus matches the given package/activity."""
    if not any((window_package, window_activity, window_contains)):
        return _err(
            "wait_for_window requires window_package, window_activity, "
            "or window_contains"
        )

    deadline = _time_ui.monotonic() + timeout
    polls = 0
    last_focus = None
    while _time_ui.monotonic() < deadline:
        polls += 1
        r = _run(["shell", "dumpsys", "window"], serial=serial, timeout=5)
        focus_m = _re_ui.search(
            r"mCurrentFocus=Window\{\w+ u\d+ ([^\}]+)\}",
            r["stdout"] or "",
        )
        focus = focus_m.group(1) if focus_m else ""
        last_focus = focus

        hit = False
        if window_package and focus.startswith(window_package):
            hit = True
        if window_activity and window_activity in focus:
            hit = True
        if window_contains and window_contains in focus:
            hit = True

        if hit:
            elapsed = timeout - (deadline - _time_ui.monotonic())
            return _ok(
                f"⏳ window matched after {elapsed:.2f}s: {focus}",
                focus=focus,
                elapsed_sec=elapsed,
                polls=polls,
            )
        _time_ui.sleep(poll_interval)

    return _err(
        f"timeout — current focus was {last_focus!r}"
    )


def _handle_tap_element(
    serial: Optional[str], instance: int = 0, **selector
) -> Dict[str, Any]:
    """Find element then tap its center. Combines find + tap in one call."""
    sel = _build_selector(**selector)
    if not sel:
        return _err("tap_element requires at least one selector field")

    xml = _ui_dump(serial)
    matches = _find_elements(xml, sel, limit=instance + 10)
    if not matches or instance >= len(matches):
        preview = ", ".join(f"{k}={v!r}" for k, v in sel.items())
        return _err(f"no match for ({preview})")

    el = matches[instance]
    cx, cy = el["_cx"], el["_cy"]
    _tap(cx, cy, serial)

    pub = _element_to_public(el)
    preview = pub["text"] or pub["content_desc"] or pub["resource_id"]
    return _ok(
        f"👆 tapped: {preview[:40]} at ({cx},{cy})",
        element=pub,
        tap_point=[cx, cy],
    )


def _handle_type_into(
    serial: Optional[str],
    input_text: str = "",
    clear: bool = True,
    dismiss_keyboard: bool = True,
    instance: int = 0,
    **selector,
) -> Dict[str, Any]:
    """Find an input field, tap to focus it, clear, then type.

    Handles the full form-fill cycle:
      1. Find element (must be an EditText or have focusable=true)
      2. Tap to focus
      3. Optionally select-all + delete (to clear existing content)
      4. Send text via `input text` (safe encoding of spaces/special chars)
      5. Optionally press Back to dismiss the soft keyboard
    """
    if not input_text and not clear:
        return _err("type_into requires input_text (or clear=True)")

    sel = _build_selector(**selector)
    if not sel:
        return _err("type_into requires at least one selector field")

    xml = _ui_dump(serial)
    matches = _find_elements(xml, sel, limit=instance + 10)
    if not matches or instance >= len(matches):
        preview = ", ".join(f"{k}={v!r}" for k, v in sel.items())
        return _err(f"no match for ({preview})")

    el = matches[instance]
    cx, cy = el["_cx"], el["_cy"]

    steps: List[str] = []

    # 1. Tap to focus
    _tap(cx, cy, serial)
    steps.append(f"tap@({cx},{cy})")
    _time_ui.sleep(0.35)

    # 2. Clear existing: Ctrl+A then Del. KEYCODE_A=29, KEYCODE_DEL=67
    # meta=4096 is META_CTRL_LEFT_ON
    if clear:
        _run(
            ["shell", "input", "keyevent", "--longpress", "123", "122"],
            serial=serial, timeout=3,
        )  # MOVE_END, MOVE_HOME (select all simulation)
        # Proper select-all via keycombo:
        _run(["shell", "input", "keycombination", "113", "29"],
             serial=serial, timeout=3)  # CTRL_LEFT + A
        _time_ui.sleep(0.15)
        _run(["shell", "input", "keyevent", "KEYCODE_DEL"],
             serial=serial, timeout=3)
        steps.append("clear")
        _time_ui.sleep(0.15)

    # 3. Type. `input text` requires spaces as %s and escaping of shell metas.
    if input_text:
        # Android `input text` treats %s as a literal space. Other shell-
        # meta chars must be escaped for the shell (ADB passes the command
        # through /system/bin/sh).
        safe = input_text.replace(" ", "%s")
        for ch in ('"', "'", "$", "`", "\\"):
            safe = safe.replace(ch, "\\" + ch)
        _run(["shell", "input", "text", safe], serial=serial, timeout=10)
        steps.append(f"type({len(input_text)} chars)")
        _time_ui.sleep(0.25)

    # 4. Optionally dismiss soft keyboard
    if dismiss_keyboard:
        _run(["shell", "input", "keyevent", "KEYCODE_BACK"],
             serial=serial, timeout=3)
        steps.append("dismiss_kbd")

    pub = _element_to_public(el)
    preview = pub["text"] or pub["content_desc"] or pub["resource_id"]
    return _ok(
        f"⌨️  typed into '{preview[:30]}': {input_text[:40]!r} ({len(steps)} steps)",
        element=pub,
        typed=input_text,
        steps=steps,
    )


def _handle_app_launch(
    serial: Optional[str],
    package: Optional[str] = None,
    app_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Launch an app by package name, or fuzzy-resolve from a friendly name."""
    resolved_pkg = package

    if not resolved_pkg and app_name:
        # Fuzzy resolve: search installed packages
        r = _run(["shell", "pm", "list", "packages", "-3", "-e"],
                 serial=serial, timeout=10)
        # Format: 'package:com.app.name'
        all_pkgs = [
            l.replace("package:", "").strip()
            for l in (r["stdout"] or "").splitlines()
            if l.startswith("package:")
        ]
        # Also include system packages (gmail, messages are system)
        r2 = _run(["shell", "pm", "list", "packages", "-s", "-e"],
                  serial=serial, timeout=10)
        all_pkgs += [
            l.replace("package:", "").strip()
            for l in (r2["stdout"] or "").splitlines()
            if l.startswith("package:")
        ]

        needle = app_name.lower().replace(" ", "")

        # Prefer exact suffix match, e.g. 'gmail' → 'com.google.android.gm'
        aliases = {
            "gmail": "com.google.android.gm",
            "chrome": "com.android.chrome",
            "youtube": "com.google.android.youtube",
            "maps": "com.google.android.apps.maps",
            "photos": "com.google.android.apps.photos",
            "calendar": "com.google.android.calendar",
            "messages": "com.google.android.apps.messaging",
            "phone": "com.google.android.dialer",
            "settings": "com.android.settings",
            "playstore": "com.android.vending",
            "camera": "com.google.android.GoogleCamera",
            "calculator": "com.google.android.calculator",
            "clock": "com.google.android.deskclock",
            "files": "com.google.android.documentsui",
        }
        if needle in aliases and aliases[needle] in all_pkgs:
            resolved_pkg = aliases[needle]
        else:
            # Score each package by how well it matches
            scored: List[Tuple[int, str]] = []
            for pkg in all_pkgs:
                low = pkg.lower()
                # Last segment: 'com.google.android.gm' → 'gm'
                last = low.rsplit(".", 1)[-1]
                if last == needle:
                    scored.append((100, pkg))
                elif needle in last:
                    scored.append((80, pkg))
                elif needle in low:
                    scored.append((60, pkg))
            scored.sort(reverse=True)
            if scored:
                resolved_pkg = scored[0][1]

    if not resolved_pkg:
        return _err(
            f"could not resolve app_name={app_name!r}. "
            "Pass package='com.exact.name' explicitly."
        )

    # Find the LAUNCHER intent for this package
    r = _run(
        ["shell", "cmd", "package", "resolve-activity", "--brief",
         "-c", "android.intent.category.LAUNCHER", resolved_pkg],
        serial=serial, timeout=10,
    )
    # Expected output:
    #   priority=0 ...
    #   com.example/com.example.MainActivity
    component = None
    for line in (r["stdout"] or "").splitlines():
        line = line.strip()
        if "/" in line and not line.startswith(("priority", "No", "Warn")):
            component = line
            break

    if not component:
        # Fallback to monkey launcher (less reliable but works for stubborn apps)
        r2 = _run(
            ["shell", "monkey", "-p", resolved_pkg,
             "-c", "android.intent.category.LAUNCHER", "1"],
            serial=serial, timeout=10,
        )
        ok = "Events injected: 1" in (r2["stdout"] or "")
        if not ok:
            return _err(
                f"could not launch {resolved_pkg} — "
                f"no LAUNCHER intent found"
            )
        launch_method = "monkey"
    else:
        _run(["shell", "am", "start", "-n", component], serial=serial, timeout=10)
        launch_method = "am_start"

    return _ok(
        f"🚀 launched {resolved_pkg} via {launch_method}",
        package=resolved_pkg,
        component=component,
        method=launch_method,
        resolved_from=app_name,
    )


def _handle_foreground_info(serial: Optional[str]) -> Dict[str, Any]:
    """What's on top right now? {package, activity, full}."""
    r = _run(["shell", "dumpsys", "window"], serial=serial, timeout=5)
    m = _re_ui.search(
        r"mCurrentFocus=Window\{\w+ u\d+ ([^\}]+)\}",
        r["stdout"] or "",
    )
    focus = m.group(1) if m else ""
    if "/" in focus:
        pkg, _, activity = focus.partition("/")
    else:
        pkg, activity = focus, ""
    # Expand relative class names: '.NexusLauncherActivity' → full path
    if activity.startswith("."):
        activity = pkg + activity

    return _ok(
        f"🪟 foreground: {pkg}" + (f" / {activity.split('.')[-1]}" if activity else ""),
        package=pkg,
        activity=activity,
        focus_raw=focus,
    )


# =============================================================================
# 🔓  Session Lifecycle & Smart Unlock  (Frontier #14)
# =============================================================================
#
# Actions:
#   is_locked       → {locked: bool, trust_state, awake}
#   wake            → wake device (no unlock, just screen on)
#   sleep           → power off screen + lock
#   unlock          → full state machine: wake → dismiss bouncer → enter PIN
#                     → verify. Handles AlternateBouncerView (biometric) and
#                     PrimaryBouncer (PIN pad) on modern Pixels.
#   keep_awake      → toggle stay_on_while_plugged_in
#
# How the lockscreen state machine works on Pixel 10 / Android 16:
#
#   1. Device wakes → AlternateBouncerView appears (fingerprint icon only)
#   2. Tap the fingerprint sensor coords → PIN pad appears (PrimaryBouncer)
#   3. Tap digits → tap Enter → keyguard dismisses
#   4. Focus switches to NexusLauncherActivity → deviceLocked=0
#
# Older devices show the PIN pad directly after a swipe-up. We auto-detect
# the bouncer type by looking for '0'-'9' content-desc buttons in the dump.

import re as _re_lock


def _dumpsys_trust(serial: Optional[str]) -> str:
    r = _run(["shell", "dumpsys", "trust"], serial=serial, timeout=8)
    return r["stdout"] or ""


def _read_device_locked(serial: Optional[str]) -> Optional[bool]:
    """Return True/False, or None if we can't tell."""
    out = _dumpsys_trust(serial)
    m = _re_lock.search(r"deviceLocked=(\d+)", out)
    return (m.group(1) == "1") if m else None


def _ui_dump(serial: Optional[str]) -> str:
    """Return UIAutomator XML dump for the current screen."""
    r = _run(
        ["shell",
         "uiautomator dump /sdcard/adbtool_dump.xml >/dev/null 2>&1 && "
         "cat /sdcard/adbtool_dump.xml"],
        serial=serial, timeout=10,
    )
    return r["stdout"] or ""


def _find_node_center(xml: str, content_desc: str) -> Optional[Tuple[int, int]]:
    """Find first node with given content-desc, return center (x,y)."""
    # Escape for regex use in the content-desc value
    cd = _re_lock.escape(content_desc)
    pattern = _re_lock.compile(
        r'content-desc="' + cd + r'"[^/]*'
        r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
    )
    m = pattern.search(xml)
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def _tap(x: int, y: int, serial: Optional[str]) -> None:
    _run(["shell", "input", "tap", str(x), str(y)], serial=serial, timeout=5)


def _handle_is_locked(serial: Optional[str]) -> Dict[str, Any]:
    """Quick lock-state query — returns locked + awake + trust signals."""
    trust_out = _dumpsys_trust(serial)
    locked_m = _re_lock.search(r"deviceLocked=(\d+)", trust_out)
    trust_m = _re_lock.search(r"trustState=(\w+)", trust_out)

    # Wakefulness from power manager
    r_pow = _run(["shell", "dumpsys", "power"], serial=serial, timeout=8)
    wake_m = _re_lock.search(r"mWakefulness=(\w+)", r_pow["stdout"] or "")

    locked = (locked_m.group(1) == "1") if locked_m else None
    awake = (wake_m.group(1) == "Awake") if wake_m else None
    trust_state = trust_m.group(1) if trust_m else None

    summary = (
        f"🔒 locked={locked}  awake={awake}  trust={trust_state}"
    )
    return _ok(
        summary,
        locked=locked,
        awake=awake,
        wakefulness=(wake_m.group(1) if wake_m else None),
        trust_state=trust_state,
    )


def _handle_wake(serial: Optional[str]) -> Dict[str, Any]:
    """Wake the screen (no unlock). Idempotent."""
    _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
         serial=serial, timeout=5)
    # Verify
    r_pow = _run(["shell", "dumpsys", "power"], serial=serial, timeout=5)
    wake_m = _re_lock.search(r"mWakefulness=(\w+)", r_pow["stdout"] or "")
    awake = wake_m and wake_m.group(1) == "Awake"
    return _ok(
        f"⏰ wake: {wake_m.group(1) if wake_m else 'unknown'}",
        awake=bool(awake),
        wakefulness=(wake_m.group(1) if wake_m else None),
    )


def _handle_sleep(serial: Optional[str]) -> Dict[str, Any]:
    """Turn screen off and lock the device."""
    _run(["shell", "input", "keyevent", "KEYCODE_SLEEP"],
         serial=serial, timeout=5)
    # Verify
    r_pow = _run(["shell", "dumpsys", "power"], serial=serial, timeout=5)
    wake_m = _re_lock.search(r"mWakefulness=(\w+)", r_pow["stdout"] or "")
    return _ok(
        f"😴 sleep: {wake_m.group(1) if wake_m else 'unknown'}",
        awake=False,
        wakefulness=(wake_m.group(1) if wake_m else None),
    )


def _handle_keep_awake(
    keep_awake_enabled: bool, serial: Optional[str]
) -> Dict[str, Any]:
    """Toggle Settings.Global.stay_on_while_plugged_in.

    Values: 0=never, 1=AC, 2=USB, 4=Wireless (bitmask). We set to 7 when
    True (all sources), 0 when False.
    """
    value = "7" if keep_awake_enabled else "0"
    _run(
        ["shell", "settings", "put", "global",
         "stay_on_while_plugged_in", value],
        serial=serial, timeout=5,
    )
    r2 = _run(
        ["shell", "settings", "get", "global",
         "stay_on_while_plugged_in"],
        serial=serial, timeout=5,
    )
    current = (r2["stdout"] or "").strip()
    return _ok(
        f"📺 keep_awake={keep_awake_enabled} (now={current})",
        keep_awake=keep_awake_enabled,
        raw_value=current,
    )


def _handle_unlock(
    pin: Optional[str],
    serial: Optional[str],
    max_retries: int = 2,
) -> Dict[str, Any]:
    """Unlock the device by entering the PIN.

    State machine:
      a) Sleeping            → wake first
      b) Already unlocked    → return success immediately
      c) AlternateBouncerView (biometric) → tap fingerprint icon → PIN pad
      d) Swipe-to-unlock keyguard         → swipe up → PIN pad
      e) PrimaryBouncer (PIN pad)         → tap digits → Enter

    Args:
        pin: PIN to enter. If None, read from env var ADB_DEVICE_PIN.
        max_retries: how many times to retry the PIN tap sequence.
    """
    import time as _time

    # Resolve PIN: explicit arg > env var
    if not pin:
        pin = os.environ.get("ADB_DEVICE_PIN", "")
    if not pin:
        return _err(
            "no PIN supplied. Pass pin='1234' or set ADB_DEVICE_PIN env var."
        )
    if not pin.isdigit():
        return _err(f"PIN must be digits only (got {len(pin)} chars)")

    steps: List[str] = []

    # Step 1: already unlocked?
    already = _read_device_locked(serial)
    if already is False:
        return _ok(
            "🔓 already unlocked", locked=False, steps=["already_unlocked"],
        )
    steps.append(f"initial_locked={already}")

    # Step 2: wake if sleeping
    r_pow = _run(["shell", "dumpsys", "power"], serial=serial, timeout=5)
    wake_m = _re_lock.search(r"mWakefulness=(\w+)", r_pow["stdout"] or "")
    if not wake_m or wake_m.group(1) != "Awake":
        _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
             serial=serial, timeout=5)
        _time.sleep(0.4)
        steps.append("wake")

    # Step 3: reach the PIN pad.
    # On Pixel 10 Pro / Android 16 the flow is:
    #   (a) Fresh wake   → NotificationShade hosts the lockscreen face
    #   (b) wm dismiss-keyguard → switches to AlternateBouncerView (biometric)
    #   (c) Tap fingerprint icon → PrimaryBouncer with PIN pad appears
    #
    # On older devices, step (b) may not be needed, or the PIN pad may
    # appear directly after (b). We handle all variants with fall-throughs.

    xml = _ui_dump(serial)
    has_pin_pad = all(
        _find_node_center(xml, str(d)) is not None for d in "0123456789"
    )

    if not has_pin_pad:
        # Step 3a: request keyguard dismissal (forces bouncer view)
        _run(["shell", "wm", "dismiss-keyguard"], serial=serial, timeout=5)
        _time.sleep(1.2)
        steps.append("dismiss_keyguard")
        xml = _ui_dump(serial)
        has_pin_pad = all(
            _find_node_center(xml, str(d)) is not None for d in "0123456789"
        )

    if not has_pin_pad:
        # Step 3b: dismiss biometric bouncer by tapping FP sensor
        fp = _find_node_center(xml, "Fingerprint sensor")
        if fp:
            _tap(*fp, serial)
            _time.sleep(1.2)
            steps.append(f"tap_fp@{fp}")
            xml = _ui_dump(serial)
            has_pin_pad = all(
                _find_node_center(xml, str(d)) is not None for d in "0123456789"
            )

    if not has_pin_pad:
        # Step 3c: legacy swipe-up fallback
        _run(
            ["shell", "input", "touchscreen", "swipe",
             "540", "1800", "540", "600", "300"],
            serial=serial, timeout=5,
        )
        _time.sleep(1.0)
        steps.append("swipe_up")
        xml = _ui_dump(serial)
        has_pin_pad = all(
            _find_node_center(xml, str(d)) is not None for d in "0123456789"
        )

    if not has_pin_pad:
        err = _err(
            "could not reach PIN pad. UI may have changed or device is in "
            "an unexpected state."
        )
        err["steps"] = steps
        return err

    # Step 4: enter PIN + tap Enter, with retries
    last_error = None
    for attempt in range(1, max_retries + 1):
        # Look up each digit every attempt (positions are stable but dump
        # could theoretically change). Tap them in order.
        positions = {d: _find_node_center(xml, d) for d in "0123456789"}
        enter_pos = _find_node_center(xml, "Enter")
        delete_pos = _find_node_center(xml, "Delete")

        if not all(positions.values()) or not enter_pos:
            last_error = f"missing keys (attempt {attempt})"
            _time.sleep(0.5)
            xml = _ui_dump(serial)
            continue

        # Tap each digit
        for digit in pin:
            x, y = positions[digit]
            _tap(x, y, serial)
            _time.sleep(0.12)

        # Tap Enter
        _tap(*enter_pos, serial)
        _time.sleep(1.4)  # give keyguard time to dismiss

        # Verify
        locked_after = _read_device_locked(serial)
        steps.append(f"attempt{attempt}: locked_after={locked_after}")

        if locked_after is False:
            return _ok(
                f"🔓 unlocked in {attempt} attempt(s) (steps: {len(steps)})",
                locked=False,
                attempts=attempt,
                steps=steps,
            )

        # Failure: PIN pad may need re-dumping for retry. Tap Delete enough
        # times to clear, then try again.
        if delete_pos and attempt < max_retries:
            for _ in range(len(pin) + 2):
                _tap(*delete_pos, serial)
                _time.sleep(0.08)
            _time.sleep(0.4)
            xml = _ui_dump(serial)
            # Re-check it's still a PIN pad
            if not _find_node_center(xml, "0"):
                last_error = "PIN pad vanished mid-retry"
                break

    err = _err(
        f"unlock failed after {max_retries} attempt(s): {last_error or 'unknown'}"
    )
    err["steps"] = steps
    return err


# =============================================================================
# 🔐  Security & Posture  (Frontier #13)
# =============================================================================
#
# Security posture inspection — boot integrity, encryption, SELinux, lock
# screen, biometric sensors, device admin apps, and VPN state. All read-only
# introspection. Four actions:
#
#   security_posture     → device-wide security snapshot (pass/fail signals)
#   security_lock        → lock screen config (PIN/pattern/password/none)
#   security_biometrics  → fingerprint + face sensor enrollment
#   security_vpn         → active VPN connections
#
# Why this matters to agents: "is this device safe to run automation on?"
# Posture answers that in one call.


# Biometric modality bits (android.hardware.biometrics.BiometricAuthenticator.Modality)
#   TYPE_NONE         = 0
#   TYPE_CREDENTIAL   = 1 << 0 = 1
#   TYPE_FINGERPRINT  = 1 << 1 = 2
#   TYPE_IRIS         = 1 << 2 = 4
#   TYPE_FACE         = 1 << 3 = 8
BIOMETRIC_MODALITY = {
    1:  "credential",      # device PIN/pattern/password fallback
    2:  "fingerprint",     # TYPE_FINGERPRINT (also UDFPS on Pixel)
    4:  "iris",            # TYPE_IRIS
    8:  "face",            # TYPE_FACE
}
# Strength classes (android.hardware.biometrics.BiometricManager.Authenticators)
BIOMETRIC_STRENGTH = {
    15:  "strong",       # BIOMETRIC_STRONG (Class 3)
    255: "weak",         # BIOMETRIC_WEAK   (Class 2)
    32768: "convenience", # DEVICE_CREDENTIAL
}

# Lock screen credential types (android.app.admin.DevicePolicyManager)
LOCK_CRED_TYPES = {
    -1: "managed",
    0:  "none",
    1:  "pattern",
    2:  "pin",
    3:  "password",
    4:  "password_or_pin",
    5:  "managed",
}


def _handle_security_posture(serial: Optional[str]) -> Dict[str, Any]:
    """Device-wide security posture snapshot.

    Checks: verified boot, SELinux enforcement, file-based encryption,
    bootloader lock, OEM unlock, developer-options / ADB status,
    Play Protect / package verifier, and current user count.
    """
    # Multi-prop fetch in one shell call (much faster than N calls)
    keys = [
        "ro.boot.verifiedbootstate",     # green / yellow / orange / red
        "ro.boot.veritymode",            # enforcing / logging / disabled
        "ro.boot.flash.locked",          # 1 / 0
        "ro.oem_unlock_supported",       # 1 / 0
        "ro.crypto.state",               # encrypted / unencrypted / unsupported
        "ro.crypto.type",                # file / block / <empty>
        "ro.build.selinux",              # 1 / 0 / <empty>
        "ro.build.version.security_patch",  # YYYY-MM-DD
        "ro.build.version.release",      # Android version
        "ro.product.device",
    ]
    script = " ; ".join(f"echo '{k}='; getprop {k}" for k in keys)
    r = _run(["shell", script], serial=serial, timeout=8)
    if r["returncode"] != 0:
        return _err(f"getprop failed: {r['stderr'][:120]}")

    props: Dict[str, str] = {}
    current_key: Optional[str] = None
    for line in (r["stdout"] or "").splitlines():
        if line.endswith("=") and line[:-1] in keys:
            current_key = line[:-1]
        elif current_key is not None:
            props[current_key] = line.strip()
            current_key = None

    # Separately: SELinux enforcement state (runtime, not prop)
    r2 = _run(["shell", "getenforce"], serial=serial, timeout=5)
    selinux_enforcing = (r2["stdout"] or "").strip().lower() == "enforcing"

    # Dev options + ADB + package verifier (via settings, fast)
    r3 = _run(
        ["shell",
         "settings get global development_settings_enabled ;"
         "echo --- ;"
         "settings get global adb_enabled ;"
         "echo --- ;"
         "settings get global package_verifier_enable ;"
         "echo --- ;"
         "pm list users"],
        serial=serial, timeout=8,
    )
    parts = (r3["stdout"] or "").split("---")
    dev_enabled = parts[0].strip() == "1" if len(parts) > 0 else None
    adb_enabled = parts[1].strip() == "1" if len(parts) > 1 else None
    pkg_verifier = parts[2].strip() if len(parts) > 2 else ""
    user_count = 0
    if len(parts) > 3:
        user_count = parts[3].count("UserInfo{")

    # Interpret verified boot
    vbs = props.get("ro.boot.verifiedbootstate", "").lower()
    verified_boot_ok = vbs == "green"

    # Bootloader locked? (flash.locked=1 means locked)
    bootloader_locked = props.get("ro.boot.flash.locked", "0") == "1"

    # Encryption: both state=encrypted AND type present
    encrypted = (
        props.get("ro.crypto.state", "").lower() == "encrypted"
        and props.get("ro.crypto.type", "") != ""
    )

    # Summary "safe-to-use" posture
    strong = (
        verified_boot_ok
        and bootloader_locked
        and selinux_enforcing
        and encrypted
    )

    # Build warnings list
    # Separate critical warnings (posture failures) from informational ones
    critical: List[str] = []
    if not verified_boot_ok:
        critical.append(f"verified_boot={vbs!r} (expected 'green')")
    if not bootloader_locked:
        critical.append("bootloader unlocked")
    if not selinux_enforcing:
        critical.append("SELinux not enforcing")
    if not encrypted:
        critical.append("storage not encrypted")
    info: List[str] = []
    if dev_enabled:
        info.append("developer options enabled")
    if adb_enabled:
        info.append("ADB enabled (needed for this tool)")
    if pkg_verifier == "0":
        info.append("package verifier disabled")

    warnings = critical + info  # combined for callers that want the full list

    # 🟢 strong + no info warnings
    # 🟡 strong but info warnings present (dev/ADB enabled but boot is fine)
    # 🔴 any critical warning
    if critical:
        posture_emoji = "🔴"
    elif info:
        posture_emoji = "🟡"
    else:
        posture_emoji = "🟢"

    lines = [
        f"{posture_emoji} Security posture: "
        f"{'STRONG' if strong else 'WEAKENED'} "
        f"(Android {props.get('ro.build.version.release', '?')}, "
        f"patch {props.get('ro.build.version.security_patch', '?')})",
        f"   verified_boot: {vbs or 'unknown'}",
        f"   bootloader:    {'locked' if bootloader_locked else 'UNLOCKED'}",
        f"   selinux:       {'enforcing' if selinux_enforcing else 'PERMISSIVE'}",
        f"   encryption:    "
        f"{props.get('ro.crypto.state', '?')}"
        f"{' (' + props['ro.crypto.type'] + '-based)' if props.get('ro.crypto.type') else ''}",
        f"   developer:     {'ON' if dev_enabled else 'off'}  "
        f"ADB: {'ON' if adb_enabled else 'off'}",
    ]

    return _ok(
        "\n".join(lines),
        strong_posture=strong,
        android_version=props.get("ro.build.version.release"),
        security_patch=props.get("ro.build.version.security_patch"),
        verified_boot_state=vbs or None,
        verified_boot_ok=verified_boot_ok,
        verity_mode=props.get("ro.boot.veritymode"),
        bootloader_locked=bootloader_locked,
        oem_unlock_supported=props.get("ro.oem_unlock_supported") == "1",
        selinux_enforcing=selinux_enforcing,
        encrypted=encrypted,
        encryption_type=props.get("ro.crypto.type") or None,
        encryption_state=props.get("ro.crypto.state") or None,
        developer_options=dev_enabled,
        adb_enabled=adb_enabled,
        package_verifier=pkg_verifier,
        user_count=user_count,
        warnings=warnings,
        raw_props=props,
    )


def _handle_security_lock(serial: Optional[str]) -> Dict[str, Any]:
    """Lock screen configuration: credential type, device-locked state."""
    r = _run(
        ["shell", "dumpsys", "lock_settings"],
        serial=serial, timeout=8,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys lock_settings failed: {r['stderr'][:120]}")

    import re as _re
    out = r["stdout"] or ""

    # Per-user blocks. We care about user 0 (primary).
    # Format:
    #   User State:
    #     User 0
    #       ...
    #       Quality: 0
    #       CredentialType: PIN
    #       SeparateChallenge: true
    users: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    user_re = _re.compile(r"^\s*User\s+(\d+)\s*$")
    kv_re = _re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ]+?):\s*(.+?)\s*$")

    for raw in out.splitlines():
        um = user_re.match(raw)
        if um:
            if current:
                users.append(current)
            current = {"user_id": int(um.group(1))}
            continue
        if current is None:
            continue
        km = kv_re.match(raw)
        if km:
            key = km.group(1).strip().lower().replace(" ", "_")
            val = km.group(2).strip()
            current[key] = val
    if current:
        users.append(current)

    # Trust manager state (running state: locked/unlocked)
    r2 = _run(
        ["shell", "dumpsys", "trust"],
        serial=serial, timeout=8,
    )
    trust_out = r2["stdout"] or ""

    # Per-user trust state:
    #   User "Name" (id=0, flags=0x..) (current): trustState=UNTRUSTED,
    #   trustManaged=1, deviceLocked=1, isActiveUnlockRunning=0, strongAuthRequired=0x0
    trust_re = _re.compile(
        r'User\s+"[^"]*"\s+\(id=(\d+),[^)]*\)[^:]*:\s*'
        r'trustState=(\w+),\s*trustManaged=(\d+),\s*deviceLocked=(\d+)'
    )
    trust_by_user: Dict[int, Dict[str, Any]] = {}
    for tm in trust_re.finditer(trust_out):
        uid = int(tm.group(1))
        trust_by_user[uid] = {
            "trust_state": tm.group(2),  # TRUSTED / UNTRUSTED
            "trust_managed": tm.group(3) == "1",
            "device_locked": tm.group(4) == "1",
        }

    # Merge trust info into users
    for u in users:
        ts = trust_by_user.get(u["user_id"], {})
        u.update(ts)

    # Primary user (0) for summary
    primary = next((u for u in users if u["user_id"] == 0), users[0] if users else {})
    cred_type = primary.get("credentialtype", "none")
    secure = cred_type.lower() not in ("none", "")

    lines = [
        f"🔒 Lock screen ({'secured' if secure else 'NOT SECURED'}): "
        f"{cred_type}",
    ]
    if primary:
        if primary.get("device_locked") is not None:
            lines.append(
                f"   currently: {'LOCKED' if primary['device_locked'] else 'unlocked'}"
            )
        if "quality" in primary:
            lines.append(f"   quality: {primary['quality']}")
    if len(users) > 1:
        lines.append(f"   {len(users)} user(s) / profile(s)")

    return _ok(
        "\n".join(lines),
        secured=secure,
        credential_type=cred_type,
        users=users,
        primary_user=primary,
        user_count=len(users),
    )


def _handle_security_biometrics(serial: Optional[str]) -> Dict[str, Any]:
    """Biometric sensor inventory: fingerprint + face + iris capabilities."""
    r = _run(
        ["shell", "dumpsys", "biometric"],
        serial=serial, timeout=8,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys biometric failed: {r['stderr'][:120]}")

    import re as _re
    out = r["stdout"] or ""

    # Sensor lines:
    #   ID(1), oemStrength: 15, updatedStrength: 15, modality 8, state: 0, cookie: 0
    #   ID(37748992), oemStrength: 15, updatedStrength: 15, modality 2, state: 0, cookie: 0
    sensor_re = _re.compile(
        r"ID\((\d+)\),\s*oemStrength:\s*(\d+),\s*updatedStrength:\s*(\d+),"
        r"\s*modality\s*(\d+),\s*state:\s*(\d+)"
    )
    sensors: List[Dict[str, Any]] = []
    for sm in sensor_re.finditer(out):
        sid = int(sm.group(1))
        oem_strength = int(sm.group(2))
        cur_strength = int(sm.group(3))
        modality_bits = int(sm.group(4))
        state = int(sm.group(5))

        # Decode modality bitmask — single bit typically, but build list for safety
        modalities: List[str] = []
        for bit, label in BIOMETRIC_MODALITY.items():
            if modality_bits & bit:
                modalities.append(label)
        # If none matched and value is non-zero, record unknown
        if not modalities and modality_bits:
            modalities.append(f"unknown({modality_bits})")

        sensors.append({
            "id": sid,
            "oem_strength": oem_strength,
            "current_strength": cur_strength,
            "strength_class": BIOMETRIC_STRENGTH.get(cur_strength, f"unknown({cur_strength})"),
            "modality_bits": modality_bits,
            "modalities": modalities,
            "state": state,
            "enabled": state == 0,  # state=0 means idle/available
        })

    # Legacy mode signal
    legacy = "Legacy Settings: true" in out

    # Split by modality for summary
    fingerprint = [s for s in sensors if "fingerprint" in s["modalities"]]
    face = [s for s in sensors if "face" in s["modalities"]]
    iris = [s for s in sensors if "iris" in s["modalities"]]

    has_any = bool(sensors)
    parts = []
    if fingerprint:
        parts.append(f"fingerprint({len(fingerprint)})")
    if face:
        parts.append(f"face({len(face)})")
    if iris:
        parts.append(f"iris({len(iris)})")

    summary = (
        f"👆 Biometrics: {', '.join(parts) or 'none detected'}"
        if has_any else "👆 Biometrics: none"
    )

    return _ok(
        summary,
        has_biometrics=has_any,
        sensors=sensors,
        fingerprint_count=len(fingerprint),
        face_count=len(face),
        iris_count=len(iris),
        legacy_mode=legacy,
        count=len(sensors),
    )


def _handle_security_vpn(serial: Optional[str]) -> Dict[str, Any]:
    """Active VPN detection from connectivity manager.

    A VPN tunnel shows up as a NetworkAgentInfo with 'Transports: VPN'
    in its NetworkCapabilities. Regular Wi-Fi/cellular/ethernet all
    carry the NOT_VPN capability.
    """
    r = _run(
        ["shell", "dumpsys", "connectivity"],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys connectivity failed: {r['stderr'][:120]}")

    import re as _re
    out = r["stdout"] or ""

    # NetworkAgentInfo{network{N}  ... Transports: X,Y,Z Capabilities: ...}
    # We want lines where Transports contains VPN (not NOT_VPN).
    agent_re = _re.compile(
        r"NetworkAgentInfo\{network\{(\d+)\}.*?"
        r"Transports:\s*([A-Z&_,|]+).*?"
        r"Capabilities:\s*([A-Z&_,|]+)",
        _re.DOTALL,
    )
    vpns: List[Dict[str, Any]] = []
    for am in agent_re.finditer(out):
        transports = am.group(2)
        # Check 'VPN' is in transports but not as part of NOT_VPN (capability)
        # Transports are separated by & or ,
        tokens = _re.split(r"[&,|]", transports)
        if "VPN" not in tokens:
            continue

        caps = am.group(3)
        interface_m = _re.search(
            r"InterfaceName:\s*(\S+)",
            out[am.start():am.end() + 1000],  # look ahead for LinkProperties
        )
        interface = interface_m.group(1).strip().rstrip('}') if interface_m else None

        vpns.append({
            "network_id": int(am.group(1)),
            "transports": tokens,
            "capabilities": _re.split(r"[&,|]", caps),
            "interface": interface,
        })

    # Summary
    if vpns:
        summary = f"🔒 VPN: ACTIVE ({len(vpns)} tunnel{'s' if len(vpns) != 1 else ''})"
        for v in vpns:
            summary += f"\n   network#{v['network_id']} on {v['interface']}"
    else:
        summary = "🔒 VPN: not connected"

    return _ok(
        summary,
        active=bool(vpns),
        vpns=vpns,
        count=len(vpns),
    )



# =============================================================================
# 📡  Connectivity: Wi-Fi, Bluetooth, Airplane Mode  (Frontier #7)
# =============================================================================
#
# Agents read + control radio state. All APIs are standard `cmd` services —
# no root required.
#
#   cmd wifi status|set-wifi-enabled|start-scan|list-scan-results
#       |list-networks|connect-network|forget-network
#   cmd bluetooth_manager enable|disable|wait-for-state:STATE_ON
#   dumpsys bluetooth_manager    — state + bonded devices
#   cmd connectivity airplane-mode [enable|disable]
#
# We favour cmd (service dispatcher) over legacy `svc wifi` because cmd is
# the modern non-deprecated path and gives structured output.


def _extract_leading_int(v: str) -> Optional[int]:
    """Extract the leading integer from a string like '2401Mbps', '-57', '6135MHz'."""
    import re as _re
    if v is None:
        return None
    m = _re.match(r"\s*(-?\d+)", str(v))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


# Wi-Fi security types accepted by `cmd wifi add/connect-network`
WIFI_SECURITY_TYPES = {"open", "owe", "wpa2", "wpa3", "wep"}


def _parse_wifi_status(out: str) -> Dict[str, Any]:
    """Parse `cmd wifi status` output.

    Example output (connected):
      Wifi is enabled
      ==== Primary ClientModeManager instance ====
      Wifi is connected to TestSSID
      WifiInfo: SSID: "TestSSID", BSSID: aa:bb:cc:dd:ee:ff, ...
      Supplicant state: COMPLETED
      ...

    Example output (disconnected):
      Wifi is enabled
      Wifi is not connected
    """
    info: Dict[str, Any] = {
        "enabled": False,
        "connected": False,
        "ssid": None,
        "bssid": None,
        "ip_address": None,
        "link_speed_mbps": None,
        "rssi": None,
        "frequency": None,
    }
    for raw in (out or "").splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("wifi is enabled"):
            info["enabled"] = True
        elif low.startswith("wifi is disabled"):
            info["enabled"] = False
        elif low.startswith("wifi is connected to"):
            info["connected"] = True
            raw_ssid = line.split("connected to", 1)[1].strip()
            info["ssid"] = raw_ssid.strip().strip('"')
        elif low.startswith("wifi is not connected"):
            info["connected"] = False
        elif line.startswith("WifiInfo:"):
            # "WifiInfo: SSID: \"X\", BSSID: aa:..., Supplicant state: ..."
            # Parse key:value pairs separated by ", "
            payload = line.split(":", 1)[1]
            parts = [p.strip() for p in payload.split(",")]
            for part in parts:
                if ":" not in part:
                    continue
                k, v = part.split(":", 1)
                k = k.strip().lower()
                v = v.strip().strip('"')
                if k == "ssid" and info["ssid"] is None:
                    info["ssid"] = v
                elif k == "bssid":
                    info["bssid"] = v
                elif k in ("ip", "ip_address"):
                    info["ip_address"] = v
                elif k == "rssi":
                    info["rssi"] = _extract_leading_int(v)
                elif k == "link speed":
                    # "Link speed: 2401Mbps" — there's also "Tx Link speed",
                    # "Rx Link speed" etc; only match exact "link speed"
                    info["link_speed_mbps"] = _extract_leading_int(v)
                elif k == "frequency":
                    info["frequency"] = _extract_leading_int(v)
    return info


def _parse_scan_results(out: str) -> List[Dict[str, Any]]:
    """Parse `cmd wifi list-scan-results` table.

    Header row:
      BSSID              Frequency      RSSI           Age(sec)     SSID           Flags
    Data rows use whitespace columns — SSID may contain spaces, Flags may
    contain brackets. Strategy: split on ≥2 spaces, take first 4 fields,
    then SSID is field 4 and Flags is everything after the SSID field.
    """
    results: List[Dict[str, Any]] = []
    lines = (out or "").splitlines()
    if not lines:
        return results
    # Skip until we see the header
    for i, line in enumerate(lines):
        if line.strip().startswith("BSSID") and "RSSI" in line and "SSID" in line:
            data_lines = lines[i + 1:]
            break
    else:
        # No header — nothing to parse (e.g. "No scan results")
        return results

    for raw in data_lines:
        line = raw.strip()
        if not line:
            continue
        # Split on runs of 2+ spaces to preserve SSID that has spaces
        # but NOT to glue columns together when SSID is empty.
        parts = [p for p in _split_on_multi_space(line) if p]
        if len(parts) < 4:
            continue
        try:
            bssid = parts[0]
            freq = int(parts[1])
            rssi = int(parts[2])
            age = float(parts[3])
        except ValueError:
            continue
        # If parts[4] starts with '[', it's actually the flags column —
        # meaning this network has an empty SSID (hidden network).
        ssid = ""
        flags_start_idx = 4
        if len(parts) >= 5:
            if parts[4].startswith("["):
                ssid = ""
                flags_start_idx = 4
            else:
                ssid = parts[4]
                flags_start_idx = 5
        # Flags are bracketed; collect anything starting with "["
        flags: List[str] = []
        for p in parts[flags_start_idx:]:
            if p.startswith("["):
                # may be multiple concatenated flags
                for chunk in _split_flags(p):
                    flags.append(chunk)
        # Determine security class from flags
        security = _security_from_flags(flags)
        results.append({
            "bssid": bssid,
            "frequency": freq,
            "rssi": rssi,
            "age_sec": age,
            "ssid": ssid,
            "flags": flags,
            "security": security,
            "band": ("2.4GHz" if freq < 3000 else
                     "5GHz" if freq < 6000 else "6GHz"),
        })
    return results


def _split_on_multi_space(line: str) -> List[str]:
    """Split on 2+ spaces. Preserves single spaces inside SSIDs."""
    import re as _re
    return _re.split(r"\s{2,}", line)


def _split_flags(s: str) -> List[str]:
    """Split 'foo][bar]' or '[foo][bar]' into ['[foo]', '[bar]']."""
    out = []
    cur = ""
    depth = 0
    for ch in s:
        cur += ch
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and cur:
                out.append(cur)
                cur = ""
    if cur:
        out.append(cur)
    return out


def _security_from_flags(flags: List[str]) -> str:
    """Derive human-friendly security type from the flags column."""
    flat = " ".join(flags).upper()
    if "SAE" in flat:  return "wpa3"
    if "WPA2" in flat or "RSN" in flat: return "wpa2"
    if "WPA" in flat:  return "wpa"
    if "WEP" in flat:  return "wep"
    if "OWE" in flat:  return "owe"
    if not flags or flat.strip() in ("", "[ESS]"): return "open"
    return "unknown"


def _parse_saved_networks(out: str) -> List[Dict[str, Any]]:
    """Parse `cmd wifi list-networks` table.

    Header:  Network Id      SSID                         Security type
    Data:    0            Verizon_SG4VBJ                   wpa2-psk
             0            Verizon_SG4VBJ                   wpa3-sae^
    The same network_id can appear on multiple lines (one per security
    type variant). We collapse them into a single entry with a list of
    security types.
    """
    by_id: Dict[int, Dict[str, Any]] = {}
    lines = (out or "").splitlines()
    in_data = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Network Id"):
            in_data = True
            continue
        if not in_data:
            continue
        parts = _split_on_multi_space(line)
        if len(parts) < 3:
            continue
        try:
            nid = int(parts[0])
        except ValueError:
            continue
        ssid = parts[1]
        sec = parts[2].rstrip("^").strip()

        entry = by_id.setdefault(nid, {
            "network_id": nid,
            "ssid": ssid,
            "security_types": [],
        })
        if sec not in entry["security_types"]:
            entry["security_types"].append(sec)

    return list(by_id.values())


def _parse_bt_status(out: str) -> Dict[str, Any]:
    """Parse `dumpsys bluetooth_manager` output."""
    info: Dict[str, Any] = {
        "enabled": False,
        "name": None,
        "address": None,
        "state": None,
        "discovering": False,
        "bonded_count": 0,
        "connection_state": None,
    }
    in_props = False
    for raw in (out or "").splitlines():
        line = raw.strip()
        # Top-level "State: ON/OFF"
        if line.startswith("State:"):
            val = line.split(":", 1)[1].strip()
            info["state"] = val
            info["enabled"] = (val == "ON")
        elif line.startswith("Name:"):
            info["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Address:"):
            info["address"] = line.split(":", 1)[1].strip()
        elif line.startswith("ConnectionState:"):
            info["connection_state"] = line.split(":", 1)[1].strip()
        elif line.startswith("Discovering:"):
            info["discovering"] = line.split(":", 1)[1].strip().lower() == "true"
        elif line.startswith("Bonded devices:"):
            try:
                info["bonded_count"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return info


# --- handlers ----------------------------------------------------------

def _handle_wifi_status(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(["shell", "cmd", "wifi", "status"], serial=serial, timeout=5)
    if r["returncode"] != 0:
        return _err(f"wifi status failed: {r['stderr'][:120]}")
    info = _parse_wifi_status(r["stdout"] or "")
    if info["connected"]:
        msg = (f"📡 wifi ON, connected to {info['ssid']} "
               f"({info.get('rssi', '?')} dBm, {info.get('frequency', '?')} MHz)")
    elif info["enabled"]:
        msg = "📡 wifi ON, not connected"
    else:
        msg = "📡 wifi OFF"
    return _ok(msg, **info)


def _handle_wifi_enable(
    enabled: bool, serial: Optional[str]
) -> Dict[str, Any]:
    flag = "enabled" if enabled else "disabled"
    r = _run(
        ["shell", "cmd", "wifi", "set-wifi-enabled", flag],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"wifi toggle failed: {r['stderr'][:120]}")

    # Give it a moment, then verify
    time.sleep(1.5)
    status = _handle_wifi_status(serial)
    ok = status.get("enabled") == enabled
    return _ok(
        f"📡 wifi → {'ON' if enabled else 'OFF'} "
        f"(verified: {'✓' if ok else '⏳'})",
        requested=enabled,
        actual_enabled=status.get("enabled"),
    )


def _handle_wifi_scan(
    serial: Optional[str], wait_sec: float = 4.0
) -> Dict[str, Any]:
    """Trigger a fresh scan and return results."""
    # Must be enabled first
    status = _run(["shell", "cmd", "wifi", "status"], serial=serial, timeout=5)
    if "Wifi is disabled" in (status["stdout"] or ""):
        return _err("wifi is disabled; enable first with wifi_enable")

    r = _run(["shell", "cmd", "wifi", "start-scan"], serial=serial, timeout=5)
    if r["returncode"] != 0:
        return _err(f"start-scan failed: {r['stderr'][:120]}")

    time.sleep(wait_sec)

    r = _run(
        ["shell", "cmd", "wifi", "list-scan-results"],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"list-scan-results failed: {r['stderr'][:120]}")

    networks = _parse_scan_results(r["stdout"] or "")
    # Sort by RSSI (strongest first)
    networks.sort(key=lambda n: n["rssi"], reverse=True)

    lines = [f"📡 {len(networks)} networks found:"]
    for n in networks[:10]:
        lines.append(
            f"  {n['rssi']:>4} dBm  {n['band']:>6}  "
            f"{n['security']:>7}  {n['ssid']!r}"
        )
    return _ok(
        "\n".join(lines),
        networks=networks,
        count=len(networks),
    )


def _handle_wifi_list_saved(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(
        ["shell", "cmd", "wifi", "list-networks"],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"list-networks failed: {r['stderr'][:120]}")
    saved = _parse_saved_networks(r["stdout"] or "")

    lines = [f"📡 {len(saved)} saved networks:"]
    for n in saved[:15]:
        sec = "/".join(n["security_types"])
        lines.append(
            f"  [{n['network_id']:>3}]  {n['ssid']!r:30s}  ({sec})"
        )
    return _ok("\n".join(lines), saved=saved, count=len(saved))


def _handle_wifi_connect(
    ssid: str, security: str, passphrase: Optional[str],
    serial: Optional[str],
) -> Dict[str, Any]:
    if not ssid:
        return _err("wifi_connect requires wifi_ssid")
    security = (security or "").lower().strip()
    if security not in WIFI_SECURITY_TYPES:
        return _err(
            f"security must be one of {sorted(WIFI_SECURITY_TYPES)} "
            f"(got {security!r})"
        )
    if security in ("wpa2", "wpa3", "wep") and not passphrase:
        return _err(f"{security} requires wifi_passphrase")
    if security in ("open", "owe") and passphrase:
        return _err(f"{security} does not take a passphrase")

    args = ["shell", "cmd", "wifi", "connect-network", ssid, security]
    if passphrase:
        args.append(passphrase)

    r = _run(args, serial=serial, timeout=15)
    if r["returncode"] != 0:
        return _err(f"connect-network failed: {r['stderr'][:120]}")

    # Give it a moment
    time.sleep(2.0)
    status = _handle_wifi_status(serial)
    return _ok(
        f"📡 connect requested for {ssid!r} "
        f"(now: {'connected to ' + str(status.get('ssid')) if status.get('connected') else 'not yet connected'})",
        ssid=ssid,
        security=security,
        **{k: status.get(k) for k in ("connected", "ssid", "bssid")},
    )


def _handle_wifi_forget(
    network_id: int, serial: Optional[str]
) -> Dict[str, Any]:
    if not isinstance(network_id, int) or network_id < 0:
        return _err("wifi_network_id must be a non-negative int")
    r = _run(
        ["shell", "cmd", "wifi", "forget-network", str(network_id)],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"forget-network failed: {r['stderr'][:120]}")
    return _ok(
        f"📡 forgot network id {network_id}",
        network_id=network_id,
        stdout=(r["stdout"] or "").strip()[:200],
    )


def _handle_bt_status(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(
        ["shell", "dumpsys", "bluetooth_manager"],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys bluetooth_manager failed: {r['stderr'][:120]}")

    info = _parse_bt_status(r["stdout"] or "")
    if info["enabled"]:
        msg = (f"🅱️ bluetooth ON — {info.get('name', '?')} "
               f"({info.get('address', '?')[:8]}…), "
               f"{info['bonded_count']} bonded, "
               f"{'discovering' if info['discovering'] else 'idle'}")
    else:
        msg = f"🅱️ bluetooth {info.get('state') or 'OFF'}"
    return _ok(msg, **info)


def _handle_bt_enable(
    enabled: bool, serial: Optional[str]
) -> Dict[str, Any]:
    cmd = "enable" if enabled else "disable"
    r = _run(
        ["shell", "cmd", "bluetooth_manager", cmd],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"bluetooth toggle failed: {r['stderr'][:120]}")

    # Wait for state change; check up to 5s
    want = "STATE_ON" if enabled else "STATE_OFF"
    for _ in range(10):
        time.sleep(0.5)
        status = _handle_bt_status(serial)
        if status.get("enabled") == enabled:
            break

    return _ok(
        f"🅱️ bluetooth → {'ON' if enabled else 'OFF'} "
        f"(state: {status.get('state')})",
        requested=enabled,
        actual_enabled=status.get("enabled"),
    )


def _handle_airplane_mode_get(serial: Optional[str]) -> Dict[str, Any]:
    r = _run(
        ["shell", "cmd", "connectivity", "airplane-mode"],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"airplane-mode get failed: {r['stderr'][:120]}")
    out = (r["stdout"] or "").strip().lower()
    enabled = out == "enabled"
    return _ok(
        f"✈️ airplane mode: {'ON' if enabled else 'OFF'}",
        enabled=enabled,
        raw=out,
    )


def _handle_airplane_mode_set(
    enabled: bool, serial: Optional[str]
) -> Dict[str, Any]:
    flag = "enable" if enabled else "disable"
    r = _run(
        ["shell", "cmd", "connectivity", "airplane-mode", flag],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"airplane-mode set failed: {r['stderr'][:120]}")

    time.sleep(1.0)
    # Verify
    verify = _handle_airplane_mode_get(serial)
    return _ok(
        f"✈️ airplane mode → {'ON' if enabled else 'OFF'} "
        f"(verified: {'✓' if verify.get('enabled') == enabled else '⏳'})",
        requested=enabled,
        actual_enabled=verify.get("enabled"),
    )



# =============================================================================
# 🎵  Media Session & AVRCP  (Frontier #5)
# =============================================================================
#
# Agents control media playback and volume across any app: Spotify, YouTube,
# Music, Podcasts, etc. Dispatch any media key, query/adjust/set stream
# volumes, and read session state (active app, playback state, metadata).
#
# APIs harnessed (no root):
#
#   cmd media_session dispatch <KEY>        — global media key
#       KEY: play, pause, play-pause, mute, headsethook, stop,
#            next, previous, rewind, record, fast-forward
#   cmd media_session volume --stream N --get|--set N|--adj raise|same|lower
#       STREAM: 0=voice_call, 1=system, 2=ring, 3=music, 4=alarm,
#               5=notification, 6=bluetooth_sco, 10=accessibility
#   cmd media_session list-sessions          — session tags
#   dumpsys media_session                    — full state: active/inactive
#                                              sessions, packages, metadata
#   input keyevent 24|25|164                 — volume up/down/mute fallback
#
# The volume command outputs multiple "[V]" debug lines. Parse the final
# "volume is N in range [min..max]" line for the numeric value.

MEDIA_KEYS = {
    "play", "pause", "play-pause", "play_pause",
    "mute", "headsethook", "stop",
    "next", "previous", "rewind", "record",
    "fast-forward", "fast_forward", "ff",
}

# Mapping of agent-friendly aliases to the exact CLI token
MEDIA_KEY_ALIASES = {
    "playpause":   "play-pause",
    "play_pause":  "play-pause",
    "toggle":      "play-pause",
    "fast_forward": "fast-forward",
    "ff":          "fast-forward",
    "skip":        "next",
    "back":        "previous",
    "prev":        "previous",
}

# Stream name → AudioManager.STREAM_* constant
AUDIO_STREAMS = {
    "voice_call":    0,
    "voice":         0,
    "call":          0,
    "system":        1,
    "sys":           1,
    "ring":          2,
    "ringtone":      2,
    "music":         3,
    "media":         3,
    "alarm":         4,
    "notification":  5,
    "notif":         5,
    "bluetooth_sco": 6,
    "bt":            6,
    "dtmf":          8,
    "accessibility": 10,
    "a11y":          10,
}

# Reverse lookup so `volume_get` can name the stream
AUDIO_STREAM_NAMES = {
    0: "voice_call",
    1: "system",
    2: "ring",
    3: "music",
    4: "alarm",
    5: "notification",
    6: "bluetooth_sco",
    8: "dtmf",
    10: "accessibility",
}


def _resolve_media_key(key: str) -> Optional[str]:
    """Resolve a media key name (with aliases) to the CLI token."""
    if not key:
        return None
    low = key.lower().strip()
    # Alias first
    low = MEDIA_KEY_ALIASES.get(low, low)
    if low in MEDIA_KEYS:
        return low
    return None


def _resolve_stream(stream: Any) -> Optional[int]:
    """Resolve a stream name/number to AudioManager constant."""
    if isinstance(stream, int):
        return stream if stream in AUDIO_STREAM_NAMES else None
    if isinstance(stream, str):
        low = stream.lower().strip()
        if low in AUDIO_STREAMS:
            return AUDIO_STREAMS[low]
        if low.isdigit() and int(low) in AUDIO_STREAM_NAMES:
            return int(low)
    return None


def _parse_volume_output(out: str) -> Dict[str, Optional[int]]:
    """Parse `cmd media_session volume --get` output.

    Last line is of form: `[V] volume is N in range [MIN..MAX]`
    """
    volume = vol_min = vol_max = None
    for line in (out or "").splitlines():
        line = line.strip()
        # "[V] volume is 5 in range [0..25]"
        if "volume is" in line and "range" in line:
            try:
                after_is = line.split("volume is", 1)[1].strip()
                parts = after_is.split()
                volume = int(parts[0])
                # Extract the [min..max] bracket
                if "[" in after_is and "]" in after_is:
                    inner = after_is.split("[", 1)[1].split("]", 1)[0]
                    if ".." in inner:
                        mn, mx = inner.split("..", 1)
                        vol_min = int(mn)
                        vol_max = int(mx)
            except (ValueError, IndexError):
                pass
    return {"volume": volume, "min": vol_min, "max": vol_max}


def _handle_media_dispatch(key: str, serial: Optional[str]) -> Dict[str, Any]:
    """Dispatch a media key globally."""
    resolved = _resolve_media_key(key)
    if resolved is None:
        return _err(
            f"unknown media key '{key}'. Valid: {sorted(MEDIA_KEYS)}. "
            f"Aliases: {sorted(MEDIA_KEY_ALIASES.keys())}."
        )

    r = _run(
        ["shell", "cmd", "media_session", "dispatch", resolved],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"dispatch failed: {r['stderr'][:120]}")

    return _ok(
        f"🎵 dispatched '{resolved}'",
        key=resolved,
        original_key=key,
    )


def _handle_media_volume_get(
    stream: Any, serial: Optional[str]
) -> Dict[str, Any]:
    """Get current volume for an audio stream."""
    stream_id = _resolve_stream(stream)
    if stream_id is None:
        return _err(
            f"unknown stream '{stream}'. Valid: {sorted(AUDIO_STREAMS.keys())}"
        )

    r = _run(
        ["shell", "cmd", "media_session", "volume",
         "--stream", str(stream_id), "--get"],
        serial=serial, timeout=5,
    )
    if r["returncode"] != 0:
        return _err(f"volume get failed: {r['stderr'][:120]}")

    parsed = _parse_volume_output(r["stdout"] or "")
    stream_name = AUDIO_STREAM_NAMES.get(stream_id, str(stream_id))

    if parsed["volume"] is None:
        return _err(f"could not parse volume output: {r['stdout'][:200]!r}")

    return _ok(
        f"🎵 {stream_name} volume: {parsed['volume']}/{parsed['max']}",
        stream=stream_name,
        stream_id=stream_id,
        volume=parsed["volume"],
        volume_min=parsed["min"],
        volume_max=parsed["max"],
    )


def _handle_media_volume_set(
    stream: Any, index: int, show_ui: bool, serial: Optional[str]
) -> Dict[str, Any]:
    """Set volume to a specific index."""
    stream_id = _resolve_stream(stream)
    if stream_id is None:
        return _err(
            f"unknown stream '{stream}'. Valid: {sorted(AUDIO_STREAMS.keys())}"
        )
    if index < 0 or index > 100:
        return _err("volume index must be 0..100")

    args = ["shell", "cmd", "media_session", "volume",
            "--stream", str(stream_id), "--set", str(index)]
    if show_ui:
        args.append("--show")

    r = _run(args, serial=serial, timeout=5)
    if r["returncode"] != 0:
        return _err(f"volume set failed: {r['stderr'][:120]}")

    # Read back to confirm
    verify = _run(
        ["shell", "cmd", "media_session", "volume",
         "--stream", str(stream_id), "--get"],
        serial=serial, timeout=5,
    )
    parsed = _parse_volume_output(verify["stdout"] or "")

    stream_name = AUDIO_STREAM_NAMES.get(stream_id, str(stream_id))
    return _ok(
        f"🎵 {stream_name} volume → {parsed.get('volume', index)}",
        stream=stream_name,
        stream_id=stream_id,
        requested=index,
        actual=parsed.get("volume"),
        volume_max=parsed.get("max"),
    )


def _handle_media_volume_adjust(
    stream: Any, direction: str, show_ui: bool, serial: Optional[str]
) -> Dict[str, Any]:
    """Adjust volume up/down/same."""
    stream_id = _resolve_stream(stream)
    if stream_id is None:
        return _err(
            f"unknown stream '{stream}'. Valid: {sorted(AUDIO_STREAMS.keys())}"
        )
    direction = direction.lower().strip()
    # Accept common English aliases
    dir_map = {
        "up":    "raise",
        "raise": "raise",
        "louder": "raise",
        "higher": "raise",
        "down":  "lower",
        "lower": "lower",
        "quieter": "lower",
        "softer": "lower",
        "same":  "same",
        "keep":  "same",
    }
    if direction not in dir_map:
        return _err(
            f"direction must be one of {sorted(dir_map.keys())} (got '{direction}')"
        )
    adj = dir_map[direction]

    args = ["shell", "cmd", "media_session", "volume",
            "--stream", str(stream_id), "--adj", adj]
    if show_ui:
        args.append("--show")

    r = _run(args, serial=serial, timeout=5)
    if r["returncode"] != 0:
        return _err(f"volume adjust failed: {r['stderr'][:120]}")

    # Verify
    verify = _run(
        ["shell", "cmd", "media_session", "volume",
         "--stream", str(stream_id), "--get"],
        serial=serial, timeout=5,
    )
    parsed = _parse_volume_output(verify["stdout"] or "")

    stream_name = AUDIO_STREAM_NAMES.get(stream_id, str(stream_id))
    return _ok(
        f"🎵 {stream_name} {adj} → {parsed.get('volume', '?')}",
        stream=stream_name,
        stream_id=stream_id,
        direction=adj,
        volume=parsed.get("volume"),
        volume_max=parsed.get("max"),
    )


def _handle_media_sessions_list(serial: Optional[str]) -> Dict[str, Any]:
    """List all media sessions parsed from dumpsys media_session.

    Each session has: tag, package, userId, active (bool), state,
    playbackState, and optionally metadata (title/artist/album).
    """
    r = _run(
        ["shell", "dumpsys", "media_session"],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"dumpsys media_session failed: {r['stderr'][:120]}")

    sessions: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    in_metadata = False
    metadata_buf: Dict[str, str] = {}

    # Regex-free line-oriented parser
    for raw in (r["stdout"] or "").splitlines():
        stripped = raw.strip()
        # Session header looks like:
        #   "play_movies_media com.google.android.videos/play_movies_media/3 (userId=0)"
        # It's indented 2 or 4 spaces, has "(userId=" at end
        # Session headers look like:
        #   "HeadsetMediaButton com.android.server.telecom/HeadsetMediaButton/1 (userId=0)"
        # They have: one-word tag, then pkg/TAG/N (userId=U). Prose lines
        # like "Global priority session is ..." and "3 sessions listeners"
        # also contain "(userId=" but don't match the pkg/TAG/N shape.
        is_header = False
        tag = pkg = None
        uid = None
        if "(userId=" in stripped and "/" in stripped:
            parts = stripped.split(" ", 1)
            if len(parts) == 2:
                cand_tag = parts[0]
                rest = parts[1]
                # Component must look like "pkg/TAG/N (userId=U)"
                # The tag in component must match cand_tag
                slash_parts = rest.split(" (userId=", 1)[0].split("/")
                if (len(slash_parts) >= 2
                        and slash_parts[1] == cand_tag
                        and not cand_tag.startswith(("owner", "Global",
                                                       "priority"))):
                    is_header = True
                    tag = cand_tag
                    pkg = slash_parts[0]
                    uid_part = rest.split("(userId=", 1)[-1].rstrip(")")
                    try:
                        uid = int(uid_part)
                    except ValueError:
                        uid = None

        if is_header:
            # Flush previous session
            if current is not None:
                current["metadata"] = metadata_buf
                sessions.append(current)
                metadata_buf = {}
            current = {
                "tag": tag,
                "package": pkg,
                "userId": uid,
                "active": None,
                "state": None,
                "flags": None,
            }
        elif current is not None:
            # Session attributes
            if stripped.startswith("active="):
                val = stripped.split("=", 1)[1].strip()
                current["active"] = val.lower() == "true"
            elif stripped.startswith("package="):
                current["package"] = stripped.split("=", 1)[1].strip()
            elif stripped.startswith("flags="):
                current["flags"] = stripped.split("=", 1)[1].strip()
            elif stripped.startswith("state=") and "PlaybackState" in stripped:
                # "state=PlaybackState {state=PLAYING(3), position=...}"
                state_part = stripped.split("state=", 2)
                if len(state_part) >= 3:
                    inner = state_part[2]
                    # Grab just the state identifier
                    if inner.startswith("PLAYING"):
                        current["state"] = "PLAYING"
                    elif inner.startswith("PAUSED"):
                        current["state"] = "PAUSED"
                    elif inner.startswith("STOPPED"):
                        current["state"] = "STOPPED"
                    elif inner.startswith("BUFFERING"):
                        current["state"] = "BUFFERING"
                    elif inner.startswith("NONE"):
                        current["state"] = "NONE"
                    else:
                        current["state"] = inner.split("(", 1)[0].split(",", 1)[0]
            elif stripped.startswith("state=null"):
                current["state"] = None
            elif stripped == "metadata:":
                in_metadata = True
            elif stripped.startswith("metadata="):
                # metadata=null or metadata=Metadata { ... }
                val = stripped.split("=", 1)[1].strip()
                if val == "null":
                    current["metadata_raw"] = None
            elif in_metadata:
                # Under "metadata:" block, look for android.media.metadata.* keys
                # Lines often indented further. Try to pick key=value.
                if "=" in stripped:
                    k, v = stripped.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    # Shorten common keys
                    if "TITLE" in k.upper():
                        metadata_buf["title"] = v
                    elif "ARTIST" in k.upper():
                        metadata_buf["artist"] = v
                    elif "ALBUM" in k.upper() and "ART" not in k.upper():
                        metadata_buf["album"] = v
                    elif "DURATION" in k.upper():
                        metadata_buf["duration"] = v

    # Flush last session
    if current is not None:
        current["metadata"] = metadata_buf
        sessions.append(current)

    # Stats
    active_sessions = [s for s in sessions if s.get("active")]
    playing_sessions = [
        s for s in sessions if (s.get("state") == "PLAYING")
    ]

    lines = [
        f"🎵 {len(sessions)} media sessions "
        f"({len(active_sessions)} active, {len(playing_sessions)} playing):"
    ]
    for sess in sessions[:10]:
        mark = "▶" if sess.get("state") == "PLAYING" else (
            "⏸" if sess.get("state") == "PAUSED" else " "
        )
        act = "●" if sess.get("active") else "○"
        lines.append(
            f"  {mark} {act}  {sess.get('package', '?'):40s}  "
            f"[{sess.get('state') or '—'}]"
        )

    return _ok(
        "\n".join(lines),
        sessions=sessions,
        count=len(sessions),
        active_count=len(active_sessions),
        playing_count=len(playing_sessions),
    )


def _handle_media_now_playing(serial: Optional[str]) -> Dict[str, Any]:
    """Return info about the currently-playing session (if any)."""
    r = _handle_media_sessions_list(serial)
    if r["status"] != "success":
        return r

    # Prefer PLAYING state, fallback to first active session
    playing = [s for s in r["sessions"] if s.get("state") == "PLAYING"]
    if not playing:
        playing = [s for s in r["sessions"] if s.get("active")]

    if not playing:
        return _ok(
            "🎵 nothing is playing",
            playing=None,
            sessions_count=r["count"],
        )

    sess = playing[0]
    md = sess.get("metadata") or {}
    title = md.get("title") or "?"
    artist = md.get("artist") or "?"
    album = md.get("album") or ""

    summary = (
        f"🎵 now playing in {sess.get('package')}\n"
        f"   title:  {title}\n"
        f"   artist: {artist}\n"
        f"   album:  {album or '—'}\n"
        f"   state:  {sess.get('state')}"
    )
    return _ok(
        summary,
        playing=sess,
        package=sess.get("package"),
        title=title,
        artist=artist,
        album=album,
        state=sess.get("state"),
    )



# =============================================================================
# 🔔  Notification Pipeline  (Frontier #11)
# =============================================================================
#
# Agents gain read/write control over the Android notification manager: list
# active notifications, post their own, snooze/unsnooze, manage DND (zen
# mode), control per-package bypass, and query stats.
#
# APIs harnessed (no root, no NotificationListenerService needed):
#
#   cmd notification list                  — all active notification keys
#   cmd notification get <key>             — full NotificationRecord dump
#   cmd notification snooze --for <ms> <key>
#   cmd notification unsnooze <key>
#   cmd notification post [flags] <tag> <text>
#   cmd notification set_dnd [on|off|priority|alarms|all|none]
#   cmd notification allow_dnd <pkg>
#   cmd notification disallow_dnd <pkg>
#   settings get global zen_mode
#   dumpsys notification                    — full state (parsed for stats)
#
# Key gotcha: notification keys like 0|com.shell|2020|tag|2000 contain `|`
# pipes — shell splits on them. Every call must wrap the key in single
# quotes inside the double-quoted shell arg.

# Zen / DND modes recognized by `cmd notification set_dnd`
DND_MODES = {
    "off":      "off",       # zen_mode=0, allow everything
    "on":       "on",        # alias for priority
    "none":     "none",      # zen_mode=2, allow nothing
    "priority": "priority",  # zen_mode=1, priority only
    "alarms":   "alarms",    # zen_mode=3, alarms only
    "all":      "all",       # alias for off
}

# Post styles supported by `cmd notification post -S <style>`
POST_STYLES = {"bigtext", "bigpicture", "inbox", "messaging", "media"}


def _shq(s: str) -> str:
    """Shell-quote — wrap in single quotes, escape embedded quotes."""
    return "'" + s.replace("'", "'\\''") + "'"


def _parse_notif_key(raw: str) -> Dict[str, Any]:
    """Split a notification key into its 5 parts.

    Format: userId|package|id|tag|uid
    Example: 0|com.google.android.gm|12345|null|10123
    """
    parts = raw.split("|")
    if len(parts) < 5:
        return {"raw": raw}
    return {
        "raw":     raw,
        "user_id": parts[0],
        "package": parts[1],
        "id":      parts[2],
        "tag":     parts[3] if parts[3] != "null" else None,
        "uid":     parts[4],
    }


def _handle_notifications_list(serial: Optional[str]) -> Dict[str, Any]:
    """List all currently posted notifications with parsed keys."""
    r = _run(["shell", "cmd", "notification", "list"],
             serial=serial, timeout=10)
    if r["returncode"] != 0:
        return _err(f"notification list failed: {r['stderr'][:120]}")

    notifications = []
    for line in (r["stdout"] or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        notifications.append(_parse_notif_key(line))

    by_pkg: Dict[str, int] = {}
    for n in notifications:
        pkg = n.get("package", "?")
        by_pkg[pkg] = by_pkg.get(pkg, 0) + 1

    lines = [f"🔔 {len(notifications)} active notifications across "
             f"{len(by_pkg)} apps:"]
    for pkg, count in sorted(by_pkg.items(), key=lambda kv: -kv[1])[:15]:
        lines.append(f"  {count:3d}  {pkg}")
    if len(by_pkg) > 15:
        lines.append(f"  ... and {len(by_pkg) - 15} more apps")

    return _ok(
        "\n".join(lines),
        notifications=notifications,
        count=len(notifications),
        by_package=by_pkg,
    )


def _handle_notifications_get(
    key: str, serial: Optional[str]
) -> Dict[str, Any]:
    """Fetch full details of a specific notification by key."""
    if not key:
        return _err("notification_key required. "
                    "Use action='notifications_list' first.")
    if "|" not in key:
        return _err(f"invalid notification key: {key!r} "
                    f"(expected userId|pkg|id|tag|uid)")

    r = _run(["shell", f"cmd notification get '{key}'"],
             serial=serial, timeout=10)
    if r["returncode"] != 0:
        return _err(f"notification get failed: {r['stderr'][:120]}")

    raw = r["stdout"] or ""
    if not raw.strip() or "not found" in raw.lower():
        return _err(f"notification not found: {key}")

    parsed = _parse_notif_key(key)
    details: Dict[str, Any] = {"key": key, **parsed}

    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("android.title="):
            details["title_info"] = line.split("=", 1)[1]
        elif line.startswith("android.text="):
            details["text_info"] = line.split("=", 1)[1]
        elif line.startswith("android.subText="):
            details["subtext_info"] = line.split("=", 1)[1]
        elif line.startswith("pri="):
            details["priority"] = line.split("=", 1)[1]
        elif line.startswith("flags="):
            details["flags"] = line.split("=", 1)[1]
        elif "channel=" in line and "NotificationChannel" not in line:
            after = line.split("channel=", 1)[1]
            details.setdefault("channel", after.split()[0] if after else None)
        elif line.startswith("importance="):
            details["importance"] = line.split("=", 1)[1].split()[0]

    summary = (
        f"🔔 {parsed.get('package')} (id={parsed.get('id')})\n"
        f"  channel:    {details.get('channel', '?')}\n"
        f"  priority:   {details.get('priority', '?')}\n"
        f"  importance: {details.get('importance', '?')}\n"
        f"  flags:      {details.get('flags', '?')}"
    )
    return _ok(summary, raw_dump=raw[:3000], **details)


def _handle_notifications_snooze(
    key: str, duration_ms: int, serial: Optional[str]
) -> Dict[str, Any]:
    """Snooze a notification for N milliseconds."""
    if not key or "|" not in key:
        return _err("valid notification key required")
    if duration_ms < 100 or duration_ms > 7 * 24 * 3600 * 1000:
        return _err("duration_ms must be 100..604800000 (1 week)")

    r = _run(
        ["shell", f"cmd notification snooze --for {duration_ms} '{key}'"],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"snooze failed: {r['stderr'][:120]}")

    return _ok(
        f"🔔 snoozed {key} for {duration_ms}ms "
        f"({duration_ms/1000:.1f}s)",
        key=key,
        duration_ms=duration_ms,
        output=r["stdout"],
    )


def _handle_notifications_unsnooze(
    key: str, serial: Optional[str]
) -> Dict[str, Any]:
    """Unsnooze a previously snoozed notification."""
    if not key or "|" not in key:
        return _err("valid notification key required")
    r = _run(
        ["shell", f"cmd notification unsnooze '{key}'"],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"unsnooze failed: {r['stderr'][:120]}")
    return _ok(f"🔔 unsnoozed {key}", key=key)


def _handle_notifications_post(
    title: Optional[str],
    text: str,
    tag: str,
    style: Optional[str],
    serial: Optional[str],
) -> Dict[str, Any]:
    """Post a notification via shell. Appears under com.android.shell."""
    if not text:
        return _err("text required (the notification body)")
    if not tag:
        tag = f"strands_adb_{int(time.time())}"

    cmd_parts = ["cmd", "notification", "post"]
    if title:
        cmd_parts += ["-t", _shq(title)]
    if style:
        if style not in POST_STYLES:
            return _err(
                f"unknown style '{style}'. Valid: {sorted(POST_STYLES)}"
            )
        cmd_parts += ["-S", style]
    cmd_parts += [_shq(tag), _shq(text)]

    r = _run(["shell", " ".join(cmd_parts)], serial=serial, timeout=10)
    if r["returncode"] != 0:
        return _err(f"post failed: {r['stderr'][:120]}")

    return _ok(
        f"🔔 posted notification (tag={tag})",
        tag=tag,
        title=title,
        body_text=text,
        style=style,
        output=r["stdout"][:500],
    )


def _handle_notifications_set_dnd(
    mode: str, serial: Optional[str]
) -> Dict[str, Any]:
    """Set Do Not Disturb zen mode."""
    if mode not in DND_MODES:
        return _err(
            f"unknown DND mode '{mode}'. Valid: {sorted(DND_MODES.keys())}"
        )

    r = _run(
        ["shell", "cmd", "notification", "set_dnd", DND_MODES[mode]],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"set_dnd failed: {r['stderr'][:120]}")

    zen_r = _run(
        ["shell", "settings", "get", "global", "zen_mode"],
        serial=serial, timeout=5,
    )
    zen = (zen_r["stdout"] or "").strip() if zen_r["returncode"] == 0 else "?"

    return _ok(
        f"🔔 DND set to '{mode}' (zen_mode={zen})",
        mode=mode,
        zen_mode=zen,
    )


def _handle_notifications_dnd_package(
    package: str, allow: bool, serial: Optional[str]
) -> Dict[str, Any]:
    """Allow or disallow a package to bypass DND."""
    if not package:
        return _err("package required")

    verb = "allow_dnd" if allow else "disallow_dnd"
    r = _run(
        ["shell", "cmd", "notification", verb, package],
        serial=serial, timeout=10,
    )
    if r["returncode"] != 0:
        return _err(f"{verb} failed: {r['stderr'][:120]}")

    action_verb = ("allowed to bypass DND" if allow
                   else "blocked from bypassing DND")
    return _ok(f"🔔 {package} {action_verb}",
               package=package, allow=allow)


def _handle_notifications_stats(serial: Optional[str]) -> Dict[str, Any]:
    """Snapshot of notification system state (counts, DND, bans)."""
    list_r = _run(
        ["shell", "cmd", "notification", "list"],
        serial=serial, timeout=10,
    )
    active_count = 0
    if list_r["returncode"] == 0:
        active_count = len([
            ln for ln in (list_r["stdout"] or "").splitlines() if "|" in ln
        ])

    zen_r = _run(
        ["shell", "settings", "get", "global", "zen_mode"],
        serial=serial, timeout=5,
    )
    zen = (zen_r["stdout"] or "").strip() if zen_r["returncode"] == 0 else "?"
    zen_name = {"0": "off", "1": "priority", "2": "none",
                "3": "alarms"}.get(zen, f"unknown({zen})")

    dump_r = _run(
        ["shell", "dumpsys", "notification"],
        serial=serial, timeout=15,
    )
    banned_count = recordcount = 0
    if dump_r["returncode"] == 0:
        out = dump_r["stdout"] or ""
        banned_count = out.count('"banned":true')
        recordcount = out.count("NotificationRecord(")

    lines = [
        "🔔 Notification system stats:",
        f"  active notifications:     {active_count}",
        f"  NotificationRecords:      {recordcount}",
        f"  DND / zen_mode:           {zen_name} ({zen})",
        f"  package bans (parsed):    {banned_count}",
    ]
    return _ok(
        "\n".join(lines),
        active_count=active_count,
        record_count=recordcount,
        zen_mode=zen,
        zen_mode_name=zen_name,
        banned_count=banned_count,
    )



ACTIONS = {
    # device
    "list_devices", "select_device", "device_info", "battery", "wake", "unlock",
    # shell
    "shell",
    # ui
    "tap", "swipe", "type", "key", "back", "home", "recent",
    # screen
    "screenshot", "ui_dump",
    # apps
    "list_packages", "launch", "kill", "install", "uninstall",
    "clear_data", "current_app",
    # files
    "push", "pull", "ls",
    # intent
    "open_url", "share_text", "start_activity",
    # notif
    "notifications",
    # logs
    "logcat",
    # smart layer
    "notifications_parsed", "ui_find", "smart_tap",
    "sensors", "thermals", "wifi_info",
    # media + comms
    "screen_record", "screen_record_start", "screen_record_stop", "screen_record_status", "dial", "sms_compose", "media", "volume",
    # camera (v0.4.0)
    "camera_photo", "camera_video",
    # logcat stream (v0.5.0)
    "log_stream_start", "log_stream_stop", "log_stream_status",
    # settings mutation (v0.6.0)
    "setting_get", "setting_put", "setting_delete", "setting_list",
    "setting_dump", "set_ringer", "set_brightness", "set_bluetooth",
    "set_airplane_mode",
    # UI query DSL (v0.7.0)
    "ui_find", "ui_tap_by", "ui_wait_for",
    # Screen frames / CV (v0.8.0)
    "screen_frames", "video_frames",
    # Gesture streaming (v0.9.0)
    "gesture_stream", "gesture_long_press", "gesture_path", "gesture_pinch",
    # Accessibility / ATC (v0.10.0)
    "accessibility_list", "accessibility_toggle_service",
    "accessibility_system_action", "accessibility_captions",
    "accessibility_magnification", "accessibility_font_scale",
    "accessibility_status",
    # Notification pipeline (v0.11.0)
    "notifications_list", "notifications_get", "notifications_snooze",
    "notifications_unsnooze", "notifications_post", "notifications_set_dnd",
    "notifications_dnd_package", "notifications_stats",
    # Media session (v0.12.0)
    "media_dispatch", "media_volume_get", "media_volume_set",
    "media_volume_adjust", "media_sessions_list", "media_now_playing",
    # Connectivity (v0.13.0)
    "wifi_status", "wifi_enable", "wifi_scan",
    "wifi_list_saved", "wifi_connect", "wifi_forget",
    "bt_status", "bt_enable",
    "airplane_mode_get", "airplane_mode_set",
    # Sensor feeds (v0.14.0)
    "sensors_list", "sensors_recent", "sensor_get",
    # Power & battery (v0.15.0)
    "power_status", "power_thermal",
    "power_consumers", "power_subsystems",
    # Security (v0.16.0)
    "security_posture", "security_lock",
    "security_biometrics", "security_vpn",
    # Session lifecycle (v0.17.0)
    "is_locked", "wake", "sleep", "unlock", "keep_awake",
    # UI state machine + forms (v0.18.0)
    "find_element", "find_elements",
    "wait_for_element", "wait_for_gone", "wait_for_idle", "wait_for_window",
    "tap_element", "type_into",
    "app_launch", "foreground_info",
}


@tool
def adb(
    action: str,
    serial: Optional[str] = None,
    command: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    x1: Optional[int] = None,
    y1: Optional[int] = None,
    x2: Optional[int] = None,
    y2: Optional[int] = None,
    duration_ms: int = 300,
    text: Optional[str] = None,
    key: Optional[str] = None,
    output_path: Optional[str] = None,
    return_base64: bool = False,
    include_image: bool = True,
    package: Optional[str] = None,
    apk_path: Optional[str] = None,
    local: Optional[str] = None,
    remote: Optional[str] = None,
    url: Optional[str] = None,
    component: Optional[str] = None,
    extras: Optional[Dict[str, str]] = None,
    filter_text: Optional[str] = None,
    lines: int = 200,
    pin: Optional[str] = None,
    timeout: int = 30,
    # Smart UI
    resource_id: Optional[str] = None,
    desc_filter: Optional[str] = None,
    # Dial / SMS
    phone: Optional[str] = None,
    call_now: bool = False,
    send: bool = False,
    # Media
    media_action: Optional[str] = None,
    volume_direction: Optional[str] = None,
    # Screen record
    duration_sec: int = 10,
    # Camera (v0.4.0)
    facing: str = "back",
    auto_pull: bool = True,
    camera_timeout: int = 15,
    # Logcat stream (v0.5.0)
    log_filters: Optional[List[str]] = None,
    # Settings mutation (v0.6.0)
    namespace: Optional[str] = None,
    setting_key: Optional[str] = None,
    setting_value: Optional[Any] = None,
    auto_brightness: Optional[bool] = None,
    # UI query DSL (v0.7.0)
    class_name: Optional[str] = None,
    clickable_filter: Optional[bool] = None,
    scrollable_filter: Optional[bool] = None,
    ui_index: int = 0,
    ui_timeout: float = 5.0,
    ui_poll_interval: float = 0.5,
    # Screen frames / CV (v0.8.0)
    frames_n: int = 5,
    frames_interval: float = 0.5,
    frames_output_dir: Optional[str] = None,
    # Gesture streaming (v0.9.0)
    gesture_points: Optional[List[List[float]]] = None,
    gesture_hold_ms: int = 500,
    gesture_step_delay_ms: int = 30,
    gesture_shape: str = "line_h",
    gesture_size: int = 300,
    gesture_steps: int = 20,
    # Accessibility / ATC (v0.10.0)
    a11y_service: Optional[str] = None,
    a11y_enable: bool = True,
    a11y_system_action: Optional[str] = None,
    a11y_font_scale: float = 1.0,
    # Notification pipeline (v0.11.0)
    notification_key: Optional[str] = None,
    notification_duration_ms: int = 60_000,
    notification_title: Optional[str] = None,
    notification_text: Optional[str] = None,
    notification_tag: str = "strands_adb",
    notification_style: Optional[str] = None,
    notification_dnd_mode: str = "off",
    notification_package: Optional[str] = None,
    notification_allow: bool = True,
    # Media session (v0.12.0)
    media_key: Optional[str] = None,
    media_stream: str = "music",
    media_volume_index: int = 0,
    media_volume_direction: str = "raise",
    media_volume_show_ui: bool = False,
    # Connectivity (v0.13.0)
    wifi_enabled: bool = True,
    wifi_ssid: Optional[str] = None,
    wifi_security: str = "wpa2",
    wifi_passphrase: Optional[str] = None,
    wifi_network_id: int = 0,
    wifi_scan_wait_sec: float = 4.0,
    bt_enabled: bool = True,
    airplane_enabled: bool = False,
    # Sensor feeds (v0.14.0)
    sensor_query: Any = None,
    # Power & battery (v0.15.0)
    power_top: int = 20,
    # Session lifecycle (v0.17.0)  (pin already declared above)
    keep_awake_enabled: bool = False,
    # UI state machine + forms (v0.18.0)
    ui_text: Optional[str] = None,
    ui_text_contains: Optional[str] = None,
    ui_content_desc: Optional[str] = None,
    ui_content_desc_contains: Optional[str] = None,
    ui_resource_id: Optional[str] = None,
    ui_resource_id_contains: Optional[str] = None,
    ui_class_name: Optional[str] = None,
    ui_package_sel: Optional[str] = None,
    ui_clickable: Optional[bool] = None,
    ui_scrollable: Optional[bool] = None,
    ui_focusable: Optional[bool] = None,
    ui_checked: Optional[bool] = None,
    ui_enabled: Optional[bool] = None,
    ui_instance: int = 0,
    ui_limit: int = 50,
    # ui_timeout + ui_poll_interval already declared above (v0.7.0)
    ui_quiet_ms: int = 500,
    ui_window_package: Optional[str] = None,
    ui_window_activity: Optional[str] = None,
    ui_window_contains: Optional[str] = None,
    ui_input_text: str = "",
    ui_clear: bool = True,
    ui_dismiss_keyboard: bool = True,
    ui_package_arg: Optional[str] = None,
    ui_app_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    🤖 Control an Android device via adb.

    One tool, many actions. Dispatches commands through the Android Debug
    Bridge (adb). Supports device management, UI automation (tap/swipe/type),
    screenshots, app control, file transfer, intents, notifications, and
    logcat.

    Args:
        action: What to do. One of:
            Device:  list_devices, select_device, device_info, battery,
                     wake, unlock
            Shell:   shell (needs command)
            UI:      tap (x,y), swipe (x1,y1,x2,y2,duration_ms),
                     type (text), key (key), back, home, recent
            Screen:  screenshot (output_path, return_base64), ui_dump
            Apps:    list_packages (filter_text), launch (package),
                     kill (package), install (apk_path),
                     uninstall (package), clear_data (package), current_app
            Files:   push (local, remote), pull (remote, local),
                     ls (remote)
            Intent:  open_url (url), share_text (text),
                     start_activity (component, extras)
            Notif:   notifications
            Logs:    logcat (filter_text, lines)
            Camera:  camera_photo (facing="back"|"front", auto_pull, output_path),
                     camera_video (duration_sec, facing, output_path)
            Stream:  log_stream_start (filters), log_stream_stop,
                     log_stream_status — pipes logcat → devduck event_bus
            UI/forms: find_element / find_elements (ui_text=..., ui_resource_id=...,
                      ui_content_desc=..., ui_class_name=..., ui_clickable=...)
                      → element attrs w/ bounds+center. Selector is AND of all
                      fields. Use ui_instance=N for Nth match.
                      wait_for_element / wait_for_gone / wait_for_idle /
                      wait_for_window → block until condition met (ui_timeout
                      default 10s, poll every ui_poll_interval=0.3s).
                      tap_element(...) → find + tap center in one call.
                      type_into(ui_input_text='hello', ui_clear=True, ...)
                      → tap to focus + ctrl-a+del + `input text` + back.
                      app_launch(ui_app_name='gmail') → fuzzy resolve to
                      com.google.android.gm via package aliases + scan.
                      Pass ui_package_arg='com.exact.pkg' to skip fuzzy.
                      foreground_info → {package, activity} of top window.
            Session: is_locked → {locked, awake, trust_state, wakefulness}.
                     wake / sleep → screen on / off (no unlock on wake).
                     unlock(pin='1234') → wakes, detects bouncer type,
                     dismisses biometric bouncer by tapping fingerprint
                     icon, enters PIN via UI taps (works on Android 16 /
                     AlternateBouncerView), retries on failure. PIN falls
                     back to ADB_DEVICE_PIN env var if not passed.
                     keep_awake(keep_awake_enabled=True) → toggles
                     Settings.Global.stay_on_while_plugged_in.
            Security: security_posture → device-wide snapshot (verified
                      boot, bootloader lock, SELinux, encryption, dev
                      options, ADB, Play Protect). Returns
                      strong_posture bool + warnings list.
                      security_lock → lock screen config: credential
                      type ('none'|'pattern'|'pin'|'password'|
                      'password_or_pin'|'managed'), device_locked,
                      quality score, per-user profiles.
                      security_biometrics → fingerprint/face/iris sensor
                      inventory with modality bitmask decoded, strength
                      class (strong/weak/convenience), state.
                      security_vpn → active VPN tunnels via connectivity
                      Transports=VPN. Returns interface name + caps.
            Power: power_status → battery level, voltage, temperature,
                   charging state, health, plug source.
                   power_thermal → overall thermal throttling status +
                   per-zone temperatures (battery/skin/CPU big/mid/little/
                   TPU/GPU etc.) from `dumpsys thermalservice`.
                   power_consumers (power_top=N) → top N UIDs by mAh
                   drained since last unplug, with package names
                   resolved and per-subsystem breakdown (cpu, screen,
                   camera, mobile_radio, wifi, wakelock, …).
                   power_subsystems → global power breakdown by
                   subsystem (screen/cpu/cell/wifi/gnss/camera/ambient_
                   display/etc.) from batterystats 'Estimated power use'.
            Sensors: sensors_list → all 40+ sensors with rates, vendor,
                     wake-up, reporting mode. Grouped motion/env/composite.
                     sensors_recent → latest events across all active
                     sensors (~10 events each), with labeled axes.
                     sensor_get (sensor_query='accelerometer'|'gyro'|
                     'light'|'proximity'|'pressure'|'gravity'|'rotation'|
                     ...|type_id int) → latest reading with semantic
                     labels ({x,y,z} for accel, {lux} for light, etc.).
            Connect: wifi_status → enabled/connected/ssid/rssi/frequency.
                     wifi_enable (wifi_enabled=True|False).
                     wifi_scan (wifi_scan_wait_sec=4.0) → list of {bssid,
                     ssid, rssi, frequency, band, security, flags}.
                     wifi_list_saved → saved networks {network_id, ssid,
                     security_types}.
                     wifi_connect (wifi_ssid=..., wifi_security='wpa2'|
                     'wpa3'|'wep'|'open'|'owe', wifi_passphrase=...)
                     → connect + save.
                     wifi_forget (wifi_network_id=N) → remove saved.
                     bt_status → {enabled, name, address, state,
                     bonded_count, discovering, connection_state}.
                     bt_enable (bt_enabled=True|False).
                     airplane_mode_get → {enabled}.
                     airplane_mode_set (airplane_enabled=True|False).
            Media:   media_dispatch (media_key='play-pause'|'play'|'pause'|'next'|
                     'previous'|'stop'|'rewind'|'fast-forward'|'mute'|'headsethook')
                     → global media key (works across Spotify/YouTube/Music/etc).
                     media_volume_get (media_stream='music'|'ring'|'alarm'|'voice'
                     |'notification'|'accessibility'|...) → current volume.
                     media_volume_set (media_stream=..., media_volume_index=0..max,
                     media_volume_show_ui=True) → set volume to exact index.
                     media_volume_adjust (media_stream=..., media_volume_direction=
                     'up'|'down'|'same') → bump volume one step.
                     media_sessions_list → all sessions with active/state.
                     media_now_playing → currently playing session + metadata.
            Notif:   notifications_list → all active + per-pkg counts.
                     notifications_get (notification_key='0|pkg|id|tag|uid').
                     notifications_snooze / notifications_unsnooze.
                     notifications_post (notification_title=..., notification_text=...,
                     notification_tag=..., notification_style=bigtext|bigpicture|
                     inbox|messaging|media).
                     notifications_set_dnd (notification_dnd_mode=off|on|none|
                     priority|alarms|all).
                     notifications_dnd_package (notification_package=com.foo,
                     notification_allow=True).
                     notifications_stats → counts, zen mode, bans.
            A11y:    accessibility_list → installed + enabled services.
                     accessibility_toggle_service (a11y_service='talkback',
                     a11y_enable=True) → toggle by alias or full component.
                     accessibility_system_action (a11y_system_action='home'|
                     'back'|'notifications'|'recents'|'quick_settings'|'power_dialog'|
                     'screenshot'|'lock_screen'|…) → invoke global system action.
                     accessibility_captions (a11y_enable=bool) → system captions.
                     accessibility_magnification (a11y_enable=bool) → zoom.
                     accessibility_font_scale (a11y_font_scale=1.3) → text size.
                     accessibility_status → snapshot of all a11y settings.
            Gesture: gesture_stream (gesture_points=[[x,y],...], gesture_step_delay_ms=30)
                     → streams motionevent DOWN, N×MOVE, UP. Arbitrary paths.
                     gesture_long_press (x, y, gesture_hold_ms=500)
                     → DOWN → hold → UP. For context menus.
                     gesture_path (x, y, gesture_shape=line_h|line_v|circle|arc|
                     zigzag|square, gesture_size, gesture_steps)
                     → high-level path DSL.
                     gesture_pinch → documented stub; multi-touch requires root.
            Frames:  screen_frames (frames_n=5, frames_interval=0.5, output_path=dir)
                     → N live screencaps as image blocks.
                     video_frames (output_path=<mp4>, frames_n=5)
                     → extract N evenly-spaced frames from video via ffmpeg.
                     Both return Converse image blocks so agent SEES pixels.
            UI DSL:  ui_find, ui_tap_by, ui_wait_for — filters: text, resource_id,
                     class_name, desc_filter (content-desc), clickable_filter,
                     scrollable_filter, package. Matchers: "foo" substring,
                     "=foo" exact, "^regex" regex. ui_tap_by defaults to
                     clickable=True; ui_wait_for takes ui_timeout / ui_poll_interval.
            Settings: setting_get/put/delete/list (namespace, setting_key, setting_value),
                      setting_dump (full snapshot),
                      set_ringer (setting_value="normal"|"silent"|"vibrate"),
                      set_brightness (setting_value=0..255, auto_brightness=True/False),
                      set_bluetooth (setting_value=True/False),
                      set_airplane_mode (setting_value=True/False, flag only)
        serial: Device serial (overrides selected). Use list_devices first.
        command: Shell command string for action='shell'.
        x, y: Tap coords.
        x1, y1, x2, y2, duration_ms: Swipe coords + duration.
        text: Text to type or share.
        key: Key name (back, home, enter, volume_up, etc.) or raw keycode.
        output_path: Screenshot output path (default /tmp/adb_screenshot_*.png).
        return_base64: If True, include base64 of screenshot in response.
        include_image: If True (default), include a Converse API image block
            so the agent can visually see the screen (same format as
            strands_tools.image_reader). Set False for pure text/path response.
        package: Android package name (e.g. com.whatsapp).
        apk_path: Local APK path for install.
        local, remote: File paths for push/pull.
        url: URL to open.
        component: Activity component (e.g. com.app/.MainActivity).
        extras: Intent extras dict.
        filter_text: Filter for list_packages or logcat tag.
        lines: Logcat line count.
        pin: PIN for unlock (optional).
        timeout: Command timeout in seconds (default 30).

    Returns:
        Dict with status ("success"|"error") and content list. Some actions
        attach extra fields (devices, packages, path, xml, etc.).

    Examples:
        adb(action="list_devices")
        adb(action="screenshot", output_path="/tmp/shot.png")
        adb(action="tap", x=540, y=1200)
        adb(action="type", text="hello world")
        adb(action="launch", package="com.whatsapp")
        adb(action="open_url", url="https://github.com")
        adb(action="shell", command="dumpsys battery")
    """
    # 🎬 strands-adb recorder hook (non-intrusive)
    try:
        from strands_adb.recorder import _STATE as _REC_STATE, _json_safe as _rec_json_safe
        if _REC_STATE.get("recording") and _REC_STATE.get("mode") == "agent":
            _REC_SKIP = {
                "list_devices", "device_info", "battery", "screenshot", "ui_dump",
                "list_packages", "current_app", "notifications", "notifications_parsed",
                "logcat", "sensors", "thermals", "wifi_info", "ui_find", "ui_wait_for",
                "screen_frames", "setting_get", "setting_list", "setting_dump", "ls",
                "log_stream_status", "accessibility_list",
            }
            if action not in _REC_SKIP:
                import time as _rec_time
                _rec_kw = {}
                _rec_locals = dict(locals())
                for _k, _v in _rec_locals.items():
                    if _k.startswith("_rec") or _k in ("action",) or _v is None:
                        continue
                    if _rec_json_safe(_v):
                        _rec_kw[_k] = _v
                _REC_STATE["events"].append({"ts": _rec_time.time(), "action": action, "kwargs": _rec_kw})
    except Exception:
        pass

    if action not in ACTIONS:
        return _err(
            f"unknown action: {action}. Valid: {', '.join(sorted(ACTIONS))}"
        )

    try:
        if action == "list_devices":
            return _handle_list_devices()
        if action == "select_device":
            return _handle_select_device(serial or "")
        if action == "device_info":
            return _handle_device_info(serial)
        if action == "battery":
            return _handle_battery(serial)

        if action == "shell":
            return _handle_shell(command or "", serial, timeout)
        if action == "tap":
            if x is None or y is None:
                return _err("tap requires x and y")
            return _handle_tap(int(x), int(y), serial)
        if action == "swipe":
            if None in (x1, y1, x2, y2):
                return _err("swipe requires x1,y1,x2,y2")
            return _handle_swipe(int(x1), int(y1), int(x2), int(y2), duration_ms, serial)
        if action == "type":
            return _handle_type(text or "", serial)
        if action == "key":
            return _handle_key(key or "", serial)
        if action == "back":
            return _handle_key("back", serial)
        if action == "home":
            return _handle_key("home", serial)
        if action == "recent":
            return _handle_key("recent", serial)
        if action == "screenshot":
            return _handle_screenshot(
                output_path, serial, return_base64, include_image
            )
        if action == "ui_dump":
            return _handle_ui_dump(serial)
        if action == "list_packages":
            return _handle_list_packages(filter_text, serial)
        if action == "launch":
            return _handle_launch(package or "", serial)
        if action == "kill":
            return _handle_kill(package or "", serial)
        if action == "install":
            return _handle_install(apk_path or "", serial)
        if action == "uninstall":
            return _handle_uninstall(package or "", serial)
        if action == "clear_data":
            return _handle_clear_data(package or "", serial)
        if action == "current_app":
            return _handle_current_app(serial)
        if action == "push":
            return _handle_push(local or "", remote or "", serial)
        if action == "pull":
            return _handle_pull(remote or "", local or "", serial)
        if action == "ls":
            return _handle_ls(remote or "/sdcard", serial)
        if action == "open_url":
            return _handle_open_url(url or "", serial)
        if action == "share_text":
            return _handle_share_text(text or "", serial)
        if action == "start_activity":
            return _handle_start_activity(component or "", serial, extras)
        if action == "notifications":
            return _handle_notifications(serial)
        if action == "logcat":
            return _handle_logcat(filter_text, lines, serial)

        # --- Smart layer ---
        if action == "notifications_parsed":
            return _handle_notifications_parsed(serial)
        # Old ui_find removed — use the richer v0.7.0 ui_find below.
        if action == "smart_tap":
            return _handle_smart_tap_legacy(serial, text, desc_filter, resource_id)
        if action == "sensors":
            return _handle_sensors(serial)
        if action == "thermals":
            return _handle_thermals(serial)
        if action == "wifi_info":
            return _handle_wifi_info(serial)
        if action == "screen_record":
            return _handle_screen_record(duration_sec, output_path, serial)
        if action == "screen_record_start":
            return _handle_screen_record_start(
                serial, output_path,
                bit_rate_mbps=screenrec_bit_rate_mbps,
                size=screenrec_size,
                segment_sec=screenrec_segment_sec,
            )
        if action == "screen_record_stop":
            return _handle_screen_record_stop(serial)
        if action == "screen_record_status":
            return _handle_screen_record_status()
        if action == "camera_photo":
            return _handle_camera_photo(
                output_path, serial, facing, auto_pull,
                include_image, return_base64, camera_timeout,
            )
        if action == "camera_video":
            return _handle_camera_video(
                duration_sec, output_path, serial, facing, auto_pull,
            )
        if action == "log_stream_start":
            return _handle_log_stream_start(serial, log_filters)
        if action == "log_stream_stop":
            return _handle_log_stream_stop()
        if action == "log_stream_status":
            return _handle_log_stream_status()

        # Settings (v0.6.0)
        if action == "setting_get":
            return _handle_setting_get(
                namespace or "system", setting_key or "", serial
            )
        if action == "setting_put":
            return _handle_setting_put(
                namespace or "system", setting_key or "",
                setting_value if setting_value is not None else "", serial
            )
        if action == "setting_delete":
            return _handle_setting_delete(
                namespace or "system", setting_key or "", serial
            )
        if action == "setting_list":
            return _handle_setting_list(
                namespace or "system", filter_text, serial
            )
        if action == "setting_dump":
            return _handle_setting_dump(serial)
        if action == "set_ringer":
            return _handle_set_ringer(setting_value or "normal", serial)
        if action == "set_brightness":
            lv = int(setting_value) if setting_value is not None else 128
            return _handle_set_brightness(lv, auto_brightness, serial)
        if action == "set_bluetooth":
            return _handle_set_bluetooth(
                bool(setting_value) if setting_value is not None else True, serial
            )
        if action == "set_airplane_mode":
            return _handle_set_airplane_mode(
                bool(setting_value) if setting_value is not None else False, serial
            )

        # UI Query DSL (v0.7.0)
        if action == "ui_find":
            return _handle_ui_find(
                serial, text, resource_id, class_name, desc_filter,
                clickable_filter, scrollable_filter, package,
            )
        if action == "ui_tap_by":
            return _handle_ui_tap_by(
                serial, text, resource_id, class_name, desc_filter,
                clickable_filter, package, ui_index,
            )
        if action == "ui_wait_for":
            return _handle_ui_wait_for(
                serial, text, resource_id, class_name, desc_filter,
                clickable_filter, package, ui_timeout, ui_poll_interval,
            )

        # Screen frames / CV (v0.8.0)
        if action == "screen_frames":
            return _handle_screen_frames(
                frames_n, frames_interval, include_image,
                output_path, serial,
            )
        if action == "video_frames":
            if not output_path:
                return _err("video_frames requires output_path=<video_path>")
            return _handle_video_frames(
                output_path, frames_n, include_image,
                frames_output_dir,
            )

        # Gesture streaming (v0.9.0)
        if action == "gesture_stream":
            return _handle_gesture_stream(
                gesture_points or [], gesture_step_delay_ms, serial,
            )
        if action == "gesture_long_press":
            if x is None or y is None:
                return _err("gesture_long_press requires x, y")
            return _handle_gesture_long_press(x, y, gesture_hold_ms, serial)
        if action == "gesture_path":
            if x is None or y is None:
                return _err("gesture_path requires x, y (path anchor)")
            return _handle_gesture_path(
                gesture_shape, x, y, gesture_size, gesture_steps,
                gesture_step_delay_ms, serial,
            )
        if action == "gesture_pinch":
            if x is None or y is None:
                return _err("gesture_pinch requires x, y (pinch center)")
            return _handle_gesture_pinch(x, y, gesture_size, gesture_shape, serial)

        # Accessibility / ATC (v0.10.0)
        if action == "accessibility_list":
            return _handle_accessibility_list(serial)
        if action == "accessibility_toggle_service":
            if not a11y_service:
                return _err("accessibility_toggle_service requires a11y_service=<name|component>")
            return _handle_accessibility_toggle_service(
                a11y_service, a11y_enable, serial,
            )
        if action == "accessibility_system_action":
            return _handle_accessibility_system_action(a11y_system_action, serial)
        if action == "accessibility_captions":
            return _handle_accessibility_captions(a11y_enable, serial)
        if action == "accessibility_magnification":
            return _handle_accessibility_magnification(a11y_enable, serial)
        if action == "accessibility_font_scale":
            return _handle_accessibility_font_scale(a11y_font_scale, serial)
        if action == "accessibility_status":
            return _handle_accessibility_status(serial)

        # Notification pipeline (v0.11.0)
        if action == "notifications_list":
            return _handle_notifications_list(serial)
        if action == "notifications_get":
            return _handle_notifications_get(notification_key, serial)
        if action == "notifications_snooze":
            return _handle_notifications_snooze(
                notification_key, notification_duration_ms, serial,
            )
        if action == "notifications_unsnooze":
            return _handle_notifications_unsnooze(notification_key, serial)
        if action == "notifications_post":
            return _handle_notifications_post(
                notification_title, notification_text, notification_tag,
                notification_style, serial,
            )
        if action == "notifications_set_dnd":
            return _handle_notifications_set_dnd(notification_dnd_mode, serial)
        if action == "notifications_dnd_package":
            if not notification_package:
                return _err("notifications_dnd_package requires notification_package")
            return _handle_notifications_dnd_package(
                notification_package, notification_allow, serial,
            )
        if action == "notifications_stats":
            return _handle_notifications_stats(serial)

        # Media session (v0.12.0)
        if action == "media_dispatch":
            if not media_key:
                return _err("media_dispatch requires media_key (e.g. 'play-pause', 'next')")
            return _handle_media_dispatch(media_key, serial)
        if action == "media_volume_get":
            return _handle_media_volume_get(media_stream, serial)
        if action == "media_volume_set":
            return _handle_media_volume_set(
                media_stream, media_volume_index, media_volume_show_ui, serial,
            )
        if action == "media_volume_adjust":
            return _handle_media_volume_adjust(
                media_stream, media_volume_direction, media_volume_show_ui, serial,
            )
        if action == "media_sessions_list":
            return _handle_media_sessions_list(serial)
        if action == "media_now_playing":
            return _handle_media_now_playing(serial)

        # Connectivity (v0.13.0)
        if action == "wifi_status":
            return _handle_wifi_status(serial)
        if action == "wifi_enable":
            return _handle_wifi_enable(wifi_enabled, serial)
        if action == "wifi_scan":
            return _handle_wifi_scan(serial, wait_sec=wifi_scan_wait_sec)
        if action == "wifi_list_saved":
            return _handle_wifi_list_saved(serial)
        if action == "wifi_connect":
            return _handle_wifi_connect(
                wifi_ssid, wifi_security, wifi_passphrase, serial,
            )
        if action == "wifi_forget":
            return _handle_wifi_forget(wifi_network_id, serial)
        if action == "bt_status":
            return _handle_bt_status(serial)
        if action == "bt_enable":
            return _handle_bt_enable(bt_enabled, serial)
        if action == "airplane_mode_get":
            return _handle_airplane_mode_get(serial)
        if action == "airplane_mode_set":
            return _handle_airplane_mode_set(airplane_enabled, serial)

        # Sensor feeds (v0.14.0)
        if action == "sensors_list":
            return _handle_sensors_list(serial)
        if action == "sensors_recent":
            return _handle_sensors_recent(serial)
        if action == "sensor_get":
            if not sensor_query:
                return _err("sensor_get requires sensor_query (e.g. 'accelerometer', 'gyro', 5)")
            return _handle_sensor_get(sensor_query, serial)

        # Power & battery (v0.15.0)
        if action == "power_status":
            return _handle_power_status(serial)
        if action == "power_thermal":
            return _handle_power_thermal(serial)
        if action == "power_consumers":
            return _handle_power_consumers(power_top, serial)
        if action == "power_subsystems":
            return _handle_power_subsystems(serial)

        # Security (v0.16.0)
        if action == "security_posture":
            return _handle_security_posture(serial)
        if action == "security_lock":
            return _handle_security_lock(serial)
        if action == "security_biometrics":
            return _handle_security_biometrics(serial)
        if action == "security_vpn":
            return _handle_security_vpn(serial)

        # Session lifecycle (v0.17.0)
        if action == "is_locked":
            return _handle_is_locked(serial)
        if action == "wake":
            return _handle_wake(serial)
        if action == "sleep":
            return _handle_sleep(serial)
        if action == "unlock":
            return _handle_unlock(pin, serial)
        if action == "keep_awake":
            return _handle_keep_awake(keep_awake_enabled, serial)

        # UI state machine + forms (v0.18.0)
        _ui_selector_kwargs = dict(
            text=ui_text,
            text_contains=ui_text_contains,
            content_desc=ui_content_desc,
            content_desc_contains=ui_content_desc_contains,
            resource_id=ui_resource_id,
            resource_id_contains=ui_resource_id_contains,
            class_name=ui_class_name,
            package=ui_package_sel,
            clickable=ui_clickable,
            scrollable=ui_scrollable,
            focusable=ui_focusable,
            checked=ui_checked,
            enabled=ui_enabled,
        )
        if action == "find_element":
            return _handle_find_element(serial, instance=ui_instance, **_ui_selector_kwargs)
        if action == "find_elements":
            return _handle_find_elements(serial, limit=ui_limit, **_ui_selector_kwargs)
        if action == "wait_for_element":
            return _handle_wait_for_element(
                serial, timeout=ui_timeout, poll_interval=ui_poll_interval,
                **_ui_selector_kwargs,
            )
        if action == "wait_for_gone":
            return _handle_wait_for_gone(
                serial, timeout=ui_timeout, poll_interval=ui_poll_interval,
                **_ui_selector_kwargs,
            )
        if action == "wait_for_idle":
            return _handle_wait_for_idle(
                serial, timeout=ui_timeout, quiet_ms=ui_quiet_ms,
                poll_interval=ui_poll_interval,
            )
        if action == "wait_for_window":
            return _handle_wait_for_window(
                serial,
                window_package=ui_window_package,
                window_activity=ui_window_activity,
                window_contains=ui_window_contains,
                timeout=ui_timeout, poll_interval=ui_poll_interval,
            )
        if action == "tap_element":
            return _handle_tap_element(serial, instance=ui_instance, **_ui_selector_kwargs)
        if action == "type_into":
            return _handle_type_into(
                serial, input_text=ui_input_text, clear=ui_clear,
                dismiss_keyboard=ui_dismiss_keyboard, instance=ui_instance,
                **_ui_selector_kwargs,
            )
        if action == "app_launch":
            return _handle_app_launch(serial, package=ui_package_arg, app_name=ui_app_name)
        if action == "foreground_info":
            return _handle_foreground_info(serial)
        if action == "dial":
            return _handle_dial(phone or "", call_now, serial)
        if action == "sms_compose":
            return _handle_sms_compose(phone or "", text or "", send, serial)
        if action == "media":
            return _handle_media_control(media_action or "", serial)
        if action == "volume":
            return _handle_volume("", volume_direction or "", serial)

        return _err(f"action not implemented: {action}")
    except ADBError as e:
        logger.error("adb error: %s", e)
        return _err(f"adb error: {e}")
    except Exception as e:  # pragma: no cover
        logger.exception("unexpected error in adb tool")
        return _err(f"unexpected error: {e}")
