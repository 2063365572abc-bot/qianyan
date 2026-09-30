"""Create development-only credentials in ignored local files, without printing secrets."""
import hashlib
import json
import secrets
from pathlib import Path


root = Path(__file__).resolve().parents[1]
env_file = root / ".env"
local_dir = root / ".local"
if env_file.exists():
    raise SystemExit(".env exists; leaving existing credentials unchanged.")
local_dir.mkdir(exist_ok=True)
owner_password = secrets.token_urlsafe(24)
database_password = secrets.token_urlsafe(32)
salt = secrets.token_hex(16)
digest = hashlib.pbkdf2_hmac("sha256", owner_password.encode(), salt.encode(), 600_000).hex()
password_hash = f"pbkdf2_sha256$600000${salt}${digest}"
content = (root / ".env.example").read_text(encoding="utf-8")
content = content.replace("POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={database_password}\n")
content = content.replace("REPLACE_WITH_LOCAL_DB_PASSWORD", database_password)
content = content.replace("SESSION_SECRET=\n", f"SESSION_SECRET={secrets.token_urlsafe(48)}\n")
# Quotes keep dollar characters literal for both dotenv and Docker Compose.
content = content.replace("OWNER_PASSWORD_HASH=\n", f"OWNER_PASSWORD_HASH='{password_hash}'\n")
env_file.write_text(content, encoding="utf-8")
(local_dir / "access.json").write_text(json.dumps({"local_owner_password": owner_password, "purpose": "Local development only; rotate for production"}, indent=2), encoding="utf-8")
print("Created .env and .local/access.json for local development. No secrets were printed. Configure real providers separately.")
