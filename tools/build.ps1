# Builds "AC Link.exe" and the player zip (share\AC Link.zip: exe + README + LICENSE).
# Needs: Python 3.10+ and pip install -r requirements.txt.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
python -m PyInstaller --noconfirm --onefile --windowed --name "AC Link" --icon "$root\ui\icon.ico" `
    --add-data "$root\ui;ui" --hidden-import share --distpath build\dist --workpath build\work --specpath build aclink.py
Copy-Item "build\dist\AC Link.exe" "$root\AC Link.exe" -Force

# the player zip is just the app: AssettoServer is downloaded by AC Link itself on first launch (after asking)
$out = "$root\share\AC Link"
if (Test-Path "$root\share") { Remove-Item "$root\share" -Recurse -Force }
New-Item -ItemType Directory -Force $out | Out-Null
Copy-Item "$root\AC Link.exe", "$root\README.txt", "$root\LICENSE" $out
Compress-Archive -Path $out -DestinationPath "$root\share\AC Link.zip" -CompressionLevel Optimal -Force
Write-Host "Built: $root\AC Link.exe and $root\share\AC Link.zip"
