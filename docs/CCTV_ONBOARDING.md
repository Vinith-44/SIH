# CCTV onboarding

**Owner:** B · **Filled in:** M2 · **Status:** URL cheat-sheet done; installer checklist and `docs/templates/` still to come.

## What goes here

The 15-minute installer checklist, questions to ask college IT, URL cheat-sheet, permission-letter and DPDP-sign templates (`docs/templates/`).

## Read first

- research/24_CCTV_INTEGRATION.md §3, §5, §8

---

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
DVR/NVR ──rtsp──> go2rtc :8554 ──> pipeline, dashboard, snapshots
```

`go2rtc_streams()` builds the `streams:` block of `go2rtc.yaml` from the camera list;
the pipeline then opens `go2rtc_stream_url("entrance")` →
`rtsp://127.0.0.1:8554/entrance`, and snapshots come from
`go2rtc_snapshot_url("entrance")` → `http://127.0.0.1:1984/api/frame.jpeg?src=entrance`.

`go2rtc.yaml` contains passwords and is git-ignored, as is `tools/bin/go2rtc.exe`.
