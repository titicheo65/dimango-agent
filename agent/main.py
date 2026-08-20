# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit

"""
Servidor principal del agente de WhatsApp.
Funciona con cualquier proveedor (Meta, Twilio) gracias a la capa de providers.
"""

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, esta_pausada, guardar_nombre
from agent.providers import obtener_proveedor
from agent.admin import router as admin_router
from agent.colacion import (
    buscar_empleado,
    manejar_mensaje_colacion,
    loop_recordatorios,
    loop_recordatorios_diarios,
    migrar_esquema,
)
from agent.colacion_web import router as colacion_router
from agent.maximus import es_maximus, responder as responder_maximus
from agent import telegram_maximus as tg

load_dotenv()

# Configuración de logging según entorno
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

# Proveedor de WhatsApp (se configura en .env con WHATSAPP_PROVIDER)
proveedor = obtener_proveedor()

# Proveedor de Instagram (opcional): solo se activa si hay IG_ACCESS_TOKEN en .env
proveedor_instagram = None
if os.getenv("IG_ACCESS_TOKEN"):
    from agent.providers.instagram import ProveedorInstagram
    proveedor_instagram = ProveedorInstagram()

# Proveedor de Messenger (opcional): solo se activa si hay MESSENGER_PAGE_TOKEN en .env
proveedor_messenger = None
if os.getenv("MESSENGER_PAGE_TOKEN"):
    from agent.providers.messenger import ProveedorMessenger
    proveedor_messenger = ProveedorMessenger()

PORT = int(os.getenv("PORT", 8000))

# Clave secreta para que DimangoToGo pueda llamar a /api/pedido de forma segura
API_SECRET = os.getenv("API_SECRET", "")

# Mapeo de área de la app → nombre de la plantilla aprobada en Meta
PLANTILLAS_POR_AREA = {
    "tortas": "pedido_tortas",
    "playa": "retiro_playa",
    "mall": "retiro_mall",
    "reserva": "reserva_confirmada",
}


