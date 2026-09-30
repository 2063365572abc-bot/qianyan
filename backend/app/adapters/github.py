"""Only reads a bounded slice of a bound repository at one immutable head SHA."""

from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import quote

import httpx

from .types import NOT_MODIFIED, AdapterError, HTTPAdapter, Observation, fingerprint


class GitHubAdapter(HTTPAdapter):
    def __init__(
        self, token: str = "", *, client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = "https://api.github.com", timeout: float = 20.0,
    ):
        super().__init__(base_url, client=client, transport=transport, timeout=timeout)
        self._headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._etag_cache: dict[tuple, tuple] = {}

    async def _get(self, path: str, params: dict | None = None):
        key = (path, tuple(sorted((params or {}).items())))
        cached = self._etag_cache.get(key)
        headers = self._headers | ({"If-None-Match": cached[0]} if cached else {})
        data, response_headers = await self.request_json(
            "GET", path, headers=headers, params=params, allow_not_modified=cached is not None,
        )
        if data is NOT_MODIFIED and cached:
            return cached[1], cached[2]
        etag = response_headers.get("etag")
        if etag:
            if len(self._etag_cache) >= 128 and key not in self._etag_cache:
                self._etag_cache.pop(next(iter(self._etag_cache)))
            self._etag_cache[key] = (etag, data, response_headers)
        return data, response_headers

    async def observe(
        self, owner: str, repo: str, branch: str | None = None,
        workflow: str | None = None, core_dir: str | None = "src",
    ) -> list[Observation]:
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) or name in (".", "..") for name in (owner, repo)):
            raise AdapterError("input", "Invalid GitHub repository binding.")
        if core_dir is not None and (
            len(core_dir) > 300 or core_dir.startswith("/")
            or any(p in (".", "..") for p in core_dir.split("/"))
        ):
            raise AdapterError("input", "Invalid repository directory binding.")
        prefix = f"/repos/{owner}/{repo}"
        metadata, _ = await self._get(prefix)
        if not isinstance(metadata, dict) or not metadata.get("id"):
            raise AdapterError("invalid_response", "GitHub returned invalid repository metadata.")
        branch = branch or metadata.get("default_branch")
        repo_id = f"{owner}/{repo}"
        repo_facts = {"kind": "repo", "exists": True, "repo_id": metadata["id"], "branch": branch}
        observations = [Observation("github", repo_id, fingerprint(repo_facts), repo_facts, metadata.get("updated_at"), metadata.get("html_url"))]
        if not branch:
            return observations
        try:
            rows, _ = await self._get(prefix + "/commits", {"sha": branch, "per_page": 1})
        except AdapterError as exc:
            if exc.code != "conflict":
                raise
            rows = []  # GitHub returns 409 for an empty repository.
        if not isinstance(rows, list):
            raise AdapterError("invalid_response", "GitHub returned invalid commit results.")
        if not rows:
            facts = {"kind": "commit", "branch": branch, "observed": False, "sha": None}
            observations.append(Observation("github", repo_id + ":commit", fingerprint(facts), facts, partial=True))
            return observations
        if not isinstance(rows[0], dict) or not rows[0].get("sha"):
            raise AdapterError("invalid_response", "GitHub returned an invalid commit.")
        head = rows[0]
        sha = str(head["sha"])
        if not re.fullmatch(r"[A-Za-z0-9]{1,64}", sha):
            raise AdapterError("invalid_response", "GitHub returned an invalid commit identifier.")
        updated = head.get("commit", {}).get("committer", {}).get("date")
        detail, headers = await self._get(prefix + "/commits/" + sha, {"per_page": 100})
        files = detail.get("files", []) if isinstance(detail, dict) else []
        if not isinstance(detail, dict) or not isinstance(files, list) or any(not isinstance(f, dict) for f in files):
            raise AdapterError("invalid_response", "GitHub returned invalid changed files.")
        files_partial = len(files) >= 100 or 'rel="next"' in headers.get("link", "")
        observations.append(Observation(
            "github", repo_id + ":commit", sha,
            {"kind": "commit", "sha": sha, "branch": branch, "changed_files": [
                {"filename": f.get("filename"), "status": f.get("status")} for f in files[:100]
            ], "changes_scope": "head_commit_only"}, updated, head.get("html_url"), files_partial,
        ))
        observations.append(await self._read_content(prefix, repo_id, "README.md", sha, updated, "readme"))
        if core_dir:
            observations.append(await self._read_content(prefix, repo_id, core_dir, sha, updated, "core_dir"))
        if workflow:
            if len(str(workflow)) > 200:
                raise AdapterError("input", "Workflow binding is too long.")
            runs, _ = await self._get(
                prefix + "/actions/workflows/" + quote(str(workflow), safe="") + "/runs",
                {"branch": branch, "head_sha": sha, "per_page": 30},
            )
            if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
                raise AdapterError("invalid_response", "GitHub returned invalid workflow results.")
            matched = [r for r in runs["workflow_runs"] if isinstance(r, dict) and r.get("head_sha") == sha and r.get("head_branch") == branch]
            matched.sort(key=lambda r: (r.get("created_at") or r.get("run_started_at") or r.get("updated_at") or "", r.get("id") or 0, r.get("run_attempt") or 1), reverse=True)
            run = matched[0] if matched else None
            facts = {
                "kind": "ci", "workflow": str(workflow), "head_sha": sha,
                "observed": run is not None, "status": run.get("status") if run else "unknown",
                "conclusion": run.get("conclusion") if run else None,
                "passed": bool(run and run.get("status") == "completed" and run.get("conclusion") == "success"),
                "scope": "configured_workflow_only", "run_id": run.get("id") if run else None,
                "run_attempt": run.get("run_attempt") if run else None,
            }
            observations.append(Observation(
                "github", repo_id + ":ci:" + str(workflow), fingerprint(facts), facts,
                run.get("updated_at") if run else None, run.get("html_url") if run else None,
                partial=run is None,
            ))
        return observations

    async def _read_content(self, prefix, repo_id, path, sha, updated, kind) -> Observation:
        source_id = repo_id + ":" + path
        try:
            data, _ = await self._get(prefix + "/contents/" + quote(path, safe="/"), {"ref": sha})
        except AdapterError as exc:
            if exc.code != "not_found":
                raise
            # A 404 can also hide an inaccessible file; do not invent absence.
            facts = {"kind": kind, "path": path, "head_sha": sha, "exists": None, "observed": False, "reason": "not_found_or_inaccessible"}
            return Observation("github", source_id, fingerprint(facts), facts, updated, partial=True)
        if kind == "core_dir":
            is_directory = isinstance(data, list)
            entries = data[:100] if is_directory else []
            if any(not isinstance(entry, dict) for entry in entries):
                raise AdapterError("invalid_response", "GitHub returned invalid directory entries.")
            facts = {"kind": kind, "path": path, "head_sha": sha, "exists": True, "is_directory": is_directory, "entries": [
                {"name": entry.get("name"), "type": entry.get("type"), "sha": entry.get("sha")} for entry in entries
            ]}
            return Observation("github", source_id, fingerprint(facts), facts, updated, partial=(len(data) > 100 if is_directory else True))
        if not isinstance(data, dict):
            raise AdapterError("invalid_response", "GitHub returned invalid README metadata.")
        size = data.get("size") or 0
        content = ""
        partial = data.get("type") != "file" or data.get("encoding") != "base64" or size > 65_536
        if data.get("encoding") == "base64" and isinstance(data.get("content"), str):
            try:
                decoded = base64.b64decode("".join(data["content"].split()), validate=True)
                partial = partial or len(decoded) > 65_536
                content = decoded[:65_536].decode("utf-8", errors="replace")
                partial = partial or "\ufffd" in content
            except (ValueError, binascii.Error):
                partial = True
        facts = {"kind": "readme", "path": path, "head_sha": sha, "exists": True, "content": content, "file_sha": data.get("sha"), "size": size}
        return Observation("github", source_id, fingerprint(facts), facts, updated, data.get("html_url"), partial)
