# Quickstart

From zero to a phone-controlling agent in under 2 minutes.

---

## The Journey

```mermaid
graph LR
    A["1️⃣ Install"] --> B["2️⃣ Connect"]
    B --> C["3️⃣ Import adb"]
    C --> D["4️⃣ Ask anything"]

    style A fill:#1e3a5f,stroke:#60a5fa,color:#fff
    style B fill:#3DDC84,stroke:#3DDC84,color:#000
    style C fill:#4a1d96,stroke:#a78bfa,color:#fff
    style D fill:#92400e,stroke:#fbbf24,color:#fff
```

## 1. Install

```bash
pip install strands-adb
brew install android-platform-tools   # or apt / pacman / winget
```

## 2. Connect

```bash
adb devices
# 59230DLCH0012Z  device
```

If you see `unauthorized`, accept the trust dialog on the phone.

## 3. Hello, Phone

```python
from strands import Agent
from strands_adb import adb

agent = Agent(tools=[adb])
agent("what's on my phone screen right now?")
```

The agent will:

1. Call `adb(action="screenshot")`
2. Receive a PNG image block (it can literally **see**)
3. Describe what's on screen

## 4. Drive the UI

```python
agent("open whatsapp and tell me the last message")
```

Under the hood:

```mermaid
sequenceDiagram
    participant U as 🧑 You
    participant A as 🤖 Agent
    participant T as 🔧 adb tool
    participant P as 📱 Phone

    U->>A: "open whatsapp & read last msg"
    A->>T: action="launch", package="com.whatsapp"
    T->>P: am start com.whatsapp
    P-->>T: launched
    A->>T: action="screenshot"
    T->>P: screencap -p
    P-->>T: PNG bytes
    T-->>A: image block
    A->>A: (vision) read chat
    A-->>U: "Mom: on my way back, 20m"
```

## 5. Common One-Liners

```python
# Reality check
agent("take a photo with the front camera and describe me")

# Device state
agent("how's my battery? any thermal warnings?")

# Notifications
agent("read me my current notifications")

# Launch app
agent("open spotify and tell me what's playing")

# UI automation
agent("tap the button that says 'Send'")

# Settings mutation
agent("enable airplane mode, I'm about to board")

# Sensors
agent("is the phone face-down right now?")
```

## 6. DevDuck (Recommended)

[DevDuck](https://dev.duck.nyc) is the minimalist agent runtime. With one env var, `adb` becomes a first-class tool:

```bash
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell"
devduck "text mom 'on my way' via whatsapp"
```

→ [DevDuck integration guide](../guide/devduck.md)

## 7. Multi-Device

Target a specific device when you have several connected:

```python
agent("take a screenshot of device 59230DLCH0012Z")

# Or set a default:
import os
os.environ["ADB_SERIAL"] = "59230DLCH0012Z"
```

→ See [Connect a Device](connect.md) for wireless / SSH / multi-device setups.

---

## What's Next

```mermaid
graph LR
    QS["✅ You are here:<br/>Quickstart"] --> V["👁️ Vision"]
    QS --> SM["🎯 Smart Tap"]
    QS --> DD["🦆 DevDuck"]
    QS --> EX["📚 Examples"]

    style QS fill:#3DDC84,stroke:#3DDC84,color:#000
    style V fill:#1e3a5f,stroke:#60a5fa,color:#fff
    style SM fill:#4a1d96,stroke:#a78bfa,color:#fff
    style DD fill:#92400e,stroke:#fbbf24,color:#fff
    style EX fill:#831843,stroke:#f472b6,color:#fff
```

- [**Vision / Screenshots**](../guide/vision.md) — how the agent actually sees
- [**Smart Tap**](../guide/smart-tap.md) — semantic UI automation
- [**DevDuck Integration**](../guide/devduck.md) — 1-line agent runtime
- [**Examples**](../examples/overview.md) — WhatsApp, notifications, autonomous
