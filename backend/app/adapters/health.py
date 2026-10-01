"""Bounded HTTPS observation of provider-confirmed production origins.

Resolve once, reject non-public addresses, then connect to the pinned IP while
validating TLS against the original hostname. Never follow redirects or forward
provider credentials. Only response headers are needed for reachability.
"""
import asyncio
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit


def public_address(value):
    try:
        address = ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast or address.is_reserved:
            return False
        if isinstance(address, ipaddress.IPv6Address) and (address.ipv4_mapped or address.sixtofour or address.teredo):
            return False
        return True
    except ValueError:
        return False


async def resolve(host):
    rows = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(row[4][0] for row in rows))


async def probe_https(url, allowed_hosts, *, resolver=resolve, connector=asyncio.open_connection, timeout=6):
    writer = None
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        confirmed = {str(h).encode("idna").decode("ascii").lower().rstrip(".") for h in allowed_hosts}
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment or host not in confirmed
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host) or "." not in host
                or host.endswith((".local", ".localhost", ".internal"))):
            return {"status": "blocked_url", "meaning": "No request made to an unapproved origin"}
        async with asyncio.timeout(max(0.1, min(timeout, 10))):
            addresses = await resolver(host)
            if not addresses or any(not public_address(address) for address in addresses):
                return {"status": "blocked_address", "meaning": "No request made to non-public DNS answers"}
            tls = ssl.create_default_context()
            tls.set_alpn_protocols(["http/1.1"])
            reader, writer = await connector(addresses[0], 443, ssl=tls, server_hostname=host,
                limit=8192, ssl_handshake_timeout=min(timeout, 6), ssl_shutdown_timeout=1)
            writer.write((f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Qianyan-Reachability/0.1\r\n"
                          "Accept: */*\r\nConnection: close\r\n\r\n").encode("ascii"))
            await writer.drain()
            headers = await reader.readuntil(b"\r\n\r\n")
            if len(headers) > 8192:
                return {"status": "network_unknown", "meaning": "Response headers exceeded observation limit"}
            match = re.match(rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: |\r\n)", headers)
            if not match:
                return {"status": "network_unknown", "meaning": "Invalid HTTP response"}
            status = int(match[1])
            if status in (401, 403):
                state, meaning = "protected", "Origin responded but requires authentication"
            elif 200 <= status < 300:
                state, meaning = "reachable", "Origin responded; functional acceptance is not tested"
            elif 300 <= status < 400:
                state, meaning = "redirect_unchecked", "Redirect observed and not followed"
            elif status == 429:
                state, meaning = "rate_limited", "Origin responded with a request limit"
            else:
                state, meaning = "http_error", "Origin returned an HTTP error; application health needs investigation"
            return {"status": state, "http_status": status, "meaning": meaning}
    except (TimeoutError, OSError, ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return {"status": "network_unknown", "meaning": "Observation failed; this does not prove the service is down"}
    finally:
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, OSError, ssl.SSLError):
                pass
