"""Alert manager - dedupe, cooldown, escalation, and outputs staff can act on.

Audit item Q5: the legacy system's entire actuation was `winsound.Beep`, which is
Windows-only and useless in a shop.  Novelty pillar N5 says an alert must reach a
human who can do something: the dashboard, a spoken line in the staff's own
language, a tower light at the billing counter.

Rules:

*   **Dedupe** on `message_key` (e.g. `SLOT_EMPTY:shelf-a:A3`) - the same
    condition never fires twice inside `cooldown_s`.
*   **Escalation** - an alert not acknowledged within `escalate_after_s` is
    re-emitted at the next severity up, addressed to the owner.
*   **Outputs** are pluggable sinks; every sink failure is swallowed and logged,
    because a missing speaker must never stop the analytics.

Voice: offline TTS via pyttsx3 for English, plus pre-recorded clips for Telugu
and Hindi (`storemind/api/voice_clips/`, see its README for the exact sentences
the team must record).  Nothing here needs the internet.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from ..core.clock import Clock
from ..core.events import AlertData, Event, EventType, Severity, make_event

log = logging.getLogger(__name__)

VOICE_DIR = Path(__file__).resolve().parent.parent / "api" / "voice_clips"

# message_key prefix -> (English line, Hindi clip, Telugu clip)
VOICE_LINES: dict[str, tuple[str, str, str]] = {
    "QUEUE_FORECAST": ("Please open another billing counter.",
                       "counter_kholiye_hi.wav", "counter_teravandi_te.wav"),
    "QUEUE_CONGESTED": ("Billing queue is long. Please help at the counter.",
                        "queue_lambi_hi.wav", "queue_pedda_te.wav"),
    "SLOT_EMPTY": ("A shelf slot is empty. Please refill.",
                   "shelf_khali_hi.wav", "shelf_khaali_te.wav"),
    "SLOT_LOW": ("A shelf slot is running low.",
                 "shelf_kam_hi.wav", "shelf_takkuva_te.wav"),
    "WRONG_ITEM": ("Wrong product found in a shelf slot.",
                   "galat_saman_hi.wav", "tappu_vastuvu_te.wav"),
    "HIDDEN_DEPLETION": ("Shelf looks full but stock is low at the back.",
                         "peeche_khali_hi.wav", "venaka_khaali_te.wav"),
    "SHRINK": ("Unexpected weight change on a shelf.",
               "vajan_badla_hi.wav", "baruvu_marindi_te.wav"),
    "CAMERA_TAMPER": ("A camera has moved or is blocked.",
                      "camera_hila_hi.wav", "camera_kadilindi_te.wav"),
}


@dataclass
class _Live:
    alert_id: str
    key: str
    severity: Severity
    first_s: float
    last_s: float
    escalated: bool = False
    ack: bool = False
    message: str = ""


class Sink:
    def emit(self, alert: AlertData) -> None:
        raise NotImplementedError


class ConsoleSink(Sink):
    def emit(self, alert: AlertData) -> None:
        marker = {"INFO": "[i]", "WARN": "[!]", "CRITICAL": "[!!]"}.get(alert.severity.value, "[?]")
        print(f"{marker} ALERT {alert.severity.value}: {alert.message}", flush=True)


class SoundSink(Sink):
    """Cross-platform attention sound.  Windows uses winsound, Linux/Pi uses the
    terminal bell or aplay - never a hard dependency."""

    def emit(self, alert: AlertData) -> None:
        try:
            if sys.platform == "win32":
                import winsound
                frequency = 1200 if alert.severity is Severity.CRITICAL else 800
                winsound.Beep(frequency, 250)
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception:
            log.debug("sound sink failed", exc_info=True)


class VoiceSink(Sink):
    """Offline speech.  English through pyttsx3; Hindi/Telugu through pre-recorded
    clips because no good offline Indic TTS ships on a Pi by default."""

    def __init__(self, lang: str = "en") -> None:
        self.lang = lang
        self._engine = None
        self._lock = threading.Lock()

    def _get_engine(self):
        if self._engine is None:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", 165)
        return self._engine

    def emit(self, alert: AlertData) -> None:
        prefix = alert.message_key.split(":", 1)[0]
        line = VOICE_LINES.get(prefix)
        if line is None:
            return
        english, hindi_clip, telugu_clip = line
        clip_name = {"hi": hindi_clip, "te": telugu_clip}.get(self.lang)
        if clip_name:
            clip = VOICE_DIR / clip_name
            if clip.is_file() and self._play(clip):
                return
            log.info("voice clip %s missing, falling back to English TTS", clip_name)
        try:
            with self._lock:
                engine = self._get_engine()
                engine.say(english)
                engine.runAndWait()
        except Exception:
            log.debug("TTS failed", exc_info=True)

    @staticmethod
    def _play(path: Path) -> bool:
        try:
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(str(path), winsound.SND_FILENAME)
            else:
                subprocess.run(["aplay", "-q", str(path)], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False


class TowerLightSink(Sink):
    """Drives the STM32 tower light / buzzer through the sensor bridge (`@L`, `@Z`)."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge

    def emit(self, alert: AlertData) -> None:
        colour = {"INFO": "G", "WARN": "A", "CRITICAL": "R"}.get(alert.severity.value, "G")
        try:
            self.bridge.set_light(colour)
            if alert.severity is Severity.CRITICAL:
                self.bridge.buzz(2)
        except Exception:
            log.debug("tower light sink failed", exc_info=True)


