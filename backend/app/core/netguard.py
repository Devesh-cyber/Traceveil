"""Safe outbound requests (protection against server-side request forgery, "SSRF").

Our crawler visits addresses it finds on web pages. Without a guard, a hostile page could
point it at our own network (a database, a cloud "metadata" address, a home router) and
make our server fetch things the page's author could never reach.

Before EVERY request, including every hop of a redirect, we check:
  * the scheme is http or https
  * there is no user:password in the address
  * the port is a normal web port
  * every IP address the hostname points to is a public, globally routable address
    (private, loopback, link-local, carrier-grade NAT and multicast ranges are refused)

Known gap: a hostile DNS server could answer differently between our check and the
actual connection ("DNS rebinding"). When deploying, also run the worker with a firewall
that only allows outbound web traffic to the public internet.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}
CACHE_SECONDS = 300
CACHE_MAX_ENTRIES = 4096

Resolver = Callable[[str], Awaitable[list[str]]]


class BlockedByNetworkPolicy(Exception):
    """The request was refused by our own safety rules (not by the website)."""


async def system_resolver(host: str) -> list[str]:
    """Ask the operating system which IP addresses a hostname points to."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})

def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(value.split("%")[0])  # drop an IPv6 zone like %eth0
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:  # ::ffff:10.0.0.1 is really 10.0.0.1
        ip = ip.ipv4_mapped
    return ip


def is_public_ip(value: str) -> bool:
    ip = _parse_ip(value)
    return ip is not None and ip.is_global and not ip.is_multicast


def is_loopback_ip(value: str) -> bool:
    ip = _parse_ip(value)
    return ip is not None and ip.is_loopback


class NetGuard:
    def __init__(
        self,
        resolver: Resolver = system_resolver,
        enabled: bool = True,
        allow_loopback: bool = False,
    ) -> None:
        self._resolver = resolver
        self.enabled = enabled
        # DEVELOPMENT ONLY: lets us test against a fake website on this computer
        # (localhost / 127.0.0.1, any port). Every other private address stays blocked.
        self.allow_loopback = allow_loopback
        self._cache: dict[str, tuple[float, list[str]]] = {}

    async def check(self, url: str) -> None:
        """Raise BlockedByNetworkPolicy if the URL is unsafe. A hostname that cannot be
        resolved raises the resolver's own OSError (that is 'unreachable', not 'unsafe')."""
        if not self.enabled:
            return
        parts = urlsplit(url)
        if parts.scheme not in ALLOWED_SCHEMES:
            raise BlockedByNetworkPolicy(f"scheme not allowed: {parts.scheme or 'none'}")
        if parts.username or parts.password:
            raise BlockedByNetworkPolicy("credentials in the address are not allowed")
        try:
            port = parts.port
        except ValueError:
            raise BlockedByNetworkPolicy("invalid port") from None
        host = parts.hostname
        if not host:
            raise BlockedByNetworkPolicy("missing host")
        if port not in ALLOWED_PORTS and not self.allow_loopback:
            raise BlockedByNetworkPolicy(f"port not allowed: {port}")

        addresses = await self._addresses_for(host)

        if self.allow_loopback and addresses and all(is_loopback_ip(a) for a in addresses):
            return  # a service on this very computer, in development
        if port not in ALLOWED_PORTS:
            raise BlockedByNetworkPolicy(f"port not allowed: {port}")
        if not addresses or not all(is_public_ip(a) for a in addresses):
            raise BlockedByNetworkPolicy("host points to a non-public address")

    async def _addresses_for(self, host: str) -> list[str]:
        now = time.monotonic()
        cached = self._cache.get(host)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        if _parse_ip(host) is not None:
            addresses = [host]  # the address is already a literal IP
        else:
            addresses = await self._resolver(host)
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache.clear()
        self._cache[host] = (now, addresses)
        return addresses