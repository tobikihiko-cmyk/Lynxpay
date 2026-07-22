"""Request networking helpers shared by callback and audit code."""

import ipaddress

from fastapi import Request

from app.core.config import settings
from app.database import get_db


def ip_in_cidrs(ip: str, cidr_csv: str, *, _label: str = "CIDR list") -> bool:
    del _label
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for raw in cidr_csv.split(","):
        try:
            if raw.strip() and address in ipaddress.ip_network(raw.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def get_client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    # Forwarding headers have no integrity unless the immediate peer is a
    # configured reverse proxy. This applies to Cloudflare's header too.
    if ip_in_cidrs(direct, settings.TRUSTED_PROXY_CIDRS):
        if settings.TRUST_CF_CONNECTING_IP:
            cloudflare = request.headers.get("cf-connecting-ip", "").strip()
            try:
                if cloudflare:
                    return str(ipaddress.ip_address(cloudflare))
            except ValueError:
                pass
        forwarded = request.headers.get("x-forwarded-for", "").strip()
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            try:
                return str(ipaddress.ip_address(real))
            except ValueError:
                pass
    return direct


__all__ = ["get_client_ip", "get_db", "ip_in_cidrs"]
