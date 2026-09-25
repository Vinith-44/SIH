# Operations

**Owner:** Ram (Person B) · **Status:** M7b (install script, systemd units, maintenance, soak and chaos on the
laptop). **Not yet installed on a real Pi**: the Pi steps and the 24 h soak are in `HARDWARE_TODO.md` "M8".

Analogy: a shop's till has to work on the day nobody technical is around. So the box restarts what dies,
cleans up after itself at night, keeps its logs small, and says out loud (dashboard, tower light) when
something is wrong.

## 1. Install / update (one command)

On the Pi, from a clone of the repo:

```bash
sudo ./scripts/install_pi5.sh
```

Run it again after `git pull` to update: it syncs the code to `/opt/storemind`, updates the venv and
restarts the services, and **never overwrites** `/etc/storemind/*` (your config wins). `--dry-run` prints
every step and changes nothing; `--pi-hardware` also does the UART / udev / chrony / RTC setup (M8,
docs/SETUP_PI5.md).

| Path | What |
|---|---|
| `/opt/storemind` | code + venv (`storemind/.venv`), go2rtc + MediaMTX in `tools/bin` |
| `/etc/storemind/store.yaml` | the store config (generated once from `configs/demo.yaml`; edit cameras here) |
| `/etc/storemind/secrets.yaml` | MQTT password (generated), camera credentials; `root:storemind 0640`, never in git |
| `/etc/storemind/go2rtc.yaml` | camera gateway streams (`tools/probe.py` prints ready-to-paste lines) |
| `/var/lib/storemind` | `storemind.db` (events, WAL mode), `reorder.db`, heatmaps |

## 2. Services

| Unit | Runs | Restart / watchdog |
|---|---|---|
| `storemind-go2rtc` | camera gateway on `127.0.0.1:8554` | `Restart=always` |
| `storemind-pipeline` | cameras → analytics → SQLite → dashboard `:8000` | `Type=notify`, `WatchdogSec=60`: a WATCHDOG ping per HEALTH event (every 10 s); a hung loop is restarted. `MemoryMax=2500M` |
| `storemind-bridge` | `/dev/storemind-mcu` ↔ MQTT, LED / buzzer from alerts | `Type=notify`, `WatchdogSec=30`; `BindsTo` the device, so it stops when the node is unplugged and starts when it comes back |
| `mosquitto` | broker, `127.0.0.1:1883` only, password required, persistent | distro unit |
| `storemind-maintenance.timer` | nightly 03:30 (±10 min) | `Persistent=true`: runs at boot if the box was off at 03:30 |

The pipeline's systemd hook (READY + WATCHDOG) is in `run.py` (contract PR "run.py --sensors + systemd
notify"); `health/systemd.py` does the `sd_notify` with no dependency and is a no-op on Windows.

```bash
systemctl status 'storemind-*'
journalctl -u storemind-pipeline -f
sudo systemctl restart storemind-pipeline      # after editing /etc/storemind/store.yaml
```

## 3. Logs

journald keeps them (`deploy/pi5/journald-storemind.conf`): persistent, at most 200 MB, 14 days,
rate-limited so a crash loop cannot fill the SD card. Nothing writes log files of its own.

## 4. Database

SQLite in **WAL** mode (`store/db.py`: survives power cuts far better than the rollback journal). Every
night `python -m storemind.store.maintenance --config /etc/storemind/store.yaml`:

1. deletes raw events older than `storage.retention_days` (default 30); the per-minute aggregates the
   reports read are kept;
2. `PRAGMA wal_checkpoint(TRUNCATE)` so the `-wal` file cannot grow without bound;
3. `PRAGMA optimize`, and `VACUUM` only when > 25 % of the file is free pages.

Every event has a UUID, so an HQ sync can be retried without duplicates.

## 5. Soak test

```bash
python scripts/soak.py --hours 1 --label laptop_1h
```

One process runs what the box runs: the pipeline on 3 live RTSP cameras (fake CCTV: MediaMTX + ffmpeg
looping the synthetic clips), the serial bridge to the STM32 simulator, and the dashboard API. Every
minute it records memory, threads, OS handles, DB + WAL size, events, the serial error counters, and
whether the pipeline and API are alive; it writes a CSV and
`storemind/storemind/eval/results/platform/soak_<label>.json`. Pass = everything alive every sample, RSS
growth after a 10-min warm-up below 20 MB/h, stable thread count, 0 serial errors, events in every sample.
On the laptop the detector is `stub` (no model weights on this laptop), so it is a **platform** soak; on
the Pi run it with `--detector ultralytics --hours 24` (HARDWARE_TODO.md "M8").

## 6. Chaos test

```bash
python scripts/chaos.py
```

| Fault | Expected |
|---|---|
| kill one camera stream for 40 s | other cameras keep going; the killed one shows not-ok; frames resume by themselves |
| unplug the sensor node (stop the simulator) for 40 s | `NODE_HEALTH link=down` + one `SENSOR_LINK` alert; the bridge reconnects by itself |
| disk full (every SQLite write fails) for 40 s | pipeline keeps running, events are dropped (never queued without bound), writes resume |
| MQTT broker restart | **not run on the laptop** (no broker): Pi step |
| lens covered | **not run on the laptop** (needs a camera): Pi step; the tamper check is unit-tested |

Run the soak and the chaos test one after the other, not together: each starts its own MediaMTX, and a
second MediaMTX cannot bind the default RTP ports.

## 7. Results so far

See `storemind/storemind/eval/results/platform/` (included in RESULTS.md by `run_all`). Laptop numbers are
bucket S (soak) / C (chaos, HIL on the simulator); nothing on the Pi has been measured yet.
