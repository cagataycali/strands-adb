# DevDuck Integration

[DevDuck](https://dev.duck.nyc) is the minimalist, self-adapting agent runtime. `strands-adb` is a first-class tool drop-in — one env var, no code.

---

## 1-Line Setup

```bash
pip install devduck strands-adb
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell"
devduck "take a screenshot of my phone"
```

That's it. DevDuck auto-loads `adb` from `strands_adb`, adds `shell` from `strands_tools`, and you have a phone-aware agent.

## The DEVDUCK_TOOLS Format

```
DEVDUCK_TOOLS="package1:tool1,tool2;package2:tool3"
```

- **`;`** separates packages
- **`,`** separates tools in the same package
- **`:`** separates package name from tools

### Recommended configurations

=== "Minimal phone agent"
    ```bash
    export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell"
    ```

=== "Phone + vision + tasks"
    ```bash
    export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell,file_read,file_write;devduck.tools:tasks,scheduler,notify"
    ```

=== "Full autonomous phone agent"
    ```bash
    export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell,file_read,file_write,use_agent;devduck.tools:tasks,scheduler,ambient_mode,notify,telegram,system_prompt"
    ```

## Persistent via `service`

Install as a systemd / launchd service that auto-restarts:

```bash
devduck service install \
  --name phone-agent \
  --tools "strands_adb:adb;strands_tools:shell" \
  --startup-prompt "monitor notifications, text me on telegram for anything urgent" \
  --env TELEGRAM_BOT_TOKEN=xxx
```

Now DevDuck runs in the background 24/7 on that host, watching the phone.

→ [DevDuck service docs](https://cagataycali.github.io/devduck/guide/self-replication/)

## Logcat → Event Bus

DevDuck has an internal event bus. `strands-adb` pushes logcat events into it:

```python
# In DevDuck:
adb(action="log_stream_start",
    filter="NotificationManagerService",
    topic="phone.notifications")
```

Every new notification appears in the agent's context automatically on the next query.

```mermaid
graph LR
    PHONE["📱 Phone"] -->|logcat| ADB["strands_adb"]
    ADB -->|publish| BUS["🚌 DevDuck event_bus"]
    BUS -->|inject context| AGENT["🤖 DevDuck agent"]
    AGENT -->|react| PHONE

    style AGENT fill:#3DDC84,color:#000
```

## Ambient Mode

DevDuck's ambient mode runs the agent in a background loop. Perfect for phone monitoring:

```bash
export DEVDUCK_AMBIENT_MODE=true
export DEVDUCK_AMBIENT_IDLE_SECONDS=60
devduck "monitor my phone every minute and alert me on anything urgent"
```

Or **autonomous mode**:

```bash
devduck
# then at the prompt:
🦆 auto
🦆 keep my phone at <30°C; drop brightness when it gets hot
```

→ [DevDuck ambient mode docs](https://cagataycali.github.io/devduck/guide/ambient-mode/)

## Scheduler

Cron-style jobs via `scheduler`:

```python
# Inside devduck:
scheduler(action="add",
  name="morning-brief",
  schedule="0 8 * * *",
  prompt="read my notifications and summarize what I missed overnight",
  tools="strands_adb.adb,strands_tools.shell")
```

→ [DevDuck scheduler docs](https://cagataycali.github.io/devduck/guide/self-replication/)

## Remote Phone, Cloud Agent

DevDuck + wireless adb + SSH tunnel = full remote control:

```bash
# on the box with your phone:
ssh -R 5037:localhost:5037 cloud.example.com
adb -a -P 5037 nodaemon server &

# on cloud box:
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell"
devduck "check my phone battery and notifications"
```

→ [SSH / Wireless ADB guide](ssh.md)

## Multi-Device via `identity`

DevDuck's `identity` tool lets you spawn parallel agents per device:

```python
identity(action="fan_out", system_knowledge='''[
  {"identity": "pixel-phone",   "task": "monitor notifications"},
  {"identity": "samsung-tablet", "task": "keep brightness adaptive"}
]''')
```

Each identity runs in parallel with its own `ADB_SERIAL`.

## Example: Complete Setup

```bash
#!/bin/bash
# ~/.devduck_phone_agent.sh
export MODEL_PROVIDER=bedrock
export STRANDS_MODEL_ID="global.anthropic.claude-sonnet-4"
export AWS_BEARER_TOKEN_BEDROCK=xxx
export ADB_SERIAL="192.168.1.42:5555"
export DEVDUCK_TOOLS="strands_adb:adb;strands_tools:shell,file_read,file_write;devduck.tools:tasks,scheduler,ambient_mode,notify,telegram,system_prompt"
export DEVDUCK_AMBIENT_MODE=true
export TELEGRAM_BOT_TOKEN=xxx

devduck "$@"
```

## What's Next

- [**Examples**](../examples/overview.md) — full DevDuck flows
- [**SSH / Wireless ADB**](ssh.md) — remote setups
- [**Safety**](safety.md) — production hardening
