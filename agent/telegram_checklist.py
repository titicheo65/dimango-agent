# agent/telegram_checklist.py — Bots de Telegram del checklist operativo (uno por local)

"""
Wrapper mínimo de la API de Telegram para el checklist operativo (tareas con
horario, botones y escalamiento).

Un bot POR LOCAL (ej. Checklistmall_bot, Checklistplaya_bot), cada uno metido
solo en el grupo de su local — así un bot nunca puede ver ni tocar el grupo
del otro local. Todos DISTINTOS del bot privado de Maximus
(agent/telegram_maximus.py), que es 1:1 con el dueño.

El token de cada bot vive en TELEGRAM_CHECKLIST_BOT_TOKEN_<LOCAL> (mayúsculas).
Telegram solo permite un webhook por bot, así que cada uno se registra a su
propia URL: /telegram/checklist/webhook/<local> (ver agent/main.py).
"""

import os
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")


def _cargar_tokens() -> dict[str, str]:
    """Junta TELEGRAM_CHECKLIST_BOT_TOKEN_<LOCAL> para cada local conocido."""
    tokens = {}
    for local in ("mall", "playa"):
        token = os.getenv(f"TELEGRAM_CHECKLIST_BOT_TOKEN_{local.upper()}", "")
        if token:
            tokens[local] = token
    return tokens


TOKENS = _cargar_tokens()


def _api(local: str) -> str:
    return f"https://api.telegram.org/bot{TOKENS.get(local, '')}"


def configurado(local: str) -> bool:
    return local in TOKENS


def _teclado(envio_id: int) -> dict:
    """Botones de una tarea: hecho / problema / posponer 5 min."""
    return {
        "inline_keyboard": [[
            {"text": "✅ Hecho", "callback_data": f"chk:{envio_id}:hecho"},
            {"text": "⚠️ Problema", "callback_data": f"chk:{envio_id}:problema"},
            {"text": "⏳ +5 min", "callback_data": f"chk:{envio_id}:posponer"},
        ]]
    }


async def enviar_tarea(local: str, chat_id, texto: str, envio_id: int) -> str | None:
    """Envía un recordatorio de tarea con botones, con el bot del local dado."""
    if not configurado(local):
        logger.error(f"[CHECKLIST-TG] Falta TELEGRAM_CHECKLIST_BOT_TOKEN_{local.upper()}")
        return None
    api = _api(local)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{api}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": texto,
                    "parse_mode": "Markdown",
                    "reply_markup": _teclado(envio_id),
                },
            )
            if r.status_code != 200:
                logger.warning(f"[CHECKLIST-TG] Markdown rechazado ({r.status_code}), reenvío en texto plano")
                r = await client.post(
                    f"{api}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": texto,
                        "reply_markup": _teclado(envio_id),
                    },
                )
            if r.status_code != 200:
                logger.error(f"[CHECKLIST-TG] Error enviando tarea ({local}): {r.status_code} — {r.text}")
                return None
            return str(r.json().get("result", {}).get("message_id", ""))
    except Exception as e:
        logger.error(f"[CHECKLIST-TG] Error enviando tarea ({local}): {e}")
        return None


async def editar_mensaje(local: str, chat_id, message_id: str, texto: str) -> bool:
    """Reemplaza el texto de un mensaje ya enviado y le quita los botones (tarea resuelta)."""
    if not configurado(local) or not message_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{_api(local)}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": int(message_id),
                    "text": texto,
                    "parse_mode": "Markdown",
                },
            )
            return r.status_code == 200
    except Exception as e:
        logger.error(f"[CHECKLIST-TG] Error editando mensaje ({local}): {e}")
        return False


async def enviar_mensaje(local: str, chat_id, texto: str) -> bool:
    """Mensaje simple sin botones (aviso al supervisor), con el bot del local dado."""
    if not configurado(local):
        return False
    api = _api(local)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{api}/sendMessage",
                json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"},
            )
            if r.status_code != 200:
                r = await client.post(f"{api}/sendMessage", json={"chat_id": chat_id, "text": texto})
            return r.status_code == 200
    except Exception as e:
        logger.error(f"[CHECKLIST-TG] Error enviando mensaje ({local}): {e}")
        return False


async def responder_callback(local: str, callback_id: str, texto: str = "") -> None:
    """Cierra el 'reloj' de carga del botón tocado, con un aviso corto (toast)."""
    if not configurado(local):
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"{_api(local)}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": texto[:190]},
            )
    except Exception as e:
        logger.error(f"[CHECKLIST-TG] Error respondiendo callback ({local}): {e}")
