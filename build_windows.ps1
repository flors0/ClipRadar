$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e .

$VendorBin = Join-Path $ProjectRoot "vendor\bin"
New-Item -ItemType Directory -Force -Path $VendorBin | Out-Null

foreach ($Tool in @("ffmpeg", "ffprobe")) {
    $Command = Get-Command $Tool -ErrorAction SilentlyContinue
    if (-not $Command) {
        throw "$Tool is required to build ClipRadar. Install FFmpeg and ensure it is on PATH."
    }
    Copy-Item $Command.Source (Join-Path $VendorBin "$Tool.exe") -Force
}

$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
python scripts\generate_icon.py
python -m PyInstaller --noconfirm --clean clipradar.spec
& ".\dist\ClipRadar\ClipRadar.exe" --self-test
if ($LASTEXITCODE -ne 0) { throw "Packaged ClipRadar self-test failed." }

$Archive = Join-Path $ProjectRoot "dist\ClipRadar-Windows-x64.zip"
if (Test-Path $Archive) { Remove-Item $Archive -Force }
Compress-Archive -Path ".\dist\ClipRadar\*" -DestinationPath $Archive -CompressionLevel Optimal
Write-Host "Built $Archive"
