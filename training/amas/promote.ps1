# Move a retrained MEO detector from its holding folder onto Ariel's model
# (rig\inline\models). From the Caliban folder:
#
#   powershell -ExecutionPolicy Bypass -File training\amas\promote.ps1 -Name meo-20261019-1653
#
# Refuses unless compare_models.py found it better than the model there now;
# -Force skips that check. Then it prints the push and the scale to set.

param([Parameter(Mandatory = $true)][string]$Name, [switch]$Force)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here "..\..")
$runs = Join-Path $(if ($env:INLINE_LABELING_DIR) { $env:INLINE_LABELING_DIR } else { Join-Path $HOME "inline_labeling" }) "amas_runs"
$hold = Join-Path $runs "$Name\ariel"
$models = Join-Path $repo "rig\inline\models"

if (-not (Test-Path (Join-Path $hold "meo.onnx"))) { Write-Host "No held model at $hold." -ForegroundColor Red; exit 1 }
$cmp = Join-Path $runs "$Name\compare.json"
$live = (Get-Content (Join-Path $models "meo.json") | ConvertFrom-Json).version
if (-not $Force) {
    if (-not (Test-Path $cmp)) {
        Write-Host "Not scored against Ariel's model yet: python training\amas\compare_models.py --name $Name" -ForegroundColor Red; exit 1
    }
    $verdict = Get-Content $cmp | ConvertFrom-Json
    if ($verdict.live -ne $live) {
        Write-Host "It was scored against $($verdict.live), but Ariel now has $live. Score it again first." -ForegroundColor Red; exit 1
    }
    if (-not $verdict.better) { Write-Host "$Name did not score better than $live. Not promoted (-Force to override)." -ForegroundColor Red; exit 1 }
}

Copy-Item (Join-Path $hold "meo.onnx") (Join-Path $models "meo.onnx") -Force
Copy-Item (Join-Path $hold "meo.json") (Join-Path $models "meo.json") -Force
$scale = (Get-Content (Join-Path $hold "meo.json") | ConvertFrom-Json).yolo_scale_fitted
Write-Host "$Name copied to rig\inline\models (replacing $live)." -ForegroundColor Green
Write-Host @"

  1. Push it (from the Caliban folder):
       git add rig/inline/models
       git commit -m "Inline: MEO detector $Name"
       git push
     Ariel pulls it within 5 minutes and restarts its loop by itself.

  2. Set its scale in Supabase (SQL editor):
       update system_config set value = (value::jsonb || '{"yolo_scale": $scale}'::jsonb)::text
       where key = 'inline_settings';

Do both together: the scale belongs to this model.
To undo before pushing: git checkout rig\inline\models
"@
