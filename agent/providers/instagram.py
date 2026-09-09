# agent/providers/instagram.py — Adaptador para Instagram Messaging (Meta)
# Generado por AgentKit

"""
Proveedor de mensajería directa (DM) de Instagram usando la API de Meta
con "inicio de sesión de Instagram" (Instagram Login).

El cliente escribe un DM a la cuenta del negocio -> Meta manda un webhook
a /webhook -> este proveedor normaliza el mensaje al mismo formato que WhatsApp,
y la respuesta del agente se envía de vuelta por la API de Instagram.
"""

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorInstagram(ProveedorWhatsApp):
    """Proveedor de DMs de Instagram (Meta - Instagram Login)."""

    def __init__(self):
        self.access_token = os.getenv("IG_ACCESS_TOKEN")
        # Reutilizamos el mismo verify token de Meta para validar el webhook
        self.verify_token = os.getenv("META_VERIFY_TOKEN", "agentkit-verify")
        self.api_version = "v21.0"

        # Instagram tiene DOS caminos y cada uno responde por un host distinto.
        # Elegir mal el host hace que el mensaje del cliente SÍ llegue y la
        # respuesta muera con un error de autenticación — el peor de los fallos,
        # porque el cliente queda esperando y en el log solo se ve un 400.
        #
        #  - Por Página (Messenger -> Configuración de Instagram): token de la
        #    Página, se responde por graph.facebook.com igual que Messenger.
        #    Usa los mismos permisos que ya están aprobados para Messenger.
        #  - Por "Instagram Login": token de Instagram y graph.instagram.com,
        #    pero exige revisión de Meta para atender clientes reales.
        via_pagina = os.getenv("IG_VIA_PAGINA", "1").strip().lower()
        self.usa_pagina = via_pagina not in ("0", "false", "no")
        self.api_host = "graph.facebook.com" if self.usa_pagina else "graph.instagram.com"

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
        """Parsea el payload de mensajería de Instagram (entry[].messaging[])."""
        body = await request.json()
        mensajes = []
        for entry in body.get("entry", []):
            for evento in entry.get("messaging", []):
                mensaje = evento.get("message", {})

                # Ignorar "echoes": mensajes que envía el propio negocio
                if mensaje.get("is_echo"):
                    continue

                texto = mensaje.get("text", "")
                if not texto:
                    # Eventos sin texto (lecturas, reacciones, etc.) se ignoran
                    continue

                # El "sender.id" es el IGSID (ID del usuario en Instagram).
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
        """Envía un DM de Instagram via la API de Meta (ver self.api_host)."""
        if not self.access_token:
            logger.warning("IG_ACCESS_TOKEN no configurado")
            return False
        url = f"https://{self.api_host}/{self.api_version}/me/messages"
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
                logger.error(f"Error Instagram API: {r.status_code} — {r.text}")
            return r.status_code == 200
