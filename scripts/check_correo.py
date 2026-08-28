#!/usr/bin/env python3
"""scripts/check_correo.py - avisa por Telegram cuando llega correo nuevo.

Por que existe: Ricardo pidio que Maximus le avise cuando llegue correo
nuevo a titicheo@gmail.com y presupuestodimango@gmail.com - solo leer,
solo avisar quien manda y de que, nada de contenido ni de responder
(ver P-013 en la memoria de Maximus).

Como funciona: IMAP de solo lectura contra Gmail, con una Contraseña de
Aplicacion por cuenta (no la clave normal de la cuenta - se genera en la
configuracion de seguridad de Google, requiere verificacion en 2 pasos
activada de antemano). BODY.PEEK en vez de FETCH normal, y select() con
readonly=True: no se marca nada como leido, no se modifica la bandeja.

Lleva su propio registro de que UID ya avisó, un archivo de texto por
cuenta en estado_correo/. La primera corrida por cuenta NO avisa nada -
solo establece la linea base, para no volcar todo el historico de la
bandeja como si fuera nuevo.

Se corre solo, cada 5 minutos, via una Tarea Programada de Windows -
mismo patron que scripts/monitor-maximus.ps1 (S-023).
"""

import email
import imaplib
import os
import pathlib
from email.header import decode_header

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
ESTADO_DIR = BASE_DIR / "estado_correo"
ESTADO_DIR.mkdir(exist_ok=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_OWNER_CHAT_IDS", "").split(",")[0].strip()

IMAP_HOST = "imap.gmail.com"

CUENTAS = [
    {
        "nombre": "titicheo@gmail.com",
        "user": os.getenv("CORREO_TITICHEO_USER", ""),
        "password": os.getenv("CORREO_TITICHEO_APP_PASSWORD", ""),
    },
    {
        "nombre": "presupuestodimango@gmail.com",
        "user": os.getenv("CORREO_PRESUPUESTO_USER", ""),
        "password": os.getenv("CORREO_PRESUPUESTO_APP_PASSWORD", ""),
    },
]


def avisar(texto: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[correo] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_OWNER_CHAT_IDS, no se puede avisar")
        return
    try:
        with httpx.Client(timeout=10) as client:
            client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": texto},
            )
    except Exception as e:
        print(f"[correo] Error avisando por Telegram: {e}")


def decodificar(valor: str) -> str:
    if not valor:
        return "(sin remitente)"
    partes = decode_header(valor)
    resultado = ""
    for texto, codificacion in partes:
        if isinstance(texto, bytes):
            resultado += texto.decode(codificacion or "utf-8", errors="replace")
        else:
            resultado += texto
    return resultado


def archivo_estado(nombre_cuenta: str) -> pathlib.Path:
    seguro = nombre_cuenta.replace("@", "_at_").replace(".", "_")
    return ESTADO_DIR / f"ultimo_uid_{seguro}.txt"


def revisar_cuenta(cuenta: dict) -> None:
    nombre = cuenta["nombre"]
    if not cuenta["user"] or not cuenta["password"]:
        print(f"[correo] {nombre}: sin credenciales configuradas, se omite")
        return

    archivo = archivo_estado(nombre)
    primera_vez = not archivo.exists()
    ultimo_uid = 0 if primera_vez else int(archivo.read_text().strip() or 0)

    conexion = None
    try:
        conexion = imaplib.IMAP4_SSL(IMAP_HOST)
        conexion.login(cuenta["user"], cuenta["password"])
        conexion.select("INBOX", readonly=True)

        estado, datos = conexion.uid("search", None, "ALL")
        if estado != "OK":
            print(f"[correo] {nombre}: fallo la busqueda IMAP")
            return

        uids = [int(u) for u in datos[0].split()] if datos[0] else []

        if primera_vez:
            if uids:
                archivo.write_text(str(max(uids)))
            print(f"[correo] {nombre}: primera corrida, linea base en UID {max(uids) if uids else 0}, no se avisa nada")
            return

        nuevos = [u for u in uids if u > ultimo_uid]

        for uid in nuevos:
            estado_fetch, datos_msg = conexion.uid(
                "fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"
            )
            if estado_fetch != "OK" or not datos_msg or not datos_msg[0]:
                continue
            msg = email.message_from_bytes(datos_msg[0][1])
            remitente = decodificar(msg.get("From", ""))
            asunto = decodificar(msg.get("Subject", "(sin asunto)"))
            avisar(f"Correo nuevo en {nombre}\nDe: {remitente}\nAsunto: {asunto}")

        if uids:
            archivo.write_text(str(max(uids)))

        print(f"[correo] {nombre}: {len(nuevos)} nuevo(s)")
    except Exception as e:
        print(f"[correo] {nombre}: error - {e}")
    finally:
        if conexion is not None:
            try:
                conexion.logout()
            except Exception:
                pass


if __name__ == "__main__":
    for cuenta in CUENTAS:
        revisar_cuenta(cuenta)
