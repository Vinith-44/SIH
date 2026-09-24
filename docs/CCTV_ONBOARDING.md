# CCTV onboarding

**Owner:** B · **Filled in:** M2 · **Status:** steps 3–4 (discover, probe) and the URL cheat-sheet done; the rest of the installer checklist and `docs/templates/` still to come.

## What goes here

The 15-minute installer checklist, questions to ask college IT, URL cheat-sheet, permission-letter and DPDP-sign templates (`docs/templates/`).

## Read first

- research/24_CCTV_INTEGRATION.md §3, §5, §8

---

## Find the cameras: discover, then probe

Steps 3 and 4 of the 15-minute onboarding (research/24 §5). **Get written permission
before you scan anything** — §5 step 1 and §8.

**Step 3 — what is on this network?**

```powershell
python storemind\tools\discover.py --subnet 192.168.1.0/24
```

It TCP-scans the camera ports and guesses a brand from which ones answer: 37777 →
CP Plus/Dahua, 8000 → Hikvision/Prama, 2020 → Tapo, 5543 → some CP Plus. Then it prints
the candidate sub-stream URL for each guess. Port 554 on its own gives no guess, because
RTSP alone genuinely does not identify a brand — try each template instead. It refuses
ranges over `--max-hosts` (256), so scan one subnet at a time. `--json` to pipe it.

**Step 4 — is that stream usable?**

```powershell
python storemind\tools\probe.py "rtsp://USER:PASS@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1" --expect-fps 8
```

The number that matters is **measured** FPS, not the one in the stream header. A
recorder will happily claim 25 and deliver 6, and only the measured figure tells you
whether step 5's "8–10 FPS for entrance" actually happened. The probe also flags a
stream wider than 1280px (you are on the main stream — the quickest way to overload a
Pi running four cameras), a dark/IR picture, and a uniform frame that means the lens is
covered.

Exit codes suit an install script: `0` clean, `1` warnings, `2` could not open.
`--cam entrance` additionally prints the `CAMERA_HEALTH` payload, so the verdict the
health panel will show later is the one you see at onboarding.

Credentials are redacted in all output from both tools, so the result is safe to paste
into a chat while onboarding.

## URL cheat-sheet

You do not type RTSP URLs. Write the brand, IP and channel in `configs/store.yaml` and
`storemind.ingest.urls` builds the URL from the table in research/24 §3:

```yaml
cameras:
  - name: entrance
    brand: cpplus        # hikvision | prama | cpplus | cp-plus | dahua | tapo
    ip: 192.168.1.108
    channel: 1
    stream: sub          # default; 'main' only when you mean it
    role: entrance
```

Credentials never appear here. They come from the git-ignored `configs/secrets.yaml`
(or `STOREMIND_CAM_<NAME>_USERNAME` / `_PASSWORD`) and are applied when the URL is
built. Anything logged goes through `redact()` first.

**Sub-stream URLs** these settings produce — `192.0.2.10` stands in for the recorder's
IP. Every one of these lines is checked against the code by
`tests/test_ingest_urls.py`, so if firmware forces a change here, the test says so:

```text
hikvision        1 sub  rtsp://192.0.2.10:554/Streaming/Channels/102
hikvision        2 sub  rtsp://192.0.2.10:554/Streaming/Channels/202
hikvision        1 main rtsp://192.0.2.10:554/Streaming/Channels/101
prama            1 sub  rtsp://192.0.2.10:554/Streaming/Channels/102
hikvision_legacy 1 sub  rtsp://192.0.2.10:554/h264/ch1/sub/av_stream
cpplus           1 sub  rtsp://192.0.2.10:554/cam/realmonitor?channel=1&subtype=1
cpplus           2 sub  rtsp://192.0.2.10:554/cam/realmonitor?channel=2&subtype=1
cpplus           1 main rtsp://192.0.2.10:554/cam/realmonitor?channel=1&subtype=0
dahua            1 sub  rtsp://192.0.2.10:554/cam/realmonitor?channel=1&subtype=1
tapo             1 sub  rtsp://192.0.2.10:554/stream2
tapo             1 main rtsp://192.0.2.10:554/stream1
```

**Snapshot URLs** (research/24 §2 method C — shelves, one frame every 1–5 minutes):

```text
hikvision        1 sub  http://192.0.2.10/ISAPI/Streaming/channels/101/picture
cpplus           1 sub  http://192.0.2.10/cgi-bin/snapshot.cgi?channel=1
dahua            2 sub  http://192.0.2.10/cgi-bin/snapshot.cgi?channel=2
```

Tapo has no HTTP snapshot API. `snapshot_url()` returns `None` for it, and you read
`go2rtc_snapshot_url(name)` instead.

### When the template is wrong

Firmware varies — research/24 §3 says to confirm on the actual device. Two escape
hatches, in order of preference:

1. `path:` overrides the template for that camera. Some CP Plus models want
   `/live/channel0`; `port: 5543` also exists in the wild.
2. `brand: hikvision_legacy` for firmware older than the ISAPI scheme
   (`/h264/ch1/sub/av_stream`).

If neither fits, run ONVIF discovery and read the stream URI off the device rather than
guessing. Ports worth scanning: **554** RTSP, **80/443** web/ISAPI, **2020** Tapo ONVIF,
**5543** some CP Plus, **8000** Hikvision SDK, **37777** Dahua SDK.

### go2rtc is the only thing that talks to the recorder

A DVR refuses clients once its limit is hit, so go2rtc connects **once** per camera and
everything else reads its restream (research/24 §6):

```
DVR/NVR ──rtsp──> go2rtc :8564 ──> pipeline, dashboard, snapshots
```

`go2rtc_streams()` builds the `streams:` block of `go2rtc.yaml` from the camera list;
the pipeline then opens `go2rtc_stream_url("entrance")` →
`rtsp://127.0.0.1:8564/entrance`, and snapshots come from
`go2rtc_snapshot_url("entrance")` → `http://127.0.0.1:1984/api/frame.jpeg?src=entrance`.

**Why 8564 and not 8554.** go2rtc's RTSP server defaults to 8554 — and so does MediaMTX,
which `tools/fake_cctv.py` uses for the demo cameras. Running both, as the end-to-end
check does, would have go2rtc fighting MediaMTX for the port, so go2rtc is moved to 8564
and 8554 is left to the fake CCTV.

`go2rtc.yaml` contains passwords and is git-ignored, as is `tools/bin/go2rtc.exe`.

## Fake CCTV for the demo

research/24 §9 keeps a backup that needs no venue Wi-Fi and no borrowed DVR: replay our
recorded clips as real RTSP cameras, through the same code path as a real store.

```powershell
python storemind\tools\fake_cctv.py --testsrc 2          # no video files needed
python storemind\tools\fake_cctv.py --video videos\entrance\synthetic_entrance.mp4
```

It serves `rtsp://127.0.0.1:8554/cam1`, `cam2`, … and a camera pointed at it is configured
like any other, with `brand: mediamtx`. Ctrl+C shuts MediaMTX and ffmpeg down cleanly; a
"Broken pipe" from ffmpeg on the way out is normal.

Needs `mediamtx` and `ffmpeg` in `tools\bin\` (git-ignored) or on PATH.

## Proving the chain works

```powershell
python tools\e2e_smoke.py
```

Starts fake CCTV → go2rtc → reader, then checks the camera comes online at a sane frame
rate, that killing the publisher is *detected*, that it recovers on its own, and that the
`CAMERA_HEALTH` payloads never contain the password. Exit code 0 means all checks passed,
1 a failed check, 2 a missing binary.
