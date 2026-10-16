# Start Label Studio for labeling the inline photos.
#
# Listens on 127.0.0.1 only: nothing outside this PC can reach it, so the
# Windows firewall has nothing to allow and no admin rights are needed.
# Data lives in INLINE_LABELING_DIR (default ~\inline_labeling), see common.py.
#
# The first start creates the login and an API token and saves both to
# ls-login.txt in that folder (the scripts read the token from there).
# Leave this window open while labeling; Ctrl+C stops Label Studio.
#
#   powershell -ExecutionPolicy Bypass -File training\labelstudio\start.ps1

$root = $env:INLINE_LABELING_DIR
if (-not $root) { $root = Join-Path $HOME "inline_labeling" }
New-Item -ItemType Directory -Force (Join-Path $root "images") | Out-Null

$login = Join-Path $root "ls-login.txt"
if (-not (Test-Path $login)) {
    $chars = [char[]]"abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    $password = -join (1..16 | ForEach-Object { $chars | Get-Random })
    $token = -join (1..40 | ForEach-Object { "0123456789abcdef"[(Get-Random -Maximum 16)] })
    @("url=http://127.0.0.1:8080", "username=l.t.townshend@gmail.com",
      "password=$password", "token=$token") | Set-Content -Encoding utf8 $login
    Write-Host "Login created, saved in $login"
}
$cfg = @{}
Get-Content $login | ForEach-Object { $k, $v = $_ -split "=", 2; $cfg[$k] = $v }

# Lets tasks point at the photos on disk instead of uploading copies.
$env:LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED = "true"
$env:LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT = $root

Write-Host "Label Studio: http://127.0.0.1:8080  ($($cfg.username) / $($cfg.password))"
& (Join-Path $HOME ".local\bin\label-studio.exe") start `
    --data-dir (Join-Path $root "ls-data") `
    --internal-host 127.0.0.1 --port 8080 `
    --username $cfg.username --password $cfg.password `
    --user-token $cfg.token --enable-legacy-api-token
