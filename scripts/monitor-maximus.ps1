# scripts/monitor-maximus.ps1 - vigilante de maximus-agent
#
# Por que existe: hasta el 26-ago-2026, la unica forma de saber que
# Maximus estaba caido era que Ricardo le preguntara algo y no respondiera.
# Tres veces en tres dias un proceso ajeno (no lanzado por PM2) ocupo el
# puerto 8000 y PM2 no podia tomarlo - PM2 por si solo reinicia procesos
# que se caen, pero no sabe que el puerto esta tomado por otra cosa.
#
# Que hace, en orden:
#   1. Revisa si algo responde bien en el puerto 8000.
#   2. Si no, mira quien tiene el puerto. Si es un proceso que PM2 no
#      reconoce como el suyo (el patron del zombi), lo mata.
#   3. Reinicia maximus-agent con PM2 y espera a que levante.
#   4. Si sigue sin responder, avisa por Telegram - por la API directa,
#      no por el agente (asi funciona aunque el agente este completamente
#      caido). Usa el mismo bot y el mismo chat_id que ya usa Maximus.
#
# Se corre solo, cada 5 minutos, via una Tarea Programada de Windows.
# Nota deliberada: este archivo no usa tildes ni caracteres especiales -
# PowerShell 5.1 en Windows a veces lee mal el UTF-8 de archivos bajados
# por git y eso genera comillas fantasma que rompen el parser entero.

$ErrorActionPreference = 'SilentlyContinue'
$rutaAgente = "C:\dimango-agent"
$envFile = Join-Path $rutaAgente ".env"
$logFile = Join-Path $rutaAgente "monitor-maximus.log"
$chatId = "8208785474"   # Ricardo, mismo chat_id que TELEGRAM_OWNER_CHAT_IDS

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Add-Content -Path $logFile
}

function Salud-Ok {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/" -TimeoutSec 5 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (Salud-Ok) {
    exit 0
}

Log "Salud fallo. Revisando quien tiene el puerto 8000."

$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    $pidPuerto = $conn.OwningProcess
    $pm2Info = pm2 jlist | ConvertFrom-Json | Where-Object { $_.name -eq 'maximus-agent' }
    $pidPm2 = $pm2Info.pid
    if ($pidPuerto -and ($pidPuerto -ne $pidPm2)) {
        $proc = Get-Process -Id $pidPuerto -ErrorAction SilentlyContinue
        Log "Puerto 8000 tomado por PID $pidPuerto ($($proc.ProcessName)), no es el de PM2 ($pidPm2). Matando zombi."
        Stop-Process -Id $pidPuerto -Force -ErrorAction SilentlyContinue
    }
}

Log "Reiniciando maximus-agent con PM2."
pm2 restart maximus-agent | Out-Null
Start-Sleep -Seconds 8

if (Salud-Ok) {
    Log "Recuperado solo tras el reinicio."
    exit 0
}

Log "Sigue caido despues de reiniciar. Avisando por Telegram."
try {
    $linea = Get-Content $envFile | Where-Object { $_ -match "^TELEGRAM_BOT_TOKEN=" }
    $token = ($linea -split "=", 2)[1]
    if ($token) {
        $texto = "Maximus esta caido en ServidorPlaya y no se pudo reiniciar solo. Hay que revisarlo por RDP."
        Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/sendMessage" -Method Post -Body @{ chat_id = $chatId; text = $texto } | Out-Null
        Log "Aviso enviado por Telegram."
    } else {
        Log "No se encontro TELEGRAM_BOT_TOKEN en .env - no se pudo avisar."
    }
} catch {
    Log "Fallo al avisar por Telegram: $_"
}
