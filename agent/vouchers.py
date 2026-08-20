# agent/vouchers.py — Reenvío de comprobantes de pago a Google Drive
# Generado por AgentKit

"""
Cuando un número AUTORIZADO (personal de Dimango) envía una FOTO de comprobante
de pago por WhatsApp, el agente la reenvía a un Google Apps Script que la guarda
en Google Drive.

El LOCAL se identifica por el texto (caption) que acompaña la foto:
"mall" o "playa". Según el local se define la carpeta y la fecha del cierre:

  - mall  → carpeta "voucher mall",  fecha = el mismo día
  - playa → carpeta "voucher playa", fecha = el día ANTERIOR
            (el cierre de Playa se hace pasadas las 00:00, ya del día siguiente,
             pero corresponde al día que cerró)

Dentro de cada carpeta de local se mantiene la estructura Año / Mes / Día.

La URL del Apps Script se configura en el .env con VOUCHER_WEBHOOK_URL
(así no queda en el repositorio, que es público).
"""

import os
import base64
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("agentkit")

# Zona horaria de Chile (para calcular la fecha del cierre correctamente)
TZ_CHILE = ZoneInfo("America/Santiago")

# Nombres de meses en español, igual que en el Apps Script (capitalizados)
_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
          "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# URL del Apps Script desplegado como App web (termina en /exec).
# Se define en el .env del servidor: VOUCHER_WEBHOOK_URL=https://script.google.com/.../exec
VOUCHER_WEBHOOK_URL = os.getenv("VOUCHER_WEBHOOK_URL", "").strip()

# Números autorizados a enviar comprobantes (solo dígitos, con código país 56).
# Se pueden sobrescribir por .env (VOUCHER_AUTHORIZED_NUMBERS separados por coma).
_DEFAULT_AUTORIZADOS = "56974965689,56956673866,56967210490,56974596595,56956673184"
NUMEROS_AUTORIZADOS = {
    n.strip()
    for n in os.getenv("VOUCHER_AUTHORIZED_NUMBERS", _DEFAULT_AUTORIZADOS).split(",")
    if n.strip()
}

# Carpeta raíz por local
CARPETA_POR_LOCAL = {
    "mall": "voucher mall",
    "playa": "voucher playa",
}

# Respuestas al remitente
MENSAJE_CONFIRMACION = "Comprobante recibido ✅"
MENSAJE_ERROR = "No pude guardar el comprobante. Por favor reenvíalo en un momento."
MENSAJE_FALTA_LOCAL = (
    "Recibí tu foto, pero necesito saber de qué local es. "
    "Reenvíala escribiendo *mall* o *playa* junto a la imagen. 🙏"
)


def solo_digitos(telefono: str) -> str:
    """Deja solo los dígitos de un número (quita +, espacios, etc.)."""
    return "".join(c for c in (telefono or "") if c.isdigit())


def es_numero_autorizado(telefono: str) -> bool:
    """True si el número está autorizado a enviar comprobantes."""
    return solo_digitos(telefono) in NUMEROS_AUTORIZADOS


def detectar_local(caption: str) -> str | None:
    """
    Identifica el local a partir del texto que acompaña la foto.
    Retorna "mall", "playa" o None si no se reconoce.
    """
    texto = (caption or "").lower()
    if "mall" in texto:
        return "mall"
    if "playa" in texto:
        return "playa"
    return None


def carpetas_destino(local: str) -> dict:
    """
    Calcula las carpetas de destino en Drive según el local y su fecha de cierre.

    - mall  → hoy
    - playa → ayer (el cierre se hace pasada la medianoche)

    Retorna los nombres de carpeta: local, año, mes, día.
    """
    fecha = datetime.now(TZ_CHILE)
    if local == "playa":
        fecha = fecha - timedelta(days=1)

    mes_nombre = f"{_MESES[fecha.month - 1]} {fecha.year}"
    dia_nombre = f"{fecha.day}-{fecha.month}-{str(fecha.year)[-2:]}"

    return {
        "localFolder": CARPETA_POR_LOCAL.get(local, "voucher sin local"),
        "yearFolder": str(fecha.year),
        "monthFolder": mes_nombre,
        "dayFolder": dia_nombre,
    }


async def subir_voucher(
    imagen_bytes: bytes, mime_type: str, telefono: str, local: str
) -> bool:
    """
    Envía la imagen del comprobante al Apps Script (que la guarda en Drive),
    indicándole la carpeta del local y la fecha de cierre ya calculadas.

    Returns:
        True si el Apps Script confirmó que la guardó.
    """
    if not VOUCHER_WEBHOOK_URL:
        logger.warning("VOUCHER_WEBHOOK_URL no configurado en .env — no se sube el comprobante")
        return False
    if not imagen_bytes:
        return False

    carpetas = carpetas_destino(local)
    payload = {
        "from": telefono,
        "mediaBase64": base64.b64encode(imagen_bytes).decode(),
        "mimeType": mime_type or "image/jpeg",
        "local": local,
        **carpetas,  # localFolder, yearFolder, monthFolder, dayFolder
    }
    try:
        # follow_redirects=True: Apps Script /exec responde con un redirect 302
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.post(VOUCHER_WEBHOOK_URL, json=payload)
            if r.status_code != 200:
                logger.error(f"Voucher: Apps Script respondió {r.status_code} — {r.text}")
                return False
            data = r.json()
            if not data.get("ok"):
                logger.error(f"Voucher: Apps Script no ok — {data}")
                return False
            logger.info(
                f"Voucher {local} guardado en Drive "
                f"({carpetas['localFolder']}/{carpetas['dayFolder']}): {data.get('fileUrl')}"
            )
            return True
    except Exception as e:
        logger.error(f"Voucher: error enviando a Apps Script — {e}")
        return False
