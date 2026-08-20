# agent/brain.py — Cerebro del agente: conexión con Claude API
# Generado por AgentKit

"""
Lógica de IA del agente. Lee el system prompt de prompts.yaml
y genera respuestas usando la API de Anthropic Claude.
"""

import os
import yaml
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# Zona horaria de Chile — para que el agente sepe qué día es hoy al agendar reservas
TZ_CHILE = ZoneInfo("America/Santiago")

# Nombres en español (datetime.weekday(): 0=lunes ... 6=domingo)
_DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def contexto_fecha_actual() -> str:
    """
    Genera un bloque con la fecha y día de la semana actuales en hora de Chile.
    Se antepone al system prompt para que el agente NUNCA adivine el día al agendar
    reservas (ej: confundir sábado con domingo).
    """
    ahora = datetime.now(TZ_CHILE)
    dia_semana = _DIAS_SEMANA[ahora.weekday()]
    mes = _MESES[ahora.month - 1]
    return (
        "## Fecha y hora actual (referencia obligatoria)\n"
        f"Hoy es {dia_semana} {ahora.day} de {mes} de {ahora.year}, "
        f"{ahora.strftime('%H:%M')} hrs (hora de Chile).\n"
        "Usa SIEMPRE esta fecha para calcular días al agendar reservas. Cuando el cliente "
        "diga un día (\"el sábado\", \"mañana\", \"el 20\"), calcula la fecha exacta a partir "
        "de HOY y CONFIRMA explícitamente el día de la semana junto con la fecha "
        "(ej: \"domingo 19 de julio\"). Nunca inventes ni adivines el día de la semana.\n"
    )

# Cliente de Anthropic
client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    """Lee el system prompt desde config/prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres un asistente útil. Responde en español.")


def obtener_mensaje_error() -> str:
    """Retorna el mensaje de error configurado en prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo en unos minutos.")


def obtener_mensaje_fallback() -> str:
    """Retorna el mensaje de fallback configurado en prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo?")


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta usando Claude API.

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]

    Returns:
        La respuesta generada por Claude
    """
    # Si el mensaje es muy corto o vacío, usar fallback
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    # Anteponer la fecha/día actual para que el agente no adivine al agendar reservas
    system_prompt = contexto_fecha_actual() + "\n" + cargar_system_prompt()

    # Construir mensajes para la API
    mensajes = []
    for msg in historial:
        mensajes.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Agregar el mensaje actual
    mensajes.append({
        "role": "user",
        "content": mensaje
    })

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=mensajes
        )

        respuesta = response.content[0].text
        logger.info(f"Respuesta generada ({response.usage.input_tokens} in / {response.usage.output_tokens} out)")
        return respuesta

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()
