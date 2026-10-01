import asyncio
import pytest
from app.adapters.health import probe_https, public_address


class Writer:
    def __init__(self):
        self.request, self.closed = b"", False
    def write(self, value):
        self.request += value
    async def drain(self):
        pass
    def close(self):
        self.closed = True
    async def wait_closed(self):
        pass


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "100.64.0.1", "::1", "fe80::1", "::ffff:127.0.0.1", "2002:7f00:1::1"])
def test_non_public_and_transition_addresses_blocked(address):
    assert not public_address(address)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://demo.vercel.app", "https://user:secret@demo.vercel.app", "https://demo.vercel.app:8000", "https://demo.vercel.app/?secret=x", "https://other.vercel.app", "https://service.local"])
async def test_unapproved_origin_never_resolves(url):
    async def forbidden(host):
        raise AssertionError("Blocked URL must not resolve")
    result = await probe_https(url, {"demo.vercel.app"}, resolver=forbidden)
    assert result["status"] == "blocked_url"


@pytest.mark.asyncio
async def test_mixed_public_private_dns_never_connects():
    async def resolver(host):
        return ["8.8.8.8", "127.0.0.1"]
    async def forbidden(*args, **kwargs):
        raise AssertionError("Private DNS result must not connect")
    assert (await probe_https("https://demo.vercel.app", {"demo.vercel.app"}, resolver=resolver, connector=forbidden))["status"] == "blocked_address"


@pytest.mark.asyncio
@pytest.mark.parametrize("code,state", [(200, "reachable"), (401, "protected"), (403, "protected"), (302, "redirect_unchecked"), (429, "rate_limited"), (503, "http_error")])
async def test_pinned_ip_tls_hostname_and_header_only_probe(code, state):
    writer = Writer()
    resolver_calls = []
    async def resolver(host):
        resolver_calls.append(host)
        return ["8.8.8.8"]
    async def connector(ip, port, **kwargs):
        assert ip == "8.8.8.8" and port == 443
        assert kwargs["server_hostname"] == "demo.vercel.app"
        assert kwargs["ssl"].check_hostname and kwargs["ssl"].verify_mode != 0
        reader = asyncio.StreamReader(limit=8192)
        reader.feed_data(f"HTTP/1.1 {code} Response\r\nLocation: http://127.0.0.1/\r\nSet-Cookie: private\r\n\r\n".encode())
        return reader, writer
    result = await probe_https("https://demo.vercel.app", {"demo.vercel.app"}, resolver=resolver, connector=connector)
    assert result["status"] == state and result["http_status"] == code
    assert resolver_calls == ["demo.vercel.app"] and writer.closed
    assert b"Authorization" not in writer.request and b"Cookie:" not in writer.request
    assert "private" not in str(result) and "127.0.0.1" not in str(result)


@pytest.mark.asyncio
async def test_observation_failure_is_unknown_not_offline():
    async def resolver(host):
        raise OSError("DNS failure")
    result = await probe_https("https://demo.vercel.app", {"demo.vercel.app"}, resolver=resolver)
    assert result["status"] == "network_unknown" and "down" in result["meaning"]
