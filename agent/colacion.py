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

import os
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

# Horarios (hora de Chile) en que el agente recuerda tomar colación, por local.
# En cada horario se avisa SOLO a quienes aún no tomaron colación ese día y no
# están marcados como "libre". Así el aviso "insiste" hasta lograr el 100%.
HORARIOS_RECORDATORIO = {
    "playa": ["14:00", "19:00", "22:30"],
    "mall": ["14:00", "19:00"],
}

# Nombre de la plantilla aprobada en Meta para el recordatorio de colación.
# El cuerpo debe tener una variable {{1}} = nombre del trabajador.
PLANTILLA_RECORDATORIO = os.getenv("PLANTILLA_RECORDATORIO", "recordatorio_colacion")


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
    # Días libres FIJOS de la semana (no se le recuerda ni cuenta para el 100%).
    # Se guardan como números separados por coma: 0=lunes ... 6=domingo. Ej: "0,3".
    dias_libres: Mapped[str] = mapped_column(String(20), default="")


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


class RecordatorioColacion(Base):
    """Registro de recordatorios de colación ya enviados (evita duplicados por horario)."""
    __tablename__ = "recordatorios_colacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    fecha: Mapped[str] = mapped_column(String(10), index=True)  # "YYYY-MM-DD" (hora Chile)
    slot: Mapped[str] = mapped_column(String(5))                # "14:00"


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


def fecha_chile(dt: datetime) -> str:
    """Convierte una fecha UTC (naive) a fecha de Chile en formato DD/MM/YYYY."""
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ_CHILE).strftime("%d/%m/%Y")


def _inicio_del_dia_utc() -> datetime:
    """Medianoche de hoy en hora de Chile, expresada en UTC (naive)."""
    ahora_local = datetime.now(TZ_CHILE)
    medianoche = ahora_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return medianoche.astimezone(timezone.utc).replace(tzinfo=None)


def _hoy_str() -> str:
    """Fecha de hoy en hora de Chile, en formato 'YYYY-MM-DD'."""
    return datetime.now(TZ_CHILE).strftime("%Y-%m-%d")


# Días de la semana según datetime.weekday(): 0=lunes ... 6=domingo
DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _dia_semana_hoy() -> int:
    """Día de la semana de hoy en hora de Chile (0=lunes ... 6=domingo)."""
    return datetime.now(TZ_CHILE).weekday()


def _parse_dias_libres(valor: str) -> list[int]:
    """Convierte '0,3' -> [0, 3]. Ignora valores fuera de rango o vacíos."""
    dias = []
    for parte in (valor or "").split(","):
        parte = parte.strip()
        if parte.isdigit():
            n = int(parte)
            if 0 <= n <= 6 and n not in dias:
                dias.append(n)
    return sorted(dias)


def _normalizar_dias_libres(dias) -> str:
    """Normaliza una lista/valor de días libres a texto '0,3' (ordenado, sin repetir)."""
    if isinstance(dias, str):
        nums = _parse_dias_libres(dias)
    else:
        nums = _parse_dias_libres(",".join(str(d) for d in (dias or [])))
    return ",".join(str(n) for n in nums)


async def migrar_esquema() -> None:
    """Agrega columnas nuevas a tablas ya existentes (idempotente, solo SQLite)."""
    from sqlalchemy import text
    try:
        async with async_session() as session:
            cols = (await session.execute(text("PRAGMA table_info(empleados)"))).all()
            nombres = {c[1] for c in cols}
            if "dias_libres" not in nombres:
                await session.execute(
                    text("ALTER TABLE empleados ADD COLUMN dias_libres VARCHAR(20) DEFAULT ''"))
                await session.commit()
                logger.info("[COLACIÓN] Columna dias_libres agregada a la tabla empleados")
    except Exception as e:
        logger.warning(f"[COLACIÓN] No se pudo migrar el esquema (dias_libres): {e}")


def _rango_dias_utc(desde_str: str, hasta_str: str) -> tuple[datetime, datetime]:
    """
    Convierte fechas 'YYYY-MM-DD' (en hora de Chile) a límites UTC naive.
    Devuelve [inicio, fin) donde 'fin' es la medianoche del día siguiente a 'hasta',
    de modo que el rango incluye el día completo de 'hasta'.
    """
    d0 = datetime.strptime(desde_str, "%Y-%m-%d").replace(tzinfo=TZ_CHILE)
    d1 = (datetime.strptime(hasta_str, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=TZ_CHILE)
    return (d0.astimezone(timezone.utc).replace(tzinfo=None),
            d1.astimezone(timezone.utc).replace(tzinfo=None))


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
                "dias_libres": _parse_dias_libres(e.dias_libres),
            }
            for e in emps
        ]


