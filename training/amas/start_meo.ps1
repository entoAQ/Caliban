# One click to label MEO: starts Label Studio (training\labelstudio\start.ps1)
# in its own window, waits for it, brings the MEO project up to date
# (sync_amas.py) and opens it in the browser.
#
# Close the Label Studio window to stop it. Labels are saved as you submit.
#
#   powershell -ExecutionPolicy Bypass -File training\amas\start_meo.ps1

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://127.0.0.1:8080"

function Up { try { (Invoke-WebRequest "$url/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false } }

if (-not (Up)) {
    Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $here "..\labelstudio\start.ps1")
    Write-Host "Starting Label Studio..."
    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Up)) {
        if ((Get-Date) -gt $deadline) { Write-Host "Label Studio did not start: see its window."; pause; exit 1 }
        Start-Sleep -Seconds 2
    }
}

python (Join-Path $here "sync_amas.py")
Start-Process "$url/projects/"
