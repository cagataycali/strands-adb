# Connect a Device

Get your phone talking to `adb`. Three modes: USB, wireless, SSH-over-adb.

---

## Mode 1: USB (simplest)

1. **Enable Developer Options**: `Settings → About phone → tap Build number 7 times`
2. **Enable USB debugging**: `Settings → System → Developer options → USB debugging`
3. Plug in USB cable. Accept the trust dialog on the phone.
4. Verify:

```bash
adb devices
# 59230DLCH0012Z  device
```

!!! warning "unauthorized?"
    If `adb devices` shows `unauthorized`, unlock the phone and accept the dialog. Still stuck? `adb kill-server && adb start-server`.

## Mode 2: Wireless adb (no cable after pairing)

**Android 11+** supports native wireless debugging.

### Pair once

1. `Settings → Developer options → Wireless debugging` → toggle on
2. Tap **Pair device with pairing code** — note the IP, port, and 6-digit code.
3. On your host:

```bash
adb pair 192.168.1.42:41337
# Enter pairing code: 123456
# Successfully paired
```

### Connect

```bash
adb connect 192.168.1.42:5555
adb devices
# 192.168.1.42:5555  device
```

### Make it stick

Add to `~/.zshrc` / `~/.bashrc`:

```bash
alias phone="adb connect 192.168.1.42:5555"
```

→ Details: [`SSH_ANDROID.md`](https://github.com/cagataycali/strands-adb/blob/main/SSH_ANDROID.md)

## Mode 3: SSH over adb (remote agent → phone)

You want your DevDuck running on a remote box (Jetson, VPS) to control a phone sitting on your desk. Two subsolutions:

### 3a. adb over SSH tunnel

On the host with the phone:

```bash
adb -a -P 5037 nodaemon server &   # listen on all interfaces
```

On the remote box:

```bash
ssh -L 5037:localhost:5037 user@host-with-phone
adb devices   # sees the remote phone
```

### 3b. adb running on the phone itself (Termux)

Install [Termux](https://termux.dev) on the phone, then:

```bash
pkg install android-tools openssh
sshd
adb tcpip 5555
adb connect localhost:5555
```

SSH into the phone from your agent's host, run commands there.

→ Full walkthrough: [`SSH_ANDROID.md`](https://github.com/cagataycali/strands-adb/blob/main/SSH_ANDROID.md)

## Multi-Device Targeting

Got more than one phone connected?

### Option A: Set a default globally

```bash
export ADB_SERIAL=59230DLCH0012Z
```

All `adb(...)` calls will target that device unless overridden.

### Option B: Per-call override

```python
adb(action="screenshot", serial="59230DLCH0012Z")
adb(action="battery",    serial="emulator-5554")
```

### Option C: Let the agent choose

```python
agent("list all connected devices, then take a screenshot of the Pixel")
# Agent calls list_devices → sees serials + models → picks the right one
```

## Custom adb Binary

If `adb` is not on PATH (or you want a specific version):

```bash
export ADB_BIN=/opt/android-sdk/platform-tools/adb
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `adb: command not found` | Install `android-platform-tools` (see [Installation](installation.md)) |
| `device unauthorized` | Accept the USB debug dialog on the phone |
| `no devices` after reboot | `adb kill-server && adb start-server` |
| Wireless drops after suspend | Reconnect with `adb connect IP:5555` (script it) |
| Screenshot is black | Phone is locked — try `adb(action="wake")` first |
| `Permission denied` on push | Target a world-writable path like `/sdcard/` |

## What's Next

- [**Quickstart**](quickstart.md) — first queries
- [**Actions Overview**](../guide/actions.md) — full capability list
- [**DevDuck Integration**](../guide/devduck.md) — production setup
