# agent/tuya_client.py — cliente genérico de la API de Tuya, compartido por
# cualquier automatización física (portón, riego, lo que venga después).
#
# Por qué es un módulo aparte: el portón y el riego son proyectos distintos
# (uno de DiMango, otro de la parcela personal de Ricardo) pero hablan con
# el mismo Cloud Project de Tuya, con las mismas credenciales. Duplicar la
# firma HMAC en cada módulo significa arreglar el mismo bug dos veces si
# algo de la API de Tuya cambia.

import hashlib
import hmac
import json
import logging
import os
import time

import httpx

logger = logging.getLogger("agentkit")

TUYA_CLIENT_ID = os.getenv("TUYA_CLIENT_ID", "")
TUYA_CLIENT_SECRET = os.getenv("TUYA_CLIENT_SECRET", "")
# Data center de Tuya -- EEUU por defecto; si el proyecto está en otra
# región (China, Europa, India) hay que cambiar esto. Se ve en el panel
# de iot.tuya.com, en el detalle del Cloud Project.
TUYA_REGION_URL = os.getenv("TUYA_REGION_URL", "https://openapi.tuyaus.com")


def _firmar(mensaje: str) -> str:
    return hmac.new(
        TUYA_CLIENT_SECRET.encode("utf-8"), mensaje.encode("utf-8"), hashlib.sha256
    ).hexdigest().upper()


async def _obtener_token(cliente: httpx.AsyncClient) -> str | None:
    t = str(int(time.time() * 1000))
    metodo, ruta = "GET", "/v1.0/token?grant_type=1"
    sha_cuerpo = hashlib.sha256(b"").hexdigest()
    string_a_firmar = f"{metodo}\n{sha_cuerpo}\n\n{ruta}"
    firma = _firmar(TUYA_CLIENT_ID + t + string_a_firmar)

    headers = {
        "client_id": TUYA_CLIENT_ID, "sign": firma, "t": t,
        "sign_method": "HMAC-SHA256",
    }
    try:
        r = await cliente.get(TUYA_REGION_URL + ruta, headers=headers, timeout=15)
        datos = r.json()
        if not datos.get("success"):
            logger.error(f"[TUYA] Rechazó el token: {datos}")
            return None
        return datos["result"]["access_token"]
    except Exception as e:
        logger.error(f"[TUYA] Error pidiendo token: {e}")
        return None


async def _enviar_comando(cliente: httpx.AsyncClient, access_token: str, device_id: str, code: str, valor) -> bool:
    t = str(int(time.time() * 1000))
    metodo, ruta = "POST", f"/v1.0/iot-03/devices/{device_id}/commands"
    cuerpo = {"commands": [{"code": code, "value": valor}]}
    cuerpo_json = json.dumps(cuerpo, separators=(",", ":"))
    sha_cuerpo = hashlib.sha256(cuerpo_json.encode("utf-8")).hexdigest()
    string_a_firmar = f"{metodo}\n{sha_cuerpo}\n\n{ruta}"
    firma = _firmar(TUYA_CLIENT_ID + access_token + t + string_a_firmar)

    headers = {
        "client_id": TUYA_CLIENT_ID, "access_token": access_token,
        "sign": firma, "t": t, "sign_method": "HMAC-SHA256",
        "Content-Type": "application/json",
    }
    try:
        r = await cliente.post(TUYA_REGION_URL + ruta, headers=headers, content=cuerpo_json, timeout=15)
        datos = r.json()
        if not datos.get("success"):
            logger.error(f"[TUYA] Rechazó el comando ({device_id}): {datos}")
            return False
        return True
    except Exception as e:
        logger.error(f"[TUYA] Error mandando el comando ({device_id}): {e}")
        return False


async def enviar_comando(device_id: str, code: str, valor) -> tuple[bool, str]:
    """
    Manda un comando a un dispositivo Tuya. `code` es el nombre de la
    función del dispositivo (ej. "switch_1"), `valor` su nuevo estado
    (True/False para on/off, o el valor que corresponda).
    """
    if not (TUYA_CLIENT_ID and TUYA_CLIENT_SECRET):
        return False, "Falta configurar TUYA_CLIENT_ID/SECRET en el .env"
    if not device_id:
        return False, "Falta el device_id"
    async with httpx.AsyncClient() as cliente:
        token = await _obtener_token(cliente)
        if not token:
            return False, "No se pudo autenticar con Tuya"
        ok = await _enviar_comando(cliente, token, device_id, code, valor)
        return (True, "") if ok else (False, "Tuya rechazó el comando")