async def guardar_empleado(nombre: str, telefono: str, local: str,
                           es_encargado: bool, dias_libres=None):
    """Agrega o actualiza un empleado (clave = teléfono normalizado)."""
    tel = normalizar_telefono(telefono)
    nombre = (nombre or "").strip()
    local = (local or "").strip().lower()
    if not tel or not nombre or local not in LOCALES:
        raise ValueError("Datos inválidos: nombre, teléfono y local (mall|playa) son obligatorios")
    dias_txt = _normalizar_dias_libres(dias_libres)
    async with async_session() as session:
        emp = (await session.execute(
            select(Empleado).where(Empleado.telefono == tel)
        )).scalars().first()
        if emp:
            emp.nombre = nombre
            emp.local = local
            emp.es_encargado = es_encargado
            emp.dias_libres = dias_txt
            # No reactivamos automáticamente: activar/desactivar es una acción aparte
        else:
            session.add(Empleado(
                telefono=tel, nombre=nombre, local=local,
                es_encargado=es_encargado, activo=True, dias_libres=dias_txt,
            ))
        await session.commit()


async def set_activo_empleado(telefono: str, activo: bool) -> bool:
    """Activa o desactiva a un trabajador (desactivado = de vacaciones, no se le escribe)."""
    tel = normalizar_telefono(telefono)
    async with async_session() as session:
        emp = (await session.execute(
            select(Empleado).where(Empleado.telefono == tel)
        )).scalars().first()
        if not emp:
            return False
        emp.activo = activo
        await session.commit()
        return True


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


async def colaciones_rango(desde_str: str, hasta_str: str, local: str | None = None) -> list[dict]:
    """
    Colaciones terminadas dentro de un rango de fechas (hora de Chile), para reportes.

    Args:
        desde_str: fecha inicial 'YYYY-MM-DD' (inclusive)
        hasta_str: fecha final 'YYYY-MM-DD' (inclusive, día completo)
        local: 'mall' | 'playa' | None (None = todos los locales)

    Returns:
        Lista de dicts con fecha, nombre, local, inicio, fin, duración y si excedió.
    """
    ini, fin = _rango_dias_utc(desde_str, hasta_str)
    async with async_session() as session:
        q = select(Colacion).where(
            Colacion.fin.is_not(None),
            Colacion.inicio >= ini,
            Colacion.inicio < fin,
        )
        if local in LOCALES:
            q = q.where(Colacion.local == local)
        cols = (await session.execute(
            q.order_by(Colacion.local, Colacion.inicio.asc())
        )).scalars().all()
        filas = []
        for c in cols:
            dur = round(c.duracion_min or 0)
            filas.append({
                "fecha": fecha_chile(c.inicio),
                "nombre": c.nombre,
                "local": c.local,
                "inicio": hora_chile(c.inicio),
                "fin": hora_chile(c.fin),
                "duracion_min": dur,
                "excedido": dur > LIMITE_MIN,
                "exceso_min": max(0, dur - LIMITE_MIN),
            })
        return filas


# ════════════════════════════════════════════════════════════
# Recordatorio diario de colación y control de cumplimiento (100%)
# ════════════════════════════════════════════════════════════

async def _libres_hoy_set() -> set[str]:
    """
    Teléfonos que HOY tienen día libre fijo asignado (según el día de la semana).
    A estos no se les recuerda ni cuentan para el 100%.
    """
    hoy_dow = _dia_semana_hoy()
    async with async_session() as session:
        emps = (await session.execute(
            select(Empleado).where(Empleado.activo.is_(True))
        )).scalars().all()
    return {e.telefono for e in emps if hoy_dow in _parse_dias_libres(e.dias_libres)}


async def _telefonos_con_colacion_hoy() -> set[str]:
    """Teléfonos que ya iniciaron al menos una colación hoy (en curso o terminada)."""
    desde = _inicio_del_dia_utc()
    async with async_session() as session:
        filas = (await session.execute(
            select(Colacion.telefono).where(Colacion.inicio >= desde)
        )).scalars().all()
        return set(filas)


