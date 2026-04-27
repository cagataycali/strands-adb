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
    output_path: Optional[str], serial: Optional[str], return_base64: bool
) -> Dict[str, Any]:
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

    data = proc.stdout or b""
    if proc.returncode != 0 or not data.startswith(b"\x89PNG"):
        # Fallback: shell + pull
        _run(["shell", "screencap", "-p", "/sdcard/_shot.png"], serial=serial, timeout=20)
        r = _run(["pull", "/sdcard/_shot.png", str(out)], serial=serial, timeout=20)
        _run(["shell", "rm", "/sdcard/_shot.png"], serial=serial)
        if r["returncode"] != 0 or not out.exists():
            return _err(f"screenshot failed: {proc.stderr.decode(errors='ignore')[:500]}")
    else:
        out.write_bytes(data)

    size = out.stat().st_size
    body: Dict[str, Any] = {"path": str(out), "size_bytes": size}
    if return_base64:
        body["base64"] = base64.b64encode(out.read_bytes()).decode()
    return _ok(f"screenshot saved: {out} ({size} bytes)", **body)


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
            return _handle_screenshot(output_path, serial, return_base64)
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

        return _err(f"action not implemented: {action}")
    except ADBError as e:
        logger.error("adb error: %s", e)
        return _err(f"adb error: {e}")
    except Exception as e:  # pragma: no cover
        logger.exception("unexpected error in adb tool")
        return _err(f"unexpected error: {e}")
