# Instala (ou remove) a tarefa "Forja Automatico": o backend do Forja sem janela, ao ligar o PC, antes do login.
# Aberto pelo botão em Conteúdo › Ajustes, já como administrador.
#
# Modo S4U ("não armazenar senha"): o Windows roda a tarefa como a sua conta sem guardar senha nenhuma. É o único
# que funciona com conta Microsoft e "Permitir apenas o Windows Hello" ligado (aí a senha é recusada). Limite do
# S4U: segredos protegidos pelo Windows (DPAPI, ex.: a chave do Ollama Cloud) não abrem até você entrar na conta —
# modelo local, Claude Code, Edge TTS e Remotion funcionam.
param([string]$Python, [string]$Backend, [string]$Data, [int]$Port = 47810, [switch]$Remover)

$nome = "Forja Automatico"
$ErrorActionPreference = "Stop"
try {
    if ($Remover) {
        Unregister-ScheduledTask -TaskName $nome -Confirm:$false
        Write-Host "Tarefa '$nome' removida." -ForegroundColor Green
    } else {
        Write-Host "Forja: rodar as automações do Conteúdo ao ligar o PC, sem precisar entrar na conta." -ForegroundColor Cyan
        Write-Host "Pasta de dados: $Data   Porta: $Port"
        $acao = New-ScheduledTaskAction -Execute $Python -Argument "-m app.servico --data `"$Data`" --port $Port" -WorkingDirectory $Backend
        $gatilho = New-ScheduledTaskTrigger -AtStartup
        $gatilho.Delay = "PT30S"   # rede e drivers de vídeo prontos antes
        $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Priority 5
        $conta = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
        Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $ajustes -Principal $conta -Force | Out-Null
        Write-Host "Pronto: na próxima vez que o PC ligar, o Forja roda as automações mesmo na tela de bloqueio." -ForegroundColor Green
        Write-Host "Sem senha guardada: modelos de nuvem com chave (Ollama Cloud) só funcionam depois que você entra na conta." -ForegroundColor DarkGray
    }
} catch {
    Write-Host "Não deu certo: $($_.Exception.Message)" -ForegroundColor Red
}
Write-Host ""
Read-Host "Enter para fechar"
