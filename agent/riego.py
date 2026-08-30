# agent/riego.py — Encendido/apagado del riego de la parcela por WhatsApp

"""
Mismo criterio que agent/porton.py: control físico, cero interpretación de
IA. Palabra clave exacta -> acción exacta. Sin ventana horaria a propósito
(pedido de Ricardo: este dispositivo es libre de horario) -- solo lista de
autorizados.

Config en config/riego.json (palabras clave, lista de autorizados).
Credenciales de Tuya (secretas) van en el .env, compartidas con el portón
vía agent/tuya_client.py.

Cada intento —encendido, apagado, denegado por número— se registra en la
tabla `riego_log`.
"""

import json
import logging
import os
import unicodedata

from sqlalchemy import String, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from agent.memory import Base, async_session
from agent import tuya_client
from agent.porton import normalizar_telefono

logger = logging.getLogger("agentkit")

RUTA_CONFIG = "config/riego.json"

TUYA_DEVICE_ID_RIEGO = os.getenv("TUYA_DEVICE_ID_RIEGO", "")


class RiegoLog(Base):
    """Cada intento de prender/apagar el riego. No se borra nunca."""
    __tablename__ = "riego_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    resultado: Mapped[str] = mapped_column(String(30))  # encendido | apagado | denegado_numero | error_tuya
    detalle: Mapped[str] = mapped_column(String(300), default="")
    fecha_hora: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def _normalizar_texto(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().lower()


def cargar_config() -> dict:
    try:
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"[RIEGO] No se encontró {RUTA_CONFIG}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"[RIEGO] {RUTA_CONFIG} tiene JSON inválido: {e}")
        return {}


def _accion(texto: str, cfg: dict) -> str | None:
    """Devuelve 'on', 'off', o None si el texto no es ninguna de las dos
    palabras clave configuradas."""
    t = _normalizar_texto(texto)
    if t == _normalizar_texto(cfg.get("palabra_on", "agua")):
        return "on"
    if t == _normalizar_texto(cfg.get("palabra_off", "no agua")):
        return "off"
    return None


def _autorizado(telefono: str, cfg: dict) -> bool:
    tel = normalizar_telefono(telefono)
    for persona in cfg.get("autorizados", []):
        if normalizar_telefono(persona.get("telefono", "")) == tel and persona.get("activo"):
            return True
    return False


async def _registrar(telefono: str, resultado: str, detalle: str = "") -> None:
    async with async_session() as session:
        session.add(RiegoLog(telefono=telefono, resultado=resultado, detalle=detalle))
        await session.commit()


async def _accionar_riego(encender: bool) -> tuple[bool, str]:
    if not TUYA_DEVICE_ID_RIEGO:
        return False, "Falta configurar TUYA_DEVICE_ID_RIEGO en el .env"
    return await tuya_client.enviar_comando(TUYA_DEVICE_ID_RIEGO, "switch_1", encender)


# ════════════════════════════════════════════════════════════
# Punto de entrada — llamado desde webhook_handler
# ════════════════════════════════════════════════════════════

async def procesar_mensaje_riego(telefono: str, texto: str) -> str | None:
    """
    Devuelve None si el mensaje NO es ninguna de las palabras clave del
    riego (el llamador debe seguir con el flujo normal). Si SÍ lo es,
    siempre devuelve una respuesta de texto, y el llamador no debe hacer
    nada más con ese mensaje.
    """
    cfg = cargar_config()
    if not cfg:
        return None

    accion = _accion(texto, cfg)
    if accion is None:
        return None

    if not _autorizado(telefono, cfg):
        await _registrar(telefono, "denegado_numero", accion)
        logger.warning(f"[RIEGO] Intento denegado (número no autorizado): {telefono}")
        return "No tienes autorización para controlar el riego."

    encender = accion == "on"
    ok, error = await _accionar_riego(encender)
    if ok:
        await _registrar(telefono, "encendido" if encender else "apagado")
        logger.info(f"[RIEGO] {'Encendido' if encender else 'Apagado'} por {telefono}")
        return "Riego encendido 💧" if encender else "Riego apagado"
    else:
        await _registrar(telefono, "error_tuya", error)
        logger.error(f"[RIEGO] Falló ({accion}) para {telefono}: {error}")
        return "No pude cambiar el riego — avísale a Ricardo."
