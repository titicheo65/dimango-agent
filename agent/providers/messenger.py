# agent/providers/messenger.py — Adaptador para Facebook Messenger (Meta)
# Generado por AgentKit

"""
Proveedor de mensajería directa (DM) de Facebook Messenger usando la API de Meta.

El cliente escribe a la página de Facebook del negocio -> Meta manda un webhook
a /webhook -> este proveedor normaliza el mensaje al mismo formato que WhatsApp,
y la respuesta del agente se envía de vuelta por la API de Messenger.
"""

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorMessenger(ProveedorWhatsApp):
    """Proveedor de DMs de Facebook Messenger (Meta)."""

    def __init__(self):
        self.access_token = os.getenv("MESSENGER_PAGE_TOKEN")
        # Reutilizamos el mismo verify token de Meta para validar el webhook
        self.verify_token = os.getenv("META_VERIFY_TOKEN", "agentkit-verify")
        self.api_version = "v21.0"

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Verificación GET del webhook (Meta usa hub.verify_token)."""
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and token == self.verify_token:
            return int(challenge)
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de mensajería de Messenger (entry[].messaging[])."""
        body = await request.json()
        mensajes = []
        for entry in body.get("entry", []):
            for evento in entry.get("messaging", []):
                mensaje = evento.get("message", {})

                # Ignorar "echoes": mensajes que envía la propia página
                if mensaje.get("is_echo"):
                    continue

                texto = mensaje.get("text", "")
                if not texto:
                    # Eventos sin texto (lecturas, postbacks, etc.) se ignoran
                    continue

                # El "sender.id" es el PSID (ID del usuario para esta página).
                # Lo usamos como clave de conversación, igual que el teléfono en WhatsApp.
                remitente = evento.get("sender", {}).get("id", "")
                mensajes.append(MensajeEntrante(
                    telefono=remitente,
                    texto=texto,
                    mensaje_id=mensaje.get("mid", ""),
                    es_propio=False,
                ))
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía un mensaje de Messenger via la API de Meta (graph.facebook.com)."""
        if not self.access_token:
            logger.warning("MESSENGER_PAGE_TOKEN no configurado")
            return False
        url = f"https://graph.facebook.com/{self.api_version}/me/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": telefono},
            "message": {"text": mensaje},
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error Messenger API: {r.status_code} — {r.text}")
            return r.status_code == 200
