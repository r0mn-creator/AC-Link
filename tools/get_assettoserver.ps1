# Downloads AssettoServer (Windows x64) from its official GitHub releases into vendor\assettoserver.
# Usage: powershell -ExecutionPolicy Bypass -File tools\get_assettoserver.ps1 [-Tag v0.0.55-pre45]
param([string]$Tag = 'v0.0.55-pre45')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$dest = Join-Path $root 'vendor\assettoserver'
$zip = Join-Path $root "vendor\assetto-server-win-x64-$Tag.zip"
New-Item -ItemType Directory -Force $dest | Out-Null
$url = "https://github.com/compujuckel/AssettoServer/releases/download/$Tag/assetto-server-win-x64.zip"
Write-Host "Downloading $url"
Invoke-WebRequest $url -OutFile $zip
Expand-Archive $zip $dest -Force
# AssettoServer writes its config schemas on first run with -g; AC Link builds its advanced settings from them
Push-Location $dest
New-Item -ItemType Directory -Force presets\schema_probe | Out-Null
"[SERVER]`nNAME=probe`nCARS=x`nTRACK=x`nMAX_CLIENTS=1`nUDP_PORT=9600`nTCP_PORT=9600`nHTTP_PORT=8081`nREGISTER_TO_LOBBY=0" | Set-Content presets\schema_probe\server_cfg.ini
$p = Start-Process .\AssettoServer.exe -ArgumentList '-p schema_probe -g' -NoNewWindow -PassThru -RedirectStandardOutput NUL
if (-not $p.WaitForExit(30000)) { $p.Kill() }
Remove-Item presets\schema_probe -Recurse -Force
Pop-Location
if (Test-Path "$dest\cfg\schemas\extra_cfg.schema.json") { Write-Host "AssettoServer $Tag ready in $dest" }
else { Write-Warning "AssettoServer unpacked, but cfg\schemas was not generated. Run AC Link once and check Settings." }
