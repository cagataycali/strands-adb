# 🔐 SSH into Android's Linux VM

Bridge your laptop directly into the Debian VM running on the phone.
Once connected, you can `pip install`, `git clone`, edit files with `nvim`,
or run `duck` remotely — no need to touch the phone screen.

## How it works (the mental model)

```
┌─────────┐         ┌───────────────────────┐         ┌──────────────┐
│ Mac/Lin │  adb    │   Android (host OS)   │ Port FW │  Debian VM   │
│  ssh -p │◄──────► │  Terminal app          │◄──────► │  sshd :22    │
│  2222   │  USB/   │  (crosvm + virtmgr)   │         │  (inside VM) │
│localhost│  wifi   │  Forwards VM:22 →     │         │              │
│         │         │  android:<random>     │         │              │
└─────────┘         └───────────────────────┘         └──────────────┘
```

The Terminal app auto-forwards configured guest ports from the VM to a
random port on the Android host. We then `adb forward` that port to
localhost on your laptop → plain `ssh -p 2222 droid@localhost` works.

**Why adb forward instead of direct wifi?** Works over USB even without
shared wifi, survives network changes, doesn't require knowing the phone's
IP, and doesn't expose the VM to your LAN.

---

## Prerequisites

**On the phone:**
- Android 15+ with Linux Development Environment installed
  → Settings → System → Developer options → Linux development environment → Install
- Terminal app launched at least once (VM is created)
- USB debugging enabled
- USB cable to Mac/laptop (or wireless adb paired)

**On your laptop:**
- `adb` (`brew install android-platform-tools`)
- Standard OpenSSH client (built into macOS/Linux)

---

## One-time VM setup (inside the phone Terminal)

Open the Terminal app on your phone, and in the Debian shell run:

```bash
sudo apt update
sudo apt install -y openssh-server

# Enable ssh to start on VM boot
sudo systemctl enable --now ssh

# Set a password for the default user 'droid' (if not already set)
sudo passwd droid
```

Now expose port 22 via the Terminal app:
- Open **Terminal app → Settings (⚙) → Port forwarding → Add**
- **Guest port: 22**, Protocol: TCP
- Save. The app will allocate a random host port (e.g. `53601`).

---

## Connect from your laptop

**Option A — auto-setup script (recommended):**

```bash
cd ~/strands-adb
bash scripts/ssh_setup_android.sh
```

The script:
1. Auto-detects the VM's forwarded port on Android
2. Sets up `adb forward tcp:2222 tcp:<vm-port>`
3. Tests the SSH handshake
4. Prints a ready-to-copy `~/.ssh/config` stanza

**Option B — manual:**

```bash
# 1. Find the port the Terminal app allocated
adb shell "ss -tln" | grep 192.168

# Example output:
#   LISTEN 0  0  [::ffff:192.168.1.6]:53601  *:*

# 2. Forward it to localhost:2222
adb forward tcp:2222 tcp:53601

# 3. Connect
ssh -p 2222 droid@localhost
```

---

## Persistent SSH config

Add to `~/.ssh/config`:

```ssh-config
Host android-vm
  HostName localhost
  Port 2222
  User droid
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  LogLevel ERROR
```

Then:

```bash
adb forward tcp:2222 tcp:53601   # run once after plugging in
ssh android-vm                    # anytime
scp file.txt android-vm:~/        # copy files
rsync -avz ~/code/ android-vm:~/code/  # sync projects
```

## Key-based auth (skip password prompt)

```bash
ssh-copy-id android-vm
# Or manually:
cat ~/.ssh/id_ed25519.pub | ssh android-vm 'cat >> ~/.ssh/authorized_keys'
```

---

## Combining with DevDuck + strands-adb

Now you have **three ways** to reach the phone simultaneously:

| Layer | Tool | Use case |
|---|---|---|
| Android UI | `strands-adb` | Tap apps, read notifications, drive Gmail |
| Android OS | `adb shell` | System-level dumpsys, logcat, settings |
| Linux VM | `ssh android-vm` | Run devduck, python, git inside the VM |

### Example workflow

```bash
# Terminal 1 — drive the phone from Mac devduck
duck "what's on my phone's screen?"   # uses strands-adb

# Terminal 2 — SSH into the VM and run devduck there
ssh android-vm
$ duck "zenoh_peer(action='list_peers')"
# Phone's devduck is now a peer in your mesh!

# Back on Mac
duck "zenoh_peer(action='broadcast', message='sync now')"
# Phone-side devduck receives it
```

---

## Enabling wireless adb (optional, so you can leave USB)

```bash
# With phone plugged in via USB:
adb tcpip 5555
adb shell ip addr show wlan0 | grep 'inet '
# → 192.168.1.6

# Unplug. From Mac:
adb connect 192.168.1.6:5555

# Now all adb commands work over wifi, including:
adb forward tcp:2222 tcp:53601
ssh android-vm
```

On Android 11+, you can also use **Wireless debugging** in Developer options
(pair with a code, no USB ever needed).

---

## Troubleshooting

**`Connection closed by 127.0.0.1`**
→ Port forward works but sshd isn't running inside the VM. Open the Terminal
app on the phone and verify: `systemctl status ssh`.

**`Connection refused`**
→ `adb forward` isn't set up, or VM isn't running.
   Run: `adb forward --list` — if empty, the Terminal app may not be launched.

**`Permission denied (publickey,password)`**
→ sshd is running but user/password wrong. Default user is `droid`.
   If you haven't set a password: `sudo passwd droid` inside the Terminal app.

**Port forward keeps breaking**
→ adb forwards don't persist across phone reboots or USB disconnects. Re-run
   `adb forward tcp:2222 tcp:53601`. To automate, drop this into `~/.zshrc`:

```bash
android_ssh_up() {
  VM_PORT=$(adb shell 'ss -tln' | grep -oE '192\.168\.[0-9.]+:[0-9]+' | head -1 | cut -d: -f2)
  [[ -z "$VM_PORT" ]] && { echo "VM not running"; return 1; }
  adb forward tcp:2222 tcp:$VM_PORT
  echo "✅ Forwarded localhost:2222 → phone:$VM_PORT"
}
```

Then just run `android_ssh_up` before `ssh android-vm`.

---

## Adding this to strands-adb as a tool action

*(Future work — documented in FRONTIERS.md entry 14)*

We could add a `ssh_setup` action to `strands_adb/adb_tool.py`:

```python
adb(action="ssh_setup", local_port=2222, vm_user="droid")
```

Which would:
1. Auto-detect the VM's forwarded port
2. Run `adb forward` 
3. Write the `~/.ssh/config` entry
4. Return the connection string

This would let DevDuck bootstrap an SSH tunnel into the phone VM entirely
autonomously — no human needed in the loop.
