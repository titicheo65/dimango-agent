# agent/colacion.py — Control de colación del personal (Mall y Playa)
# Generado por AgentKit

"""
Sistema de control de colación (descanso de 30 min) para el personal.

Flujo:
  1. El trabajador (reconocido por su número de WhatsApp) escribe "colación"
     cuando sale a su descanso. El agente registra la hora.
  2. A los 30 min, un loop en segundo plano le avisa por WhatsApp que vuelva.
  3. El encargado ve todo en vivo en la pantalla /colacion (con alerta).
  4. El trabajador escribe "vuelvo" al regresar; queda el tiempo real registrado
     para control de disciplina.

Comparte la misma base de datos que agent/memory.py (mismo Base / async_session).
"""

import re
import asyncio
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import String, Integer, Float, DateTime, Boolean, select
from sqlalchemy.orm import Mapped, mapped_column

from agent.memory import Base, async_session

logger = logging.getLogger("agentkit")

# Duración máxima de la colación, en minutos
LIMITE_MIN = 30

# Zona horaria para mostrar horas legibles (las fechas se guardan en UTC)
TZ_CHILE = ZoneInfo("America/Santiago")

# Locales válidos
LOCALES = ["mall", "playa"]


# ════════════════════════════════════════════════════════════
# Modelos de base de datos
# ════════════════════════════════════════════════════════════

