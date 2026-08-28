# agent/checklist_operativo.py — Checklist operativo por Telegram (tareas con horario)

"""
Sistema de checklist operativo: un grupo de Telegram por local, tareas con
horario fijo definidas en config/checklist_operativo.json, botones de
respuesta (hecho / problema / posponer 5 min) y escalamiento automático si
nadie responde.

Flujo:
  1. Un loop en segundo plano revisa cada minuto el horario de Chile. Cuando
     coincide con una tarea programada, envía el recordatorio al grupo del
     local correspondiente con tres botones.
  2. Si nadie responde en REENVIO_MINUTOS, se reenvía el mismo recordatorio
     al grupo (una sola vez).
  3. Si nadie responde en ESCALAMIENTO_MINUTOS, se avisa al supervisor.
  4. "Posponer 5 min" reprograma un nuevo recordatorio en ese plazo, con los
     mismos botones — reinicia el reloj de reenvío/escalamiento.
  5. Cada respuesta queda registrada con quién, cuándo y en qué local,
     identificada por el "id" de la tarea (config), para poder sacar
     estadísticas de cumplimiento por tarea.

Comparte la misma base de datos que agent/memory.py (mismo Base / async_session),
así que ya queda listo para migrar de SQLite a PostgreSQL sin tocar nada acá.

Listo para integrar con Maximus: resumen_periodo() devuelve cumplimiento y
tiempos de respuesta por tarea, en el mismo formato de dict que ya usan
agent/alertas_venta.py y agent/notas_personales.py para sus herramientas.
"""

import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import String, Integer, Boolean, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.memory import Base, async_session

logger = logging.getLogger("agentkit")

RUTA_CONFIG = "config/checklist_operativo.json"
TZ_CHILE = ZoneInfo("America/Santiago")

ESTADOS_ABIERTOS = ("pendiente", "pospuesto")
MAX_POSPOSICIONES = 2  # después de esto, no se ofrece más "posponer" — solo hecho/problema


# ════════════════════════════════════════════════════════════
# Modelo de base de datos — una fila por recordatorio enviado
# ════════════════════════════════════════════════════════════

class EnvioChecklist(Base):
    """Un envío de una tarea a un grupo, con su estado y quién respondió."""
    __tablename__ = "checklist_envios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tarea_id: Mapped[str] = mapped_column(String(80), index=True)       # "id" en checklist_operativo.json
    tarea_texto: Mapped[str] = mapped_column(String(300))               # snapshot del texto de la tarea
    local: Mapped[str] = mapped_column(String(20), index=True)
    fecha: Mapped[str] = mapped_column(String(10), index=True)          # "YYYY-MM-DD" (hora Chile) del envío original
    hora_programada: Mapped[str] = mapped_column(String(5))             # "11:00"
    chat_id: Mapped[str] = mapped_column(String(50))
    mensaje_id: Mapped[str] = mapped_column(String(50), default="")     # último mensaje de Telegram (se edita al resolver)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # pendiente|hecho|problema|pospuesto|vencido
    enviado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reenviado: Mapped[bool] = mapped_column(Boolean, default=False)
    escalado: Mapped[bool] = mapped_column(Boolean, default=False)
    pospuesto_veces: Mapped[int] = mapped_column(Integer, default=0)
    proxima_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # cuándo reactivar tras "posponer"
    respondido_por: Mapped[str] = mapped_column(String(120), default="")
    respondido_por_id: Mapped[str] = mapped_column(String(50), default="")
    respondido_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EsperaNombre(Base):
    """
    Teléfono compartido por local: el nombre de Telegram no identifica a la
    persona real. Al tocar 'hecho'/'problema' no se marca todavía — se guarda
    acá que ese chat está esperando que alguien escriba su nombre, y recién
    con el siguiente mensaje de texto en ese chat se completa la respuesta.
    """
    __tablename__ = "checklist_espera_nombre"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(50), index=True)
    local: Mapped[str] = mapped_column(String(20))
    envio_id: Mapped[int] = mapped_column(Integer)
    accion: Mapped[str] = mapped_column(String(20))  # hecho | problema
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ════════════════════════════════════════════════════════════
# Utilidades
# ════════════════════════════════════════════════════════════

