# Architecture

How `strands-adb` is structured internally.

---

## Package Structure

```
strands_adb/
├── __init__.py                  # exports: adb
├── adb_tool.py                  # main @tool dispatch (90+ actions)
└── smart.py                     # smart_tap + UI helpers (XML parsing,
                                 # resource-id / content-desc matching)
```

One tool, one dispatch function, action-specific handlers.

## The Dispatch Pattern

```mermaid
graph TD
    AGENT["🗣️ Strands Agent"] -->|"@tool call: adb(action='screenshot', ...)"| TOOL["strands_adb.adb"]
    TOOL -->|match action| ROUTER{Router}

    ROUTER -->|list_devices| H1["_handle_list_devices"]
    ROUTER -->|screenshot| H2["_handle_screenshot"]
    ROUTER -->|smart_tap| H3["_handle_smart_tap"]
    ROUTER -->|camera_photo| H4["_handle_camera_photo"]
    ROUTER -->|...| HN["_handle_..."]

    H1 --> RUN["_run(args)"]
    H2 --> RUN
    H3 --> RUN
    H4 --> RUN
    HN --> RUN

    RUN -->|subprocess| ADB_BIN["adb binary"]
    ADB_BIN -->|USB/TCP| PHONE["📱 Android device"]
    PHONE -->|stdout| ADB_BIN
    ADB_BIN -->|result| RUN
    RUN -->|format| RESP["{status, content, ...}"]
    RESP --> AGENT

    style TOOL fill:#3DDC84,color:#000
    style PHONE fill:#3DDC84,color:#000
```

## Tool Surface

One `@tool` decorated function exposes all 90+ actions:

```python
@tool
def adb(
    action: str,
    # Device targeting
    serial: Optional[str] = None,
    # Generic params
    x: int = None, y: int = None, text: str = None,
    package: str = None, url: str = None,
    # Screenshot / camera
    output_path: str = None, include_image: bool = True,
    # UI automation
    resource_id: str = None, content_desc: str = None,
    # Sensors / settings
    namespace: str = None, key: str = None, value: str = None,
    # ... (see api-reference.md for full list)
):
    """Android device control via adb."""
    ...
```

The LLM sees one tool with a clearly-documented action enum. Parameters are only required when their respective action needs them.

## The `_run` Core

Every action eventually calls:

```python
def _run(args: List[str], serial=None, timeout=30):
    cmd = [_adb_bin()]
    if serial or _SELECTED_SERIAL:
        cmd += ["-s", serial or _SELECTED_SERIAL]
    cmd += args
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return {"stdout": ..., "stderr": ..., "returncode": ...}
```

- Serial selection priority: explicit arg → `_SELECTED_SERIAL` → `$ADB_SERIAL` → first available device
- Timeout defaults to 30s (configurable)
- Binary path via `$ADB_BIN` env

## The Converse API Bridge

Screenshot + camera actions return a content block compatible with AWS Converse API:

```python
{
    "status": "success",
    "content": [
        {"text": "screenshot saved: /tmp/...png (284512 bytes)"},
        {"image": {"format": "png", "source": {"bytes": b"\x89PNG..."}}},
    ],
    "path": "/tmp/...png",
    "size_bytes": 284512,
}
```

Strands Agent passes this straight into the vision model's context. Same format as `strands_tools.image_reader`.

## Smart Tap Pipeline

`smart.py` handles semantic UI lookup:

```mermaid
sequenceDiagram
    participant T as adb tool
    participant P as Phone
    participant X as XML parser

    T->>P: uiautomator dump /sdcard/_ui.xml
    T->>P: cat /sdcard/_ui.xml
    P-->>T: <hierarchy>...</hierarchy>
    T->>P: rm /sdcard/_ui.xml
    T->>X: parse XML
    X->>X: find nodes matching criteria
    X->>X: priority: resource-id > text > content-desc > partial
    X-->>T: [{text, bounds, ...}]
    T->>T: compute center = ((x1+x2)/2, (y1+y2)/2)
    T->>P: input tap cx cy
```

## Camera Flow

```mermaid
sequenceDiagram
    participant A as Agent
    participant T as adb tool
    participant C as GoogleCamera
    participant D as /sdcard/DCIM

    A->>T: camera_photo, facing=front
    T->>D: ls -t (baseline)
    T->>C: am start STILL_IMAGE_CAMERA
    T->>C: wait 2s for viewfinder
    T->>C: smart_tap "Switch to front camera"
    T->>C: wait 2.5s
    loop retry 3x
        T->>C: tap shutter by resource-id
    end
    T->>D: poll every 500ms (max 15s)
    D-->>T: new IMG_*.jpg
    T->>T: adb pull → /tmp/
    T-->>A: image block (JPEG bytes)
```

## Logcat Streaming

```mermaid
graph LR
    subgraph "main thread"
        AGENT["🤖 Agent"]
    end

    subgraph "background thread"
        TAIL["adb logcat -v<br/>subprocess.Popen"]
        PARSE["parse line"]
        BUFFER["ring buffer"]
    end

    TAIL --> PARSE
    PARSE --> BUFFER
    PARSE -->|publish| BUS["DevDuck event_bus"]
    BUS -->|inject context| AGENT
```

- Streaming is opt-in (`log_stream_start`)
- Line-buffered subprocess
- Parsed into structured events
- Ring buffer (default N=500)
- Each line → published to `phone.logcat` (or custom topic) on the event bus

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `ADB_BIN` | Path to `adb` binary | `adb` (PATH) |
| `ADB_SERIAL` | Default device serial | none |
| (none others required) | | |

## Error Handling

Every handler returns:

```python
# Success
{"status": "success", "content": [{"text": "..."}], ...}

# Error
{"status": "error", "content": [{"text": "descriptive error"}]}
```

Exceptions caught + converted. `ADBError` raised internally for clarity, then caught at tool boundary.

## Testing

```
tests/
├── test_adb.py                 # action-level integration tests
├── test_smart.py               # smart_tap / UI matching
├── test_camera.py              # physical camera (requires device)
└── conftest.py                 # fixtures: mock + real device
```

11/11 tests passing on Pixel 10 Pro (Android 16).

```bash
python tests/test_adb.py
```

## Extending

Adding a new action:

1. Add `_handle_foo(...)` in `adb_tool.py`
2. Add `elif action == "foo":` branch in the dispatch
3. Document parameters in the `@tool` docstring
4. Add a test in `tests/test_adb.py`

That's it — no other wiring needed. The LLM will pick it up automatically once it's in the action list.

## What's Next

- [**API Reference**](api-reference.md) — every action's parameters
- [**FRONTIERS.md**](https://github.com/cagataycali/strands-adb/blob/main/FRONTIERS.md) — open roadmap
- [**Contributing**](https://github.com/cagataycali/strands-adb/blob/main/README.md) — how to add your own actions
