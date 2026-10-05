# Instala (ou remove) a tarefa "Forja Automático": o backend do Forja sem janela, ao ligar o PC, antes do login.
# Aberto pelo botão em Conteúdo › Ajustes, já como administrador. A senha é pedida pela janela do próprio Windows
# e guardada pelo Agendador de Tarefas; o Forja não a vê.
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
        $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" `
            -Message "Senha da sua conta do Windows. O Windows guarda no Agendador de Tarefas para rodar o Forja sem login; o Forja não vê a senha."
        if (-not $cred) { throw "Cancelado." }
        $acao = New-ScheduledTaskAction -Execute $Python -Argument "-m app.servico --data `"$Data`" --port $Port" -WorkingDirectory $Backend
        $gatilho = New-ScheduledTaskTrigger -AtStartup
        $gatilho.Delay = "PT30S"   # rede e drivers de vídeo prontos antes
        $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Priority 5
        Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $ajustes -RunLevel Limited `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password -Force | Out-Null
        Write-Host "Pronto: na próxima vez que o PC ligar, o Forja roda as automações mesmo na tela de bloqueio." -ForegroundColor Green
    }
} catch {
    Write-Host "Não deu certo: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Se sua conta entra só com PIN/Windows Hello, use a senha da conta Microsoft." -ForegroundColor Yellow
}
Write-Host ""
Read-Host "Enter para fechar"
