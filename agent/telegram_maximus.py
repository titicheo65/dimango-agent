# agent/telegram_maximus.py — Canal privado de Maximus por Telegram

"""
Canal dedicado entre Ricardo y Maximus.

Por qué Telegram y no WhatsApp para este rol:
- Meta no permite iniciar conversación fuera de la ventana de 24h sin plantilla
  aprobada. Eso hace imposible el saludo matutino y las alertas proactivas.
- Telegram permite escribir primero, sin costo y sin aprobación previa.

Este módulo es ADITIVO: expone su propio endpoint y no toca en nada el webhook
de WhatsApp que atiende a los clientes.
"""

import os
import logging
import httpx
from dotenv import load_dotenv

from agent.transcripcion import transcribir_audio

load_dotenv()
logger = logging.getLogger("agentkit")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Chat IDs autorizados, coma-separados. Vacío = modo setup (ver abajo).
_OWNERS_RAW = os.getenv("TELEGRAM_OWNER_CHAT_IDS", "")
OWNER_CHAT_IDS = {t.strip() for t in _OWNERS_RAW.split(",") if t.strip()}

# Telegram corta los mensajes en 4096 caracteres
LIMITE_MENSAJE = 4000


def configurado() -> bool:
    return bool(BOT_TOKEN)


def es_owner(chat_id: str) -> bool:
    return str(chat_id) in OWNER_CHAT_IDS


def _trocear(texto: str) -> list[str]:
    """Parte un texto largo en trozos que Telegram acepte, cortando por líneas."""
    if len(texto) <= LIMITE_MENSAJE:
        return [texto]
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > LIMITE_MENSAJE:
            if actual:
                trozos.append(actual)
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        trozos.append(actual)
    return trozos


async def enviar_mensaje(chat_id: str | int, texto: str) -> bool:
    """Envía texto a un chat. Devuelve True si todos los trozos salieron bien."""
    if not BOT_TOKEN:
        logger.error("[TELEGRAM] Falta TELEGRAM_BOT_TOKEN")
        return False

    ok = True
    async with httpx.AsyncClient(timeout=30) as client:
        for trozo in _trocear(texto):
            try:
                r = await client.post(
                    f"{API}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": trozo,
                        "parse_mode": "Markdown",
                    },
                )
                if r.status_code != 200:
                    # Markdown mal formado es la causa típica: reintentar en plano
                    logger.warning(f"[TELEGRAM] Markdown rechazado ({r.status_code}), reenvío en texto plano")
                    r = await client.post(
                        f"{API}/sendMessage",
                        json={"chat_id": chat_id, "text": trozo},
                    )
                ok = ok and r.status_code == 200
            except Exception as e:
                logger.error(f"[TELEGRAM] Error enviando: {e}")
                ok = False
    return ok


async def _descargar_voz(file_id: str) -> bytes | None:
    """Descarga una nota de voz de Telegram por su file_id."""
    if not BOT_TOKEN:
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{API}/getFile", params={"file_id": file_id})
            if r.status_code != 200:
                return None
            file_path = r.json().get("result", {}).get("file_path")
            if not file_path:
                return None
            r2 = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
            return r2.content if r2.status_code == 200 else None
    except Exception as e:
        logger.error(f"[TELEGRAM] Error descargando voz: {e}")
        return None


async def enviar_audio(chat_id: str | int, audio_mp3: bytes) -> bool:
    """Envía un MP3 como mensaje de audio. Si falla, el texto ya salió antes."""
    if not BOT_TOKEN or not audio_mp3:
        return False
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{API}/sendAudio",
                data={"chat_id": str(chat_id), "title": "Maximus"},
                files={"audio": ("maximus.mp3", audio_mp3, "audio/mpeg")},
            )
            if r.status_code != 200:
                logger.error(f"[TELEGRAM] Envío de audio falló: {r.status_code}")
            return r.status_code == 200
    except Exception as e:
        logger.error(f"[TELEGRAM] Error enviando audio: {e}")
        return False


async def parsear_update(body: dict) -> tuple[str, str, bool] | None:
    """
    Convierte un update de Telegram en (chat_id, texto, fue_audio).
    Las notas de voz se transcriben con el mismo Groq Whisper que ya usa WhatsApp.
    Devuelve None si el update no trae nada que procesar.
    """
    mensaje = body.get("message") or body.get("edited_message")
    if not mensaje:
        return None

    chat_id = str(mensaje.get("chat", {}).get("id", ""))
    if not chat_id:
        return None

    texto = (mensaje.get("text") or "").strip()
    fue_audio = False

    if not texto and "voice" in mensaje:
        fue_audio = True
        file_id = mensaje["voice"].get("file_id", "")
        audio = await _descargar_voz(file_id)
        if audio:
            texto = (await transcribir_audio(audio, "voz.ogg")).strip()
            logger.info(f"[TELEGRAM] Nota de voz transcrita: {texto[:80]}")

    if not texto and "audio" in mensaje:
        fue_audio = True
        file_id = mensaje["audio"].get("file_id", "")
        audio = await _descargar_voz(file_id)
        if audio:
            texto = (await transcribir_audio(audio, "audio.mp3")).strip()

    if not texto:
        return None

    return chat_id, texto, fue_audio


MENSAJE_SETUP = (
    "Soy Maximus, pero todavía no sé si eres tú.\n\n"
    "Tu chat_id es: {chat_id}\n\n"
    "Ponlo en el .env del servidor así:\n"
    "TELEGRAM_OWNER_CHAT_IDS={chat_id}\n\n"
    "Reinicia el agente y volvemos a hablar."
)

MENSAJE_NO_AUTORIZADO = "Este canal es privado."