class Empleado(Base):
    """Personal autorizado a registrar colación, identificado por su WhatsApp."""
    __tablename__ = "empleados"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)  # normalizado (solo dígitos)
    nombre: Mapped[str] = mapped_column(String(120))
    local: Mapped[str] = mapped_column(String(20))          # "mall" | "playa"
    es_encargado: Mapped[bool] = mapped_column(Boolean, default=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Colacion(Base):
    """Registro de cada colación: inicio, fin y duración real."""
    __tablename__ = "colaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    nombre: Mapped[str] = mapped_column(String(120))         # snapshot del nombre
    local: Mapped[str] = mapped_column(String(20))           # snapshot del local
    inicio: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    fin: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duracion_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    aviso_enviado: Mapped[bool] = mapped_column(Boolean, default=False)


# ════════════════════════════════════════════════════════════
# Utilidades
# ════════════════════════════════════════════════════════════

def normalizar_telefono(tel: str) -> str:
    """Deja solo dígitos y normaliza móviles chilenos (9XXXXXXXX -> 569XXXXXXXX)."""
    d = re.sub(r"\D", "", tel or "")
    if len(d) == 9 and d.startswith("9"):
        d = "56" + d
    return d


def _normalizar_texto(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar palabras clave."""
    t = (texto or "").lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


def hora_chile(dt: datetime) -> str:
    """Convierte una fecha UTC (naive) a hora de Chile en formato HH:MM."""
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ_CHILE).strftime("%H:%M")


def _inicio_del_dia_utc() -> datetime:
    """Medianoche de hoy en hora de Chile, expresada en UTC (naive)."""
    ahora_local = datetime.now(TZ_CHILE)
    medianoche = ahora_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return medianoche.astimezone(timezone.utc).replace(tzinfo=None)


# Palabras clave para detectar la intención del trabajador.
# Se revisa FIN primero (ej. "termino mi colación" debe cerrar, no abrir).
PALABRAS_FIN = ["vuelvo", "volvi", "regreso", "regrese", "termin", "fin",
                "listo", "de vuelta", "ya estoy", "back"]
PALABRAS_INICIO = ["colacion", "salgo", "descanso", "break", "almuerzo",
                   "almorzar", "salida", "salir"]


# ════════════════════════════════════════════════════════════
# Empleados (registro de personal)
# ════════════════════════════════════════════════════════════

async def buscar_empleado(telefono: str) -> Empleado | None:
    """Devuelve el empleado activo asociado a un número, o None si no existe."""
    tel = normalizar_telefono(telefono)
    async with async_session() as session:
        emp = (await session.execute(
            select(Empleado).where(Empleado.telefono == tel)
        )).scalars().first()
        return emp if (emp and emp.activo) else None


async def listar_empleados() -> list[dict]:
    """Lista todo el personal registrado, ordenado por local y nombre."""
    async with async_session() as session:
        emps = (await session.execute(
            select(Empleado).order_by(Empleado.local, Empleado.nombre)
        )).scalars().all()
        return [
            {
                "telefono": e.telefono,
                "nombre": e.nombre,
                "local": e.local,
                "es_encargado": e.es_encargado,
                "activo": e.activo,
            }
            for e in emps
        ]


async def guardar_empleado(nombre: str, telefono: str, local: str, es_encargado: bool):
    """Agrega o actualiza un empleado (clave = teléfono normalizado)."""
    tel = normalizar_telefono(telefono)
    nombre = (nombre or "").strip()
    local = (local or "").strip().lower()
    if not tel or not nombre or local not in LOCALES:
        raise ValueError("Datos inválidos: nombre, teléfono y local (mall|playa) son obligatorios")
    async with async_session() as session:
        emp = (await session.execute(
            select(Empleado).where(Empleado.telefono == tel)
        )).scalars().first()
        if emp:
            emp.nombre = nombre
            emp.local = local
            emp.es_encargado = es_encargado
            emp.activo = True
        else:
            session.add(Empleado(
                telefono=tel, nombre=nombre, local=local,
                es_encargado=es_encargado, activo=True,
            ))
        await session.commit()


async def eliminar_empleado(telefono: str):
    """Elimina un empleado del registro."""
    tel = normalizar_telefono(telefono)
    async with async_session() as session:
        emp = (await session.execute(
            select(Empleado).where(Empleado.telefono == tel)
        )).scalars().first()
        if emp:
            await session.delete(emp)
            await session.commit()


# ════════════════════════════════════════════════════════════
# Colaciones (registro de descansos)
# ════════════════════════════════════════════════════════════

async def colacion_activa(telefono: str) -> Colacion | None:
    """Devuelve la colación abierta (sin fin) de un teléfono, si existe."""
    tel = normalizar_telefono(telefono)
    async with async_session() as session:
        return (await session.execute(
            select(Colacion)
            .where(Colacion.telefono == tel, Colacion.fin.is_(None))
            .order_by(Colacion.inicio.desc())
        )).scalars().first()


async def iniciar_colacion(empleado: Empleado) -> Colacion:
    """Crea una nueva colación para el empleado y la devuelve."""
    async with async_session() as session:
        col = Colacion(
            telefono=empleado.telefono,
            nombre=empleado.nombre,
            local=empleado.local,
            inicio=datetime.utcnow(),
            aviso_enviado=False,
        )
        session.add(col)
        await session.commit()
        await session.refresh(col)
        return col


async def terminar_colacion(telefono: str) -> Colacion | None:
    """Cierra la colación activa, calcula la duración real y la devuelve."""
    tel = normalizar_telefono(telefono)
    async with async_session() as session:
        col = (await session.execute(
            select(Colacion)
            .where(Colacion.telefono == tel, Colacion.fin.is_(None))
            .order_by(Colacion.inicio.desc())
        )).scalars().first()
        if not col:
            return None
        col.fin = datetime.utcnow()
        col.duracion_min = (col.fin - col.inicio).total_seconds() / 60
        await session.commit()
        await session.refresh(col)
        return col


async def colaciones_por_avisar() -> list[Colacion]:
    """Colaciones activas que ya pasaron el límite y aún no recibieron aviso."""
    limite_dt = datetime.utcnow() - timedelta(minutes=LIMITE_MIN)
    async with async_session() as session:
        return (await session.execute(
            select(Colacion).where(
                Colacion.fin.is_(None),
                Colacion.aviso_enviado.is_(False),
                Colacion.inicio <= limite_dt,
            )
        )).scalars().all()


async def marcar_aviso(colacion_id: int):
    """Marca que ya se envió el recordatorio de fin de colación."""
    async with async_session() as session:
        col = await session.get(Colacion, colacion_id)
        if col:
            col.aviso_enviado = True
            await session.commit()


def _estado_activa(col: Colacion, ahora: datetime) -> dict:
    """Arma el dict de una colación activa para la pantalla en vivo."""
    transcurrido = (ahora - col.inicio).total_seconds()
    return {
        "id": col.id,
        "telefono": col.telefono,
        "nombre": col.nombre,
        "local": col.local,
        "inicio": hora_chile(col.inicio),
        "transcurrido_seg": int(transcurrido),
        "limite_seg": LIMITE_MIN * 60,
        "excedido": transcurrido > LIMITE_MIN * 60,
    }


async def listar_activas() -> list[dict]:
    """Todas las colaciones en curso ahora mismo (para la pantalla)."""
    ahora = datetime.utcnow()
    async with async_session() as session:
        cols = (await session.execute(
            select(Colacion)
            .where(Colacion.fin.is_(None))
            .order_by(Colacion.inicio.asc())
        )).scalars().all()
        return [_estado_activa(c, ahora) for c in cols]


async def historial_hoy() -> list[dict]:
    """Colaciones ya terminadas hoy (para revisar disciplina del día)."""
    desde = _inicio_del_dia_utc()
    async with async_session() as session:
        cols = (await session.execute(
            select(Colacion)
            .where(Colacion.fin.is_not(None), Colacion.inicio >= desde)
            .order_by(Colacion.inicio.desc())
        )).scalars().all()
        return [
            {
                "nombre": c.nombre,
                "local": c.local,
                "inicio": hora_chile(c.inicio),
                "fin": hora_chile(c.fin),
                "duracion_min": round(c.duracion_min or 0),
                "excedido": (c.duracion_min or 0) > LIMITE_MIN,
            }
            for c in cols
        ]


# ════════════════════════════════════════════════════════════
# Manejo de mensajes de WhatsApp del personal
# ════════════════════════════════════════════════════════════

async def manejar_mensaje_colacion(empleado: Empleado, texto: str) -> str:
    """Interpreta el mensaje de un empleado y devuelve la respuesta a enviar."""
    t = _normalizar_texto(texto)
    activa = await colacion_activa(empleado.telefono)

    # 1) ¿Está volviendo de colación?
    if any(p in t for p in PALABRAS_FIN):
        if not activa:
            return ("No tienes una colación activa. Escribe *colación* cuando "
                    "salgas a tu descanso.")
        col = await terminar_colacion(empleado.telefono)
        dur = round(col.duracion_min or 0)
        if dur > LIMITE_MIN:
            extra = dur - LIMITE_MIN
            return (f"✅ Colación terminada. Estuviste {dur} min "
                    f"(te pasaste {extra} min). ¡A trabajar!")
        return f"✅ Colación terminada. Estuviste {dur} min. ¡Gracias {empleado.nombre}!"

    # 2) ¿Está saliendo a colación?
    if any(p in t for p in PALABRAS_INICIO):
        if activa:
            return (f"Ya estás en colación desde las {hora_chile(activa.inicio)}. "
                    f"Escribe *vuelvo* cuando regreses a tu puesto.")
        col = await iniciar_colacion(empleado)
        fin_prevista = col.inicio + timedelta(minutes=LIMITE_MIN)
        return (f"✅ {empleado.nombre}, colación iniciada a las {hora_chile(col.inicio)}.\n"
                f"Vuelves a las {hora_chile(fin_prevista)} ({LIMITE_MIN} min).\n"
                f"Te aviso cuando se acabe el tiempo.")

    # 3) Mensaje no reconocido → ayuda según el estado
    if activa:
        return (f"Estás en colación desde las {hora_chile(activa.inicio)}. "
                f"Escribe *vuelvo* cuando regreses a tu puesto.")
    return (f"Hola {empleado.nombre} 👋\n"
            f"Escribe *colación* cuando salgas a tu descanso de {LIMITE_MIN} min, "
            f"y *vuelvo* cuando regreses.")


# ════════════════════════════════════════════════════════════
# Loop en segundo plano: recordatorio al cumplir 30 min
# ════════════════════════════════════════════════════════════

MENSAJE_RECORDATORIO = (
    "⏰ {nombre}, se acabaron tus {limite} min de colación.\n"
    "Por favor vuelve a tu puesto. ¡Gracias!"
)


async def loop_recordatorios(proveedor, intervalo: int = 20):
    """
    Revisa periódicamente las colaciones vencidas y avisa al trabajador por WhatsApp.
    El encargado ve el aviso en la pantalla /colacion (alerta visual y sonora).
    """
    logger.info("Loop de recordatorios de colación iniciado")
    while True:
        try:
            pendientes = await colaciones_por_avisar()
            for col in pendientes:
                mensaje = MENSAJE_RECORDATORIO.format(nombre=col.nombre, limite=LIMITE_MIN)
                ok = await proveedor.enviar_mensaje(col.telefono, mensaje)
                if ok:
                    await marcar_aviso(col.id)
                    logger.info(f"[COLACIÓN] Recordatorio enviado a {col.nombre} ({col.telefono})")
                else:
                    logger.warning(f"[COLACIÓN] No se pudo avisar a {col.nombre}; se reintentará")
        except Exception as e:
            logger.error(f"[COLACIÓN] Error en loop de recordatorios: {e}")
        await asyncio.sleep(intervalo)
