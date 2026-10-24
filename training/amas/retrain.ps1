# Retrain the MEO detector from everything labelled so far, and get it ready
# for Ariel. Run it with Label Studio open (the desktop shortcut), from the
# Caliban folder:
#
#   powershell -ExecutionPolicy Bypass -File training\amas\retrain.ps1
#   powershell -ExecutionPolicy Bypass -File training\amas\retrain.ps1 -Epochs 40
#   powershell -ExecutionPolicy Bypass -File training\amas\retrain.ps1 -Arch yolox-nano
#
# About 3 hours on this laptop (YOLOX-Tiny, 30 rounds; Nano about half the
# training time); keep it awake and plugged in. Steps:
#   1. export the labelled tiles                      (export_amas.py)
#   2. train a new model, exported for Ariel          (train_meo.py)
#   3. read every lab lot with it, fit the scale      (lot_estimate.py)
#   4. put the scale with it in its holding folder    (amas_runs\<name>\ariel\)
#   5. score it against the model on Ariel            (compare_models.py)
#   6. pre-fill the unlabelled tiles with it          (prelabel_amas.py)
# rig\inline\models (what Ariel runs) is not touched: a new model waits in
# its holding folder until promote.ps1 moves it there, and only if step 5 says
# it is better. Nothing reaches Ariel until that is pushed and the scale set.

# Cut off part-way (laptop asleep, window closed)? Run it again with the
# -Name it printed at the start: the training carries on from its last round.
param([int]$Epochs = 30, [string]$Arch = "yolox-tiny", [string]$Name = "")
# Not "Stop": Python writes its progress to stderr, which Windows PowerShell
# would then treat as a failure. Each step checks the exit code instead.
$ErrorActionPreference = "Continue"
# Python's output as UTF-8 both ends, or its progress bars (line-drawing
# characters) come through as garbled symbols instead of being filtered.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here "..\..")
$name = if ($Name) { $Name } else { "meo-" + (Get-Date -Format "yyyyMMdd-HHmm") }
$runs = Join-Path $(if ($env:INLINE_LABELING_DIR) { $env:INLINE_LABELING_DIR } else { Join-Path $HOME "inline_labeling" }) "amas_runs"
$log = Join-Path $runs "$name.log"
New-Item -ItemType Directory -Force $runs | Out-Null

function Step($title, $script, $arguments) {
    Write-Host "`n=== $title ===" -ForegroundColor Cyan
    # Through cmd so Python's stderr (its progress) comes through as plain
    # lines, not as PowerShell error records.
    $cmdline = 'python "' + (Join-Path $here $script) + '" ' + ($arguments -join ' ') + ' 2>&1'
    & cmd /c $cmdline | Tee-Object -FilePath $log -Append |
        Where-Object { $_ -notmatch ("^\s+\d+/\d+|[" + [char]0x2500 + [char]0x2501 + "]") }
    if ($LASTEXITCODE -ne 0) { Write-Host "Stopped: $script failed (see $log)." -ForegroundColor Red; exit 1 }
}

Write-Host "Retraining as $name (log: $log)"
Step "1/6 Export labelled tiles" "export_amas.py" @()
Step "2/6 Train $Arch ($Epochs rounds)" "train_meo.py" @("--epochs", $Epochs, "--name", $name, "--arch", $Arch)
Step "3/6 Lot estimates and scale" "lot_estimate.py" @("--name", $name)

Write-Host "`n=== 4/6 Scale into the holding folder ===" -ForegroundColor Cyan
# train_meo.py exported the model there (meo.onnx, and meo.json with its
# kind); the scale fitted in step 3 belongs with it.
$scale = (Get-Content (Join-Path $runs "$name\scale.json") | ConvertFrom-Json).yolo_scale
$hold = Join-Path $runs "$name\ariel"
$meta = Get-Content (Join-Path $hold "meo.json") | ConvertFrom-Json
$meta | Add-Member -NotePropertyName yolo_scale_fitted -NotePropertyValue $scale -Force
$meta | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $hold "meo.json")
Write-Host "model held in $hold (version $name, $($meta.arch), scale $scale)"

Step "5/6 Against the model on Ariel" "compare_models.py" @("--name", $name)
$verdict = Get-Content (Join-Path $runs "$name\compare.json") | ConvertFrom-Json

Step "6/6 Pre-fill the queue" "prelabel_amas.py" @("--name", $name)

Write-Host "`n=== Done ===" -ForegroundColor Green
if ($verdict.better) {
    Write-Host @"
$name scored better than $($verdict.live) on $($verdict.lots.Count) blind lots (step 5).
To put it on Ariel, from the Caliban folder:
  powershell -ExecutionPolicy Bypass -File training\amas\promote.ps1 -Name $name
"@
} else {
    Write-Host @"
$name did NOT score better than $($verdict.live) (step 5); Ariel keeps its model.
It stays in its holding folder ($hold); the pre-filled boxes are still worth
reviewing in Label Studio.
"@
}
