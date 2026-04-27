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
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    time.sleep(0.3)
    _run(["shell", "input", "keyevent", "KEYCODE_MENU"], serial=serial)
    time.sleep(0.3)
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


def _handle_ui_find(
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


def _handle_smart_tap(
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
        if action == "ui_find":
            return _handle_ui_find(serial, text, desc_filter, resource_id)
        if action == "smart_tap":
            return _handle_smart_tap(serial, text, desc_filter, resource_id)
        if action == "sensors":
            return _handle_sensors(serial)
        if action == "thermals":
            return _handle_thermals(serial)
        if action == "wifi_info":
            return _handle_wifi_info(serial)
        if action == "screen_record":
            return _handle_screen_record(duration_sec, output_path, serial)
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
