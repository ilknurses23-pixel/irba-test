# irba test — derle, kurulum dosyasını üret, guncelleme/ klasörünü hazırla.
# Kullanım:  powershell -File packaging\yayinla.ps1 -Py <venv python> -Iscc <ISCC.exe> -Work <kısa geçici klasör> -Notes "..."
param(
    [Parameter(Mandatory = $true)][string]$Py,
    [Parameter(Mandatory = $true)][string]$Iscc,
    [Parameter(Mandatory = $true)][string]$Work,
    [string]$Notes = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$ver = (Select-String -Path "irba\paths.py" -Pattern 'APP_VERSION = "([\d\.]+)"').Matches[0].Groups[1].Value
$p = $ver.Split("."); while ($p.Count -lt 4) { $p += "0" }
$tuple = "(" + ($p -join ", ") + ")"
(Get-Content packaging\version_info.txt -Raw) -replace '\(\d+, \d+, \d+, \d+\)', $tuple -replace "'\d+\.\d+\.\d+'", "'$ver'" |
    Set-Content packaging\version_info.txt -Encoding utf8
Write-Host "Surum: $ver"

& $Py -m PyInstaller --noconfirm --windowed --onedir --name "irba test" --icon "$root\assets\irba.ico" `
    --version-file "$root\packaging\version_info.txt" --add-data "$root\tools\scrcpy;tools\scrcpy" `
    --add-data "$root\assets;assets" --workpath "$Work\pyi_build" --specpath "$Work\pyi_spec" `
    --distpath "$Work\dist" "$root\irba_test.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller hatasi" }

& $Iscc /Q "/DDistDir=$Work\dist\irba test" "/DOutDir=$Work\setup_out" "/DAppVersion=$ver" "packaging\irba_test.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup hatasi" }
$setup = "$Work\setup_out\irba_test_Setup_$ver.exe"

New-Item -ItemType Directory -Force guncelleme | Out-Null
Get-ChildItem guncelleme -Filter "irba_test_Setup_*.exe" | Remove-Item -Force
$env:PYTHONPATH = $root
& $Py -c "import sys; from irba import updater as U; d=U.write_manifest('guncelleme', sys.argv[1], sys.argv[2], sys.argv[3]); print('manifest:', d['version'], d['sha256'][:16])" $setup $ver $Notes
Write-Host "Hazir: $setup"
