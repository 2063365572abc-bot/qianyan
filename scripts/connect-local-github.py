"""Use the already authenticated GitHub CLI for this project's local observer.

Only the ignored local .env is updated. Tokens are captured in memory and never
printed. Production should use a separately scoped read credential.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="The explicitly selected owner/repository")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}", args.repo) or args.repo.rsplit("/", 1)[-1] in (".", ".."):
        parser.exit(1, "Invalid repository name.\n")
    root = Path(__file__).resolve().parents[1]
    env = root / ".env"
    if not env.is_file():
        parser.exit(1, "Run the local bootstrap first; .env does not exist.\n")
    try:
        result = subprocess.run(["gh", "api", "repos/" + args.repo], capture_output=True, text=True, timeout=30)
        if result.returncode:
            parser.exit(1, "Selected repository cannot be read using the configured GitHub CLI account.\n")
        metadata = json.loads(result.stdout)
        if not isinstance(metadata, dict) or metadata.get("full_name", "").lower() != args.repo.lower():
            parser.exit(1, "Repository metadata did not match the selected binding.\n")
        credential = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"], capture_output=True, text=True, timeout=10)
        token = credential.stdout.strip()
        if credential.returncode or not re.fullmatch(r"[A-Za-z0-9_]{20,300}", token):
            parser.exit(1, "GitHub CLI credential is unavailable; no environment change made.\n")
        lines = env.read_text(encoding="utf-8-sig").splitlines()
        existing = next((line.partition("=")[2].strip().strip("\"'") for line in lines if line.split("=", 1)[0].strip() == "GITHUB_TOKEN"), "")
        if existing and existing != token:
            parser.exit(1, "A different GitHub token is already configured; it was preserved.\n")
        lines = [line for line in lines if line.split("=", 1)[0].strip() != "GITHUB_TOKEN"]
        temporary = root / ".env.github-setup.tmp"
        temporary.write_text("\n".join(lines + ["GITHUB_TOKEN=" + token]) + "\n", encoding="utf-8")
        temporary.replace(env)
        print(json.dumps({"github_configured": True, "repo": args.repo,
                          "default_branch": metadata.get("default_branch"), "url": metadata.get("html_url"),
                          "restart_api_worker": True}))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        parser.exit(1, "GitHub setup failed; credential values are never printed.\n")


if __name__ == "__main__":
    main()
