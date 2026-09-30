param([int]$Port = 5432, [string]$RuntimeDirectory = "$env:LOCALAPPDATA\Qianyan\postgres16")
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path "$PSScriptRoot\..").Path
$taskPgRoot = [System.IO.Path]::GetFullPath($RuntimeDirectory)
$taskBin = Join-Path $taskPgRoot 'pgsql\bin'
$taskData = Join-Path $taskPgRoot 'data'
if (!(Test-Path -LiteralPath "$taskBin\pg_ctl.exe")) {
    throw 'Portable PostgreSQL binaries are missing. Download the official EDB PostgreSQL 16 Windows x64 binary archive and extract pgsql/ to the runtime directory. Use an ASCII-only path, such as %LOCALAPPDATA%/Qianyan/postgres16. This script installs no system service.'
}
$taskEnvFile = Join-Path $taskRoot '.env'
if (!(Test-Path -LiteralPath $taskEnvFile)) { throw 'Run scripts/bootstrap-local.py or configure .env first.' }
$taskPasswordLine = Get-Content -LiteralPath $taskEnvFile | Where-Object { $_ -match '^POSTGRES_PASSWORD=' } | Select-Object -First 1
$taskPassword = ($taskPasswordLine -split '=', 2)[1].Trim("'", '"')
if (!$taskPassword) { throw 'POSTGRES_PASSWORD is missing.' }
$taskOldPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $taskPassword
    if (!(Test-Path -LiteralPath "$taskData\PG_VERSION")) {
        $taskPasswordFile = Join-Path $taskPgRoot 'init-password.tmp'
        [System.IO.File]::WriteAllText($taskPasswordFile, $taskPassword, [System.Text.UTF8Encoding]::new($false))
        try {
            & "$taskBin\initdb.exe" -D $taskData -U qianyan -A scram-sha-256 --pwfile $taskPasswordFile --encoding=UTF8 --locale=C
            if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL initialization failed.' }
        } finally {
            Remove-Item -LiteralPath $taskPasswordFile -ErrorAction SilentlyContinue
        }
    }
    & "$taskBin\pg_ctl.exe" -D $taskData status *> $null
    if ($LASTEXITCODE -ne 0) {
        $taskLog = Join-Path $taskPgRoot 'postgres.log'
        # Existing cluster files are preserved. Bind only localhost; no system service is installed.
        & "$taskBin\pg_ctl.exe" -D $taskData -l $taskLog -o "-h 127.0.0.1 -p $Port" -w start
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL start failed. Check postgres.log in the runtime directory.' }
    }
    [string]$taskExists = & "$taskBin\psql.exe" -h 127.0.0.1 -p $Port -U qianyan -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='qianyan'"
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL connection failed.' }
    if ($taskExists -ne '1') {
        & "$taskBin\createdb.exe" -h 127.0.0.1 -p $Port -U qianyan qianyan
        if ($LASTEXITCODE -ne 0) { throw 'Database creation failed.' }
    }
    Write-Output "Local PostgreSQL is listening at 127.0.0.1:$Port. Run backend migrations next."
} finally {
    $env:PGPASSWORD = $taskOldPassword
}
