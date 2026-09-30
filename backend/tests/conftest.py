"""Integration tests use a separate PostgreSQL database, never personal data."""
import os
from app.config import config

test_url = os.environ.get("QIANYAN_TEST_DATABASE_URL") or config().database_url
if test_url.rsplit("/", 1)[-1] == "qianyan":
    test_url = test_url.rsplit("/", 1)[0] + "/qianyan_test"
if not test_url.rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError("Tests require an explicitly separate database ending in _test")
os.environ["DATABASE_URL"] = test_url
config.cache_clear()
