"""Risk tests use real HTTP contracts with MockTransport, not live credentials."""

import asyncio
import base64
import hashlib
import json
import struct
import time

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.adapters import AdapterError, GitHubAdapter, TokenFactoryAdapter, VercelAdapter, WeComAdapter


def github_transport(*, stale_ci=False, readme_size=0, directory_count=1, contents_status=200):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        path = request.url.path
        if path == "/repos/me/project":
            return httpx.Response(200, json={"id": 10, "default_branch": "main", "html_url": "https://github.com/me/project"})
        if path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": "abc123", "commit": {"committer": {"date": "2026-10-01T00:00:00Z"}}}])
        if path.endswith("/commits/abc123"):
            return httpx.Response(200, json={"files": [{"filename": "README.md", "status": "modified"}]})
        if "/contents/" in path:
            assert request.url.params["ref"] == "abc123"
        if path.endswith("/contents/README.md"):
            if contents_status != 200:
                return httpx.Response(contents_status, json={"message": "not found"})
            text = b"# Project\n## Installation\nInstall the dependencies.\n" + b"a" * readme_size
            return httpx.Response(200, json={"type": "file", "sha": "readmeblob", "encoding": "base64", "size": len(text), "content": base64.b64encode(text).decode()})
        if path.endswith("/contents/src"):
            return httpx.Response(200, json=[{"name": f"file-{i}.py", "type": "file", "sha": str(i)} for i in range(directory_count)])
        if path.endswith("/actions/workflows/ci.yml/runs"):
            assert request.url.params["head_sha"] == "abc123"
            return httpx.Response(200, json={"workflow_runs": [{"id": 12, "head_sha": "old" if stale_ci else "abc123", "head_branch": "main", "status": "completed", "conclusion": "success", "updated_at": "2026-10-01T00:01:00Z"}]})
        raise AssertionError(f"Unexpected request: {path}")

    return httpx.MockTransport(handler), calls


@pytest.mark.asyncio
async def test_github_reads_one_immutable_head_and_does_not_claim_test_coverage():
    transport, calls = github_transport()
    adapter = GitHubAdapter("read-only-token", transport=transport)
    rows = await adapter.observe("me", "project", "main", "ci.yml", "src")
    facts = {row.facts["kind"]: row.facts for row in rows}
    assert facts["readme"]["exists"] is True
    assert "Installation" in facts["readme"]["content"]
    assert facts["ci"]["passed"] is True
    assert facts["ci"]["scope"] == "configured_workflow_only"
    assert facts["commit"]["changes_scope"] == "head_commit_only"
    assert all(call.headers["X-GitHub-Api-Version"] == "2026-03-10" for call in calls)
    assert [r.version for r in rows] == [r.version for r in await adapter.observe("me", "project", "main", "ci.yml", "src")]
    await adapter.aclose()


@pytest.mark.asyncio
async def test_stale_ci_is_not_current_progress():
    transport, _ = github_transport(stale_ci=True)
    adapter = GitHubAdapter(transport=transport)
    row = next(r for r in await adapter.observe("me", "project", workflow="ci.yml") if r.facts["kind"] == "ci")
    assert row.facts["passed"] is False
    assert row.facts["observed"] is False
    assert row.partial is True
    await adapter.aclose()


@pytest.mark.asyncio
async def test_partial_readme_and_directory_are_explicit():
    transport, _ = github_transport(readme_size=70_000, directory_count=120)
    adapter = GitHubAdapter(transport=transport)
    rows = await adapter.observe("me", "project")
    readme = next(r for r in rows if r.facts["kind"] == "readme")
    directory = next(r for r in rows if r.facts["kind"] == "core_dir")
    assert readme.partial
    assert len(readme.facts["content"].encode()) == 65_536
    assert directory.partial and len(directory.facts["entries"]) == 100
    await adapter.aclose()


@pytest.mark.asyncio
async def test_404_contents_does_not_invent_a_deleted_readme():
    transport, _ = github_transport(contents_status=404)
    adapter = GitHubAdapter(transport=transport)
    row = next(r for r in await adapter.observe("me", "project") if r.facts["kind"] == "readme")
    assert row.facts["exists"] is None and row.partial
    await adapter.aclose()


@pytest.mark.asyncio
async def test_github_auth_failure_is_not_repository_absence():
    adapter = GitHubAdapter("sensitive-key", transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"message": "sensitive-key"})))
    with pytest.raises(AdapterError) as caught:
        await adapter.observe("me", "private")
    assert caught.value.code == "permission"
    assert "sensitive-key" not in str(caught.value)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_empty_repository_still_proves_repository_exists():
    def handler(request):
        if request.url.path.endswith("/commits"):
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        return httpx.Response(200, json={"id": 10, "default_branch": "main"})

    adapter = GitHubAdapter(transport=httpx.MockTransport(handler))
    rows = await adapter.observe("me", "project")
    assert rows[0].facts["kind"] == "repo" and rows[0].facts["exists"]
    assert rows[1].facts["kind"] == "commit" and not rows[1].facts["observed"]
    await adapter.aclose()


