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
from fastapi.responses import PlainTextResponse, JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial, esta_pausada, guardar_nombre,
    listar_conversaciones_privadas, obtener_conversacion_completa, es_privada,
)
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
from agent.voz import sintetizar as sintetizar_voz
from agent.alertas_venta import inicializar_alertas, loop_alertas_venta
from agent.notas_personales import inicializar_notas, loop_recordatorios_personales
from agent import checklist_operativo as checklist
from agent import telegram_checklist as tg_checklist
from agent import porton
from agent import riego
from agent.voz_control import procesar_mensaje_voz

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
    await inicializar_alertas()
    await inicializar_notas()
    from agent.delegaciones import inicializar_delegaciones
    await inicializar_delegaciones()
    from agent.equipo import inicializar_equipo
    await inicializar_equipo()
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
    # Loop que revisa las alertas de venta que Ricardo va creando por conversación
    tarea_alertas_venta = asyncio.create_task(loop_alertas_venta(proveedor))
    # Loop que avisa los recordatorios personales de Ricardo cuando vencen
    tarea_recordatorios_personales = asyncio.create_task(loop_recordatorios_personales(proveedor))
    # Checklist operativo por Telegram: dispara las tareas según su horario
    tarea_checklist_envios = asyncio.create_task(checklist.loop_envios_checklist(tg_checklist))
    # Checklist operativo: reenvía a los 10 min, escala al supervisor a los 20
    tarea_checklist_escalamiento = asyncio.create_task(checklist.loop_escalamiento_checklist(tg_checklist))
    yield
    tarea_colacion.cancel()
    tarea_recordatorio_diario.cancel()
    tarea_alertas_venta.cancel()
    tarea_recordatorios_personales.cancel()
    tarea_checklist_envios.cancel()
    tarea_checklist_escalamiento.cancel()


# En producción la documentación de la API queda cerrada: el servicio está
# expuesto a internet por ngrok y /docs enumera todos los endpoints sin pedir
# clave. Para verla en desarrollo: ENVIRONMENT=development
_dev = ENVIRONMENT == "development"

app = FastAPI(
    title="AgentKit — WhatsApp AI Agent",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _dev else None,
    redoc_url="/redoc" if _dev else None,
    openapi_url="/openapi.json" if _dev else None,
)

