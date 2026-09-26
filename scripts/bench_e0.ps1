# Roda as medições da E0 em sequência, destacado do terminal: o bench do Maestro leva horas.
#   Start-Process powershell -ArgumentList '-NoProfile','-File','scripts\bench_e0.ps1' -WindowStyle Hidden
param([string[]]$Etapas = @("a", "b"))
$Etapas = @($Etapas | ForEach-Object { $_ -split ',' } | Where-Object { $_ })  # -File entrega 'b,kv' como um texto só
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = "resources\python\python.exe"
$log = "..\.devbench\bench_e0.log"
New-Item -ItemType Directory -Force ..\.devbench | Out-Null
$saida = "docs\bench\2026-09-baseline.json"
$qwen = "Qwen3.6-35B-A3B-Q4_K_M"
foreach ($e in $Etapas) {
    "=== $e $(Get-Date -Format s)" | Out-File $log -Append -Encoding utf8
    switch ($e) {
        "a"     { & $py scripts\bench_maestro.py --maestro $qwen --worker $qwen --rotulo a-mesmo-modelo --saida $saida --timeout 150 2>&1 | Out-File $log -Append -Encoding utf8 }
        "b"     { & $py scripts\bench_maestro.py --maestro $qwen --worker Ornith-1.5-9B-Q4_K_M --rotulo b-modelos-diferentes --saida $saida --timeout 150 2>&1 | Out-File $log -Append -Encoding utf8 }
        "a-f16" { & $py scripts\bench_maestro.py --maestro $qwen --worker $qwen --rotulo a-mesmo-modelo-kv-f16 --kv f16 --saida "docs\bench\2026-09-kvcache.json" --timeout 150 2>&1 | Out-File $log -Append -Encoding utf8 }
        "kv"    { & $py scripts\bench_kvcache.py 2>&1 | Out-File $log -Append -Encoding utf8 }
    }
}
"=== fim $(Get-Date -Format s)" | Out-File $log -Append -Encoding utf8
