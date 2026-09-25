#!/usr/bin/env bash
# StoreMind one-command install on a fresh Raspberry Pi 5 (Raspberry Pi OS Bookworm 64-bit).
#
#   sudo ./scripts/install_pi5.sh                 # from a clone of the repo on the Pi
#   ./scripts/install_pi5.sh --dry-run            # print every step, change nothing
#
# Idempotent: run it again after `git pull` to update the code and restart the services.
# What it does (docs/OPERATIONS.md, docs/SETUP_PI5.md):
#   1. apt packages (python venv, ffmpeg, Mosquitto, OpenCV runtime libs, chrony)
#   2. a `storemind` system user (groups dialout, video) and /var/lib/storemind
#   3. the code to /opt/storemind, a venv with CPU PyTorch + storemind/requirements.txt
#   4. go2rtc + MediaMTX arm64 binaries (same pinned versions as CI)
#   5. /etc/storemind/{store.yaml, secrets.yaml, go2rtc.yaml, storemind.env} if missing
#      (never overwrites an existing config: yours wins)
#   6. Mosquitto: local-only listener with a generated password
#   7. journald size cap, systemd units, nightly maintenance timer; enable + (re)start
#   8. the Pi hardware steps (header UART, udev symlink, chrony, RTC): --pi-hardware (M8)
#   9. "Ask your store" local model (Ollama + qwen2.5-coder:1.5b): --with-llm (M10, optional)
# Secrets are generated on the box and never printed or committed.
set -euo pipefail

GO2RTC_VERSION="v1.9.14"
MEDIAMTX_VERSION="v1.21.1"
PREFIX="/opt/storemind"
ETC="/etc/storemind"
DATA="/var/lib/storemind"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
SKIP_APT=0
PI_HARDWARE=0
WITH_LLM=0
LLM_MODEL="qwen2.5-coder:1.5b"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  echo "Options: --dry-run  --skip-apt  --pi-hardware  --with-llm  --prefix DIR"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-apt) SKIP_APT=1 ;;
    --pi-hardware) PI_HARDWARE=1 ;;
    --with-llm) WITH_LLM=1 ;;
    --prefix) PREFIX="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

say() { printf '\n==> %s\n' "$*"; }
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '    [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}
# write_file PATH MODE: stdin -> PATH, only if PATH does not exist yet
write_new() {
  local path="$1" mode="$2"
  if [ -e "$path" ]; then
    echo "    keep existing $path"
    cat >/dev/null
    return
  fi
  if [ "$DRY_RUN" = 1 ]; then
    echo "    [dry-run] write $path ($mode)"
    cat >/dev/null
    return
  fi
  install -m "$mode" /dev/null "$path"
  cat >"$path"
}

