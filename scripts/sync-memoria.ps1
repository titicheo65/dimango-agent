# scripts/sync-memoria.ps1 - baja la memoria de Maximus a ServidorPlaya
#
# Por que existe: C:\maximus es un CLON del repo titicheo65/maximus. Todo lo
# que Maximus aprende en la sesion del Mac se commitea alla; si nadie hace
# pull aca, el Maximus de Telegram sigue contestando con la memoria del dia
# que se instalo - datos viejos dichos con seguridad, que es peor que no
# tener memoria. Es el punto 2 de P-008, abierto desde el 20-ago-2026.
#
# El 17-sep-2026 se descubrio ademas que el Mac tenia 128 commits sin subir:
# ni siquiera existia el otro lado del puente. Ya estan en GitHub.
#
# Que hace: git pull en C:\maximus cada 15 minutos. Nada mas.
# NO toca C:\dimango-agent - el codigo se despliega a mano y a proposito;
# un pull automatico del codigo puede romper la operacion sin que nadie mire.
#
# No hace falta reiniciar el agente: agent/maximus.py invalida su cache por
# fecha de modificacion de los archivos, asi que relee la memoria solo.
#
# Avisa por Telegram unicamente cuando el pull falla DOS veces seguidas, para
# no llenar el chat con ruido por un corte de internet de un minuto.
#
# Se corre via Tarea Programada de Windows, cada 15 min (igual que
# monitor-maximus.ps1). Sin tildes ni caracteres especiales: PowerShell 5.1
# lee mal el UTF-8 de archivos bajados por git y eso rompe el parser.

$ErrorActionPreference = 'SilentlyContinue'
$rutaMemoria = "C:\maximus"
$rutaAgente  = "C:\dimango-agent"
$envFile     = Join-Path $rutaAgente ".env"
$logFile     = Join-Path $rutaMemoria "sync-memoria.log"
$fallasFile  = Join-Path $rutaMemoria "sync-memoria.fallas"
$chatId      = "8208785474"   # Ricardo, mismo chat_id que TELEGRAM_OWNER_CHAT_IDS

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Add-Content -Path $logFile
}

function Avisar($texto) {
    try {
        $linea = Get-Content $envFile | Where-Object { $_ -match "^TELEGRAM_BOT_TOKEN=" }
        $token = ($linea -split "=", 2)[1]
        if ($token) {
            Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/sendMessage" -Method Post -Body @{ chat_id = $chatId; text = $texto } | Out-Null
            Log "Aviso enviado por Telegram."
        } else {
            Log "No se encontro TELEGRAM_BOT_TOKEN en .env - no se pudo avisar."
        }
    } catch {
        Log "Fallo al avisar por Telegram: $_"
    }
}

if (-not (Test-Path (Join-Path $rutaMemoria ".git"))) {
    Log "ERROR: $rutaMemoria no es un repositorio git. No hay nada que sincronizar."
    exit 1
}

Set-Location $rutaMemoria

# Antes y despues, para saber si entro algo nuevo.
$antes = (git rev-parse HEAD) 2>$null
$salida = (git pull --ff-only 2>&1 | Out-String).Trim()
$codigo = $LASTEXITCODE
$despues = (git rev-parse HEAD) 2>$null

if ($codigo -ne 0) {
    $fallas = 0
    if (Test-Path $fallasFile) { $fallas = [int](Get-Content $fallasFile) }
    $fallas = $fallas + 1
    Set-Content -Path $fallasFile -Value $fallas
    Log "FALLO el pull (intento $fallas): $salida"
    if ($fallas -eq 2) {
        Avisar "Maximus: la memoria de ServidorPlaya no se esta actualizando (git pull fallo 2 veces). Estoy contestando con datos viejos. Revisar C:\maximus\sync-memoria.log"
    }
    exit 1
}

# Pull exitoso: se borra el contador de fallas.
if (Test-Path $fallasFile) { Remove-Item $fallasFile -Force }

if ($antes -ne $despues) {
    $cuantos = (git rev-list --count "$antes..$despues") 2>$null
    Log "Memoria actualizada: $cuantos commits nuevos ($antes -> $despues)"
} else {
    Log "Sin cambios."
}

# El log no crece para siempre: se queda con las ultimas 500 lineas.
if ((Test-Path $logFile) -and ((Get-Content $logFile).Count -gt 500)) {
    Get-Content $logFile -Tail 500 | Set-Content -Path "$logFile.tmp"
    Move-Item -Path "$logFile.tmp" -Destination $logFile -Force
}