async def estado_cumplimiento_hoy() -> dict:
    """
    Estado de cumplimiento del día: por cada trabajador activo, si ya tomó su
    colación, está en colación, está libre o sigue pendiente. Incluye el % de logro.
    """
    libres = await _libres_hoy_set()
    con_colacion = await _telefonos_con_colacion_hoy()
    activas = {a["telefono"] for a in await listar_activas()}

    async with async_session() as session:
        emps = (await session.execute(
            select(Empleado)
            .where(Empleado.activo.is_(True))
            .order_by(Empleado.local, Empleado.nombre)
        )).scalars().all()

    trabajadores = []
    tomadas = pendientes = n_libres = 0
    for e in emps:
        if e.telefono in libres:
            estado = "libre"; n_libres += 1
        elif e.telefono in activas:
            estado = "en_colacion"; tomadas += 1
        elif e.telefono in con_colacion:
            estado = "tomada"; tomadas += 1
        else:
            estado = "pendiente"; pendientes += 1
        trabajadores.append({
            "telefono": e.telefono, "nombre": e.nombre,
            "local": e.local, "estado": estado,
        })

    considerados = tomadas + pendientes  # los libres no cuentan para el 100%
    porcentaje = round(tomadas / considerados * 100) if considerados else 100
    return {
        "trabajadores": trabajadores,
        "tomadas": tomadas,
        "pendientes": pendientes,
        "libres": n_libres,
        "considerados": considerados,
        "porcentaje": porcentaje,
    }


async def _pendientes_para_recordar(local: str) -> list[Empleado]:
    """Trabajadores activos de un local que aún no toman colación hoy y no están libres."""
    libres = await _libres_hoy_set()
    con_colacion = await _telefonos_con_colacion_hoy()
    async with async_session() as session:
        emps = (await session.execute(
            select(Empleado).where(
                Empleado.activo.is_(True), Empleado.local == local)
        )).scalars().all()
    return [e for e in emps
            if e.telefono not in libres and e.telefono not in con_colacion]


async def _ya_recordado(telefono: str, fecha: str, slot: str) -> bool:
    """True si ya se envió el recordatorio de ese horario a ese trabajador hoy."""
    async with async_session() as session:
        return (await session.execute(
            select(RecordatorioColacion).where(
                RecordatorioColacion.telefono == telefono,
                RecordatorioColacion.fecha == fecha,
                RecordatorioColacion.slot == slot,
            )
        )).scalars().first() is not None


async def _registrar_recordatorio(telefono: str, fecha: str, slot: str) -> None:
    """Deja constancia de que se envió el recordatorio (idempotencia)."""
    async with async_session() as session:
        session.add(RecordatorioColacion(telefono=telefono, fecha=fecha, slot=slot))
        await session.commit()


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


# ════════════════════════════════════════════════════════════
# Loop en segundo plano: recordatorio DIARIO de tomar colación
# (a los horarios fijos por local, solo a quienes aún no la tomaron)
# ════════════════════════════════════════════════════════════

async def _enviar_recordatorios_slot(proveedor, local: str, fecha: str, slot: str) -> None:
    """Envía la plantilla de recordatorio a los pendientes de un local en un horario."""
    if not hasattr(proveedor, "enviar_plantilla"):
        logger.warning("[COLACIÓN] El proveedor no soporta plantillas; no se envían recordatorios")
        return
    pendientes = await _pendientes_para_recordar(local)
    for emp in pendientes:
        if await _ya_recordado(emp.telefono, fecha, slot):
            continue
        ok = await proveedor.enviar_plantilla(emp.telefono, PLANTILLA_RECORDATORIO, [emp.nombre])
        if ok:
            await _registrar_recordatorio(emp.telefono, fecha, slot)
            logger.info(f"[COLACIÓN] Recordatorio diario ({slot}) enviado a {emp.nombre} ({local})")
        else:
            logger.warning(f"[COLACIÓN] No se pudo enviar recordatorio a {emp.nombre}; "
                           f"¿plantilla '{PLANTILLA_RECORDATORIO}' aprobada en Meta?")


async def loop_recordatorios_diarios(proveedor, intervalo: int = 30):
    """
    Cada pocos segundos revisa la hora de Chile. Cuando coincide con un horario
    configurado en HORARIOS_RECORDATORIO, envía el recordatorio de tomar colación
    a los trabajadores del local que aún no la tomaron ese día (y no están libres).
    El registro en RecordatorioColacion evita reenviar dentro del mismo horario.
    """
    logger.info("Loop de recordatorio DIARIO de colación iniciado")
    while True:
        try:
            ahora = datetime.now(TZ_CHILE)
            hhmm = ahora.strftime("%H:%M")
            hoy = ahora.strftime("%Y-%m-%d")
            for local, horarios in HORARIOS_RECORDATORIO.items():
                if hhmm in horarios:
                    await _enviar_recordatorios_slot(proveedor, local, hoy, hhmm)
        except Exception as e:
            logger.error(f"[COLACIÓN] Error en loop de recordatorio diario: {e}")
        await asyncio.sleep(intervalo)