if [ "$DRY_RUN" = 0 ] && [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0 $*" >&2
  exit 1
fi
ARCH="$(uname -m)"
if [ "$ARCH" != "aarch64" ] && [ "$DRY_RUN" = 0 ]; then
  echo "warning: this is $ARCH, not a 64-bit Pi (aarch64); continuing" >&2
fi

# --------------------------------------------------------------------------- #
say "1/8 packages"
if [ "$SKIP_APT" = 0 ]; then
  run apt-get update -q
  run apt-get install -y -q --no-install-recommends \
    python3-venv python3-dev python3-pip git rsync curl ca-certificates \
    ffmpeg libgl1 libglib2.0-0 mosquitto mosquitto-clients chrony
fi

# --------------------------------------------------------------------------- #
say "2/8 user and folders"
if ! id storemind >/dev/null 2>&1; then
  run useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin storemind
fi
run usermod -a -G dialout,video storemind
run install -d -o storemind -g storemind -m 0750 "$DATA"
run install -d -o root -g storemind -m 0750 "$ETC"

# --------------------------------------------------------------------------- #
say "3/8 code and Python environment in $PREFIX"
run install -d "$PREFIX"
run rsync -a --delete \
  --exclude '.git/' --exclude 'storemind/.venv/' --exclude 'videos/' --exclude 'data/' \
  --exclude 'build/' --exclude '__pycache__/' --exclude 'tools/bin/' --exclude 'legacy/' \
  "$REPO/" "$PREFIX/"
VENV="$PREFIX/storemind/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  run python3 -m venv "$VENV"
fi
run "$VENV/bin/pip" install -q --upgrade pip
run "$VENV/bin/pip" install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
run "$VENV/bin/pip" install -q -r "$PREFIX/storemind/requirements.txt" \
  --extra-index-url https://download.pytorch.org/whl/cpu
run chown -R root:storemind "$PREFIX"

# --------------------------------------------------------------------------- #
say "4/8 go2rtc $GO2RTC_VERSION + MediaMTX $MEDIAMTX_VERSION (arm64)"
BIN="$PREFIX/tools/bin"
run install -d "$BIN"
if [ ! -x "$BIN/go2rtc" ]; then
  run curl -fsSL -o "$BIN/go2rtc" \
    "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_arm64"
  run chmod 0755 "$BIN/go2rtc"
fi
if [ ! -x "$BIN/mediamtx" ]; then
  run curl -fsSL -o /tmp/mediamtx.tgz \
    "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_arm64.tar.gz"
  run tar -xzf /tmp/mediamtx.tgz -C "$BIN" mediamtx
fi

# --------------------------------------------------------------------------- #
say "5/8 configuration in $ETC"
MQTT_PASS="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)"
write_new "$ETC/secrets.yaml" 0640 <<EOF
# Generated by install_pi5.sh.  Never commit this file.
mqtt:
  username: storemind
  password: ${MQTT_PASS}
cameras: {}          # e.g. entrance: {username: viewer, password: ...} (docs/CCTV_ONBOARDING.md)
EOF
if [ ! -e "$ETC/store.yaml" ]; then
  if [ "$DRY_RUN" = 1 ]; then
    echo "    [dry-run] write $ETC/store.yaml from storemind/configs/demo.yaml (db in $DATA, MQTT on, UART)"
  else
    "$VENV/bin/python" - "$PREFIX/storemind/configs/demo.yaml" "$ETC/store.yaml" "$DATA" <<'PY'
import sys
import yaml
src, dst, data = sys.argv[1:4]
config = yaml.safe_load(open(src, encoding="utf-8"))
config.setdefault("storage", {})["db_path"] = f"{data}/storemind.db"
config["mqtt"] = {"enabled": True, "host": "127.0.0.1", "port": 1883, "listen": True}
sensors = config.setdefault("sensors", {})
sensors.update({"enabled": True, "port": "/dev/storemind-mcu", "tcp": None})
config.setdefault("api", {})["host"] = "0.0.0.0"
with open(dst, "w", encoding="utf-8") as f:
    f.write("# Generated from configs/demo.yaml by install_pi5.sh - edit the cameras for this store\n")
    f.write("# (python tools/calibrate.py, docs/CCTV_ONBOARDING.md).\n")
    yaml.safe_dump(config, f, sort_keys=False)
PY
    chmod 0640 "$ETC/store.yaml"
  fi
else
  echo "    keep existing $ETC/store.yaml"
fi
write_new "$ETC/go2rtc.yaml" 0640 <<'EOF'
# go2rtc: one entry per DVR/NVR channel (python tools/probe.py prints ready-to-paste lines).
api:
  listen: "127.0.0.1:1984"
rtsp:
  listen: "127.0.0.1:8554"
streams: {}
EOF
write_new "$ETC/storemind.env" 0640 <<'EOF'
# Extra environment for the StoreMind services (e.g. STOREMIND_CAM_ENTRANCE_PASSWORD=...).
EOF
run chown root:storemind "$ETC/secrets.yaml" "$ETC/store.yaml" "$ETC/go2rtc.yaml" "$ETC/storemind.env"
# The services find secrets.yaml next to store.yaml (core/config.py load_config).

# --------------------------------------------------------------------------- #
say "6/8 Mosquitto (local only, password from $ETC/secrets.yaml)"
run install -m 0644 "$REPO/deploy/pi5/mosquitto/storemind.conf" /etc/mosquitto/conf.d/storemind.conf
if [ "$DRY_RUN" = 0 ]; then
  PASS_NOW="$("$VENV/bin/python" -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['mqtt']['password'])" "$ETC/secrets.yaml")"
  mosquitto_passwd -b -c /etc/mosquitto/storemind.passwd storemind "$PASS_NOW"
  chown mosquitto:mosquitto /etc/mosquitto/storemind.passwd
  chmod 0600 /etc/mosquitto/storemind.passwd
