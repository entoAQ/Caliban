# One click to label MEO: starts Label Studio (training\labelstudio\start.ps1)
# in its own window, waits for it, cuts any new photos into tiles, brings the MEO project up to date
# (sync_amas.py) and opens it in the browser, already labelling.
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

python (Join-Path $here "tile_amas.py")
python (Join-Path $here "sync_amas.py")
# Straight into "Label All Tasks" on the MEO project: each Submit (Ctrl+Enter)
# opens the next tile nobody has labelled, so there is no list to go back to.
$proj = python -c "import sys; sys.path.insert(0, r'$here'); from sync_amas import find_project; print(find_project() or '')"
if ($proj) { Start-Process "$url/projects/$proj/data?labeling=1" } else { Start-Process "$url/projects/" }