class AlertManager:
    def __init__(self, *, cooldown_s: float = 120.0, escalate_after_s: float = 300.0,
                 store: str = "demo-store", node: str = "pi5-01", lang: str = "en") -> None:
        self.cooldown_s = cooldown_s
        self.escalate_after_s = escalate_after_s
        self.store, self.node, self.lang = store, node, lang
        self.sinks: list[Sink] = []
        self._live: dict[str, _Live] = {}
        self.log: list[AlertData] = []

    def add_sink(self, sink: Sink) -> None:
        self.sinks.append(sink)

    def acknowledge(self, alert_id: str) -> bool:
        for live in self._live.values():
            if live.alert_id == alert_id:
                live.ack = True
                for entry in self.log:
                    if entry.alert_id == alert_id:
                        entry.ack = True
                return True
        return False

    def raise_alert(self, key: str, message: str, severity: Severity, clock: Clock,
                    context: dict | None = None) -> list[Event]:
        now = clock.monotonic_s()
        live = self._live.get(key)
        if live is not None and not live.ack and now - live.last_s < self.cooldown_s:
            live.last_s = now
            return []  # deduped

        alert_id = f"{key}@{int(now)}"
        live = _Live(alert_id=alert_id, key=key, severity=severity, first_s=now,
                     last_s=now, message=message)
        self._live[key] = live
        data = AlertData(severity=severity, message_key=key, message=message,
                         lang=self.lang, alert_id=alert_id, context=context or {})
        self.log.append(data)
        self._fan_out(data)
        return [make_event(ts=clock.now(), store=self.store, node=self.node,
                           type=EventType.ALERT, data=data)]

    def tick(self, clock: Clock) -> list[Event]:
        """Escalate anything still unacknowledged after `escalate_after_s`."""
        now = clock.monotonic_s()
        events: list[Event] = []
        for key, live in list(self._live.items()):
            if live.ack or live.escalated:
                continue
            if now - live.first_s < self.escalate_after_s:
                continue
            live.escalated = True
            higher = {Severity.INFO: Severity.WARN,
                      Severity.WARN: Severity.CRITICAL,
                      Severity.CRITICAL: Severity.CRITICAL}[live.severity]
            alert_id = f"{key}@{int(now)}:escalated"
            data = AlertData(severity=higher, message_key=f"{key}", lang=self.lang,
                             message=f"UNACKNOWLEDGED: {live.message}", alert_id=alert_id,
                             context={"escalated_from": live.alert_id,
                                      "waiting_s": round(now - live.first_s, 1)})
            self.log.append(data)
            self._fan_out(data)
            events.append(make_event(ts=clock.now(), store=self.store, node=self.node,
                                     type=EventType.ALERT, data=data))
        return events

    def _fan_out(self, data: AlertData) -> None:
        for sink in self.sinks:
            try:
                sink.emit(data)
            except Exception:
                log.exception("alert sink %r failed", sink)

    def open_alerts(self) -> list[AlertData]:
        return [a for a in self.log if not a.ack]


def build_alert_manager(config, store: str, node: str, bridge=None) -> AlertManager:
    """`config` is a `core.config.AlertsConfig`."""
    manager = AlertManager(cooldown_s=config.cooldown_s, escalate_after_s=config.escalate_after_s,
                           store=store, node=node, lang=config.voice_lang)
    if config.console:
        manager.add_sink(ConsoleSink())
    if config.sound:
        manager.add_sink(SoundSink())
    if config.voice_enabled:
        manager.add_sink(VoiceSink(config.voice_lang))
    if bridge is not None:
        manager.add_sink(TowerLightSink(bridge))
    return manager
