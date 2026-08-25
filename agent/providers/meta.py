# agent/providers/meta.py — Adaptador para Meta WhatsApp Cloud API
# Generado por AgentKit

import base64
import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante
from agent.transcripcion import transcribir_audio
from agent.vouchers import (
    es_numero_autorizado,
    detectar_local,
    subir_voucher,
    MENSAJE_CONFIRMACION,
    MENSAJE_ERROR,
    MENSAJE_FALTA_LOCAL,
)

logger = logging.getLogger("agentkit")


class ProveedorMeta(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando la API oficial de Meta (Cloud API)."""

    def __init__(self):
        self.access_token = os.getenv("META_ACCESS_TOKEN")
        self.phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
        self.verify_token = os.getenv("META_VERIFY_TOKEN", "agentkit-verify")
        self.api_version = "v21.0"

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Meta requiere verificación GET con hub.verify_token."""
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and token == self.verify_token:
            # Meta espera el challenge como respuesta en texto plano
            return int(challenge)
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload anidado de Meta Cloud API."""
        body = await request.json()
        mensajes = []
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # Meta incluye el nombre de perfil del cliente en value.contacts[].
                # Lo mapeamos por wa_id para asociarlo a cada mensaje entrante.
                nombres = {
                    c.get("wa_id", ""): c.get("profile", {}).get("name", "")
                    for c in value.get("contacts", [])
                }

                for msg in value.get("messages", []):
                    tipo = msg.get("type")
                    remitente = msg.get("from", "")

                    if tipo == "text":
                        mensajes.append(MensajeEntrante(
                            telefono=remitente,
                            texto=msg.get("text", {}).get("body", ""),
                            mensaje_id=msg.get("id", ""),
                            es_propio=False,  # Meta solo envía mensajes entrantes
                            nombre=nombres.get(remitente, ""),
                        ))

                    elif tipo == "audio":
                        # Nota de voz: descargamos el audio y lo transcribimos a texto
                        media_id = msg.get("audio", {}).get("id", "")
                        texto = await self._transcribir_media(media_id)
                        if texto:
                            mensajes.append(MensajeEntrante(
                                telefono=remitente,
                                texto=texto,
                                mensaje_id=msg.get("id", ""),
                                es_propio=False,
                                nombre=nombres.get(remitente, ""),
                                fue_audio=True,
                            ))

                    elif tipo == "image":
                        # Comprobante de pago: si viene de un número autorizado (personal),
                        # lo archivamos en Google Drive y confirmamos. NO pasa por Claude.
                        if es_numero_autorizado(remitente):
                            await self._procesar_comprobante(msg, remitente)
                            continue

                        # De cualquier otro número, la foto sí pasa por Claude —
                        # Maximus la mira. Import acá adentro, no arriba: providers/
                        # no debe depender de agent.maximus al cargar el módulo.
                        from agent.maximus import es_maximus
                        if not es_maximus(remitente):
                            continue  # fotos de clientes: se ignoran, no hay flujo para eso

                        imagen = msg.get("image", {})
                        media_id = imagen.get("id", "")
                        mime = imagen.get("mime_type", "image/jpeg")
                        imagen_bytes = await self._descargar_media(media_id)
                        if not imagen_bytes:
                            continue

                        mensajes.append(MensajeEntrante(
                            telefono=remitente,
                            texto=imagen.get("caption", "").strip() or "¿Qué ves en esta foto?",
                            mensaje_id=msg.get("id", ""),
                            es_propio=False,
                            nombre=nombres.get(remitente, ""),
                            imagen_b64=base64.b64encode(imagen_bytes).decode("ascii"),
                            imagen_mime=mime,
                        ))
        return mensajes

    async def _procesar_comprobante(self, msg: dict, remitente: str) -> None:
        """Descarga la imagen de comprobante, la sube a Drive y responde al remitente.

        El local (mall/playa) se toma del texto que acompaña la foto (caption).
        Si no viene el local, se le pide al remitente que lo indique.
        """
        imagen = msg.get("image", {})
        local = detectar_local(imagen.get("caption", ""))
        if not local:
            await self.enviar_mensaje(remitente, MENSAJE_FALTA_LOCAL)
            return

        media_id = imagen.get("id", "")
        mime = imagen.get("mime_type", "image/jpeg")
        imagen_bytes = await self._descargar_media(media_id)
        ok = await subir_voucher(imagen_bytes, mime, remitente, local)
        await self.enviar_mensaje(
            remitente, MENSAJE_CONFIRMACION if ok else MENSAJE_ERROR
        )

    async def _descargar_media(self, media_id: str) -> bytes | None:
        """Descarga un archivo de media (audio, imagen, etc.) desde Meta por su ID."""
        if not media_id or not self.access_token:
            return None
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(timeout=60) as client:
            # 1) Pedir la URL temporal del media
            meta_url = f"https://graph.facebook.com/{self.api_version}/{media_id}"
            r = await client.get(meta_url, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error obteniendo URL de media: {r.status_code} — {r.text}")
                return None
            media_url = r.json().get("url")
            if not media_url:
                return None
            # 2) Descargar el archivo (requiere el mismo token en el header)
            r2 = await client.get(media_url, headers=headers)
            if r2.status_code != 200:
                logger.error(f"Error descargando media: {r2.status_code}")
                return None
            return r2.content

    async def _transcribir_media(self, media_id: str) -> str:
        """Descarga una nota de voz y la convierte a texto."""
        audio_bytes = await self._descargar_media(media_id)
        if not audio_bytes:
            return ""
        return await transcribir_audio(audio_bytes)

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via Meta WhatsApp Cloud API."""
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "text",
            "text": {"body": mensaje},
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error Meta API: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def enviar_audio(self, telefono: str, audio_mp3: bytes) -> bool:
        """
        Envía un MP3 como mensaje de audio. Dos pasos según la API de Meta:
        subir el archivo para obtener un media_id, y luego enviar el mensaje.
        Nunca lanza excepción: si falla, el llamador ya envió el texto.
        """
        if not self.access_token or not self.phone_number_id or not audio_mp3:
            return False

        base = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"
        auth = {"Authorization": f"Bearer {self.access_token}"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                subida = await client.post(
                    f"{base}/media",
                    headers=auth,
                    data={"messaging_product": "whatsapp", "type": "audio/mpeg"},
                    files={"file": ("maximus.mp3", audio_mp3, "audio/mpeg")},
                )
                if subida.status_code != 200:
                    logger.error(f"[VOZ] Subida a Meta falló: {subida.status_code} — {subida.text[:200]}")
                    return False

                media_id = subida.json().get("id")
                if not media_id:
                    return False

                envio = await client.post(
                    f"{base}/messages",
                    headers={**auth, "Content-Type": "application/json"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": telefono,
                        "type": "audio",
                        "audio": {"id": media_id},
                    },
                )
                if envio.status_code != 200:
                    logger.error(f"[VOZ] Envío de audio falló: {envio.status_code} — {envio.text[:200]}")
                return envio.status_code == 200
        except Exception as e:
            logger.error(f"[VOZ] Error enviando audio: {e}")
            return False

    async def enviar_plantilla(
        self, telefono: str, plantilla: str, parametros: list[str], idioma: str = "es"
    ) -> bool:
        """
        Envía una plantilla aprobada de WhatsApp (mensaje iniciado por el negocio).

        Args:
            telefono: Número del destinatario
            plantilla: Nombre de la plantilla aprobada en Meta (ej: "pedido_tortas")
            parametros: Valores para las variables {{1}}, {{2}}, ... en orden
            idioma: Código de idioma de la plantilla (default: "es")
        """
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        componentes = []
        if parametros:
            componentes.append({
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in parametros],
            })
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "template",
            "template": {
                "name": plantilla,
                "language": {"code": idioma},
                "components": componentes,
            },
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error Meta plantilla: {r.status_code} — {r.text}")
            return r.status_code == 200