def _ahora_chile() -> datetime:
    return datetime.now(TZ_CHILE)


def _hoy_str() -> str:
    return _ahora_chile().strftime("%Y-%m-%d")


def hora_chile(dt: datetime) -> str:
    """Convierte una fecha UTC (naive) a hora de Chile en formato HH:MM."""
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ_CHILE).strftime("%H:%M")


def cargar_config() -> dict:
    """Lee config/checklist_operativo.json. Vacío si falta o está mal formado."""
    try:
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"[CHECKLIST] No se encontró {RUTA_CONFIG}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"[CHECKLIST] {RUTA_CONFIG} tiene JSON inválido: {e}")
        return {}


def _rango_dias_utc(desde_str: str, hasta_str: str) -> tuple[datetime, datetime]:
    """Convierte fechas 'YYYY-MM-DD' (hora Chile) a límites UTC naive [inicio, fin)."""
    d0 = datetime.strptime(desde_str, "%Y-%m-%d").replace(tzinfo=TZ_CHILE)
    d1 = (datetime.strptime(hasta_str, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=TZ_CHILE)
    return (d0.astimezone(timezone.utc).replace(tzinfo=None),
            d1.astimezone(timezone.utc).replace(tzinfo=None))


# ════════════════════════════════════════════════════════════
# Escritura / lectura de envíos
# ════════════════════════════════════════════════════════════

async def _ya_enviado_hoy(tarea_id: str, local: str, fecha: str, hora: str) -> bool:
    """Evita duplicar el envío si el loop revisa el mismo minuto más de una vez."""
    async with async_session() as session:
        return (await session.execute(
            select(EnvioChecklist).where(
                EnvioChecklist.tarea_id == tarea_id,
                EnvioChecklist.local == local,
                EnvioChecklist.fecha == fecha,
                EnvioChecklist.hora_programada == hora,
            )
        )).scalars().first() is not None


async def _crear_envio(tarea_id: str, texto: str, local: str, fecha: str,
                        hora: str, chat_id) -> EnvioChecklist:
    async with async_session() as session:
        envio = EnvioChecklist(
            tarea_id=tarea_id, tarea_texto=texto, local=local,
            fecha=fecha, hora_programada=hora, chat_id=str(chat_id),
            enviado_en=datetime.utcnow(),
        )
        session.add(envio)
        await session.commit()
        await session.refresh(envio)
        return envio


async def _guardar_mensaje_id(envio_id: int, mensaje_id: str | None):
    async with async_session() as session:
        envio = await session.get(EnvioChecklist, envio_id)
        if envio:
            envio.mensaje_id = mensaje_id or ""
            await session.commit()


async def obtener_envio(envio_id: int) -> EnvioChecklist | None:
    async with async_session() as session:
        return await session.get(EnvioChecklist, envio_id)


async def marcar_respuesta(envio_id: int, estado: str, usuario_nombre: str,
                            usuario_id: str) -> EnvioChecklist | None:
    """Registra hecho/problema: quién, cuándo. Solo aplica si seguía pendiente."""
    async with async_session() as session:
        envio = await session.get(EnvioChecklist, envio_id)
        if not envio or envio.estado not in ESTADOS_ABIERTOS:
            return None
        envio.estado = estado
        envio.respondido_por = usuario_nombre
        envio.respondido_por_id = usuario_id
        envio.respondido_en = datetime.utcnow()
        await session.commit()
        await session.refresh(envio)
        return envio


async def posponer_envio(envio_id: int, minutos: int, usuario_nombre: str,
                          usuario_id: str) -> EnvioChecklist | None:
    """
    Deja la tarea 'dormida' minutos min; el loop de escalamiento la reactiva.
    Devuelve None también si ya se agotó el tope de posposiciones — para
    distinguir ese caso de "ya fue marcada", revisar tope_posposiciones_agotado()
    ANTES de llamar a esta función.
    """
    async with async_session() as session:
        envio = await session.get(EnvioChecklist, envio_id)
        if not envio or envio.estado not in ESTADOS_ABIERTOS:
            return None
        if envio.pospuesto_veces >= MAX_POSPOSICIONES:
            return None
        envio.estado = "pospuesto"
        envio.proxima_en = datetime.utcnow() + timedelta(minutes=minutos)
        envio.pospuesto_veces += 1
        envio.respondido_por = usuario_nombre
        envio.respondido_por_id = usuario_id
        envio.respondido_en = datetime.utcnow()
        await session.commit()
        await session.refresh(envio)
        return envio


async def tope_posposiciones_agotado(envio_id: int) -> bool:
    envio = await obtener_envio(envio_id)
    return bool(envio and envio.pospuesto_veces >= MAX_POSPOSICIONES)


# ════════════════════════════════════════════════════════════
# Espera de nombre — teléfono compartido, ver EsperaNombre arriba
# ════════════════════════════════════════════════════════════

async def crear_espera_nombre(chat_id, local: str, envio_id: int, accion: str) -> None:
    """Reemplaza cualquier espera anterior del mismo chat — solo una a la vez."""
    async with async_session() as session:
        viejas = (await session.execute(
            select(EsperaNombre).where(EsperaNombre.chat_id == str(chat_id))
        )).scalars().all()
        for v in viejas:
            await session.delete(v)
        session.add(EsperaNombre(
            chat_id=str(chat_id), local=local, envio_id=envio_id, accion=accion,
        ))
        await session.commit()


async def tomar_espera_nombre(chat_id) -> EsperaNombre | None:
    """Recupera y BORRA la espera pendiente de ese chat — se consume una sola vez."""
    async with async_session() as session:
        espera = (await session.execute(
            select(EsperaNombre).where(EsperaNombre.chat_id == str(chat_id))
        )).scalars().first()
        if not espera:
            return None
        await session.delete(espera)
        await session.commit()
        return espera


async def _envios_por_reenviar(minutos: int) -> list[EnvioChecklist]:
    limite = datetime.utcnow() - timedelta(minutes=minutos)
    async with async_session() as session:
        return (await session.execute(
            select(EnvioChecklist).where(
                EnvioChecklist.estado == "pendiente",
                EnvioChecklist.reenviado.is_(False),
                EnvioChecklist.enviado_en <= limite,
            )
        )).scalars().all()


async def _envios_por_escalar(minutos: int) -> list[EnvioChecklist]:
    limite = datetime.utcnow() - timedelta(minutes=minutos)
    async with async_session() as session:
        return (await session.execute(
            select(EnvioChecklist).where(
                EnvioChecklist.estado == "pendiente",
                EnvioChecklist.reenviado.is_(True),
                EnvioChecklist.escalado.is_(False),
                EnvioChecklist.enviado_en <= limite,
            )
        )).scalars().all()


async def _envios_pospuestos_listos() -> list[EnvioChecklist]:
    ahora = datetime.utcnow()
    async with async_session() as session:
        return (await session.execute(
            select(EnvioChecklist).where(
                EnvioChecklist.estado == "pospuesto",
                EnvioChecklist.proxima_en.is_not(None),
                EnvioChecklist.proxima_en <= ahora,
            )
        )).scalars().all()


async def _marcar_reenviado(envio_id: int):
    async with async_session() as session:
        e = await session.get(EnvioChecklist, envio_id)
        if e:
            e.reenviado = True
            await session.commit()


async def _marcar_escalado(envio_id: int):
    async with async_session() as session:
        e = await session.get(EnvioChecklist, envio_id)
        if e:
            e.escalado = True
            await session.commit()


async def _reactivar_pospuesto(envio_id: int, chat_id, mensaje_id: str | None):
    """Vuelve a poner la tarea como recién enviada: reinicia reenvío/escalamiento."""
    async with async_session() as session:
        e = await session.get(EnvioChecklist, envio_id)
        if e:
            e.estado = "pendiente"
            e.enviado_en = datetime.utcnow()
            e.reenviado = False
            e.escalado = False
            e.proxima_en = None
            e.chat_id = str(chat_id)
            e.mensaje_id = mensaje_id or ""
            await session.commit()


async def _vencer_envios_antiguos():
    """Cierra como 'vencido' lo que quedó abierto de días anteriores (para estadísticas)."""
    hoy = _hoy_str()
    async with async_session() as session:
        abiertos = (await session.execute(
            select(EnvioChecklist).where(
                EnvioChecklist.estado.in_(ESTADOS_ABIERTOS),
                EnvioChecklist.fecha != hoy,
            )
        )).scalars().all()
        for e in abiertos:
            e.estado = "vencido"
        if abiertos:
            await session.commit()


# ════════════════════════════════════════════════════════════
# Botones: manejo del callback_query de Telegram
# ════════════════════════════════════════════════════════════

def _nombre_usuario(from_user: dict) -> str:
    nombre = (from_user.get("first_name") or "").strip()
    apellido = (from_user.get("last_name") or "").strip()
    completo = f"{nombre} {apellido}".strip()
    return completo or from_user.get("username") or "alguien"


async def manejar_callback(callback: dict, tg, local: str) -> None:
    """
    Procesa el toque de un botón (hecho / problema / posponer).

    `local` viene de la URL del webhook (/telegram/checklist/webhook/<local>):
    identifica con qué bot hay que responder, porque cada local tiene su
    propio bot y Telegram solo entrega el callback al bot dueño del mensaje.
    """
    callback_id = callback.get("id", "")
    data = callback.get("data", "")
    from_user = callback.get("from", {}) or {}
    usuario_nombre = _nombre_usuario(from_user)
    usuario_id = str(from_user.get("id", ""))
    mensaje = callback.get("message", {}) or {}
    chat_id = mensaje.get("chat", {}).get("id")
    mensaje_id = mensaje.get("message_id")

    partes = data.split(":")
    if len(partes) != 3 or partes[0] != "chk":
        await tg.responder_callback(local, callback_id, "Acción no reconocida")
        return

    try:
        envio_id = int(partes[1])
    except ValueError:
        await tg.responder_callback(local, callback_id, "Acción no reconocida")
        return
    accion = partes[2]

    envio = await obtener_envio(envio_id)
    if not envio:
        await tg.responder_callback(local, callback_id, "Esta tarea ya no existe")
        return

    if envio.local != local:
        # No debería pasar en un setup correcto (cada bot solo está en su grupo),
        # pero si pasa, mejor no responder con el bot equivocado.
        logger.warning(f"[CHECKLIST] Callback de '{local}' para envío del local '{envio.local}' (id={envio_id})")
        await tg.responder_callback(local, callback_id, "Error de configuración: local no coincide")
        return

    if envio.estado not in ESTADOS_ABIERTOS:
        quien = envio.respondido_por or "otra persona"
        await tg.responder_callback(local, callback_id, f"Ya la marcó {quien}")
        return

    if accion in ("hecho", "problema"):
        # Teléfono compartido por local: el nombre de Telegram no identifica
        # a la persona real. No se marca todavía — se espera que alguien
        # escriba su nombre como el siguiente mensaje de texto en este chat.
        if chat_id is None:
            await tg.responder_callback(local, callback_id, "No se pudo identificar el chat")
            return
        await crear_espera_nombre(chat_id, local, envio_id, accion)
        await tg.responder_callback(local, callback_id, "Falta tu nombre 👇")
        await tg.enviar_mensaje(local, chat_id, "✍️ ¿Quién confirma? Responde este mensaje con tu nombre.")

    elif accion == "posponer":
        if await tope_posposiciones_agotado(envio_id):
            await tg.responder_callback(
                local, callback_id,
                f"Ya se pospuso {MAX_POSPOSICIONES} veces — marca hecho o problema",
            )
            return
        cfg = cargar_config()
        minutos = cfg.get("posponer_minutos", 5)
        envio = await posponer_envio(envio_id, minutos, usuario_nombre, usuario_id)
        if not envio:
            await tg.responder_callback(local, callback_id, "Ya fue marcada")
            return
        texto = (f"⏳ {envio.tarea_texto}\n"
                 f"{envio.local.title()} — pospuesto {minutos} min por {usuario_nombre}")
        if chat_id is not None and mensaje_id is not None:
            await tg.editar_mensaje(local, chat_id, mensaje_id, texto)
        await tg.responder_callback(local, callback_id, f"Pospuesto {minutos} min ⏳")

    else:
        await tg.responder_callback(local, callback_id, "Acción no reconocida")


async def manejar_mensaje_texto(message: dict, tg, local: str) -> None:
    """
    Procesa un mensaje de texto plano en el grupo. Si ese chat tenía una
    confirmación pendiente de nombre (ver crear_espera_nombre), este mensaje
    ES el nombre — completa hecho/problema con él. Si no había nada
    pendiente, se ignora (es charla normal del grupo, no le compete al bot).
    """
    chat_id = message.get("chat", {}).get("id")
    texto = (message.get("text") or "").strip()
    if chat_id is None or not texto:
        return

    espera = await tomar_espera_nombre(chat_id)
    if not espera:
        return  # no hay nada esperando nombre en este chat — mensaje normal

    if espera.local != local:
        logger.warning(f"[CHECKLIST] Espera de nombre de '{espera.local}' llegó por bot de '{local}'")
        return

    nombre = texto[:120]
    from_user = message.get("from", {}) or {}
    usuario_id = str(from_user.get("id", ""))

    envio = await marcar_respuesta(espera.envio_id, espera.accion, nombre, usuario_id)
    if not envio:
        await tg.enviar_mensaje(local, chat_id, "Esa tarea ya se había resuelto, gracias igual.")
        return

    if espera.accion == "hecho":
        confirmacion = (f"✅ {envio.tarea_texto}\n"
                        f"{envio.local.title()} — hecho por {nombre} a las {hora_chile(envio.respondido_en)}")
    else:
        confirmacion = (f"⚠️ {envio.tarea_texto}\n"
                        f"{envio.local.title()} — problema reportado por {nombre} "
                        f"a las {hora_chile(envio.respondido_en)}")

    if envio.mensaje_id:
        await tg.editar_mensaje(local, chat_id, envio.mensaje_id, confirmacion)
    await tg.enviar_mensaje(local, chat_id, f"Anotado, gracias {nombre} 🙌")

    if espera.accion == "problema":
        cfg = cargar_config()
        supervisor = cfg.get("supervisor_chat_id")
        if supervisor:
            grupo = cfg.get("grupos", {}).get(envio.local, {})
            aviso = (f"⚠️ *Problema reportado*\n{envio.tarea_texto}\n"
                     f"📍 {grupo.get('nombre', envio.local.title())} — {nombre}")
            await tg.enviar_mensaje(local, supervisor, aviso)


# ════════════════════════════════════════════════════════════
# Loop 1: envío de tareas según el horario de checklist_operativo.json
# ════════════════════════════════════════════════════════════

MENSAJE_TAREA = "🔔 *{texto}*\n📍 {local_nombre} — {hora}"


async def loop_envios_checklist(tg, intervalo: int = 20):
    """Cada pocos segundos revisa la hora de Chile y dispara las tareas que calzan."""
    logger.info("[CHECKLIST] Loop de envíos iniciado")
    while True:
        try:
            ahora = _ahora_chile()
            hhmm = ahora.strftime("%H:%M")
            hoy = ahora.strftime("%Y-%m-%d")
            dow = ahora.weekday()
            cfg = cargar_config()
            grupos = cfg.get("grupos", {})

            for tarea in cfg.get("tareas", []):
                if tarea.get("hora") != hhmm:
                    continue
                dias = tarea.get("dias")
                if dias and dow not in dias:
                    continue

                tarea_id = tarea.get("id", "")
                texto_tarea = tarea.get("texto", tarea_id)
                if not tarea_id:
                    logger.warning("[CHECKLIST] Tarea sin 'id' en checklist_operativo.json, se ignora")
                    continue

                for local in tarea.get("locales", []):
                    grupo = grupos.get(local)
                    if not grupo or not grupo.get("chat_id"):
                        logger.warning(f"[CHECKLIST] Local '{local}' sin chat_id configurado, se omite")
                        continue
                    if not tg.configurado(local):
                        logger.warning(f"[CHECKLIST] Local '{local}' sin bot configurado (TELEGRAM_CHECKLIST_BOT_TOKEN_{local.upper()}), se omite")
                        continue
                    if await _ya_enviado_hoy(tarea_id, local, hoy, hhmm):
                        continue

                    texto = MENSAJE_TAREA.format(
                        texto=texto_tarea,
                        local_nombre=grupo.get("nombre", local.title()),
                        hora=hhmm,
                    )
                    envio = await _crear_envio(tarea_id, texto_tarea, local, hoy, hhmm, grupo["chat_id"])
                    mensaje_id = await tg.enviar_tarea(local, grupo["chat_id"], texto, envio.id)
                    if mensaje_id:
                        await _guardar_mensaje_id(envio.id, mensaje_id)
                        logger.info(f"[CHECKLIST] Enviada '{tarea_id}' a {local} ({hhmm})")
                    else:
                        logger.warning(f"[CHECKLIST] No se pudo enviar '{tarea_id}' a {local}")
        except Exception as e:
            logger.error(f"[CHECKLIST] Error en loop de envíos: {e}")
        await asyncio.sleep(intervalo)


# ════════════════════════════════════════════════════════════
# Loop 2: reenvío a los 10 min, escalamiento a los 20, reactivar pospuestos
# ════════════════════════════════════════════════════════════

MENSAJE_REENVIO = "🔁 *Recordatorio* (sin respuesta hace {minutos} min)\n{texto}\n📍 {local_nombre}"
MENSAJE_ESCALAMIENTO = ("⏱️ *Sin respuesta {minutos}+ min*\n{texto}\n"
                         "📍 {local_nombre} — enviada a las {hora}\n"
                         "Nadie ha marcado hecho ni problema.")
MENSAJE_POSPUESTO_LISTO = "🔔 *{texto}*\n📍 {local_nombre} — {hora}\n(pospuesta antes)"


async def loop_escalamiento_checklist(tg, intervalo: int = 30):
    """Reenvía, escala al supervisor y reactiva las tareas pospuestas."""
    logger.info("[CHECKLIST] Loop de escalamiento iniciado")
    while True:
        try:
            cfg = cargar_config()
            grupos = cfg.get("grupos", {})
            reenvio_min = cfg.get("reenvio_minutos", 10)
            escalamiento_min = cfg.get("escalamiento_minutos", 20)
            supervisor = cfg.get("supervisor_chat_id")

            for envio in await _envios_por_reenviar(reenvio_min):
                grupo = grupos.get(envio.local, {})
                texto = MENSAJE_REENVIO.format(
                    minutos=reenvio_min, texto=envio.tarea_texto,
                    local_nombre=grupo.get("nombre", envio.local.title()),
                )
                mensaje_id = await tg.enviar_tarea(envio.local, envio.chat_id, texto, envio.id)
                await _marcar_reenviado(envio.id)
                if mensaje_id:
                    await _guardar_mensaje_id(envio.id, mensaje_id)
                logger.info(f"[CHECKLIST] Reenviada '{envio.tarea_id}' ({envio.local})")

            for envio in await _envios_por_escalar(escalamiento_min):
                await _marcar_escalado(envio.id)
                if supervisor:
                    grupo = grupos.get(envio.local, {})
                    texto = MENSAJE_ESCALAMIENTO.format(
                        minutos=escalamiento_min, texto=envio.tarea_texto,
                        local_nombre=grupo.get("nombre", envio.local.title()),
                        hora=envio.hora_programada,
                    )
                    await tg.enviar_mensaje(envio.local, supervisor, texto)
                logger.info(f"[CHECKLIST] Escalada al supervisor '{envio.tarea_id}' ({envio.local})")

            for envio in await _envios_pospuestos_listos():
                grupo = grupos.get(envio.local, {})
                texto = MENSAJE_POSPUESTO_LISTO.format(
                    texto=envio.tarea_texto,
                    local_nombre=grupo.get("nombre", envio.local.title()),
                    hora=_ahora_chile().strftime("%H:%M"),
                )
                mensaje_id = await tg.enviar_tarea(envio.local, envio.chat_id, texto, envio.id)
                await _reactivar_pospuesto(envio.id, envio.chat_id, mensaje_id)
                logger.info(f"[CHECKLIST] Reactivada tras posponer '{envio.tarea_id}' ({envio.local})")

            await _vencer_envios_antiguos()
        except Exception as e:
            logger.error(f"[CHECKLIST] Error en loop de escalamiento: {e}")
        await asyncio.sleep(intervalo)


# ════════════════════════════════════════════════════════════
# Estadísticas — por tarea (usa el "id" de checklist_operativo.json)
# ════════════════════════════════════════════════════════════

async def resumen_periodo(desde_str: str, hasta_str: str, local: str | None = None) -> dict:
    """
    Cumplimiento por tarea en un rango de fechas (hora de Chile), agrupado
    por tarea_id: total de envíos, hecho/problema/vencido/abierto, % de
    cumplimiento y tiempo promedio de respuesta en minutos.
    """
    ini, fin = _rango_dias_utc(desde_str, hasta_str)
    async with async_session() as session:
        q = select(EnvioChecklist).where(
            EnvioChecklist.enviado_en >= ini, EnvioChecklist.enviado_en < fin)
        if local:
            q = q.where(EnvioChecklist.local == local)
        envios = (await session.execute(q)).scalars().all()

    por_tarea: dict[str, dict] = {}
    for e in envios:
        d = por_tarea.setdefault(e.tarea_id, {
            "tarea_id": e.tarea_id, "texto": e.tarea_texto,
            "total": 0, "hecho": 0, "problema": 0, "vencido": 0, "abierto": 0,
            "_tiempos_min": [],
        })
        d["total"] += 1
        if e.estado == "hecho":
            d["hecho"] += 1
        elif e.estado == "problema":
            d["problema"] += 1
        elif e.estado == "vencido":
            d["vencido"] += 1
        else:
            d["abierto"] += 1
        if e.respondido_en:
            d["_tiempos_min"].append((e.respondido_en - e.enviado_en).total_seconds() / 60)

    por_tarea_lista = []
    for d in por_tarea.values():
        tiempos = d.pop("_tiempos_min")
        d["cumplimiento_pct"] = round(d["hecho"] / d["total"] * 100) if d["total"] else 0
        d["tiempo_respuesta_prom_min"] = round(sum(tiempos) / len(tiempos), 1) if tiempos else None
        por_tarea_lista.append(d)
    por_tarea_lista.sort(key=lambda x: x["tarea_id"])

    total = len(envios)
    hecho_total = sum(1 for e in envios if e.estado == "hecho")
    return {
        "desde": desde_str, "hasta": hasta_str, "local": local or "todos",
        "total_envios": total,
        "cumplimiento_general_pct": round(hecho_total / total * 100) if total else 100,
        "por_tarea": por_tarea_lista,
    }
