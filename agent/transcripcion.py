# agent/transcripcion.py — Transcripción de audios (voz a texto) con Groq
# Generado por AgentKit

"""
Convierte notas de voz de WhatsApp en texto usando la API de Groq (Whisper).
Así el agente puede "leer" los audios que le mandan los clientes.
"""

import os
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# Configuración de Groq (compatible con la API de OpenAI)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"  # Rápido y económico, buen español


async def transcribir_audio(audio_bytes: bytes, nombre_archivo: str = "audio.ogg", mime: str = "audio/ogg") -> str:
    """
    Transcribe un audio (en bytes) a texto en español usando Groq.

    Args:
        audio_bytes: Contenido del audio descargado de WhatsApp
        nombre_archivo: Nombre con extensión (ayuda a Groq a detectar el formato)

    Returns:
        El texto transcrito, o cadena vacía si falla.
    """
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY no configurada; no se puede transcribir el audio")
        return ""

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": (nombre_archivo, audio_bytes, mime or "audio/ogg")}
    data = {"model": GROQ_MODEL, "language": "es"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(GROQ_URL, headers=headers, files=files, data=data)
            if r.status_code != 200:
                logger.error(f"Error transcripción Groq: {r.status_code} — {r.text}")
                return ""
            texto = r.json().get("text", "").strip()
            logger.info(f"Audio transcrito: {texto}")
            return texto
    except Exception as e:
        logger.error(f"Error al transcribir audio: {e}")
        return ""
