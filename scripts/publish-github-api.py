"""Publish committed Git objects through GitHub's official Git Data API.

Development transport fallback, never an agent tool. Every blob/tree/commit SHA
must match local Git before a reference is changed. No forced reference updates.
"""
import argparse
import asyncio
import base64
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx
from app.config import config


def git(*arguments):
    return subprocess.check_output(["git", "-C", str(ROOT), *arguments], stderr=subprocess.DEVNULL)


def author(value):
    match = re.fullmatch(r"(.*?) <([^>]+)> (\d+) ([+-])(\d{2})(\d{2})", value)
    if not match:
        raise ValueError("Unsupported local commit author format")
    name, email, epoch, sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes)) * (1 if sign == "+" else -1)
    return {"name": name, "email": email,
            "date": datetime.fromtimestamp(int(epoch), timezone(offset)).isoformat()}


def local_commit(sha):
    headers, message = git("cat-file", "commit", sha).decode("utf-8").split("\n\n", 1)
    lines = [line.split(" ", 1) for line in headers.splitlines()]
    if any(name not in ("tree", "parent", "author", "committer") for name, _ in lines):
        raise ValueError("Signed or extended commits require native Git transport")
    fields = dict(lines)
    return {"message": message, "tree": fields["tree"],
            "parents": [value for key, value in lines if key == "parent"],
            "author": author(fields["author"]), "committer": author(fields["committer"])}


def entries(sha):
    rows = []
    for item in git("ls-tree", "-r", "-z", sha).split(b"\0"):
        if not item:
            continue
        head, path = item.decode("utf-8").split("\t", 1)
        mode, kind, identity = head.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("Only ordinary committed files are supported")
        if (path == ".env" or path.startswith(".env.") and path != ".env.example"
                or path.startswith((".local/", "devpost/archive/")) or path == "devpost/learner-profile.md"):
            raise ValueError("Private file is present in the local commit history")
        rows.append({"path": path, "mode": mode, "type": kind, "sha": identity})
    return rows


async def publish(repo, branch):
    if git("status", "--porcelain").strip():
        raise ValueError("Commit the reviewable worktree before publishing")
    cfg = config()
    if not cfg.github_token:
        raise ValueError("Configure a GitHub credential locally first")
    tip = git("rev-parse", "HEAD").decode().strip()
    revisions = git("rev-list", "--reverse", tip).decode().splitlines()
    history = [(sha, local_commit(sha), entries(sha)) for sha in revisions]
    blobs = {item["sha"] for _, _, rows in history for item in rows}
    content = {sha: git("cat-file", "blob", sha) for sha in blobs}
    secrets = [cfg.session_secret, cfg.owner_password_hash, cfg.github_token, cfg.nebius_api_key, cfg.aliyun_api_key,
               cfg.vercel_token, cfg.wecom_app_secret, cfg.wecom_callback_token, cfg.wecom_encoding_aes_key]
    password = urlsplit(cfg.database_url).password
    if password and len(password) > 8:
        secrets.append(password)
    access = ROOT / ".local/access.json"
    if access.is_file():
        secrets.append(json.loads(access.read_text())["local_owner_password"])
    if any(secret.encode() in body for secret in secrets if secret for body in content.values()):
        raise ValueError("Local secret was found in publishable history; nothing uploaded")
    prefix = "/repos/" + repo
    headers = {"Authorization": "Bearer " + cfg.github_token,
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
    async with httpx.AsyncClient(base_url="https://api.github.com", headers=headers, timeout=30, follow_redirects=False) as client:
        async def request(method, path, payload=None, allowed=(200, 201)):
            response = await client.request(method, prefix + path, json=payload)
            if response.status_code not in allowed:
                raise ValueError(f"GitHub API returned HTTP {response.status_code} for {method} {path}; no force update attempted")
            return response.json() if response.content else None
        metadata = await request("GET", "")
        if metadata.get("full_name", "").lower() != repo.lower() or not metadata.get("permissions", {}).get("push"):
            raise ValueError("Selected repository is not writable by the configured account")
        branches = await request("GET", "/branches?per_page=100")
        current = next((row["commit"]["sha"] for row in branches if row["name"] == branch), None)
        known_blobs = set()
        if current:
            if current not in revisions:
                raise ValueError("Remote branch has unrelated changes; native reconciliation required")
            offset = revisions.index(current) + 1
            known_blobs = {item["sha"] for _, _, rows in history[:offset] for item in rows}
            history = history[offset:]
        bootstrap = None
        if not branches:
            # GitHub cannot create refs in a branchless repository. Initialize a
            # checked temporary default branch, then publish the exact local history.
            readme = git("show", "HEAD:README.md")
            result = await request("PUT", "/contents/README.md", {"message": "Initialize repository for API transport",
                "content": base64.b64encode(readme).decode("ascii"),
                "committer": {"name": "Qianyan Automation", "email": "qianyan@localhost"}})
            bootstrap = (metadata["default_branch"], result["commit"]["sha"])
            if bootstrap[0] == branch:
                raise ValueError("Bootstrap branch must differ from the selected local branch")
        needed = {item["sha"] for _, _, rows in history for item in rows} - known_blobs
        print(json.dumps({"stage": "publishing_objects", "commits": len(history), "blobs": len(needed)}), flush=True)
        uploaded = set(known_blobs)
        for sha, commit, rows in history:
            for item in rows:
                identity = item["sha"]
                if identity in uploaded:
                    continue
                result = await request("POST", "/git/blobs", {"content": base64.b64encode(content[identity]).decode("ascii"), "encoding": "base64"})
                if result["sha"] != identity:
                    raise ValueError("Blob hash mismatch; branch was not changed")
                uploaded.add(identity)
                if len(uploaded) % 20 == 0:
                    print(json.dumps({"stage": "blobs_verified", "count": len(uploaded)}), flush=True)
            result = await request("POST", "/git/trees", {"tree": rows})
            if result["sha"] != commit["tree"]:
                raise ValueError("Tree hash mismatch; branch was not changed")
            result = await request("POST", "/git/commits", commit)
            if result["sha"] != sha:
                raise ValueError("Commit hash mismatch; branch was not changed")
            print(json.dumps({"stage": "commit_verified", "sha": sha}), flush=True)
        ref = "/git/refs/heads/" + branch
        if current != tip:
            if current:
                await request("PATCH", ref, {"sha": tip, "force": False})
            else:
                await request("POST", "/git/refs", {"ref": "refs/heads/" + branch, "sha": tip})
        if bootstrap:
            await request("PATCH", "", {"default_branch": branch})
            observed = await request("GET", "/git/ref/heads/" + bootstrap[0])
            if observed["object"]["sha"] == bootstrap[1]:
                await request("DELETE", "/git/refs/heads/" + bootstrap[0], allowed=(204,))
        verified = await request("GET", "/git/ref/heads/" + branch)
        if verified["object"]["sha"] != tip:
            raise ValueError("Remote head differs from the committed worktree")
        git("update-ref", "refs/remotes/origin/" + branch, tip)
        git("branch", "--set-upstream-to=origin/" + branch, branch)
        report = {"repo": repo, "branch": branch, "local_sha": tip, "remote_sha": tip,
                  "all_object_hashes_verified": True, "published_at": datetime.now(timezone.utc).isoformat()}
        (ROOT / ".local/github-publication.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="master")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}", args.repo) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", args.branch):
        parser.exit(1, "Invalid explicit repository/branch.\n")
    try:
        asyncio.run(publish(args.repo, args.branch))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "API transport failed; inspect remote state before retrying"
        parser.exit(1, message + "\n")
