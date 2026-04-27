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


def _handle_wake(serial: Optional[str]) -> Dict[str, Any]:
    _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial)
    return _ok("device woken")


def _handle_unlock(serial: Optional[str], pin: Optional[str]) -> Dict[str, Any]:
    _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial)
    time.sleep(0.8)
    _run(["shell", "input", "keyevent", "KEYCODE_MENU"], serial=serial)
    time.sleep(0.8)
    _run(["shell", "input", "swipe", "500", "1500", "500", "500", "200"], serial=serial)
    if pin:
        time.sleep(0.5)
        _run(["shell", "input", "text", pin], serial=serial)
        _run(["shell", "input", "keyevent", "KEYCODE_ENTER"], serial=serial)
    return _ok("unlock attempted")


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
    "screen_record", "dial", "sms_compose", "media", "volume",
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
        if action == "wake":
            return _handle_wake(serial)
        if action == "unlock":
            return _handle_unlock(serial, pin)
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
