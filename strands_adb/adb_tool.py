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
