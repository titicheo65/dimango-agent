# agent/providers/twilio.py — Adaptador para Twilio WhatsApp
# Generado por AgentKit

"""
Proveedor de WhatsApp usando Twilio.

Autenticación:
  Soporta dos métodos (usa el que esté configurado en .env):
  1. API Key (recomendado): TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET
     El usuario (login) es el API Key SID (SK...) y la contraseña es el secret.
     La URL siempre usa el Account SID (AC...).
  2. Auth Token clásico: TWILIO_AUTH_TOKEN
     El usuario es el Account SID y la contraseña es el Auth Token.
"""

import os
import logging
import base64
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorTwilio(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Twilio."""

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.phone_number = os.getenv("TWILIO_PHONE_NUMBER")

        # Método 1: API Key (SK...) + Secret
        self.api_key_sid = os.getenv("TWILIO_API_KEY_SID")
        self.api_key_secret = os.getenv("TWILIO_API_KEY_SECRET")

        # Método 2: Auth Token clásico
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    def _credenciales_auth(self) -> tuple[str, str] | None:
        """Devuelve (usuario, contraseña) para Basic Auth, o None si faltan datos."""
        if self.api_key_sid and self.api_key_secret:
            return (self.api_key_sid, self.api_key_secret)
        if self.account_sid and self.auth_token:
            return (self.account_sid, self.auth_token)
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload form-encoded de Twilio."""
        form = await request.form()
        texto = form.get("Body", "")
        telefono = form.get("From", "").replace("whatsapp:", "")
        mensaje_id = form.get("MessageSid", "")
        if not texto:
            return []
        return [MensajeEntrante(
            telefono=telefono,
            texto=texto,
            mensaje_id=mensaje_id,
            es_propio=False,
        )]

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via Twilio API."""
        auth = self._credenciales_auth()
        if not auth or not self.account_sid or not self.phone_number:
            logger.warning("Variables de Twilio incompletas (revisa .env)")
            return False

        usuario, contrasena = auth
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        token = base64.b64encode(f"{usuario}:{contrasena}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        data = {
            "From": f"whatsapp:{self.phone_number}",
            "To": f"whatsapp:{telefono}",
            "Body": mensaje,
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data=data, headers=headers)
            if r.status_code != 201:
                logger.error(f"Error Twilio: {r.status_code} — {r.text}")
            return r.status_code == 201