def seleccionar_proveedor(body: dict):
    """Elige el canal correcto según el tipo de webhook (WhatsApp, Instagram o Messenger)."""
    objeto = body.get("object")
    if proveedor_instagram is not None and objeto == "instagram":
        return proveedor_instagram
    if proveedor_messenger is not None and objeto == "page":
        return proveedor_messenger
    return proveedor


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos al arrancar el servidor."""
    await inicializar_db()
    await migrar_esquema()
    logger.info("Base de datos inicializada")
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    if proveedor_instagram is not None:
        logger.info(f"Proveedor de Instagram: {proveedor_instagram.__class__.__name__}")
    if proveedor_messenger is not None:
        logger.info(f"Proveedor de Messenger: {proveedor_messenger.__class__.__name__}")
    # Loop en segundo plano que avisa cuando alguien se pasa de su colación
    tarea_colacion = asyncio.create_task(loop_recordatorios(proveedor))
    # Loop que recuerda tomar colación a los horarios fijos por local (control 100%)
    tarea_recordatorio_diario = asyncio.create_task(loop_recordatorios_diarios(proveedor))
    yield
    tarea_colacion.cancel()
    tarea_recordatorio_diario.cancel()


app = FastAPI(
    title="AgentKit — WhatsApp AI Agent",
    version="1.0.0",
    lifespan=lifespan
)

# CORS: permite que la página /Colacion de la app Base44 consuma el API público
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dimangotogo.base44.app"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Panel de administración web (/admin)
app.include_router(admin_router)

# Pantalla de control de colación del personal (/colacion)
app.include_router(colacion_router)


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "service": "agentkit"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para otros)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp via el proveedor configurado.
    Procesa el mensaje, genera respuesta con Claude y la envía de vuelta.
    """
    try:
        # Detectar el canal (WhatsApp o Instagram) según el payload
        try:
            body = await request.json()
        except Exception:
            body = {}
        canal = seleccionar_proveedor(body)

        # Parsear webhook — el proveedor normaliza el formato
        mensajes = await canal.parsear_webhook(request)

        for msg in mensajes:
            # Ignorar mensajes propios o vacíos
            if msg.es_propio or not msg.texto:
                continue

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto}")

            # ¿Es Ricardo? → Maximus, su gerente virtual. Va PRIMERO: antes de
            # colación y antes de atención al cliente. Si MAXIMUS_OWNER_PHONES
            # no está configurado, es_maximus() siempre es False y este bloque
            # no existe para nadie.
            if es_maximus(msg.telefono):
                historial = await obtener_historial(msg.telefono)
                respuesta = await responder_maximus(msg.texto, historial)
                await guardar_mensaje(msg.telefono, "user", msg.texto)
                await guardar_mensaje(msg.telefono, "assistant", respuesta)
                await canal.enviar_mensaje(msg.telefono, respuesta)
                logger.info(f"[MAXIMUS] {msg.telefono}: {msg.texto[:80]}")
                continue

            # ¿Es un empleado registrado? → control de colación, no atención al cliente
            empleado = await buscar_empleado(msg.telefono)
            if empleado:
                respuesta = await manejar_mensaje_colacion(empleado, msg.texto)
                if respuesta:
                    await canal.enviar_mensaje(msg.telefono, respuesta)
                logger.info(f"[COLACIÓN] {empleado.nombre}: {msg.texto}")
                continue

            # Guardar el nombre de perfil del cliente (si el proveedor lo envió)
            if msg.nombre:
                await guardar_nombre(msg.telefono, msg.nombre)

            # Si un humano tomó control de este chat (desde el panel),
            # guardamos el mensaje pero NO respondemos automáticamente.
            if await esta_pausada(msg.telefono):
                await guardar_mensaje(msg.telefono, "user", msg.texto)
                logger.info(f"[PAUSADO] Mensaje de {msg.telefono} guardado para atención humana")
                continue

            # Obtener historial ANTES de guardar el mensaje actual
            # (brain.py agrega el mensaje actual, evitando duplicados)
            historial = await obtener_historial(msg.telefono)

            # Generar respuesta con Claude
            respuesta = await generar_respuesta(msg.texto, historial)

            # Guardar mensaje del usuario Y respuesta del agente en memoria
            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            # Enviar respuesta por el mismo canal de donde llegó el mensaje
            await canal.enviar_mensaje(msg.telefono, respuesta)

            logger.info(f"Respuesta a {msg.telefono}: {respuesta}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Canal privado de Maximus por Telegram.

    Aislado a propósito: no comparte nada con /webhook (WhatsApp). Si este
    endpoint falla, la atención a clientes sigue intacta.
    """
    if not tg.configurado():
        return {"status": "telegram no configurado"}

    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    parseado = await tg.parsear_update(body)
    if not parseado:
        return {"status": "ok"}

    chat_id, texto = parseado

    # Modo setup: sin dueños configurados, el bot solo dice quién eres.
    # Nunca entrega memoria a un desconocido.
    if not tg.OWNER_CHAT_IDS:
        await tg.enviar_mensaje(chat_id, tg.MENSAJE_SETUP.format(chat_id=chat_id))
        logger.warning(f"[TELEGRAM] Modo setup — chat_id sin autorizar: {chat_id}")
        return {"status": "setup"}

    if not tg.es_owner(chat_id):
        await tg.enviar_mensaje(chat_id, tg.MENSAJE_NO_AUTORIZADO)
        logger.warning(f"[TELEGRAM] Acceso denegado a chat_id {chat_id}")
        return {"status": "denegado"}

    clave = f"tg:{chat_id}"
    historial = await obtener_historial(clave)
    respuesta = await responder_maximus(texto, historial)
    await guardar_mensaje(clave, "user", texto)
    await guardar_mensaje(clave, "assistant", respuesta)
    await tg.enviar_mensaje(chat_id, respuesta)

    logger.info(f"[MAXIMUS/TG] {chat_id}: {texto[:80]}")
    return {"status": "ok"}


@app.post("/api/pedido")
async def api_pedido(request: Request):
    """
    Recibe una confirmación de pedido desde DimangoToGo y envía la plantilla
    de WhatsApp correspondiente al área.

    Body JSON esperado:
        {
            "secret": "...",       # debe coincidir con API_SECRET
            "telefono": "569...",  # número del cliente (con código país, sin +)
            "area": "tortas",      # tortas | playa | mall | reserva
            "nombre": "María"      # nombre del cliente (variable {{1}})
        }
    """
    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    # Validar la clave secreta
    if not API_SECRET or datos.get("secret") != API_SECRET:
        raise HTTPException(status_code=401, detail="No autorizado")

    telefono = (datos.get("telefono") or "").strip().lstrip("+")
    area = (datos.get("area") or "").strip().lower()
    nombre = (datos.get("nombre") or "").strip() or "cliente"

    if not telefono:
        raise HTTPException(status_code=400, detail="Falta el teléfono")

    plantilla = PLANTILLAS_POR_AREA.get(area)
    if not plantilla:
        raise HTTPException(
            status_code=400,
            detail=f"Área no válida: {area}. Usa: {', '.join(PLANTILLAS_POR_AREA)}"
        )

    # Enviar siempre por WhatsApp (Meta) — los pedidos vienen de la app, no de IG/Messenger
    ok = await proveedor.enviar_plantilla(telefono, plantilla, [nombre])
    if not ok:
        raise HTTPException(status_code=502, detail="No se pudo enviar la plantilla")

    # Guardar en el historial para que quede registro en el panel
    await guardar_mensaje(telefono, "assistant", f"[Confirmación {area}] Hola {nombre}")
    if nombre and nombre != "cliente":
        await guardar_nombre(telefono, nombre)

    logger.info(f"Plantilla '{plantilla}' enviada a {telefono} ({nombre})")
    return {"status": "ok", "plantilla": plantilla, "telefono": telefono}
