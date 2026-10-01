"""Validate deployment configuration without printing secret values."""
import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlparse


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        key, separator, value = raw.partition("=")
        if separator:
            values[key.strip()] = value.strip().strip("\"").strip("'")
    return {**values, **os.environ}


def validate(values: dict[str, str], require_wecom=False) -> list[str]:
    errors = []
    provider = values.get("AI_PROVIDER", "nebius")
    if provider not in ("nebius", "aliyun"):
        errors.append("AI_PROVIDER: choose nebius or aliyun")
    prefix = "ALIYUN" if provider == "aliyun" else "NEBIUS"
    required = ["POSTGRES_PASSWORD", "SESSION_SECRET", "OWNER_PASSWORD_HASH", "APP_PUBLIC_URL", "APP_DOMAIN", prefix + "_API_KEY", prefix + "_MODEL_ID"]
    for key in required:
        value = values.get(key, "")
        if not value or re.search(r"replace|placeholder|change.?me|your[_ -]", value, re.I):
            errors.append(f"{key}: missing or placeholder")
    if values.get("APP_ENV") != "production":
        errors.append("APP_ENV: must equal production")
    if len(values.get("SESSION_SECRET", "")) < 32:
        errors.append("SESSION_SECRET: use at least 32 random characters")
    owner_hash = re.fullmatch(r"pbkdf2_sha256\$([0-9]+)\$[a-f0-9]{32}\$[a-f0-9]{64}", values.get("OWNER_PASSWORD_HASH", ""))
    if not owner_hash or not 100_000 <= int(owner_hash.group(1)) <= 2_000_000:
        errors.append("OWNER_PASSWORD_HASH: use the app.auth.hash_password PBKDF2 format")
    password = values.get("POSTGRES_PASSWORD", "")
    if len(password) < 24 or not re.fullmatch(r"[A-Za-z0-9_-]+", password):
        errors.append("POSTGRES_PASSWORD: use at least 24 random URL-safe characters")
    domain = values.get("APP_DOMAIN", "")
    if not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", domain) or domain.endswith(".example.com") or domain == "example.com":
        errors.append("APP_DOMAIN: use a real DNS hostname, without scheme or port")
    parsed = urlparse(values.get("APP_PUBLIC_URL", ""))
    if parsed.scheme != "https" or parsed.hostname != domain or parsed.path not in ("", "/"):
        errors.append("APP_PUBLIC_URL: must be the HTTPS origin matching APP_DOMAIN")
    if provider == "nebius" and not values.get("NEBIUS_MODEL_ID", "").lower().startswith("nvidia/"):
        errors.append("NEBIUS_MODEL_ID: use the account-verified NVIDIA Nemotron model ID")
    if values.get("AI_MODE", "live") != "live":
        errors.append("AI_MODE: production requires live")
    if require_wecom:
        for key in ("WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_APP_SECRET", "WECOM_USER_ID", "WECOM_CALLBACK_TOKEN", "WECOM_ENCODING_AES_KEY"):
            if not values.get(key):
                errors.append(f"{key}: required for real WeCom acceptance")
        if len(values.get("WECOM_ENCODING_AES_KEY", "")) != 43:
            errors.append("WECOM_ENCODING_AES_KEY: expected 43 characters")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--require-live-wecom", action="store_true")
    args = parser.parse_args()
    if not args.env.is_file():
        parser.exit(1, f"Missing environment file: {args.env}\n")
    errors = validate(read_env(args.env), args.require_live_wecom)
    if errors:
        print("Production configuration needs attention:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("Configuration preflight passed. Network, credentials and phone delivery still require live verification.")
