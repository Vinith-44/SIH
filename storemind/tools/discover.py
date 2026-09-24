"""Find cameras and recorders on a store LAN (research/24 section 5 step 3).

Step 3 of the 15-minute onboarding is "discover", and the doc's own suggestion is
an ONVIF scan or `nmap -p 554,80,8000,37777,2020,5543 192.168.1.0/24`.  nmap is
not installed on a Pi by default and ONVIF needs a library we do not ship, so
this is the same idea in the standard library: a TCP connect scan over the ports
in `storemind.ingest.urls.SCAN_PORTS`, then a brand guess from which ports
answered.

    python tools/discover.py --subnet 192.168.1.0/24
    python tools/discover.py --subnet 192.168.1.0/24 --json > cameras.json

The brand guess is a *starting point for step 4* (probe), never a conclusion:
port 37777 says "something Dahua-flavoured", which in an Indian store is more
often CP Plus than Dahua.  The candidate URLs printed alongside are the ones
`storemind.ingest.urls` would build, so onboarding is: run this, try the printed
URL with tools/probe.py, paste the working one into store.yaml.

**Permission first.**  research/24 section 5 step 1 and section 8: scanning a
network you were not invited onto is not ours to do.  The tool says so on every
run and refuses ranges bigger than `--max-hosts`, because a wide scan is both
rude and slow.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from storemind.ingest.urls import SCAN_PORTS, CameraEndpoint

# Which open port suggests which template.  Ordered most-specific first; a
# recorder answering only on 554 tells us nothing about its brand.
PORT_BRAND_HINTS: dict[int, tuple[str, ...]] = {
    37777: ("cpplus", "dahua"),   # Dahua SDK; CP Plus recorders speak it too
    8000: ("hikvision", "prama"),  # Hikvision SDK
    2020: ("tapo",),               # Tapo's ONVIF port
    5543: ("cpplus",),             # some CP Plus models
}

# A /24 is one shop's subnet and takes a few seconds.  Anything much larger is
# either a mistake or a scan of somebody else's network.
DEFAULT_MAX_HOSTS = 256
DEFAULT_TIMEOUT_S = 0.35
DEFAULT_WORKERS = 64

PERMISSION_NOTICE = (
    "Scan only a network you have written permission to scan "
    "(research/24 section 5 step 1)."
)


@dataclass
class DiscoveredDevice:
    ip: str
    open_ports: list[int] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)

    @property
    def has_rtsp(self) -> bool:
        return 554 in self.open_ports

    def candidate_urls(self, channel: int = 1) -> dict[str, str]:
        """One candidate sub-stream URL per guessed brand, credentials omitted.

        Credentials are deliberately absent: this output is meant to be pasted
        into a terminal or a chat message while onboarding, and section 8 keeps
        passwords out of both.
        """
        urls: dict[str, str] = {}
        for brand in self.brands:
            endpoint = CameraEndpoint(name=self.ip, brand=brand, ip=self.ip, channel=channel)
            port = next((p for p in (5543,) if p in self.open_ports and brand == "cpplus"), None)
            if port is not None:
                endpoint.port = port
            urls[brand] = endpoint.stream_url()
        return urls

    def as_dict(self) -> dict[str, object]:
        return {
            "ip": self.ip,
            "open_ports": self.open_ports,
            "brands": self.brands,
            "candidates": self.candidate_urls(),
        }


def guess_brands(open_ports: list[int]) -> list[str]:
    """Brands worth trying, most likely first; empty when only RTSP answered."""
    guesses: list[str] = []
    for port, brands in PORT_BRAND_HINTS.items():
        if port in open_ports:
            guesses += [b for b in brands if b not in guesses]
    return guesses


def port_is_open(ip: str, port: int, timeout: float = DEFAULT_TIMEOUT_S) -> bool:
    """One TCP connect.  A refused connection and a filtered one both mean 'no'."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            return sock.connect_ex((ip, port)) == 0
        except OSError:
            # Unreachable host, bad interface, permission denied: not a camera.
            return False


