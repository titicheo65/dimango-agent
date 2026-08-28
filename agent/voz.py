# agent/voz.py — Texto a voz para Maximus

"""
Convierte la respuesta de Maximus en audio.

Dos motores, en este orden:
1. edge-tts  — gratis, sin API key, y tiene voces chilenas (es-CL). Es el default.
2. ElevenLabs — mejor calidad, de pago. Se activa solo si hay ELEVENLABS_API_KEY.

Se empieza por el gratuito a propósito: si la voz chilena de Edge suena bien,
no hay razón para pagar por caracteres sintetizados todos los días.

Devuelve siempre bytes de MP3, o None si falla. Nunca lanza excepción: si la voz
falla, el mensaje de texto igual se envía.
"""

import os
import re
import logging
import asyncio

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# Voces chilenas de Edge: es-CL-LorenzoNeural (hombre), es-CL-CatalinaNeural (mujer)
VOZ_EDGE = os.getenv("MAXIMUS_VOZ_EDGE", "es-CL-LorenzoNeural")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# Por encima de esto no se sintetiza: una respuesta larga en audio es peor que
# leerla, y además cuesta. Maximus responde corto por diseño.
LIMITE_CARACTERES = int(os.getenv("MAXIMUS_VOZ_MAX_CHARS", "1200"))


# Palabras que el sintetizador lee en español pero se pronuncian de otra forma.
# El orden importa: lo más largo primero, o "Mallplaza" quedaría como "Mollplaza".
PRONUNCIACION = [
    (r"\bMall\s*[Pp]laza\b", "Mol Plasa"),
    (r"\bMallplaza\b", "Mol Plasa"),
    (r"\bmallplaza\b", "mol plasa"),
    (r"\bMall\b", "Moll"),
    (r"\bmall\b", "moll"),
    (r"\bWhatsApp\b", "Guatsáp"),
    (r"\bwhatsapp\b", "guatsáp"),
    (r"\bSII\b", "S I I"),
    (r"\bDiMangoToGo\b", "DiMango Tu Gou"),
    (r"\bDiMangoWorking\b", "DiMango Wérking"),
    (r"\bToGo\b", "Tu Gou"),
    (r"\bPlaza Oeste\b", "Plasa Oeste"),
]


def _pronunciar(texto: str) -> str:
    for patron, reemplazo in PRONUNCIACION:
        texto = re.sub(patron, reemplazo, texto)
    return texto


# Plata en formato chileno: $18.000 o $18000, con o sin espacio.
# Excluye a propósito los que ya tienen decimales tipo $1.234,50 (raro en CLP,
# mejor no adivinar) -- (?!\d) evita comerse un numero mas largo por error.
_PATRON_DINERO = re.compile(r"\$\s?(\d{1,3}(?:[.,]\d{3})*)(?!\d)")


def _arreglar_espanol(palabras: str) -> str:
    """
    num2words en español tiene dos bugs conocidos para este uso:
    dice "veintiuno mil" en vez de "veintiún mil" (falta el apócope de
    "uno" antes de "mil"), y no hay forma de pedirle "un millón DE pesos"
    -- eso se resuelve después, al pegar la unidad.
    """
    palabras = re.sub(r"\buno mil\b", "un mil", palabras)
    palabras = re.sub(r"iuno mil\b", "iún mil", palabras)
    palabras = re.sub(r"\by uno mil\b", "y un mil", palabras)
    return palabras


def _dinero_a_palabras(texto: str) -> str:
    """
    $18.000 -> "dieciocho mil pesos". $18.000.000 -> "dieciocho millones de
    pesos" (con "de" -- sin eso suena mal en español). Default SIEMPRE pesos
    chilenos -- si Maximus habla de otra moneda, el texto ya lo dice
    explícito (USD, dólares) y este patrón no debería aplicar ahí.
    """
    try:
        from num2words import num2words
    except ImportError:
        logger.warning("[VOZ] num2words no instalado, el monto queda en dígitos")
        return texto

    def reemplazar(m: re.Match) -> str:
        crudo = m.group(1).replace(".", "").replace(",", "")
        try:
            monto = int(crudo)
        except ValueError:
            return m.group(0)
        palabras = _arreglar_espanol(num2words(monto, lang="es"))
        conector = " de pesos" if palabras.split()[-1] in ("millón", "millones") else " pesos"
        return palabras + conector

    return _PATRON_DINERO.sub(reemplazar, texto)


def _limpiar_para_voz(texto: str) -> str:
    """
    Saca el marcado que suena mal leído en voz alta.
    Los asteriscos de negrita de WhatsApp se leerían como "asterisco".
    """
    limpio = _dinero_a_palabras(texto)
    limpio = limpio.replace("*", "").replace("_", "").replace("`", "")
    limpio = limpio.replace("—", ",").replace("·", ",")
    # Los guiones de lista al inicio de línea se leen como pausa, no como "guion"
    lineas = [l.lstrip("- ").strip() if l.strip().startswith("-") else l for l in limpio.split("\n")]
    return _pronunciar("\n".join(l for l in lineas if l.strip()))


async def _sintetizar_edge(texto: str) -> bytes | None:
    try:
        import edge_tts
    except ImportError:
        logger.warning("[VOZ] edge-tts no instalado (pip install edge-tts)")
        return None

    try:
        comunicador = edge_tts.Communicate(texto, VOZ_EDGE)
        trozos = bytearray()
        async for evento in comunicador.stream():
            if evento["type"] == "audio":
                trozos.extend(evento["data"])
        return bytes(trozos) if trozos else None
    except Exception as e:
        logger.error(f"[VOZ] Falló edge-tts: {e}")
        return None


async def _sintetizar_elevenlabs(texto: str) -> bytes | None:
    if not (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID):
        return None

    import httpx
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                url,
                headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
                json={
                    "text": texto,
                    "model_id": ELEVENLABS_MODEL,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
            )
            if r.status_code == 200:
                return r.content
            logger.error(f"[VOZ] ElevenLabs {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[VOZ] Falló ElevenLabs: {e}")
    return None


async def sintetizar(texto: str) -> bytes | None:
    """
    Convierte texto en MP3. Devuelve None si no se pudo — el llamador debe
    seguir enviando el texto igual.
    """
    if not texto:
        return None

    limpio = _limpiar_para_voz(texto)
    if len(limpio) > LIMITE_CARACTERES:
        logger.info(f"[VOZ] Texto de {len(limpio)} caracteres, sobre el límite: se envía solo texto")
        return None

    # ElevenLabs manda si está configurado; si no, el gratuito
    if ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID:
        audio = await _sintetizar_elevenlabs(limpio)
        if audio:
            logger.info(f"[VOZ] ElevenLabs — {len(audio)} bytes")
            return audio
        logger.warning("[VOZ] ElevenLabs falló, cayendo a edge-tts")

    audio = await _sintetizar_edge(limpio)
    if audio:
        logger.info(f"[VOZ] edge-tts ({VOZ_EDGE}) — {len(audio)} bytes")
    return audio
