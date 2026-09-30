# Database backup and recovery / 数据库备份恢复

PostgreSQL data persists in `qianyan_postgres_data`. Do not run `docker compose down -v` against an instance with needed data.

Linux production:

```sh
sh scripts/backup-db.sh /your/private/backup/location
sh scripts/restore-db.sh /your/private/backup/location/qianyan-TIMESTAMP.dump CONFIRM_REPLACE_DATABASE
```

The restore command stops API and worker before replacing data. Run it first on a separate test instance and verify goals, settings, evidence, notifications and job recovery. A successful dump alone does not prove recoverability. Keep a backup before every migration. Store an encrypted copy away from the VM and protect it like personal account data. Scheduled backups and off-machine retention depend on your actual host/storage setup.

Windows PowerShell: `./scripts/backup-db.ps1`. It avoids binary stdout redirection. Restore on Linux using the script above, or use `docker compose cp` plus `pg_restore` while API/worker are stopped.

本机 volume 只保证容器重建后的数据保留，不等于异地灾备。更新前备份；恢复会替换当前数据库，必须先保留当前数据。先在独立实例验证一次恢复，核对目标、记忆、证据与后台恢复。备份包含私人数据，需访问控制与异地加密存储。

## Local restore evidence / 本地恢复证据

On **2026-10-01 00:55:30 Asia/Shanghai** (2026-09-30 16:55:30 UTC), native PostgreSQL **16.15** `pg_dump -Fc` / `pg_restore --exit-on-error` successfully restored the migrated development database `qianyan` into a newly created dedicated `qianyan_restore_test` database. The source database was read-only during verification; no existing database was replaced or deleted.

The dump used an exported repeatable-read snapshot, so the running worker could not make the baseline inconsistent. All **nine public tables** matched source snapshot row counts, column definitions, constraints and Alembic revision **0001**. The snapshot included four goals, twenty tasks and twelve records; the archive was **23,310 bytes**. No credential values or private record contents were printed. Evidence and the private backup are retained in ignored `.local/backups/` (`restore-evidence-20260930T165530Z.json`).

The helper is `scripts/verify-portable-backup.py`, run with the backend Python environment. It permits only the local development source, refuses production configuration and refuses to overwrite an existing restore target. This is evidence of native PostgreSQL recovery. **The Docker backup/restore wrapper scripts, an off-machine backup and disaster recovery have not been exercised.** Row counts/schema checks do not constitute a complete application journey test against the restored instance.

北京时间 2026-10-01 00:55 已用原生 PostgreSQL 工具完成恢复演练：迁移后的真实开发库恢复到独立测试库，九张表数量、结构、约束和迁移版本一致。源库保持只读，未覆盖任何旧库。已验证原生数据库恢复；Docker 包装脚本和异地灾备仍待真实环境核验。
