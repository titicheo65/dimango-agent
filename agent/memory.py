# agent/memory.py — Memoria de conversaciones con SQLite
# Generado por AgentKit

"""
Sistema de memoria del agente. Guarda el historial de conversaciones
por número de teléfono usando SQLite (local) o PostgreSQL (producción).
"""

import os
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, Boolean, func
from dotenv import load_dotenv

load_dotenv()

# Configuración de base de datos
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")

# Si es PostgreSQL en producción, ajustar el esquema de URL
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ── Historial privado ─────────────────────────────────────────────────
# Lo que Ricardo habla con Maximus —por el cerebro o por WhatsApp— no es
# atención al cliente: son sueldos, márgenes y decisiones del negocio. Vive en
# OTRA base de datos, no en otra tabla ni tras un filtro. El panel /admin
# consulta el engine de clientes, así que no puede mostrar esto ni por error,
# ni aunque mañana alguien agregue una consulta nueva y olvide filtrar.
PRIVADO_URL = os.getenv("MAXIMUS_DB_URL", "sqlite+aiosqlite:///./maximus_privado.db")
if PRIVADO_URL.startswith("postgresql://"):
    PRIVADO_URL = PRIVADO_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine_privado = create_async_engine(PRIVADO_URL, echo=False)
session_privada = async_sessionmaker(engine_privado, class_=AsyncSession, expire_on_commit=False)


def es_privada(telefono: str) -> bool:
    """¿Esta conversación es de Ricardo con Maximus, y no atención al cliente?"""
    if (telefono or "").startswith("web:"):
        return True                       # el cerebro visual
    try:
        from agent.maximus import es_maximus
        return es_maximus(telefono)       # su WhatsApp — misma regla, un solo lugar
    except Exception:
        return False                      # ante la duda, se trata como cliente


def _sesion_de(telefono: str):
    """Elige la base según de quién sea la conversación."""
    return session_privada if es_privada(telefono) else async_session


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    """Modelo de mensaje en la base de datos."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" o "assistant"
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EstadoConversacion(Base):
    """Estado de cada conversación (ej: pausada para atención humana)."""
    __tablename__ = "estado_conversaciones"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    pausado: Mapped[bool] = mapped_column(Boolean, default=False)


class Contacto(Base):
    """Nombre asociado a cada teléfono/ID (capturado de WhatsApp o puesto a mano)."""
    __tablename__ = "contactos"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))


async def inicializar_db():
    """Crea las tablas si no existen, en las dos bases."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine_privado.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def guardar_mensaje(telefono: str, role: str, content: str):
    """Guarda un mensaje en el historial de conversación."""
    async with _sesion_de(telefono)() as session:
        mensaje = Mensaje(
            telefono=telefono,
            role=role,
            content=content,
            timestamp=datetime.utcnow()
        )
        session.add(mensaje)
        await session.commit()


async def guardar_nombre(telefono: str, nombre: str):
    """Guarda o actualiza el nombre asociado a un teléfono/ID."""
    nombre = (nombre or "").strip()
    if not nombre:
        return
    async with async_session() as session:
        contacto = (await session.execute(
            select(Contacto).where(Contacto.telefono == telefono)
        )).scalars().first()
        if contacto:
            if contacto.nombre != nombre:
                contacto.nombre = nombre
        else:
            session.add(Contacto(telefono=telefono, nombre=nombre))
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    """
    Recupera los últimos N mensajes de una conversación.

    Args:
        telefono: Número de teléfono del cliente
        limite: Máximo de mensajes a recuperar (default: 20)

    Returns:
        Lista de diccionarios con role y content
    """
    async with _sesion_de(telefono)() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        result = await session.execute(query)
        mensajes = result.scalars().all()

        # Invertir para orden cronológico (los más recientes están primero)
        mensajes.reverse()

        return [
            {"role": msg.role, "content": msg.content}
            for msg in mensajes
        ]


async def limpiar_historial(telefono: str):
    """Borra todo el historial de una conversación."""
    async with _sesion_de(telefono)() as session:
        query = select(Mensaje).where(Mensaje.telefono == telefono)
        result = await session.execute(query)
        mensajes = result.scalars().all()
        for msg in mensajes:
            await session.delete(msg)
        await session.commit()


# ════════════════════════════════════════════════════════════
# Funciones para el panel de administración web
# ════════════════════════════════════════════════════════════

async def listar_conversaciones() -> list[dict]:
    """
    Lista todas las conversaciones con su último mensaje, total y estado de pausa.
    Ordenadas de la más reciente a la más antigua.
    """
    async with async_session() as session:
        # Agrupar por teléfono: total de mensajes y timestamp del último
        resumen = (
            select(
                Mensaje.telefono,
                func.count(Mensaje.id).label("total"),
                func.max(Mensaje.timestamp).label("ultimo"),
            )
            .group_by(Mensaje.telefono)
            .order_by(func.max(Mensaje.timestamp).desc())
        )
        filas = (await session.execute(resumen)).all()

        # Segundo cerrojo. Las conversaciones privadas ya viven en otra base y no
        # deberían aparecer acá nunca — pero las que se guardaron ANTES de esa
        # separación siguen en esta tabla. Este filtro las oculta del panel aunque
        # todavía no se hayan migrado.
        filas = [f for f in filas if not es_privada(f.telefono)]

        # Cargar los estados de pausa una sola vez
        estados = {
            e.telefono: e.pausado
            for e in (await session.execute(select(EstadoConversacion))).scalars().all()
        }

        # Cargar los nombres de contacto una sola vez
        nombres = {
            c.telefono: c.nombre
            for c in (await session.execute(select(Contacto))).scalars().all()
        }

        conversaciones = []
        for fila in filas:
            ultimo = (await session.execute(
                select(Mensaje)
                .where(Mensaje.telefono == fila.telefono)
                .order_by(Mensaje.timestamp.desc())
                .limit(1)
            )).scalars().first()
            conversaciones.append({
                "telefono": fila.telefono,
                "nombre": nombres.get(fila.telefono, ""),
                "total": fila.total,
                "ultimo_timestamp": fila.ultimo.isoformat() if fila.ultimo else None,
                "ultimo_mensaje": ultimo.content if ultimo else "",
                "ultimo_role": ultimo.role if ultimo else "",
                "pausado": estados.get(fila.telefono, False),
            })
        return conversaciones


async def obtener_conversacion_completa(telefono: str, limite: int = 200) -> list[dict]:
    """Recupera todos los mensajes de una conversación en orden cronológico."""
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.asc())
            .limit(limite)
        )
        mensajes = (await session.execute(query)).scalars().all()
        return [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp.isoformat(),
            }
            for m in mensajes
        ]


async def esta_pausada(telefono: str) -> bool:
    """Indica si una conversación está pausada (atención humana activa)."""
    async with async_session() as session:
        estado = (await session.execute(
            select(EstadoConversacion).where(EstadoConversacion.telefono == telefono)
        )).scalars().first()
        return bool(estado and estado.pausado)


async def pausar_conversacion(telefono: str, pausado: bool):
    """Pausa o reactiva el agente para una conversación específica."""
    async with async_session() as session:
        estado = (await session.execute(
            select(EstadoConversacion).where(EstadoConversacion.telefono == telefono)
        )).scalars().first()
        if estado:
            estado.pausado = pausado
        else:
            session.add(EstadoConversacion(telefono=telefono, pausado=pausado))
        await session.commit()
