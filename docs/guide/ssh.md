# SSH / Wireless ADB

Run your agent on one machine, control a phone that lives somewhere else.

---

## Use Cases

- Your DevDuck lives on a Jetson / VPS / cloud box
- Your phone is on a desk, on a different network, or in a different room
- You want no-cable phone control (after initial pairing)

## Wireless ADB (same network)

Fast, zero-overhead. Android 11+ has native wireless debugging.

```bash
# On phone: Settings → Developer options → Wireless debugging → enable
# Tap "Pair device with pairing code"
adb pair 192.168.1.42:41337
# enter 6-digit code from phone

adb connect 192.168.1.42:5555
adb devices
# 192.168.1.42:5555  device
```

Persist:

```bash
echo 'alias phone="adb connect 192.168.1.42:5555"' >> ~/.zshrc
```

## SSH Tunnel (remote network)

Phone at home, agent in the cloud:

### On home machine (with phone connected via USB/wireless):

```bash
# Run adb server listening on all interfaces
adb -a -P 5037 nodaemon server &
```

### From remote agent host:

```bash
ssh -L 5037:localhost:5037 user@home-machine.example.com
# in that SSH session:
adb devices    # sees the phone connected at home
```

Your DevDuck → `localhost:5037` → tunneled → `home-machine:5037` → actual phone.

## Termux: adb on the phone itself

Install [Termux](https://termux.dev) on the phone, then:

```bash
# in Termux
pkg update
pkg install android-tools openssh
sshd
# set a password: passwd

# enable USB debugging + adb over tcpip
adb tcpip 5555
adb connect localhost:5555
```

SSH into the phone's Termux from anywhere, run `adb` commands against localhost.

## Fleet: One Agent, Many Phones

```python
# agent.py
import os
from strands import Agent
from strands_adb import adb

phones = {
    "home":   "192.168.1.42:5555",
    "office": "10.0.0.5:5555",
    "lab":    "192.168.5.12:5555",
}

agent = Agent(tools=[adb])

for name, serial in phones.items():
    os.environ["ADB_SERIAL"] = serial
    agent(f"take a screenshot of {name} and report screen state")
```

## Security Notes

- **Never expose `adb` server to the internet** without a tunnel. Anyone who can reach `:5037` can root the phone.
- SSH keys > passwords for any remote adb setup.
- Wireless debugging should be off when you're not using it.
- Pairing codes are one-time. Unpair old hosts in Developer Options.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `connection refused` | Wireless debugging is off, or wrong port |
| `5555` not working | Try the port from `Wireless debugging` page (varies) |
| Drops after phone suspend | Normal — reconnect on demand or keep phone awake |
| Works via USB, fails wireless | Check both are on the same wifi (no AP isolation) |
| SSH tunnel works but no devices | Run `adb kill-server && adb start-server` after tunnel is up |

## What's Next

- [**Connect a Device**](../getting-started/connect.md) — basics
- [**DevDuck Integration**](devduck.md) — remote agent setup
- [**Safety**](safety.md) — production best practices
