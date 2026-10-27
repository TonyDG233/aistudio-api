param(
    [string]$Tag = "p-chat",
    [string]$Auth = "D:\LLM\Ark_AIStudioToAPI\configs\auth\auth-5.json",
    [string]$ConfigJson = "C:\Users\ADMIN\AppData\Local\Temp\kilo\wp5-p-chat.json",
    [string]$Model = "gemini-4-argon",
    [string]$Expect = "expect200",
    [string]$AccountId = "acc_46223636"
)
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$dir = 'C:\Users\ADMIN\AppData\Local\Temp\kilo\wp5-verify'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$log = "$dir\service-$Tag.out"
$errlog = "$dir\service-$Tag.log"
if (Test-Path $log) { Remove-Item $log -Force }
if (Test-Path $errlog) { Remove-Item $errlog -Force }

function Test-Port($p) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $p); $c.Close(); return $true } catch { return $false }
}

Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -match 'cloak|p3-build|spike-profile' } | ForEach-Object { Write-Output "kill stale chrome $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$ownerLine = netstat -ano | Select-String ':8080 ' | Select-String 'LISTENING' | Select-Object -First 1
if ($ownerLine) {
    $ownerpid = [int](($ownerLine.ToString().Trim() -split '\s+')[-1])
    Write-Output "kill stale 8080 owner $ownerpid"
    Stop-Process -Id $ownerpid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

Set-Location 'D:\LLM\self_programming\aistudio-api'
Write-Output "--- apply config: $ConfigJson ---"
& uv run python 'C:\Users\ADMIN\AppData\Local\Temp\kilo\wp5_set_config.py' $ConfigJson 2>&1 | ForEach-Object { Write-Output ("[cfg] " + $_) }
Write-Output "--- set active account: $AccountId ---"
& uv run python 'C:\Users\ADMIN\AppData\Local\Temp\kilo\wp5_prep.py' active $AccountId 2>&1 | ForEach-Object { Write-Output ("[prep] " + $_) }

$env:AISTUDIO_PROXY = 'http://127.0.0.1:10809'
$env:AISTUDIO_AUTH_FILE = $Auth
$svc = Start-Process -FilePath 'uv' -ArgumentList 'run','python','main.py' -RedirectStandardOutput $log -RedirectStandardError $errlog -PassThru -WindowStyle Hidden
Write-Output "service pid: $($svc.Id)"

$ok = $false
$i = 0
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 1
    if ($svc.HasExited) { Write-Output "service exited early, code $($svc.ExitCode)"; break }
    if (Test-Port 8080) { $ok = $true; break }
}
Write-Output "port 8080 ready: $ok (waited $i s)"
if (-not $ok) {
    Write-Output '=== service.log (fail) ==='
    Get-Content $errlog -Encoding UTF8 -ErrorAction SilentlyContinue | Select-Object -First 40
    Stop-Process -Id $svc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Output '--- refresh account info (tier/email) ---'
& uv run python 'C:\Users\ADMIN\AppData\Local\Temp\kilo\wp5_prep.py' refresh 2>&1 | ForEach-Object { Write-Output ("[refresh] " + $_) }
Write-Output ("refresh exit: " + $LASTEXITCODE)

Write-Output "--- call: tag=$Tag model=$Model expect=$Expect ---"
& uv run python 'C:\Users\ADMIN\AppData\Local\Temp\kilo\wp5_call.py' $Expect $Model $Tag 2>&1 | ForEach-Object { Write-Output ("[test] " + $_) }
Write-Output ("test exit code: " + $LASTEXITCODE)

Start-Sleep -Seconds 2
Stop-Process -Id $svc.Id -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='uv.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -match 'main\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -match 'cloak|p3-build|spike-profile' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Output '=== [surface] decision log ==='
Select-String -Path $errlog -Pattern '\[surface\]' -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object { $_.Line } | Select-Object -First 10
Write-Output '=== service.err.log tail ==='
Get-Content $errlog -Tail 45 -Encoding UTF8 -ErrorAction SilentlyContinue