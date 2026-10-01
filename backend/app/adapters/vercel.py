"""Read production attempts without mistaking a preview or old success for current success."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx

from .types import AdapterError, HTTPAdapter, Observation, fingerprint, redact
from .health import probe_https


def _provider_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = value if "://" in value else "https://" + value
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        return candidate
    except ValueError:
        return None


def _time(value: object) -> str | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    return value if isinstance(value, str) else None


def _created(row: dict) -> float:
    try:
        return float(row.get("created") or row.get("createdAt") or 0)
    except (ValueError, TypeError):
        return 0


class VercelAdapter(HTTPAdapter):
    def __init__(
        self, token: str, *, client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = "https://api.vercel.com", timeout: float = 20.0, health_probe=None,
    ):
        if not token:
            raise AdapterError("configuration", "A Vercel read token is required.")
        super().__init__(base_url, client=client, transport=transport, timeout=timeout)
        self._token = token
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._health_probe = health_probe or (probe_https if transport is None and client is None else None)

    async def observe(self, project_id: str, team_id: str | None = None) -> list[Observation]:
        if not project_id or len(project_id) > 200 or (team_id is not None and len(team_id) > 200):
            raise AdapterError("input", "Invalid Vercel project binding.")
        params = {"projectId": project_id, "target": "production", "limit": 10}
        team = {"teamId": team_id} if team_id else {}
        data, _ = await self.request_json("GET", "/v7/deployments", headers=self._headers, params=params | team)
        if not isinstance(data, dict) or not isinstance(data.get("deployments"), list):
            raise AdapterError("invalid_response", "Vercel returned an invalid deployment list.")
        # Enforce the filter again locally. Never treat a preview as production.
        rows = [row for row in data["deployments"] if isinstance(row, dict) and row.get("target") == "production"]
        rows.sort(key=_created, reverse=True)
        if not rows:
            facts = {"kind": "deployment", "role": "latest", "project_id": project_id, "observed": False,
                     "state": "unknown", "health": "not_observed", "production_url": None}
            return [Observation("vercel", project_id + ":latest", fingerprint(facts), facts, partial=True)]
        selected = [("latest", rows[0])]
        previous = next((row for row in rows[1:] if (row.get("readyState") or row.get("state")) == "READY"), None)
        if previous:
            selected.append(("previous_ready", previous))
        observations = []
        for role, row in selected:
            uid = row.get("uid") or row.get("id")
            if not isinstance(uid, str) or not uid or len(uid) > 200:
                raise AdapterError("invalid_response", "Vercel returned an invalid deployment identifier.")
            detail, _ = await self.request_json(
                "GET", "/v13/deployments/" + quote(uid, safe=""), headers=self._headers, params=team,
            )
            if not isinstance(detail, dict):
                raise AdapterError("invalid_response", "Vercel returned invalid deployment details.")
            if detail.get("target") not in (None, "production"):
                # Inconsistent source data is not sufficient production evidence.
                raise AdapterError("inconsistent_source", "Deployment details contradict the production listing.", True)
            state = detail.get("readyState") or detail.get("state") or row.get("readyState") or row.get("state") or "unknown"
            aliases = detail.get("alias") or []
            if not isinstance(aliases, list):
                aliases = []
            alias_urls = [url for alias in aliases if (url := _provider_url(alias))]
            deployment_url = _provider_url(detail.get("url") or row.get("url"))
            meta = detail.get("meta") or row.get("meta") or {}
            if not isinstance(meta, dict):
                meta = {}
            log_status, excerpt = "not_requested", []
            partial = False
            if role == "latest" and state in ("ERROR", "CANCELED"):
                try:
                    logs, _ = await self.request_json(
                        "GET", "/v3/deployments/" + quote(uid, safe="") + "/events",
                        headers=self._headers, params=team | {"limit": 30, "follow": 0, "direction": "backward"},
                        max_bytes=65_536,
                    )
                    if isinstance(logs, list):
                        excerpt = []
                        for event in logs[:30]:
                            if not isinstance(event, dict):
                                continue
                            payload = event.get("payload") or {}
                            text = event.get("text") or (payload.get("text") if isinstance(payload, dict) else "") or ""
                            excerpt.append(redact(str(text), (self._token,))[:500])
                        excerpt = [line for line in excerpt if line][:20]
                        log_status = "bounded_excerpt"
                        pagination = data.get("pagination") or {}
                        partial = len(logs) >= 30 or (isinstance(pagination, dict) and bool(pagination.get("next")))
                    else:
                        log_status, partial = "invalid_response", True
                except AdapterError as exc:
                    # Lack of logs must not erase the observable deployment state.
                    log_status, partial = exc.code, True
            facts = {
                "kind": "deployment", "role": role, "project_id": project_id, "observed": True,
                "completion_evidence_complete": True,
                "deployment_id": uid, "target": "production", "state": state,
                "deployment_url": deployment_url, "production_url": alias_urls[0] if alias_urls else None,
                "aliases": alias_urls, "health": "not_observed", "current_routing": "not_observed",
                "git_sha": meta.get("githubCommitSha") or meta.get("gitlabCommitSha") or meta.get("bitbucketCommitSha"),
                "build_log_status": log_status, "build_log_excerpt": excerpt,
                "meaning": "latest_production_attempt" if role == "latest" else "previous_ready_deployment_not_verified_live",
            }
            if alias_urls and self._health_probe:
                allowed = {urlsplit(u).hostname for u in alias_urls}
                facts["health"] = await self._health_probe(alias_urls[0], allowed)
                facts["health_url"] = alias_urls[0]
            updated = _time(detail.get("updatedAt") or detail.get("ready") or detail.get("createdAt") or row.get("created"))
            observations.append(Observation("vercel", project_id + ":" + role, fingerprint(facts), facts, updated, deployment_url, partial))
        return observations