# CORS: permite que la página /Colacion de la app Base44 consuma el API público
app.add_middleware(
    CORSMiddleware,
    # "null" es el Origin que manda un archivo abierto con file:// — es el
    # cerebro visual de Maximus corriendo en el Mac de Ricardo. El endpoint
    # /maximus/chat igual exige token: CORS no es la protección.
    # localhost:8899 es el mismo cerebro servido por http en vez de file://.
    # Hace falta porque en file:// Chrome no recuerda el permiso del micrófono
    # y lo pide en cada turno: no se puede conversar de corrido.
    allow_origins=["https://dimangotogo.base44.app", "null",
                   "http://localhost:8899", "http://127.0.0.1:8899"],
    allow_methods=["GET", "POST", "OPTIONS"],
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

            # ¿Es la palabra clave del portón? Chequeo determinístico, va
            # ANTES que todo lo demás -- incluido Maximus. Es una puerta
            # física: tiene que interceptar el mensaje de cualquiera, sin
            # que la IA llegue a verlo. Ver agent/porton.py para el porqué.
            respuesta_porton = await porton.procesar_mensaje_porton(msg.telefono, msg.texto)
            if respuesta_porton is not None:
                await canal.enviar_mensaje(msg.telefono, respuesta_porton)
                continue

            # ¿Es la palabra clave del riego de la parcela? Mismo criterio
            # que el portón -- determinístico, antes que Maximus.
            respuesta_riego = await riego.procesar_mensaje_riego(msg.telefono, msg.texto)
            if respuesta_riego is not None:
                await canal.enviar_mensaje(msg.telefono, respuesta_riego)
                continue

            # ¿Es Ricardo? → Maximus, su gerente virtual. Antes de colación
            # y de atención al cliente. Si MAXIMUS_OWNER_PHONES no está
            # configurado, es_maximus() siempre es False y este bloque no
            # existe para nadie.
            if es_maximus(msg.telefono):
                # "voz clonada on/off" -- determinístico, antes de gastar
                # un turno de Claude en algo que no necesita interpretación.
                respuesta_voz = await procesar_mensaje_voz(msg.texto)
                if respuesta_voz is not None:
                    await guardar_mensaje(msg.telefono, "user", msg.texto)
                    await guardar_mensaje(msg.telefono, "assistant", respuesta_voz)
                    await canal.enviar_mensaje(msg.telefono, respuesta_voz)
                    continue

                historial = await obtener_historial(msg.telefono)
                respuesta = await responder_maximus(
                    msg.texto, historial,
                    imagen_b64=msg.imagen_b64, imagen_mime=msg.imagen_mime,
                )
                await guardar_mensaje(msg.telefono, "user", msg.texto)
                await guardar_mensaje(msg.telefono, "assistant", respuesta)
                await canal.enviar_mensaje(msg.telefono, respuesta)

                # Si Ricardo mandó una nota de voz, se le contesta también en voz.
                # El texto ya salió: si la síntesis falla, no se pierde nada.
                if msg.fue_audio and hasattr(canal, "enviar_audio"):
                    audio = await sintetizar_voz(respuesta)
                    if audio:
                        await canal.enviar_audio(msg.telefono, audio)

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


@app.post("/maximus/chat")
async def maximus_chat(request: Request):
    """
    Chat con Maximus desde el cerebro visual.

    Protegido con MAXIMUS_CHAT_TOKEN. Devuelve la respuesta y los ids de las
    notas que se usaron, para que el grafo pueda iluminarlas.
    """
    token_ok = os.getenv("MAXIMUS_CHAT_TOKEN", "")
    if not token_ok:
        raise HTTPException(status_code=503, detail="Chat no habilitado")

    if request.headers.get("x-maximus-token", "") != token_ok:
        raise HTTPException(status_code=401, detail="No autorizado")

    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    mensaje = (datos.get("mensaje") or "").strip()
    if not mensaje:
        raise HTTPException(status_code=400, detail="Falta el mensaje")

    from agent import eventos
    await eventos.publicar("escuchando", mensaje=mensaje[:200], canal="web")

    sesion = f"web:{datos.get('sesion', 'cerebro')}"
    historial = await obtener_historial(sesion)

    notas = []
    try:
        from agent.maximus import _cerebro_atomico
        c = _cerebro_atomico()
        if c:
            _, _, notas = c.contexto(mensaje)
    except Exception:
        pass

    respuesta = await responder_maximus(mensaje, historial)
    await guardar_mensaje(sesion, "user", mensaje)
    await guardar_mensaje(sesion, "assistant", respuesta)

    # La voz viaja en la misma respuesta: una sola vuelta al servidor.
    # Si la síntesis falla, el texto igual llega — la voz nunca hace perder
    # una respuesta.
    audio_b64 = ""
    if datos.get("voz"):
        try:
            import base64
            audio = await sintetizar_voz(respuesta)
            if audio:
                audio_b64 = base64.b64encode(audio).decode("ascii")
        except Exception as e:
            logger.error(f"[MAXIMUS/WEB] Falló la voz: {e}")

    logger.info(f"[MAXIMUS/WEB] {mensaje[:70]}")
    return {"respuesta": respuesta, "notas": notas, "audio": audio_b64}


@app.get("/maximus/estado-locales")
async def maximus_estado_locales(request: Request):
    """
    Ventas de hoy y alertas de stock por local, para el panel del cerebro.

    El cerebro (HTML estático, corre en el navegador) nunca ve el secreto
    de DiMangoToGo — ese secreto vive solo acá, en el servidor. El cerebro
    solo tiene el token de chat, que ya usa para /maximus/chat.
    """
    token_ok = os.getenv("MAXIMUS_CHAT_TOKEN", "")
    if not token_ok or request.headers.get("x-maximus-token", "") != token_ok:
        raise HTTPException(status_code=401, detail="No autorizado")

    from agent.maximus import DIMANGOTOGO_URL, DIMANGOTOGO_SECRET
    if not DIMANGOTOGO_SECRET:
        raise HTTPException(status_code=503, detail="DiMangoToGo no configurado")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(DIMANGOTOGO_URL, json={}, headers={"x-maximus-secret": DIMANGOTOGO_SECRET})
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"DiMangoToGo respondió {r.status_code}")
        d = r.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar con DiMangoToGo: {e}")

    por_local = d.get("resumen", {}).get("por_local", {})
    stock = d.get("stock", [])

    def resumen(local: str) -> dict:
        base = por_local.get(local, {"monto": 0, "ventas": 0})
        bajo_minimo = sum(
            1 for s in stock
            if s.get(local) and s[local]["cantidad"] is not None and s[local]["minimo"] is not None
            and s[local]["cantidad"] < s[local]["minimo"]
        )
        return {"ventas_hoy": base["monto"], "num_ventas": base["ventas"], "stock_bajo_minimo": bajo_minimo}

    # sin no-store, el navegador puede repetir para siempre un 404 viejo de
    # antes del despliegue — pasó en producción el 25-ago, no es teórico.
    return JSONResponse(
        {"playa": resumen("playa"), "mall": resumen("mall")},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/maximus/ver")
async def maximus_ver(request: Request):
    """
    Maximus mira una imagen: una pantalla, una foto, un documento.

    Sirve para leer DiMangoToGo o DiMangoWorking sin integrar nada: se le
    muestra la pantalla y saca los números. La imagen NO se guarda en disco.
    """
    token_ok = os.getenv("MAXIMUS_CHAT_TOKEN", "")
    if not token_ok:
        raise HTTPException(status_code=503, detail="Chat no habilitado")
    if request.headers.get("x-maximus-token", "") != token_ok:
        raise HTTPException(status_code=401, detail="No autorizado")

    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    imagen = datos.get("imagen") or ""
    if "," in imagen:                      # viene como data:image/png;base64,....
        imagen = imagen.split(",", 1)[1]
    if not imagen:
        raise HTTPException(status_code=400, detail="Falta la imagen")

    mime = datos.get("mime", "image/png")
    pregunta = (datos.get("pregunta") or "").strip() or \
        "¿Qué estoy viendo? Si hay números, léelos y dime qué significan para el negocio."

    from agent.maximus import construir_prompt_atomico, construir_system_prompt
    atomico = construir_prompt_atomico(pregunta)
    if atomico:
        fija, variable = atomico
        bloques = [
            {"type": "text", "text": fija, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": variable},
        ]
    else:
        bloques = [{"type": "text", "text": construir_system_prompt(),
                    "cache_control": {"type": "ephemeral"}}]

    from agent.maximus import client as cliente_maximus, MODELO, MODELO_FALLBACK

    # Las instrucciones van ANTES de la imagen: puestas después, el modelo ya
    # "leyó" los montos como dólares y la corrección llegaba tarde.
    INSTRUCCION_MONEDA = (
        "Estás mirando la pantalla de un negocio en CHILE. Antes de leer nada:\n\n"
        "1. TODO monto está en PESOS CHILENOS (CLP). No existen dólares en estas "
        "pantallas. La palabra 'dólar' o 'USD' NO debe aparecer en tu respuesta.\n"
        "2. Formato chileno: el PUNTO separa MILES, la COMA separa DECIMALES.\n"
        "   $40.464.040  =  cuarenta millones cuatrocientos sesenta y cuatro mil "
        "cuarenta pesos.\n"
        "   $1.073,94    =  mil setenta y tres pesos con noventa y cuatro.\n"
        "   $159.919.975 =  ciento cincuenta y nueve millones novecientos "
        "diecinueve mil novecientos setenta y cinco pesos.\n"
        "3. Al citar un monto escribe siempre la palabra 'pesos' o el sufijo CLP. "
        "Nunca el símbolo $ solo.\n"
        "4. Para referencia: la venta mensual de este negocio ronda los 160 "
        "millones de pesos, y un almuerzo cuesta entre 8.000 y 20.000 pesos. Si "
        "un número que leíste no encaja con ese orden de magnitud, lo "
        "interpretaste mal.\n\n"
    )

    contenido = [
        {"type": "text", "text": INSTRUCCION_MONEDA},
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": imagen}},
        {"type": "text", "text": pregunta + "\n\nLee los números tal como aparecen, "
            "en pesos chilenos. Si algo no se ve bien, dilo en vez de adivinarlo."},
    ]

    for modelo in (MODELO, MODELO_FALLBACK):
        try:
            r = await cliente_maximus.messages.create(
                model=modelo, max_tokens=1200, system=bloques,
                messages=[{"role": "user", "content": contenido}],
            )
            texto = r.content[0].text
            logger.info(f"[MAXIMUS/VER] {modelo} — {len(imagen)} bytes de imagen")
            return {"respuesta": texto}
        except Exception as e:
            logger.error(f"[MAXIMUS/VER] Falló con {modelo}: {e}")
            if modelo == MODELO_FALLBACK:
                raise HTTPException(status_code=502, detail=str(e)[:200])

    raise HTTPException(status_code=502, detail="No pude analizar la imagen")


@app.get("/maximus/eventos")
async def maximus_eventos(request: Request):
    """
    Server-Sent Events para Maximus Display — TV, tablet o cualquier
    pantalla en la red local que quiera ver en vivo qué está haciendo
    Maximus. Solo lee del bus (agent/eventos.py); no toca la lógica real.

    Protegido con el mismo MAXIMUS_CHAT_TOKEN de /maximus/chat, pasado
    como query param (?token=...) porque EventSource del navegador no
    permite mandar headers personalizados.
    """
    token_ok = os.getenv("MAXIMUS_CHAT_TOKEN", "")
    if not token_ok or request.query_params.get("token", "") != token_ok:
        raise HTTPException(status_code=401, detail="No autorizado")

    from agent import eventos

    async def flujo():
        cola = eventos.suscribirse()
        try:
            yield "data: {\"tipo\": \"conectado\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    linea = await asyncio.wait_for(cola.get(), timeout=15)
                    yield f"data: {linea}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # mantiene viva la conexión
        finally:
            eventos.desuscribirse(cola)

    return StreamingResponse(
        flujo(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/maximus/display")
async def maximus_display():
    """Página estática de Maximus Display — se conecta sola a /maximus/eventos."""
    ruta = os.path.join(os.path.dirname(__file__), "static", "maximus_display.html")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="maximus_display.html no encontrado")


@app.get("/maximus/command")
async def maximus_command():
    """Command Center — pantalla dedicada, control por voz. Se conecta a /maximus/eventos
    para el estado y los paneles, y a /maximus/panel/* para los datos."""
    ruta = os.path.join(os.path.dirname(__file__), "static", "maximus_command.html")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="maximus_command.html no encontrado")


def _maximus_token_ok(request: Request) -> bool:
    """El mismo MAXIMUS_CHAT_TOKEN por header — igual que /maximus/chat."""
    tok = os.getenv("MAXIMUS_CHAT_TOKEN", "")
    return bool(tok) and request.headers.get("x-maximus-token", "") == tok


def _maximus_token_ok_header_o_query(request: Request) -> bool:
    """Como _maximus_token_ok, pero también acepta ?token=... — para que
    Ricardo pueda abrir /maximus/historial directo en el navegador, sin
    configurar headers a mano (mismo criterio que /maximus/eventos)."""
    tok = os.getenv("MAXIMUS_CHAT_TOKEN", "")
    if not tok:
        return False
    return (request.headers.get("x-maximus-token", "") == tok
            or request.query_params.get("token", "") == tok)


@app.get("/maximus/panel/ventas")
async def maximus_panel_ventas(request: Request):
    """Ventas por local + medios de pago + top productos + stock bajo. Alimenta 3 paneles."""
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    from agent import paneles
    return JSONResponse(await paneles.ventas_panel(), headers={"Cache-Control": "no-store"})


@app.get("/maximus/panel/checklist")
async def maximus_panel_checklist(request: Request):
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    from agent import paneles
    local = request.query_params.get("local", "playa")
    return JSONResponse(await paneles.checklist_panel(local), headers={"Cache-Control": "no-store"})


@app.get("/maximus/panel/alertas")
async def maximus_panel_alertas(request: Request):
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    from agent import paneles
    return JSONResponse(await paneles.alertas_panel(), headers={"Cache-Control": "no-store"})


@app.get("/maximus/panel/calendario")
async def maximus_panel_calendario(request: Request):
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    from agent import paneles
    return JSONResponse(await paneles.calendario_panel(), headers={"Cache-Control": "no-store"})


@app.get("/maximus/panel/correo")
async def maximus_panel_correo(request: Request):
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    from agent import paneles
    return JSONResponse(await paneles.correo_panel(), headers={"Cache-Control": "no-store"})


@app.get("/maximus/panel/grafo")
async def maximus_panel_grafo(request: Request):
    """Grafo de memoria de Maximus (nodos + aristas) para el panel visual."""
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    from agent import paneles
    return JSONResponse(await paneles.grafo_panel(), headers={"Cache-Control": "no-store"})


@app.get("/maximus/panel/agentes")
async def maximus_panel_agentes(request: Request):
    """Roster de agentes de Maximus + estado (incluye tareas programadas Windows)."""
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    from agent import paneles
    return JSONResponse(await paneles.agentes_panel(), headers={"Cache-Control": "no-store"})


# ════════════════════════════════════════════════════════════
# Historial privado de Ricardo con Maximus — separado a propósito de
# /admin (que usan los cajeros con una clave compartida). Ver P-011.
# ════════════════════════════════════════════════════════════

@app.get("/maximus/historial")
async def maximus_historial_pagina(request: Request):
    """Página HTML simple para revisar el historial con Maximus, protegida
    con el mismo token que /maximus/display -- no la clave de /admin."""
    if not _maximus_token_ok_header_o_query(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    return HTMLResponse(HISTORIAL_HTML)


@app.get("/maximus/historial/api")
async def maximus_historial_lista(request: Request):
    """Lista de tus conversaciones con Maximus (WhatsApp, Telegram, cerebro web)."""
    if not _maximus_token_ok_header_o_query(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    return JSONResponse(await listar_conversaciones_privadas(), headers={"Cache-Control": "no-store"})


@app.get("/maximus/historial/api/{clave:path}")
async def maximus_historial_detalle(clave: str, request: Request):
    """Mensajes de una de tus conversaciones. Nunca sirve una conversación
    de cliente aunque alguien adivine la clave -- doble chequeo con
    es_privada(), igual que hace /admin al revés."""
    if not _maximus_token_ok_header_o_query(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    if not es_privada(clave):
        raise HTTPException(status_code=404, detail="No encontrada")
    return JSONResponse(await obtener_conversacion_completa(clave), headers={"Cache-Control": "no-store"})


HISTORIAL_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Historial con Maximus</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0f1419; color: #e7e9ea; height: 100vh; display: flex; flex-direction: column; }
  header { background: #16202a; padding: 12px 18px; border-bottom: 1px solid #2a3540; }
  header h1 { font-size: 16px; font-weight: 600; }
  .layout { flex: 1; display: flex; overflow: hidden; }
  .sidebar { width: 280px; border-right: 1px solid #2a3540; background: #121a22; overflow-y: auto; }
  .btn-volver { display: none; background: #2a3540; color: #e7e9ea; border: none; border-radius: 8px; padding: 6px 11px; font-size: 16px; cursor: pointer; }
  .conv { padding: 12px 16px; border-bottom: 1px solid #1e2832; cursor: pointer; }
  .conv:hover { background: #182230; }
  .conv.activa { background: #1d2b3a; }
  .conv .canal { font-weight: 600; font-size: 14px; }
  .conv .ult { font-size: 12px; color: #8b98a5; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chat { flex: 1; display: flex; flex-direction: column; }
  .chat-header { padding: 12px 18px; border-bottom: 1px solid #2a3540; display: flex; align-items: center; gap: 10px; background: #16202a; display: none; }
  .mensajes { flex: 1; overflow-y: auto; padding: 18px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 70%; padding: 9px 13px; border-radius: 14px; font-size: 14px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; }
  .msg.user { align-self: flex-start; background: #243340; border-bottom-left-radius: 4px; }
  .msg.assistant { align-self: flex-end; background: #1d6f42; border-bottom-right-radius: 4px; }
  .msg .hora { display: block; font-size: 10px; color: rgba(255,255,255,.5); margin-top: 4px; }
  .vacio { color: #8b98a5; text-align: center; margin-top: 40px; font-size: 14px; }
  @media (max-width: 700px) {
    .sidebar { width: 100%; }
    .chat { display: none; }
    .layout.chat-abierto .sidebar { display: none; }
    .layout.chat-abierto .chat { display: flex; }
    .btn-volver { display: inline-block; }
    .msg { max-width: 85%; }
  }
</style>
</head>
<body>
<header><h1>🧠 Historial con Maximus — solo tú</h1></header>
<div class="layout" id="layout">
  <aside class="sidebar" id="lista"></aside>
  <div class="chat">
    <div class="chat-header" id="chatHeader">
      <button class="btn-volver" onclick="volver()">←</button>
      <span id="chatCanal"></span>
    </div>
    <div class="mensajes" id="mensajes"><div class="vacio">Selecciona una conversación</div></div>
  </div>
</div>
<script>
const params = new URLSearchParams(location.search);
const token = params.get('token') || '';
let activa = null;

function fmtHora(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'Z');
  return d.toLocaleString('es-CL', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' });
}
function escapar(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

async function cargarLista() {
  const r = await fetch('/maximus/historial/api?token=' + encodeURIComponent(token));
  if (!r.ok) { document.getElementById('lista').innerHTML = '<div class="vacio">No autorizado</div>'; return; }
  const convs = await r.json();
  document.getElementById('lista').innerHTML = convs.map(c => `
    <div class="conv ${c.clave===activa?'activa':''}" onclick="seleccionar('${c.clave.replace(/'/g,"\\\\'")}', '${c.canal}')">
      <div class="canal">${c.canal}</div>
      <div class="ult">${escapar((c.ultimo_mensaje||'').slice(0,60))}</div>
    </div>`).join('') || '<div class="vacio">Sin conversaciones</div>';
}

async function seleccionar(clave, canal) {
  activa = clave;
  document.getElementById('layout').classList.add('chat-abierto');
  document.getElementById('chatHeader').style.display = 'flex';
  document.getElementById('chatCanal').textContent = canal;
  const r = await fetch('/maximus/historial/api/' + encodeURIComponent(clave) + '?token=' + encodeURIComponent(token));
  const msgs = await r.json();
  document.getElementById('mensajes').innerHTML = msgs.map(m => `
    <div class="msg ${m.role}">${escapar(m.content)}<span class="hora">${fmtHora(m.timestamp)}</span></div>
  `).join('') || '<div class="vacio">Sin mensajes</div>';
  cargarLista();
}

function volver() {
  document.getElementById('layout').classList.remove('chat-abierto');
}

cargarLista();
setInterval(cargarLista, 5000);
</script>
</body>
</html>"""


@app.post("/maximus/agente/nombre")
async def maximus_agente_nombre(request: Request):
    """Renombra un agente desde la UI del Command Center (persiste el nombre)."""
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")
    clave = (datos.get("clave") or "").strip()
    nombre = (datos.get("nombre") or "").strip()
    if not clave or not nombre:
        raise HTTPException(status_code=400, detail="Falta clave o nombre")
    from agent.equipo import set_nombre
    await set_nombre(clave, nombre)
    return JSONResponse({"ok": True})


@app.get("/maximus/panel/delegaciones")
async def maximus_panel_delegaciones(request: Request):
    """Tareas que Maximus delegó a sus agentes, con estado."""
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    from agent import paneles
    return JSONResponse(await paneles.delegaciones_panel(), headers={"Cache-Control": "no-store"})


@app.post("/maximus/escuchar")
async def maximus_escuchar(request: Request):
    """Recibe un audio grabado por el navegador (iPhone/Safari graba en mp4) y lo
    transcribe con Groq Whisper. Da micrófono real donde el navegador no tiene STT
    propio (Safari de iOS). El cuerpo es el audio crudo; el mime va en Content-Type."""
    if not _maximus_token_ok(request):
        raise HTTPException(status_code=401, detail="No autorizado")
    audio = await request.body()
    if not audio or len(audio) < 400:
        raise HTTPException(status_code=400, detail="Audio vacío o muy corto")
    mime = (request.headers.get("content-type", "audio/mp4").split(";")[0].strip() or "audio/mp4")
    ext = {"audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a",
           "audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav",
           "audio/mpeg": "mp3"}.get(mime, "m4a")
    from agent.transcripcion import transcribir_audio
    texto = await transcribir_audio(audio, f"audio.{ext}", mime)
    return JSONResponse({"texto": texto}, headers={"Cache-Control": "no-store"})


@app.get("/maximus/foto")
async def maximus_foto():
    """La foto de Maximus (el perro) para el centro del Display."""
    from fastapi.responses import FileResponse
    ruta = os.path.join(os.path.dirname(__file__), "static", "img", "maximus-foto.jpeg")
    if not os.path.exists(ruta):
        raise HTTPException(status_code=404, detail="Foto no encontrada")
    return FileResponse(ruta, media_type="image/jpeg")


@app.get("/maximus/avatar")
async def maximus_avatar():
    """Avatar holográfico de Maximus para el Command Center (fondo negro → se
    vuelve transparente con blend en la pantalla)."""
    from fastapi.responses import FileResponse
    ruta = os.path.join(os.path.dirname(__file__), "static", "img", "maximus-avatar.png")
    if not os.path.exists(ruta):
        raise HTTPException(status_code=404, detail="Avatar no encontrado")
    return FileResponse(ruta, media_type="image/png")


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

    chat_id, texto, fue_audio = parseado

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

    from agent import eventos
    await eventos.publicar("escuchando", mensaje=texto[:200], canal="telegram")

    clave = f"tg:{chat_id}"

    # "voz clonada on/off" -- determinístico, antes de gastar un turno de
    # Claude en algo que no necesita interpretación.
    respuesta_voz = await procesar_mensaje_voz(texto)
    if respuesta_voz is not None:
        await guardar_mensaje(clave, "user", texto)
        await guardar_mensaje(clave, "assistant", respuesta_voz)
        await tg.enviar_mensaje(chat_id, respuesta_voz)
        logger.info(f"[MAXIMUS/TG] {chat_id}: {texto[:80]}")
        return {"status": "ok"}

    historial = await obtener_historial(clave)
    respuesta = await responder_maximus(texto, historial)
    await guardar_mensaje(clave, "user", texto)
    await guardar_mensaje(clave, "assistant", respuesta)
    await tg.enviar_mensaje(chat_id, respuesta)

    if fue_audio:
        audio = await sintetizar_voz(respuesta)
        if audio:
            await tg.enviar_audio(chat_id, audio)

    logger.info(f"[MAXIMUS/TG] {chat_id}: {texto[:80]}")
    return {"status": "ok"}


@app.post("/telegram/checklist/webhook/{local}")
async def telegram_checklist_webhook(local: str, request: Request):
    """
    Checklist operativo por Telegram: un bot POR LOCAL, cada uno solo en su
    grupo (ej. Checklistmall_bot en /telegram/checklist/webhook/mall).

    Bots y webhooks DISTINTOS del canal privado de Maximus (/telegram/webhook):
    ese es 1:1 con el dueño; estos viven en grupos donde cualquiera del grupo
    puede tocar los botones. Mezclarlos rompería ese aislamiento.
    """
    local = local.lower()
    if not tg_checklist.configurado(local):
        return {"status": f"checklist de '{local}' no configurado"}

    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    callback = body.get("callback_query")
    if callback:
        await checklist.manejar_callback(callback, tg_checklist, local)
        return {"status": "ok"}

    mensaje = body.get("message")
    if mensaje:
        await checklist.manejar_mensaje_texto(mensaje, tg_checklist, local)

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
