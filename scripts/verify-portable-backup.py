"""Restore a consistent local PostgreSQL backup into a new, isolated test database."""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

from preflight import read_env


ROOT = Path(__file__).resolve().parents[1]
TARGET_DATABASE = "qianyan_restore_test"
TABLES = ("alembic_version", "goals", "jobs", "notifications", "records", "runs", "sessions", "spaces", "tasks")


def schema(connection):
    columns = connection.execute("""
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY c.relname,a.attnum
    """).fetchall()
    constraints = connection.execute("""
        SELECT c.relname, k.conname, pg_get_constraintdef(k.oid)
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname,k.conname
    """).fetchall()
    return {"columns": columns, "constraints": constraints}


def counts(connection):
    return {table: connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] for table in TABLES}


def main():
    if os.name != "nt":
        raise SystemExit("This verification helper targets the Windows portable development PostgreSQL instance.")
    values = read_env(ROOT / ".env")
    if values.get("APP_ENV") == "production":
        raise SystemExit("Refusing to operate on a production configuration.")
    runtime = Path(os.environ["LOCALAPPDATA"]) / "Qianyan" / "postgres16" / "pgsql" / "bin"
    if not (runtime / "pg_dump.exe").is_file():
        raise SystemExit("Portable PostgreSQL tools are missing.")
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("Local PostgreSQL password is missing.")
    # Fixed local source/target, never arbitrary names or remote deployment credentials.
    connection_params = dict(host="127.0.0.1", port=5432, user="qianyan", password=password)
    environment = {**os.environ, "PGPASSWORD": password}
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_directory = ROOT / ".local" / "backups"
    backup_directory.mkdir(exist_ok=True)
    backup = backup_directory / f"qianyan-verified-{timestamp}.dump"
    evidence_file = backup_directory / f"restore-evidence-{timestamp}.json"

    with psycopg.connect(dbname="postgres", **connection_params, autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TARGET_DATABASE,)).fetchone():
            raise SystemExit("Restore target already exists; refusing to overwrite or delete it.")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET_DATABASE)))

    with psycopg.connect(dbname="qianyan", **connection_params, autocommit=True) as source:
        source.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
        source_counts = counts(source)
        source_schema = schema(source)
        revision = source.execute("SELECT version_num FROM alembic_version").fetchall()
        subprocess.run([str(runtime / "pg_dump.exe"), "-h", "127.0.0.1", "-p", "5432", "-U", "qianyan", "-d", "qianyan", "-Fc", "--snapshot", snapshot, "-f", str(backup)], env=environment, check=True)
        source.execute("COMMIT")

    subprocess.run([str(runtime / "pg_restore.exe"), "-h", "127.0.0.1", "-p", "5432", "-U", "qianyan", "-d", TARGET_DATABASE, "--no-owner", "--no-acl", "--exit-on-error", str(backup)], env=environment, check=True)
    with psycopg.connect(dbname=TARGET_DATABASE, **connection_params) as restored:
        if restored.execute("SELECT current_database()").fetchone()[0] != TARGET_DATABASE:
            raise RuntimeError("Unexpected restore database identity")
        restored_counts = counts(restored)
        assert source_counts == restored_counts, "Restored table counts differ from the exported snapshot"
        assert source_schema == schema(restored), "Restored columns/constraints differ"
        assert revision == restored.execute("SELECT version_num FROM alembic_version").fetchall(), "Migration revision differs"
    evidence = dict(checked_at_utc=timestamp, source_database="qianyan", restored_database=TARGET_DATABASE,
                    consistent_snapshot=True, row_counts=restored_counts, schema_columns_constraints_equal=True,
                    alembic_revision=revision, backup_bytes=backup.stat().st_size,
                    scope="Native PostgreSQL restore; Docker scripts and disaster recovery not exercised")
    evidence_file.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Local PostgreSQL backup restored to {TARGET_DATABASE}; schema, migration revision and snapshot counts match.")
    print(f"Evidence saved under .local/backups/{evidence_file.name}. No secret values or private rows were printed.")


if __name__ == "__main__":
    main()
