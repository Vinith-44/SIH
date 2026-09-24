"""LAN discovery: port scan, brand guess, and the guards around both.

The scan itself is exercised against a real socket on 127.0.0.1 rather than a
mock, because the thing worth testing is that a refused port and an open one are
told apart - which is exactly what a mock would assume away.  Nothing here
touches a network beyond loopback.
"""

from __future__ import annotations

import socket
import sys
from contextlib import closing
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from discover import (                      # noqa: E402
    DEFAULT_MAX_HOSTS,
    PORT_BRAND_HINTS,
    DiscoveredDevice,
    discover,
    format_report,
    guess_brands,
    hosts,
    main,
    port_is_open,
    scan_host,
)


@pytest.fixture
def open_port():
    """A real listening socket on loopback, closed when the test ends."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        yield server.getsockname()[1]


def _closed_port() -> int:
    """A port number nothing is listening on: bind, read the number, release it."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_port_is_open_detects_a_listening_socket(open_port: int):
    assert port_is_open("127.0.0.1", open_port, timeout=1.0) is True


def test_port_is_open_is_false_for_a_closed_port():
    assert port_is_open("127.0.0.1", _closed_port(), timeout=1.0) is False


def test_port_is_open_survives_an_unusable_address():
    # An invalid interface must read as "not a camera", not crash the scan.
    assert port_is_open("0.0.0.0.0", 554, timeout=0.2) is False


def test_scan_host_reports_only_open_ports(open_port: int):
    closed = _closed_port()
    device = scan_host("127.0.0.1", ports=(open_port, closed), timeout=1.0)
    assert device is not None
    assert device.open_ports == [open_port]


def test_scan_host_returns_none_when_nothing_answers():
    assert scan_host("127.0.0.1", ports=(_closed_port(),), timeout=0.5) is None


@pytest.mark.parametrize(("ports", "expected_first"), [
    ([554, 37777], "cpplus"),   # Dahua SDK port: CP Plus is likelier in an Indian shop
    ([554, 8000], "hikvision"),
    ([554, 2020], "tapo"),
    ([554, 5543], "cpplus"),
])
def test_guess_brands_from_sdk_ports(ports: list[int], expected_first: str):
    assert guess_brands(ports)[0] == expected_first


def test_guess_brands_is_empty_when_only_rtsp_answers():
    # 554 alone genuinely does not identify a brand; guessing would send the
    # installer down the wrong template.
    assert guess_brands([554]) == []
    assert guess_brands([]) == []


def test_guess_brands_has_no_duplicates_across_hints():
    every_port = list(PORT_BRAND_HINTS)
    guesses = guess_brands(every_port)
    assert len(guesses) == len(set(guesses))


def test_candidate_urls_follow_the_templates():
    device = DiscoveredDevice(ip="192.0.2.10", open_ports=[554, 37777],
                              brands=["cpplus", "dahua"])
    urls = device.candidate_urls()
    assert urls["cpplus"] == "rtsp://192.0.2.10:554/cam/realmonitor?channel=1&subtype=1"
    assert urls["dahua"] == urls["cpplus"]


def test_candidate_urls_use_5543_when_that_is_what_answered():
    device = DiscoveredDevice(ip="192.0.2.10", open_ports=[5543], brands=["cpplus"])
    assert ":5543/" in device.candidate_urls()["cpplus"]


def test_candidate_urls_carry_no_credentials():
    # This output gets pasted into chat during onboarding (research/24 section 8).
    device = DiscoveredDevice(ip="192.0.2.10", open_ports=[554, 8000], brands=["hikvision"])
    assert "@" not in device.candidate_urls()["hikvision"]


def test_hosts_expands_a_small_subnet():
    addresses = hosts("192.0.2.0/30")
    assert addresses == ["192.0.2.1", "192.0.2.2"]


def test_hosts_accepts_a_single_address():
    assert hosts("192.0.2.10") == ["192.0.2.10"]


def test_hosts_refuses_a_range_bigger_than_max_hosts():
    # A /16 is 65k hosts: a mistake, or somebody else's network.
    with pytest.raises(SystemExit, match="max-hosts"):
        hosts("10.0.0.0/16")


def test_hosts_default_limit_allows_exactly_a_slash_24():
    assert len(hosts("192.168.1.0/24", DEFAULT_MAX_HOSTS)) == 254


def test_hosts_rejects_nonsense():
    with pytest.raises(SystemExit, match="not a subnet"):
        hosts("not-an-ip")


def test_discover_finds_a_listening_loopback_port(open_port: int):
    devices = discover("127.0.0.1", ports=(open_port,), timeout=1.0)
    assert [d.ip for d in devices] == ["127.0.0.1"]
    assert devices[0].open_ports == [open_port]


def test_discover_returns_empty_when_nothing_answers():
    assert discover("127.0.0.1", ports=(_closed_port(),), timeout=0.5) == []


def test_report_mentions_the_next_step_and_the_urls():
    devices = [DiscoveredDevice(ip="192.0.2.10", open_ports=[554, 37777], brands=["cpplus"])]
    report = format_report(devices)
    assert "192.0.2.10" in report
    assert "realmonitor" in report
    assert "step 4" in report


def test_report_when_rtsp_is_open_but_brand_is_unknown():
    devices = [DiscoveredDevice(ip="192.0.2.10", open_ports=[554], brands=[])]
    report = format_report(devices)
    assert "brand is unclear" in report
    assert "probe.py" in report


def test_report_when_a_device_has_no_rtsp():
    devices = [DiscoveredDevice(ip="192.0.2.10", open_ports=[80], brands=[])]
    assert "not a camera" in format_report(devices)


def test_empty_report_suggests_the_cabling_check():
    report = format_report([])
    assert "same LAN" in report


def test_cli_json_output_and_exit_codes(open_port: int, capsys):
    code = main(["--subnet", "127.0.0.1", "--ports", str(open_port),
                 "--timeout", "1.0", "--json"])
    assert code == 0
    payload = capsys.readouterr().out
    assert '"ip": "127.0.0.1"' in payload
    # --json is meant to be piped into a file, so the notice must not pollute it.
    assert "permission" not in payload.lower()


def test_cli_exits_nonzero_when_nothing_is_found(capsys):
    assert main(["--subnet", "127.0.0.1", "--ports", str(_closed_port()),
                 "--timeout", "0.5"]) == 1
    assert "Nothing answered" in capsys.readouterr().out


def test_cli_prints_the_permission_notice_in_human_mode(open_port: int, capsys):
    main(["--subnet", "127.0.0.1", "--ports", str(open_port), "--timeout", "1.0"])
    assert "permission" in capsys.readouterr().err.lower()


def test_cli_rejects_bad_ports():
    with pytest.raises(SystemExit, match="comma-separated"):
        main(["--subnet", "127.0.0.1", "--ports", "554,abc"])
