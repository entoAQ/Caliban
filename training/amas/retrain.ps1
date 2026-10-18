# Retrain the MEO detector from everything labelled so far, and get it ready
# for Ariel. Run it with Label Studio open (the desktop shortcut), from the
# Caliban folder:
#
#   powershell -ExecutionPolicy Bypass -File training\amas\retrain.ps1
#   powershell -ExecutionPolicy Bypass -File training\amas\retrain.ps1 -Epochs 40
#
# About 1-1.5 hours on this laptop; keep it awake and plugged in. Steps:
#   1. export the labelled tiles                      (export_amas.py)
#   2. train a new model                              (train_meo.py)
#   3. read every lab lot with it, fit the scale      (lot_estimate.py)
#   4. export it for Ariel into rig\inline\models\    (meo.onnx + meo.json)
#   5. pre-fill the unlabelled tiles with it          (prelabel_amas.py)
# Nothing reaches Ariel or the screen until you push and change the scale in
# Supabase -- see what it prints at the end. To throw a run away instead:
#   git checkout rig\inline\models

param([int]$Epochs = 30)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here "..\..")
$name = "meo-" + (Get-Date -Format "yyyyMMdd-HHmm")
$runs = Join-Path $(if ($env:INLINE_LABELING_DIR) { $env:INLINE_LABELING_DIR } else { Join-Path $HOME "inline_labeling" }) "amas_runs"
$log = Join-Path $runs "$name.log"
New-Item -ItemType Directory -Force $runs | Out-Null

function Step($title, $script, $arguments) {
    Write-Host "`n=== $title ===" -ForegroundColor Cyan
    & python (Join-Path $here $script) @arguments 2>&1 | Tee-Object -FilePath $log -Append |
        Where-Object { $_ -notmatch "^\s+\d+/\d+|━" }
    if ($LASTEXITCODE -ne 0) { Write-Host "Stopped: $script failed (see $log)." -ForegroundColor Red; exit 1 }
}

Write-Host "Retraining as $name (log: $log)"
Step "1/5 Export labelled tiles" "export_amas.py" @()
Step "2/5 Train ($Epochs rounds)" "train_meo.py" @("--epochs", $Epochs, "--name", $name)
Step "3/5 Lot estimates and scale" "lot_estimate.py" @("--name", $name)

Write-Host "`n=== 4/5 Export for Ariel ===" -ForegroundColor Cyan
$weights = Join-Path $runs "$name\weights\best.pt"
& python -c "from ultralytics import YOLO; YOLO(r'$weights').export(format='onnx', imgsz=416, opset=12, simplify=False, dynamic=False)" 2>&1 |
    Tee-Object -FilePath $log -Append | Out-Null
$scale = (Get-Content (Join-Path $runs "$name\scale.json") | ConvertFrom-Json).yolo_scale
$tiles = (Get-ChildItem (Join-Path (Split-Path $runs) "amas_yolo\images\train")).Count
Copy-Item (Join-Path $runs "$name\weights\best.onnx") (Join-Path $repo "rig\inline\models\meo.onnx") -Force
@{ version = $name; trained_on_tiles = $tiles; yolo_scale_fitted = $scale } | ConvertTo-Json |
    Set-Content -Encoding utf8 (Join-Path $repo "rig\inline\models\meo.json")
Write-Host "model copied to rig\inline\models (version $name, scale $scale)"

Step "5/5 Pre-fill the queue" "prelabel_amas.py" @("--name", $name)

Write-Host "`n=== Done ===" -ForegroundColor Green
Write-Host @"
Check the table in step 3 (lots against the lab) before using it. If it is
better than the model on Ariel now:

  1. Push it (from the Caliban folder):
       git add rig/inline/models
       git commit -m "Inline: MEO detector $name"
       git push
     Ariel pulls it within 5 minutes and restarts its loop by itself,
     which loads the new model.

  2. Set its scale in Supabase (SQL editor):
       update system_config set value = (value::jsonb || '{"yolo_scale": $scale}'::jsonb)::text
       where key = 'inline_settings';

Do both together: the scale belongs to this model.
If it is not better: git checkout rig/inline/models  (keeps Ariel's model).
"@