@pytest.mark.asyncio
async def test_new_pending_run_overrides_old_success_even_if_old_finishes_later():
    transport, _ = github_transport()

    async def handler(request):
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": [
                {"id": 1, "head_sha": "abc123", "head_branch": "main", "status": "completed", "conclusion": "success", "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:06:00Z"},
                {"id": 2, "head_sha": "abc123", "head_branch": "main", "status": "queued", "conclusion": None, "created_at": "2026-10-01T00:05:00Z", "updated_at": "2026-10-01T00:05:00Z"},
            ]})
        return await transport.handle_async_request(request)

    adapter = GitHubAdapter(transport=httpx.MockTransport(handler))
    row = next(r for r in await adapter.observe("me", "project", workflow="ci.yml") if r.facts["kind"] == "ci")
    assert row.facts["passed"] is False and row.facts["run_id"] == 2
    await adapter.aclose()


@pytest.mark.asyncio
async def test_github_etag_revalidation_reuses_only_matching_response():
    seen = []

    def handler(request):
        seen.append(request)
        if request.headers.get("If-None-Match") == '"known-version"':
            return httpx.Response(304)
        return httpx.Response(200, json={"id": 10}, headers={"ETag": '"known-version"'})

    adapter = GitHubAdapter(transport=httpx.MockTransport(handler))
    assert (await adapter._get("/repos/me/project"))[0] == {"id": 10}
    assert (await adapter._get("/repos/me/project"))[0] == {"id": 10}
    await adapter._get("/repos/me/other")
    assert "If-None-Match" not in seen[0].headers
    assert seen[1].headers["If-None-Match"] == '"known-version"'
    assert "If-None-Match" not in seen[2].headers
    await adapter.aclose()


def vercel_transport(*, previews_only=False, log_denied=False):
    calls = []
    rows = [{"uid": "dpl_preview", "target": "preview", "state": "READY", "created": 4000},
            {"uid": "dpl_failed", "target": "production", "state": "ERROR", "created": 3000},
            {"uid": "dpl_old", "target": "production", "state": "READY", "created": 2000}]

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        path = request.url.path
        if path == "/v7/deployments":
            assert request.url.params["target"] == "production"
            return httpx.Response(200, json={"deployments": rows[:1] if previews_only else rows})
        if path == "/v13/deployments/dpl_failed":
            return httpx.Response(200, json={"id": "dpl_failed", "target": "production", "readyState": "ERROR", "url": None})
        if path == "/v13/deployments/dpl_old":
            return httpx.Response(200, json={"id": "dpl_old", "target": "production", "readyState": "READY", "url": "old-deployment.vercel.app", "alias": ["project.example.com"], "meta": {"githubCommitSha": "abc123"}})
        if path == "/v3/deployments/dpl_failed/events":
            assert request.url.params["follow"] == "0"
            assert request.url.params["limit"] == "30"
            if log_denied:
                return httpx.Response(403, json={"error": "not allowed"})
            return httpx.Response(200, json=[{"text": "Build error Bearer secret-vercel VERCEL_TOKEN=secret-vercel"}])
        raise AssertionError(f"Unexpected request: {path}")

    return httpx.MockTransport(handler), calls


@pytest.mark.asyncio
async def test_latest_failure_and_previous_ready_are_distinct_and_health_unknown():
    transport, calls = vercel_transport()
    adapter = VercelAdapter("secret-vercel", transport=transport)
    rows = await adapter.observe("project", "team")
    latest, previous = rows
    assert latest.facts["role"] == "latest" and latest.facts["state"] == "ERROR"
    assert latest.facts["production_url"] is None
    assert previous.facts["role"] == "previous_ready" and previous.facts["state"] == "READY"
    assert previous.facts["production_url"] == "https://project.example.com"
    assert previous.facts["health"] == "not_observed"
    assert previous.facts["current_routing"] == "not_observed"
    assert "secret-vercel" not in json.dumps(latest.facts)
    assert all(call.url.params["teamId"] == "team" for call in calls)
    assert not any("dpl_preview" in call.url.path for call in calls)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_preview_success_does_not_satisfy_production():
    transport, _ = vercel_transport(previews_only=True)
    adapter = VercelAdapter("secret", transport=transport)
    rows = await adapter.observe("project")
    assert len(rows) == 1 and rows[0].facts["observed"] is False
    assert rows[0].facts["state"] == "unknown"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_unavailable_logs_preserve_observable_failure():
    transport, _ = vercel_transport(log_denied=True)
    adapter = VercelAdapter("secret", transport=transport)
    row = (await adapter.observe("project"))[0]
    assert row.facts["state"] == "ERROR"
    assert row.facts["build_log_status"] == "permission" and row.partial
    await adapter.aclose()


