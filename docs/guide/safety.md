# Safety

`strands-adb` can do almost anything on the phone. That's the feature. With great power…

---

## The Threat Model

Your agent can:

- **Read** everything on the screen
- **Tap** any UI element
- **Install** / uninstall apps
- **Mutate** system settings (airplane, ringer, brightness)
- **Send** SMS / open dialer
- **Drain** battery (camera, recording)
- **Leak** data (push files off device)

An overly-helpful LLM or an adversarial prompt can misuse this. Defend in depth.

---

## 1. Allowlists

Restrict which packages the agent can install/uninstall:

```python
ALLOWED_INSTALL = {"com.mycompany.testapp"}
ALLOWED_UNINSTALL = {"com.spam.bloatware"}

# In your wrapper / middleware:
if action == "uninstall" and package not in ALLOWED_UNINSTALL:
    raise PermissionError(f"uninstall of {package} not allowed")
```

## 2. Dry-Run Mode

For destructive actions, preview before committing:

```python
adb(action="setting_put",
    namespace="global", key="airplane_mode_on", value="1",
    dry_run=True)
# → shows what WOULD happen, doesn't apply
```

Works for: `install`, `uninstall`, `clear_data`, `setting_put`, `set_airplane_mode`, `set_bluetooth`.

## 3. No Plaintext Secrets

- **Never** store PIN/password in code / env in plaintext.
- `unlock` takes `pin=` at call time — passed once, not persisted.
- Consider a secrets manager (AWS Secrets Manager, 1Password CLI, macOS Keychain) for anything long-lived.

## 4. Snapshot + Restore

Before a session that touches settings:

```python
snapshot = adb(action="setting_dump", namespace="system")

try:
    # agent does stuff
    agent("optimize phone for movie watching")
finally:
    # restore
    for k, v in snapshot["settings"].items():
        adb(action="setting_put", namespace="system", key=k, value=v)
```

## 5. Rate Limiting

Hot loops can drain battery and hit adb rate limits. If you're polling:

```python
import time
while True:
    state = adb(action="sensors")
    # ... do stuff
    time.sleep(1)   # at least 1 Hz
```

## 6. Don't Expose `adb` to the Internet

- Never run `adb -a -P 5037 nodaemon server` on a public IP.
- SSH tunnel only. Or Tailscale. Or WireGuard.
- Treat the adb port like a root shell — because it effectively is one.

## 7. Auditability

Log every action. DevDuck + `strands-adb` emit to stdout + log files by default. For production:

```python
import logging
logging.basicConfig(level=logging.INFO, filename="adb_audit.log")
```

All `adb` invocations are logged at DEBUG with the full command.

## 8. Tool Consent

By default DevDuck runs with `BYPASS_TOOL_CONSENT=true` (no per-tool confirmation). For sensitive deployments, turn it off:

```bash
export BYPASS_TOOL_CONSENT=false
```

The agent will prompt before executing destructive actions.

## 9. Network Isolation

If the phone handles sensitive data, keep it on a VLAN that can only reach your agent host + required internet services.

## 10. User Intent Confirmation

For anything that sends/spends/deletes:

```python
# Don't just "send the text" — confirm
agent("""
draft a text to Mom saying 'love you'.
DO NOT send it. show it to me and wait for explicit confirmation.
""")
```

Then the user reviews, then replies "send".

---

## Known Limits

`strands-adb` does **not** (by design):

- Store credentials
- Bypass screen locks without an explicit PIN
- Enable adb over TCP — user does that
- Grant itself permissions beyond what adb provides

If you need more, add companion APK capabilities ([FRONTIERS.md](https://github.com/cagataycali/strands-adb/blob/main/FRONTIERS.md)) — and review each one carefully.

## What's Next

- [**DevDuck Integration**](devduck.md) — production runtime
- [**Settings**](settings.md) — reversible mutations
- [**API Reference**](../api-reference.md) — full action signatures
