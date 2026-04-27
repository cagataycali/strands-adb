#!/usr/bin/env bash
# ========================================================================
# SSH into Android's Linux Dev Environment from your Mac/laptop
# ========================================================================
# 1. Verifies the Linux Terminal VM is running on the phone
# 2. Sets up an adb port-forward (reliable over USB & wifi-adb)
# 3. Tests connection and tells you what to do if sshd isn't running yet
# 4. Prints ready-to-use SSH aliases
#
# Usage:   bash scripts/ssh_setup_android.sh [USERNAME]
# Default VM user: "droid"
# ========================================================================

set -euo pipefail

VM_USER="${1:-droid}"
LOCAL_PORT="${SSH_LOCAL_PORT:-2222}"

echo "🦆 SSH-to-Android Linux VM setup"
echo "================================="

if ! command -v adb >/dev/null; then
  echo "❌ adb not found. brew install android-platform-tools"
  exit 1
fi

DEVICES=$(adb devices | grep -v "List of" | grep -c "device$" || true)
if [[ "$DEVICES" -eq 0 ]]; then
  echo "❌ No adb device connected."
  exit 1
fi
echo "✅ adb device connected"

echo "🔍 Scanning for Terminal VM listening ports..."
VM_PORT=$(adb shell 'ss -tln 2>/dev/null | grep -oE "192\.168\.[0-9]+\.[0-9]+:[0-9]+" | head -1 | cut -d: -f2' | tr -d '\r' || true)

if [[ -z "$VM_PORT" ]]; then
  echo "⚠️  No VM port found. Is the Terminal app open on your phone?"
  read -rp "    Enter VM port manually (or Enter to exit): " VM_PORT
  [[ -z "$VM_PORT" ]] && exit 1
fi
echo "✅ VM listening on phone at port $VM_PORT"

echo "🔗 Forwarding localhost:$LOCAL_PORT → phone:$VM_PORT ..."
adb forward tcp:$LOCAL_PORT tcp:$VM_PORT >/dev/null
adb forward --list | grep "tcp:$LOCAL_PORT" || true

echo ""
echo "🧪 Testing SSH handshake..."
TEST=$(ssh -o ConnectTimeout=5 \
           -o StrictHostKeyChecking=no \
           -o UserKnownHostsFile=/dev/null \
           -o LogLevel=ERROR \
           -o BatchMode=yes \
           -p $LOCAL_PORT $VM_USER@localhost "echo OK" 2>&1 || true)

if [[ "$TEST" == *"OK"* ]]; then
  echo "🎉 SSH works!"
  echo ""
  echo "Connect:  ssh -p $LOCAL_PORT $VM_USER@localhost"
  echo ""
  echo "Add to ~/.ssh/config for convenience:"
  cat <<CFG
  Host android-vm
    HostName localhost
    Port $LOCAL_PORT
    User $VM_USER
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
CFG
  exit 0
fi

if [[ "$TEST" == *"Permission denied"* ]] || [[ "$TEST" == *"password"* ]]; then
  echo "✅ SSH is running — auth failed."
  echo "   Try: ssh -p $LOCAL_PORT $VM_USER@localhost  (password prompt)"
  echo "   Or:  ssh-copy-id -p $LOCAL_PORT $VM_USER@localhost"
  exit 0
fi

if [[ "$TEST" == *"closed"* ]] || [[ "$TEST" == *"banner"* ]] || [[ "$TEST" == *"refused"* ]]; then
  echo "🔧 sshd not running inside the VM. Setup steps:"
  echo ""
  echo "   1) Open Terminal app on phone."
  echo "   2) Run inside the Debian shell:"
  echo ""
  echo "        sudo apt update && sudo apt install -y openssh-server"
  echo "        sudo systemctl enable --now ssh"
  echo "        sudo passwd droid"
  echo ""
  echo "   3) Terminal app → Settings → Port forwarding → Add"
  echo "      Guest port: 22, Protocol: TCP"
  echo ""
  echo "   4) Re-run this script."
fi