@pytest.mark.asyncio
async def test_token_factory_real_contract_and_model_verification():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer secret-nebius"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "nvidia/nemotron-test"}]})
        body = json.loads(request.content)
        assert body["model"] == "nvidia/nemotron-test" and body["stream"] is False
        assert body["tools"][0]["type"] == "function"
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": None, "tool_calls": [{"id": "call-1", "function": {"name": "propose_plan", "arguments": "{}"}}]}}], "usage": {"total_tokens": 21}})

    adapter = TokenFactoryAdapter("secret-nebius", "nvidia/nemotron-test", transport=httpx.MockTransport(handler))
    assert await adapter.verify_model() == "nvidia/nemotron-test"
    result = await adapter.complete([{"role": "user", "content": "Plan my goal"}], [{"type": "function", "function": {"name": "propose_plan"}}])
    assert result["content"] is None and result["tool_calls"][0]["id"] == "call-1"
    assert result["usage"]["total_tokens"] == 21
    await adapter.aclose()


@pytest.mark.asyncio
async def test_model_failure_never_becomes_a_fake_completion_or_echoes_context():
    adapter = TokenFactoryAdapter("secret-key", "nvidia/test", transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "secret-key private goal text"})))
    with pytest.raises(AdapterError) as caught:
        await adapter.complete([{"role": "user", "content": "private goal text"}])
    assert caught.value.code == "authentication" and not caught.value.retryable
    assert "secret-key" not in str(caught.value) and "private goal" not in str(caught.value)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_unavailable_nvidia_catalog_is_explicit():
    adapter = TokenFactoryAdapter("key", "nvidia/test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"id": "other/model"}]})))
    with pytest.raises(AdapterError, match="catalog"):
        await adapter.verify_model()
    await adapter.aclose()


# Independent known-answer ciphertext generated using the official Node
# @wecom/crypto 1.0.1 exports.encrypt/getSignature on 2026-10-01.
# https://cdn.jsdelivr.net/npm/@wecom/crypto@1.0.1/lib/index.js
KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
CIPHERTEXT = "4j/AuRx71kQlxVlzbpsMWDLoz+szVuWwtVufEkBLwxQdOtsDcZ23Jjc+vUxrV/zxcClXc6PrhR5dTbbeAFBsKNNWYRGbtf5OozfhwJrS2ijUaybPOrki0r2Ebs9XD2WmsMMokPLmK+I3XUd3gC7/oM96pwqhUY0b6pn2H1SeomkTNPYZv1RyrC1kzYidzoglRnQvamNS50VkYotPIEioF4BAHveFh4uz976Q7EQCHZH4gZVvArHoPOdlFxfV+sHyyuNokjZbt9DZDYmY7A/nKZhbrXbUMu82hPMAN/94XjOo8gsRPfu44t/cDV68AYY/DxfBlhchQeblwDpBnomYXA=="
SIGNATURE = "c278ca95821b7c5cf4ca0f01c0742a9137f9a470"
URL_CIPHERTEXT = "4j/AuRx71kQlxVlzbpsMWAwpCrG1H37G5T5Jv68qwhpN26e1Ig1mrcCQnm2MNJ6nL+WjBxX/B99bsFSKHLaLVA=="


def wecom_adapter(**kwargs):
    return WeComAdapter("ww-test", 1000002, "application-secret", "owner", "callback-token", KEY, **kwargs)


def encrypt_callback(plaintext, *, corp="ww-test", timestamp=None, bad_padding=False):
    """Independent test sender, matching the provider wire format."""
    data = plaintext.encode()
    raw = b"0123456789abcdef" + struct.pack(">I", len(data)) + data + corp.encode()
    amount = 32 - len(raw) % 32
    padded = raw + bytes([amount]) * amount
    if bad_padding:
        padded = padded[:-2] + bytes([amount + 1, amount])
    key = base64.b64decode(KEY + "=")
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = base64.b64encode(cipher.update(padded) + cipher.finalize()).decode()
    query = {"timestamp": str(timestamp or int(time.time())), "nonce": "nonce-test"}
    query["msg_signature"] = hashlib.sha1("".join(sorted(("callback-token", query["timestamp"], query["nonce"], encrypted))).encode()).hexdigest()
    return query, f"<xml><Encrypt>{encrypted}</Encrypt></xml>"


def inner_message(sender="owner", agent="1000002", corp="ww-test"):
    return f"<xml><ToUserName>{corp}</ToUserName><FromUserName>{sender}</FromUserName><CreateTime>1700000000</CreateTime><MsgType>text</MsgType><Content>明天继续</Content><MsgId>123456</MsgId><AgentID>{agent}</AgentID></xml>"


def test_official_wecom_known_answer_callback_and_url_verification():
    adapter = wecom_adapter(callback_max_skew=None)
    query = {"msg_signature": SIGNATURE, "timestamp": "1700000000", "nonce": "nonce-test"}
    event = adapter.parse_callback(query, f"<xml><Encrypt>{CIPHERTEXT}</Encrypt></xml>")
    assert event["text"] == "明天继续" and event["message_id"] == "123456"
    query["echostr"] = URL_CIPHERTEXT
    query["msg_signature"] = hashlib.sha1("".join(sorted(("callback-token", query["timestamp"], query["nonce"], URL_CIPHERTEXT))).encode()).hexdigest()
    assert adapter.verify_url(query) == "hello-verify"


def test_wecom_callback_roundtrip_and_signature_failure():
    adapter = wecom_adapter()
    query, xml = encrypt_callback(inner_message())
    assert adapter.parse_callback(query, xml)["user_id"] == "owner"
    query["msg_signature"] = "0" * 40
    with pytest.raises(AdapterError) as caught:
        adapter.parse_callback(query, xml)
    assert caught.value.code == "callback_invalid"


@pytest.mark.parametrize("sender,agent,corp", [("external-person", "1000002", "ww-test"), ("owner", "12", "ww-test"), ("owner", "1000002", "another-enterprise")])
def test_other_user_enterprise_and_application_rejected(sender, agent, corp):
    adapter = wecom_adapter()
    query, xml = encrypt_callback(inner_message(sender, agent, corp))
    with pytest.raises(AdapterError) as caught:
        adapter.parse_callback(query, xml)
    assert caught.value.code == "callback_recipient"


def test_cipher_corpid_padding_expiry_and_dtd_are_rejected():
    adapter = wecom_adapter()
    for query, xml in (
        encrypt_callback(inner_message(), corp="wrong-corp"),
        encrypt_callback(inner_message(), bad_padding=True),
        encrypt_callback(inner_message(), timestamp=1700000000),
    ):
        with pytest.raises(AdapterError):
            adapter.parse_callback(query, xml)
    with pytest.raises(AdapterError):
        adapter.parse_callback({}, '<!DOCTYPE xml [<!ENTITY content "attack">]><xml/>')


@pytest.mark.asyncio
async def test_wecom_caches_token_binds_recipient_and_records_acceptance_only():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={"access_token": "cached-token", "expires_in": 7200})
        payload = json.loads(request.content)
        assert payload["touser"] == "owner" and payload["agentid"] == 1000002
        assert request.url.params["access_token"] == "cached-token"
        return httpx.Response(200, json={"errcode": 0, "msgid": "provider-123"})

    adapter = wecom_adapter(transport=httpx.MockTransport(handler))
    assert await adapter.send_text("Goal needs attention") == {"accepted": True, "provider_id": "provider-123"}
    await adapter.send_text("Another meaningful change")
    assert len(calls) == 3
    with pytest.raises(AdapterError):
        await adapter.send_text("汉" * 700)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_wecom_timeout_marks_uncertain_delivery_and_redacts_secret():
    def handler(request):
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={"access_token": "private-token", "expires_in": 7200})
        raise httpx.ReadTimeout("private-token sensitive message text", request=request)

    adapter = wecom_adapter(transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError) as caught:
        await adapter.send_text("sensitive message text")
    assert caught.value.code == "delivery_unknown" and not caught.value.retryable
    assert "private-token" not in str(caught.value) and "sensitive message" not in str(caught.value)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_injected_client_remains_owned_by_caller():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []})))
    adapter = TokenFactoryAdapter("key", "nvidia/test", client=client)
    await adapter.aclose()
    assert not client.is_closed
    await client.aclose()


@pytest.mark.asyncio
async def test_absolute_timeout_bounds_a_slow_response_stream():
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"data":'
            await asyncio.sleep(1.2)
            yield b'[]}'

    adapter = TokenFactoryAdapter("key", "nvidia/test", timeout=1, transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowStream())))
    with pytest.raises(AdapterError) as caught:
        await adapter.list_models()
    assert caught.value.code == "timeout" and caught.value.retryable
    await adapter.aclose()


@pytest.mark.asyncio
async def test_response_size_and_redirects_are_bounded():
    for response, expected in (
        (httpx.Response(200, content=b" " * 1_048_577), "response_too_large"),
        (httpx.Response(302, headers={"Location": "https://another-host.example/"}), "unexpected_status"),
    ):
        adapter = TokenFactoryAdapter("key", "nvidia/test", transport=httpx.MockTransport(lambda _, resp=response: resp))
        with pytest.raises(AdapterError) as caught:
            await adapter.list_models()
        assert caught.value.code == expected
        await adapter.aclose()