def scan_host(ip: str, ports: tuple[int, ...] = SCAN_PORTS,
              timeout: float = DEFAULT_TIMEOUT_S) -> DiscoveredDevice | None:
    """Scan one address.  Returns None when nothing answered."""
    open_ports = [port for port in ports if port_is_open(ip, port, timeout)]
    if not open_ports:
        return None
    return DiscoveredDevice(ip=ip, open_ports=open_ports, brands=guess_brands(open_ports))


def hosts(subnet: str, max_hosts: int = DEFAULT_MAX_HOSTS) -> list[str]:
    """Usable addresses in a CIDR range, or the single address if given one."""
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as error:
        raise SystemExit(f"not a subnet or address: {subnet} ({error})") from None
    addresses = [str(ip) for ip in (network.hosts() if network.num_addresses > 1 else [network.network_address])]
    if len(addresses) > max_hosts:
        raise SystemExit(
            f"{subnet} covers {len(addresses)} hosts, more than --max-hosts {max_hosts}. "
            "Scan one subnet at a time (a shop is normally a /24). " + PERMISSION_NOTICE
        )
    return addresses


def discover(subnet: str, ports: tuple[int, ...] = SCAN_PORTS,
             timeout: float = DEFAULT_TIMEOUT_S, workers: int = DEFAULT_WORKERS,
             max_hosts: int = DEFAULT_MAX_HOSTS) -> list[DiscoveredDevice]:
    """Scan a subnet in parallel and return what answered, lowest IP first."""
    addresses = hosts(subnet, max_hosts)
    found: list[DiscoveredDevice] = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(addresses)))) as pool:
        for device in pool.map(lambda ip: scan_host(ip, ports, timeout), addresses):
            if device is not None:
                found.append(device)
    found.sort(key=lambda d: ipaddress.ip_address(d.ip))
    return found


def format_report(devices: list[DiscoveredDevice]) -> str:
    """The human-readable onboarding report, ready to read out to whoever owns the shop."""
    if not devices:
        return ("Nothing answered on any camera port.\n"
                "Check you are on the same LAN as the recorder, or cable straight into "
                "its LAN port (research/24 section 4 topology 2).")
    lines = [f"{len(devices)} device(s) answered:", ""]
    for device in devices:
        ports = ", ".join(str(p) for p in device.open_ports)
        lines.append(f"  {device.ip}  ports: {ports}")
        if not device.has_rtsp:
            lines.append("      no RTSP on 554 - probably not a camera or recorder")
        for brand, url in device.candidate_urls().items():
            lines.append(f"      try {brand:<10} {url}")
        if device.has_rtsp and not device.brands:
            lines.append("      RTSP is open but the brand is unclear - try each template:")
            lines.append("      tools/probe.py <url>  (see docs/CCTV_ONBOARDING.md)")
        lines.append("")
    lines.append("Next: step 4, probe a URL for codec, resolution and real FPS.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find cameras and recorders on a store LAN (research/24 section 5 step 3).",
        epilog=PERMISSION_NOTICE,
    )
    parser.add_argument("--subnet", required=True,
                        help="CIDR range or single address, e.g. 192.168.1.0/24")
    parser.add_argument("--ports", default=",".join(str(p) for p in SCAN_PORTS),
                        help="comma-separated TCP ports to try")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help="seconds to wait for each connection")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-hosts", type=int, default=DEFAULT_MAX_HOSTS,
                        help="refuse ranges larger than this")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        ports = tuple(int(p) for p in args.ports.split(",") if p.strip())
    except ValueError:
        raise SystemExit(f"--ports must be comma-separated numbers, got {args.ports!r}") from None
    if not ports:
        raise SystemExit("--ports is empty")

    if not args.json:
        print(PERMISSION_NOTICE, file=sys.stderr)
    devices = discover(args.subnet, ports, args.timeout, args.workers, args.max_hosts)
    if args.json:
        print(json.dumps([d.as_dict() for d in devices], indent=2))
    else:
        print(format_report(devices))
    return 0 if devices else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
