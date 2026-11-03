"""Local network IPv4 detection and offline QR code generation for iPhone / Mobile pairing."""

from __future__ import annotations

import base64
import io
import socket
from typing import Any

import qrcode


def _is_private_lan_ip(ip: str) -> bool:
    if not ip or ip.startswith("127.") or ip.startswith("169.254."):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    # Exclude common WSL / Docker virtual bridge 172.17-31 if 192.168 / 10 exists (handled via priority sorting)
    return a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)


def _ip_priority(ip: str) -> int:
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("10."):
        return 1
    if ip.startswith("172."):
        return 2
    return 9


def get_lan_ips() -> list[str]:
    """Detect active LAN IPv4 addresses suitable for iPhone Wi-Fi connection."""
    candidates: list[str] = []

    # Primary probe: outbound UDP routing table lookup (no packets sent)
    for target in (("223.5.5.5", 80), ("8.8.8.8", 80), ("114.114.114.114", 80)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.3)
                s.connect(target)
                ip = s.getsockname()[0]
                if _is_private_lan_ip(ip) and ip not in candidates:
                    candidates.append(ip)
                break
        except Exception:
            continue

    # Secondary probe: enumerate host interfaces
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if _is_private_lan_ip(ip) and ip not in candidates:
                candidates.append(ip)
    except Exception:
        pass

    candidates.sort(key=lambda x: (0 if candidates and x == candidates[0] and x.startswith("192.168.") else 1, _ip_priority(x)))
    return candidates or ["127.0.0.1"]


def make_qr_base64_png(data: str) -> str:
    """Generate an offline Base64 data-URI PNG QR code for instant mobile scanning."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def build_lan_pairing_info(port: int) -> dict[str, Any]:
    """Return LAN URLs and QR codes for iPhone / Web pairing modal."""
    ips = get_lan_ips()
    primary_ip = ips[0]
    primary_url = f"http://{primary_ip}:{port}"
    all_endpoints = [
        {
            "ip": ip,
            "url": f"http://{ip}:{port}",
            "shortcut_api": f"http://{ip}:{port}/api/shortcut",
            "qr_data_uri": make_qr_base64_png(f"http://{ip}:{port}"),
        }
        for ip in ips
    ]
    return {
        "port": port,
        "primary_ip": primary_ip,
        "primary_url": primary_url,
        "qr_data_uri": all_endpoints[0]["qr_data_uri"],
        "shortcut_api": f"{primary_url}/api/shortcut",
        "endpoints": all_endpoints,
    }
