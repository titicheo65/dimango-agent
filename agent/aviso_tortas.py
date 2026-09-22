# agent/aviso_tortas.py — avisa a Dimango Control cuando una conversacion compromete algo
#
# Por que existe (P-023): en septiembre de 2026 el agente comprometio una torta
# para el mismo dia. No quedo registrada en ningun sistema y nadie en el negocio
# se entero hasta que el cliente llego a buscarla. El prompt ya no promete nada
# — pero sin esto, la conversacion sigue muriendo en el chat y el negocio sigue
# sin enterarse de que alguien queria una torta.
#
# Por que asi y no como herramienta del agente: brain.py no tiene tool-use, y
# agregarselo obliga a tocar el cerebro que le habla a TODOS los clientes. Esto
# es un vigia al costado: mira el mensaje, avisa, y no puede alterar la
# respuesta ni romper la conversacion.
#
# Falla en silencio a proposito: si Telegram esta caido o falta la variable,
# el cliente igual recibe su respuesta. Un aviso perdido es malo; una atencion
# caida por un aviso es peor.

import os
import time
import logging

import httpx

logger = logging.getLogger("agentkit")

# Grupo "Dimango Control" (definido por Ricardo el 22-sep-2026). Sin valor por
# defecto: si no esta configurada, no se avisa y queda dicho en el log. Un
# default que funciona es como se filtran credenciales (H-019).
CHAT_CONTROL = os.getenv("TELEGRAM_CHAT_CONTROL", "").strip()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Palabras que delatan el tema. Van sin tilde y en minuscula; el texto entrante
# se normaliza antes de comparar, para que "TORTA" y "tortá" tambien entren.
CLAVES = (
    "torta", "tortas", "cheesecake", "queque",
    "cumpleanos", "mil hojas", "tres leches", "encargo",
)

# Una conversacion sobre tortas son varios mensajes. Sin esto, cada uno
# dispararia su propio aviso y el grupo se vuelve ruido que nadie lee.
VENTANA_SILENCIO = 6 * 60 * 60  # 6 horas por telefono
_ultimo_aviso: dict[str, float] = {}


def _normalizar(texto: str) -> str:
    t = (texto or "").lower()
    for con, sin in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        t = t.replace(con, sin)
    return t


def menciona_torta(texto: str) -> bool:
    t = _normalizar(texto)
    return any(clave in t for clave in CLAVES)


# El mismo agujero existia para el retiro en el Mall: el prompt le pedia tomar
# datos y prometer que el equipo confirmaria la preparacion. El Mall SI tiene
# link (/BienvenidaRetiroLocalMall) y el prompt ahora lo entrega — pero el
# cliente que no lo completa igual se pierde, y eso nadie lo ve. Este aviso es
# para enterarse de esa demanda. Hacen falta DOS senales juntas (el local y la
# intencion de encargar): "mall" solo aparece en cualquier consulta de horarios.
CLAVES_MALL = ("mall", "diego portales", "plaza arica", "sucursal")
# Raices, no palabras completas: "guardan", "encargue" y "reservame" tienen que
# entrar igual que "guardar". Con la palabra entera se escapaban conjugaciones
# que un cliente usa todo el tiempo.
CLAVES_ENCARGO = (
    "pedid", "pedir", "encarg", "reserv", "retir",
    "guard", "dejar list", "llevar", "anotar", "aparta",
)


def menciona_retiro_mall(texto: str) -> bool:
    t = _normalizar(texto)
    return (any(c in t for c in CLAVES_MALL)
            and any(c in t for c in CLAVES_ENCARGO))


async def avisar_si_corresponde(telefono: str, mensaje: str, respuesta: str) -> bool:
    """
    Si el cliente hablo de tortas, manda el aviso a Dimango Control.
    Devuelve True solo si se envio. Nunca lanza excepcion.
    """
    try:
        if menciona_torta(mensaje):
            titulo = "🎂 Consulta de torta por WhatsApp"
            cola = ("⚠️ El agente NO confirma tortas. Si esto va en serio, "
                    "alguien tiene que contactar al cliente para confirmar "
                    "disponibilidad y cobrar el abono.")
        elif menciona_retiro_mall(mensaje):
            titulo = "🏬 Consulta de pedido para el Mall"
            cola = ("ℹ️ El agente le dio el link de retiro del Mall. Si no lo "
                    "completa, ese pedido no existe para nadie: revisar si "
                    "vale la pena contactarlo.")
        else:
            return False

        ahora = time.time()
        previo = _ultimo_aviso.get(telefono, 0)
        if ahora - previo < VENTANA_SILENCIO:
            return False

        if not BOT_TOKEN or not CHAT_CONTROL:
            logger.warning(
                "[AVISO] %s consulto algo que requiere aviso y no se pudo enviar: "
                "falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_CONTROL",
                telefono,
            )
            return False

        texto = (
            f"{titulo}\n\n"
            f"📱 Cliente: {telefono}\n"
            f"💬 Dijo: {mensaje[:400]}\n\n"
            f"🤖 El agente respondio: {respuesta[:400]}\n\n"
            f"{cola}"
        )

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_CONTROL, "text": texto},
            )
        if r.status_code != 200:
            logger.warning("[TORTAS] Telegram respondio %s: %s", r.status_code, r.text[:200])
            return False

        _ultimo_aviso[telefono] = ahora
        logger.info("[TORTAS] Aviso enviado a Dimango Control por %s", telefono)
        return True

    except Exception as e:
        # Nunca romper la atencion al cliente por un aviso.
        logger.warning("[TORTAS] No se pudo avisar: %s", e)
        return False
