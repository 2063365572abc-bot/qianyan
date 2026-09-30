param([string]$RuntimeDirectory = "$env:LOCALAPPDATA\Qianyan\postgres16")
$ErrorActionPreference = 'Stop'
$taskPgRoot = [System.IO.Path]::GetFullPath($RuntimeDirectory)
if (!(Test-Path -LiteralPath "$taskPgRoot\pgsql\bin\pg_ctl.exe")) { throw 'Portable PostgreSQL was not found.' }
& "$taskPgRoot\pgsql\bin\pg_ctl.exe" -D "$taskPgRoot\data" -w -m fast stop
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL stop failed or was already stopped. No cluster data was removed.' }
