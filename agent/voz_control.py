# agent/voz_control.py — interruptor de la voz clonada de ElevenLabs

"""
Mismo criterio que el portón/riego: palabra clave exacta, sin
interpretación de IA, para que el cambio sea instantáneo y no dependa de
que el modelo entienda bien la intención — acá el motivo es costo (cada
respuesta con ElevenLabs gasta caracteres de pago), no seguridad física,
pero el patrón determinístico sigue siendo el más simple y confiable.

Config en config/voz.json (activo/inactivo, límite de caracteres). Se lee
en cada mensaje, sin caché — el cambio aplica de inmediato.
"""

import json
import logging
import unicodedata

logger = logging.getLogger("agentkit")

RUTA_CONFIG = "config/voz.json"
DEFECTO = {"elevenlabs_activo": True, "limite_caracteres": 1200}


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().lower()


def cargar_config() -> dict:
    try:
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return dict(DEFECTO)
    except json.JSONDecodeError as e:
        logger.error(f"[VOZ] {RUTA_CONFIG} tiene JSON inválido: {e}")
        return dict(DEFECTO)


def _guardar_config(cfg: dict) -> None:
    with open(RUTA_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


async def procesar_mensaje_voz(texto: str) -> str | None:
    """
    Devuelve None si el mensaje no es un comando de voz (el llamador sigue
    con el flujo normal). Si lo es, siempre devuelve una respuesta y el
    llamador no debe hacer nada más con ese mensaje.
    """
    t = _normalizar(texto)

    if t == "voz clonada on":
        cfg = cargar_config()
        cfg["elevenlabs_activo"] = True
        _guardar_config(cfg)
        logger.info("[VOZ] ElevenLabs activado por comando")
        return "Voz clonada activada 🎙️ — cuando te responda en audio, va a intentar usar tu voz de ElevenLabs primero."

    if t == "voz clonada off":
        cfg = cargar_config()
        cfg["elevenlabs_activo"] = False
        _guardar_config(cfg)
        logger.info("[VOZ] ElevenLabs desactivado por comando")
        return "Voz clonada desactivada — desde ahora siempre uso la voz gratis (sin costo), hasta que mandes \"voz clonada on\"."

    return None
