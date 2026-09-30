param([string]$BackupDirectory = "$PSScriptRoot\..\.local\backups")
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path "$PSScriptRoot\..").Path
New-Item -ItemType Directory -Force -Path $BackupDirectory | Out-Null
$taskBackupFile = Join-Path (Resolve-Path $BackupDirectory).Path ("qianyan-" + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '.dump')
# Use a container temporary file and docker cp: PowerShell stdout redirection corrupts binary dump data on older Windows versions.
docker compose --env-file "$taskRoot\.env" -f "$taskRoot\infra\compose.yaml" exec -T postgres pg_dump -U qianyan -d qianyan -Fc -f /tmp/qianyan-backup.dump
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
docker compose --env-file "$taskRoot\.env" -f "$taskRoot\infra\compose.yaml" cp postgres:/tmp/qianyan-backup.dump $taskBackupFile
if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed' }
docker compose --env-file "$taskRoot\.env" -f "$taskRoot\infra\compose.yaml" exec -T postgres rm /tmp/qianyan-backup.dump
Write-Output "Backup created: $taskBackupFile. Copy it to separate encrypted storage."
