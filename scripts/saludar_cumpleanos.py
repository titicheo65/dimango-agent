# scripts/saludar_cumpleanos.py — Saludo diario de cumpleaños DiMango Club
# Generado por AgentKit

"""
Envía un saludo de cumpleaños por WhatsApp a los clientes de DiMango Club
que cumplen años HOY.

Cómo funciona:
  1. Consulta la función backend de DiMango Club (cumpleanosDelDia) que
     devuelve los clientes que cumplen años hoy (día/mes en horario de Chile).
  2. Por cada cliente, envía la plantilla aprobada "cumpleanos_dimango" con
     su primer nombre como variable {{1}}.
  3. Guarda un registro para NO saludar dos veces al mismo número el mismo día.

Se ejecuta una vez al día por una Tarea Programada de Windows.
Usa las credenciales de Meta que ya están en el .env del servidor.
"""

import os
import re
import sys
import json
import logging
from datetime import datetime

import httpx
from dotenv import load_dotenv

# ── Configuración ─────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Función backend de DiMango Club que devuelve los cumpleaños del día
FUNCION_URL = "https://dimangoclub.base44.app/functions/cumpleanosDelDia"
FUNCION_TOKEN = "dmg_cumple_9x7Kp2vL"  # mismo token que valida la función

# Plantilla de WhatsApp aprobada (categoría MARKETING)
PLANTILLA = "cumpleanos_dimango"
IDIOMA = "es"

# Credenciales de Meta (desde el .env del servidor)
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
API_VERSION = "v21.0"

# Archivos de registro
LOG_FILE = os.path.join(BASE_DIR, "cumpleanos.log")
ENVIADOS_FILE = os.path.join(BASE_DIR, "cumpleanos_enviados.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("cumpleanos")


# ── Utilidades ────────────────────────────────────────────────────────────
def normalizar_telefono(telefono: str) -> str:
    """Deja solo dígitos y asegura el código de país de Chile (56)."""
    digitos = re.sub(r"\D", "", telefono or "")
    if not digitos:
        return ""
    # Número local de 9 dígitos que parte con 9 → anteponer 56
    if len(digitos) == 9 and digitos.startswith("9"):
        digitos = "56" + digitos
    return digitos


def primer_nombre(nombre_completo: str) -> str:
    """Extrae el primer nombre, con mayúscula inicial."""
    partes = (nombre_completo or "").strip().split()
    return partes[0].capitalize() if partes else "amigo"


def cargar_enviados_hoy(hoy: str) -> set:
    """Números ya saludados hoy (para no repetir si el script corre 2 veces)."""
    if not os.path.exists(ENVIADOS_FILE):
        return set()
    try:
        with open(ENVIADOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get(hoy, []))
    except (json.JSONDecodeError, IOError):
        return set()


def marcar_enviado(hoy: str, telefono: str):
    """Registra que un número ya fue saludado hoy."""
    data = {}
    if os.path.exists(ENVIADOS_FILE):
        try:
            with open(ENVIADOS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            data = {}
    data.setdefault(hoy, [])
    if telefono not in data[hoy]:
        data[hoy].append(telefono)
    # Conservar solo los últimos 15 días para no crecer sin límite
    if len(data) > 15:
        for k in sorted(data.keys())[:-15]:
            data.pop(k, None)
    with open(ENVIADOS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Envío ─────────────────────────────────────────────────────────────────
def obtener_cumpleaneros() -> list[dict]:
    """Llama a la función backend y devuelve la lista de cumpleañeros de hoy."""
    r = httpx.post(FUNCION_URL, json={"token": FUNCION_TOKEN}, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"Función devolvió error: {data['error']}")
    fecha = data.get("fecha", {})
    logger.info(f"Cumpleaños de hoy ({fecha.get('dia')}/{fecha.get('mes')}): {data.get('total', 0)}")
    return data.get("cumpleaneros", [])


def enviar_plantilla(telefono: str, nombre: str) -> bool:
    """Envía la plantilla de cumpleaños a un número vía Meta Cloud API."""
    url = f"https://graph.facebook.com/{API_VERSION}/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "template",
        "template": {
            "name": PLANTILLA,
            "language": {"code": IDIOMA},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": nombre}]}
            ],
        },
    }
    r = httpx.post(url, json=payload, headers=headers, timeout=30.0)
    if r.status_code != 200:
        logger.error(f"Error Meta ({telefono}): {r.status_code} — {r.text}")
        return False
    return True


def main():
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        logger.error("Faltan META_ACCESS_TOKEN o META_PHONE_NUMBER_ID en el .env")
        sys.exit(1)

    hoy = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"=== Saludo de cumpleaños — {hoy} ===")

    try:
        cumpleaneros = obtener_cumpleaneros()
    except Exception as e:
        logger.error(f"No se pudo consultar los cumpleaños: {e}")
        sys.exit(1)

    if not cumpleaneros:
        logger.info("Nadie cumple años hoy. Nada que enviar.")
        return

    ya_enviados = cargar_enviados_hoy(hoy)
    enviados = 0
    for c in cumpleaneros:
        telefono = normalizar_telefono(c.get("phone", ""))
        nombre = primer_nombre(c.get("full_name", ""))
        if not telefono:
            logger.warning(f"Cliente sin teléfono válido: {c.get('full_name')}")
            continue
        if telefono in ya_enviados:
            logger.info(f"Ya saludado hoy, se omite: {nombre} ({telefono})")
            continue
        if enviar_plantilla(telefono, nombre):
            marcar_enviado(hoy, telefono)
            enviados += 1
            logger.info(f"Saludo enviado a {nombre} ({telefono})")

    logger.info(f"=== Listo. Saludos enviados: {enviados}/{len(cumpleaneros)} ===")


if __name__ == "__main__":
    main()
