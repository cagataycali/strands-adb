"""
Smart layers on top of raw adb:

- parse_notifications: structured list from dumpsys notification
- parse_ui_dump:       parse uiautomator XML → semantic elements
- find_element_by:     locate by text / resource-id / content-desc
- smart_tap:           tap by label, no coordinates needed
- read_sensor:         latest value from a named sensor
- list_sensors:        structured sensor inventory
- thermals:            parsed thermal zones
- wifi_info:           parsed wifi state
- recents:             parsed recent task stack
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------------
# Notification parsing
# ----------------------------------------------------------------------------

_NOTIF_RE = re.compile(
    r"NotificationRecord\(0x[0-9a-f]+:\s*pkg=(?P<pkg>[^\s]+)\s+"
    r"user=UserHandle\{(?P<user>-?\d+)\}\s+id=(?P<id>-?\d+).*?"
    r"(?=NotificationRecord\(0x|\Z)",
    re.DOTALL,
)
_TITLE_RE = re.compile(r"android\.title=(?:String|SpannableString)\s*\((?P<v>.*?)\)")
_TEXT_RE = re.compile(
    r"android\.text=(?:String|SpannableString)\s*\((?P<v>.*?)\)"
)
_CATEGORY_RE = re.compile(r"category=(?P<v>[\w-]+)")


def parse_notifications(raw: str) -> List[Dict[str, Any]]:
    """Parse dumpsys notification output into structured list."""
    out: List[Dict[str, Any]] = []
    for block in _NOTIF_RE.finditer(raw):
        d = block.groupdict()
        body = block.group(0)
        title_m = _TITLE_RE.search(body)
        text_m = _TEXT_RE.search(body)
        cat_m = _CATEGORY_RE.search(body)
        if not title_m and not text_m:
            continue
        out.append(
            {
                "pkg": d["pkg"],
                "id": int(d["id"]),
                "title": title_m.group("v").strip() if title_m else "",
                "text": text_m.group("v").strip() if text_m else "",
                "category": cat_m.group("v") if cat_m else "",
            }
        )
    # Dedup by (pkg, title, text) — dumpsys repeats the same notif in sections
    seen = set()
    dedup = []
    for n in out:
        key = (n["pkg"], n["title"], n["text"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(n)
    return dedup


# ----------------------------------------------------------------------------
# UI dump parsing (uiautomator XML → semantic elements)
# ----------------------------------------------------------------------------

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _parse_bounds(s: str) -> Optional[Dict[str, int]]:
    m = _BOUNDS_RE.match(s or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return {
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
        "w": x2 - x1, "h": y2 - y1,
    }


def parse_ui_dump(xml: str) -> List[Dict[str, Any]]:
    """Parse uiautomator XML dump → flat list of interactable elements."""
    elements: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return elements

    for node in root.iter("node"):
        attrs = node.attrib
        text = (attrs.get("text") or "").strip()
        desc = (attrs.get("content-desc") or "").strip()
        rid = attrs.get("resource-id") or ""
        klass = attrs.get("class") or ""
        clickable = attrs.get("clickable") == "true"
        focusable = attrs.get("focusable") == "true"
        enabled = attrs.get("enabled") == "true"
        bounds = _parse_bounds(attrs.get("bounds", ""))
        if not bounds or bounds["w"] <= 0 or bounds["h"] <= 0:
            continue
        # Skip elements with no user-visible handle AND not clickable
        if not (text or desc or rid) and not clickable:
            continue
        elements.append(
            {
                "text": text,
                "desc": desc,
                "resource_id": rid,
                "class": klass,
                "clickable": clickable,
                "focusable": focusable,
                "enabled": enabled,
                "bounds": bounds,
            }
        )
    return elements


def find_element(
    elements: List[Dict[str, Any]],
    text: Optional[str] = None,
    desc: Optional[str] = None,
    resource_id: Optional[str] = None,
    partial: bool = True,
    clickable_only: bool = True,
) -> Optional[Dict[str, Any]]:
    """Find first element matching criteria. Case-insensitive."""
    def match(needle: Optional[str], hay: str) -> bool:
        if needle is None:
            return True
        if not hay:
            return False
        n, h = needle.lower(), hay.lower()
        return n in h if partial else n == h

    for el in elements:
        if clickable_only and not el["clickable"]:
            continue
        if not match(text, el["text"]):
            continue
        if not match(desc, el["desc"]):
            continue
        if not match(resource_id, el["resource_id"]):
            continue
        if any([text, desc, resource_id]):
            return el
    return None


# ----------------------------------------------------------------------------
# Sensor parsing
# ----------------------------------------------------------------------------

_SENSOR_HEADER_RE = re.compile(
    r"^(?P<handle>0x[0-9a-f]+)\)\s+(?P<name>.+?)\s+\|\s+"
    r"(?P<vendor>.+?)\s+\|\s+ver:\s*\d+\s+\|\s+type:\s*(?P<type>[^\s|]+)",
    re.MULTILINE,
)

_SENSOR_LAST_RE = re.compile(
    r"(?P<name>[\w\s\-\(\)]+):\s+last\s+\d+\s+events\s*\n"
    r"(?:\s*\d+\s+\(ts=[\d.]+,\s+wall=[\d:.]+\)\s*(?P<vals>[-\d.,\s]+)\n)+",
    re.MULTILINE,
)


def list_sensors(raw: str) -> List[Dict[str, str]]:
    """Parse sensor inventory from dumpsys sensorservice."""
    sensors = []
    for m in _SENSOR_HEADER_RE.finditer(raw):
        sensors.append(
            {
                "handle": m.group("handle"),
                "name": m.group("name").strip(),
                "vendor": m.group("vendor").strip(),
                "type": m.group("type"),
            }
        )
    return sensors


def parse_sensor_last_values(raw: str) -> Dict[str, List[float]]:
    """Extract last-event values per named sensor from dumpsys sensorservice."""
    out: Dict[str, List[float]] = {}
    # Find blocks like "<Name>: last <N> events\n 1 (ts=..., wall=...) v1, v2, v3,"
    pattern = re.compile(
        r"^(?P<name>[\w \-\(\)\.]+?):\s+last\s+\d+\s+events\s*$"
        r"(?P<body>(?:\s+\d+\s+\(ts=[\d.]+,\s+wall=[\d:.]+\)[^\n]*\n)+)",
        re.MULTILINE,
    )
    val_re = re.compile(r"wall=[\d:.]+\)\s*([-\d.,\s]+)")
    for m in pattern.finditer(raw):
        name = m.group("name").strip()
        body = m.group("body")
        last_vals = None
        for vm in val_re.finditer(body):
            nums = [
                float(x) for x in vm.group(1).split(",") if x.strip() and x.strip() != "0.00"
            ] or [
                float(x) for x in vm.group(1).split(",") if x.strip()
            ]
            last_vals = nums
        if last_vals:
            out[name] = last_vals
    return out


# ----------------------------------------------------------------------------
# Thermals
# ----------------------------------------------------------------------------

_TEMP_RE = re.compile(
    r"Temperature\{mValue=(?P<v>-?[\d.]+),\s*mType=(?P<t>-?\d+),"
    r"\s*mName=(?P<name>[^,]+),\s*mStatus=(?P<status>\d+)"
)


def parse_thermals(raw: str) -> List[Dict[str, Any]]:
    """Parse thermalservice output."""
    out = []
    for m in _TEMP_RE.finditer(raw):
        out.append(
            {
                "name": m.group("name").strip(),
                "value": float(m.group("v")),
                "type": int(m.group("t")),
                "status": int(m.group("status")),
            }
        )
    return out


# ----------------------------------------------------------------------------
# Wifi
# ----------------------------------------------------------------------------

def parse_wifi(raw: str) -> Dict[str, Any]:
    """Parse cmd wifi status output."""
    info: Dict[str, Any] = {"connected": False}
    if "not connected" in raw.lower() or "wifi is disabled" in raw.lower():
        return info
    m_ssid = re.search(r'SSID:\s*"([^"]+)"', raw)
    m_bssid = re.search(r"BSSID:\s*([0-9a-f:]+)", raw, re.IGNORECASE)
    m_ip = re.search(r"IP:\s*/?(\d+\.\d+\.\d+\.\d+)", raw)
    m_rssi = re.search(r"RSSI:\s*(-?\d+)", raw)
    m_speed = re.search(r"Link speed:\s*(\d+)Mbps", raw)
    m_freq = re.search(r"Frequency:\s*(\d+)MHz", raw)
    info["connected"] = bool(m_ssid)
    info["ssid"] = m_ssid.group(1) if m_ssid else None
    info["bssid"] = m_bssid.group(1) if m_bssid else None
    info["ip"] = m_ip.group(1) if m_ip else None
    info["rssi"] = int(m_rssi.group(1)) if m_rssi else None
    info["link_speed_mbps"] = int(m_speed.group(1)) if m_speed else None
    info["frequency_mhz"] = int(m_freq.group(1)) if m_freq else None
    return info