else
  echo "    [dry-run] mosquitto_passwd -b -c /etc/mosquitto/storemind.passwd storemind <from secrets.yaml>"
fi
run systemctl enable mosquitto
run systemctl restart mosquitto

# --------------------------------------------------------------------------- #
say "7/8 logs, systemd units, timer"
run install -d /etc/systemd/journald.conf.d
run install -m 0644 "$REPO/deploy/pi5/journald-storemind.conf" /etc/systemd/journald.conf.d/storemind.conf
run systemctl restart systemd-journald
for unit in "$REPO"/deploy/pi5/systemd/*; do
  run install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
run systemctl daemon-reload
run systemctl enable storemind-go2rtc.service storemind-pipeline.service storemind-bridge.service \
  storemind-maintenance.timer
run systemctl restart storemind-go2rtc.service storemind-pipeline.service
run systemctl start storemind-maintenance.timer
if [ -e /dev/storemind-mcu ]; then
  run systemctl restart storemind-bridge.service
else
  echo "    /dev/storemind-mcu not present: the bridge starts by itself when the node is plugged in"
  echo "    (after --pi-hardware installs the udev rule)"
fi

# --------------------------------------------------------------------------- #
say "8/8 Pi hardware (header UART, udev, chrony, RTC)"
if [ "$PI_HARDWARE" = 1 ] && [ -f "$REPO/scripts/pi5_hardware.sh" ]; then
  run bash "$REPO/scripts/pi5_hardware.sh" $([ "$DRY_RUN" = 1 ] && echo --dry-run)
elif [ "$PI_HARDWARE" = 1 ]; then
  echo "    scripts/pi5_hardware.sh not in this checkout yet (M8)"
else
  echo "    skipped: run again with --pi-hardware on the Pi (docs/SETUP_PI5.md)"
fi

# --------------------------------------------------------------------------- #
say "9 (optional) Ask your store: local model"
if [ "$WITH_LLM" = 1 ]; then
  if ! command -v ollama >/dev/null 2>&1; then
    # Ollama's official Linux installer (https://ollama.com/download/linux); it sets up its own service.
    if [ "$DRY_RUN" = 1 ]; then
      echo "    [dry-run] curl -fsSL https://ollama.com/install.sh | sh"
    else
      curl -fsSL https://ollama.com/install.sh | sh
    fi
  fi
  run ollama pull "$LLM_MODEL"
  echo "    then time it: python scripts/ask_latency.py --label pi5_${LLM_MODEL//[:.]/_}"
else
  echo "    skipped: the dashboard answers with the keyword rules; add --with-llm for the local model"
  echo "    (or set STOREMIND_LLM_MODEL=off in $ETC/storemind.env to stop probing for it)"
fi

say "done"
cat <<EOF
  dashboard:  http://$(hostname).local:8000/   (or http://<pi-ip>:8000/)
  status:     systemctl status 'storemind-*'
  logs:       journalctl -u storemind-pipeline -f
  config:     $ETC/store.yaml   (then: sudo systemctl restart storemind-pipeline)
EOF
