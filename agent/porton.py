# agent/porton.py — Apertura del portón por WhatsApp, con lista de autorizados y horario

"""
Por qué es un módulo aparte, NO una herramienta de Maximus: esto abre una
puerta física. La decisión de abrir o no NO puede depender de que un modelo
de lenguaje "interprete" la intención — se compara la palabra clave literal,
se verifica el número contra una lista explícita, y se verifica la hora
contra una ventana configurada. Cero ambigüedad posible.

Config en config/porton.json (palabra clave, horario, lista de autorizados —
activar/desactivar a alguien es cambiar 'activo' ahí, no tocar código).
Credenciales de Tuya (secretas) van en el .env, nunca en el json.

Cada intento —autorizado, denegado por número, o denegado por horario— se
registra en la tabla `porton_log`, con quién y cuándo. Es una puerta física:
siempre tiene que poder responderse "quién la abrió y cuándo".
"""

import json
import logging
import os
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import String, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.memory import Base, async_session
from agent import tuya_client

logger = logging.getLogger("agentkit")

RUTA_CONFIG = "config/porton.json"
TZ_CHILE = ZoneInfo("America/Santiago")

TUYA_DEVICE_ID = os.getenv("TUYA_DEVICE_ID", "")


class PortonLog(Base):
    """Cada intento de abrir el portón, autorizado o no. No se borra nunca."""
    __tablename__ = "porton_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    resultado: Mapped[str] = mapped_column(String(30))  # abierto | denegado_numero | denegado_horario | error_tuya
    detalle: Mapped[str] = mapped_column(String(300), default="")
    fecha_hora: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def normalizar_telefono(telefono: str) -> str:
    return "".join(c for c in (telefono or "") if c.isdigit())


def _normalizar_texto(texto: str) -> str:
    """Quita tildes y pasa a minúsculas, para que 'Portón', 'porton' y
    'PORTÓN' cuenten como lo mismo -- pero sigue siendo comparación
    literal, no interpretación."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().lower()


def cargar_config() -> dict:
    try:
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"[PORTON] No se encontró {RUTA_CONFIG}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"[PORTON] {RUTA_CONFIG} tiene JSON inválido: {e}")
        return {}


def es_palabra_clave(texto: str, cfg: dict) -> bool:
    clave = _normalizar_texto(cfg.get("palabra_clave", "porton"))
    return _normalizar_texto(texto) == clave


def _franja(cfg: dict, persona: dict | None = None) -> tuple[str, str]:
    """
    La franja de esta persona si tiene una propia; si no, la general.

    Por qué por persona (25-sep-2026): el portón lo abre gente que entra a
    horas distintas —el que recibe proveedores a las 07:00 no es el mismo que
    llega a las 08:00—. Con una sola ventana para todos, ampliarla para uno
    se la ampliaba a todos.
    """
    p = persona or {}
    inicio = p.get("desde") or cfg.get("horario_inicio", "00:00")
    fin = p.get("hasta") or cfg.get("horario_fin", "23:59")
    return inicio, fin


def _dentro_de_horario(cfg: dict, persona: dict | None = None) -> bool:
    ahora = datetime.now(TZ_CHILE).strftime("%H:%M")
    inicio, fin = _franja(cfg, persona)
    return inicio <= ahora <= fin


def _buscar_persona(telefono: str, cfg: dict) -> dict | None:
    tel = normalizar_telefono(telefono)
    for persona in cfg.get("autorizados", []):
        if normalizar_telefono(persona.get("telefono", "")) == tel and persona.get("activo"):
            return persona
    return None


async def _registrar(telefono: str, resultado: str, detalle: str = "") -> None:
    async with async_session() as session:
        session.add(PortonLog(telefono=telefono, resultado=resultado, detalle=detalle))
        await session.commit()


async def _abrir_porton_tuya() -> tuple[bool, str]:
    if not TUYA_DEVICE_ID:
        return False, "Falta configurar TUYA_DEVICE_ID en el .env"
    return await tuya_client.enviar_comando(TUYA_DEVICE_ID, "switch_1", True)


# ════════════════════════════════════════════════════════════
# Punto de entrada — llamado desde webhook_handler
# ════════════════════════════════════════════════════════════

async def procesar_mensaje_porton(telefono: str, texto: str) -> str | None:
    """
    Devuelve None si el mensaje NO era la palabra clave del portón (el
    llamador debe seguir con el flujo normal). Si SÍ lo era, siempre
    devuelve una respuesta de texto, y el llamador no debe hacer nada más
    con ese mensaje.
    """
    cfg = cargar_config()
    if not cfg or not es_palabra_clave(texto, cfg):
        return None

    persona = _buscar_persona(telefono, cfg)
    if persona is None:
        await _registrar(telefono, "denegado_numero")
        logger.warning(f"[PORTON] Intento denegado (número no autorizado): {telefono}")
        return "No tienes autorización para abrir el portón."

    inicio, fin = _franja(cfg, persona)
    if not persona.get("horario_libre") and not _dentro_de_horario(cfg, persona):
        await _registrar(telefono, "denegado_horario", f"ventana {inicio}-{fin}")
        logger.warning(f"[PORTON] Intento fuera de horario ({persona.get('nombre')}): {telefono}")
        return f"El portón solo se abre entre las {inicio} y las {fin}."

    ok, error = await _abrir_porton_tuya()
    if ok:
        await _registrar(telefono, "abierto")
        logger.info(f"[PORTON] Abierto por {telefono}")
        return "Portón abierto ✅"
    else:
        await _registrar(telefono, "error_tuya", error)
        logger.error(f"[PORTON] Falló la apertura para {telefono}: {error}")
        return "No pude abrir el portón — avísale a Ricardo."
