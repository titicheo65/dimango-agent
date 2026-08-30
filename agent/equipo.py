# agent/equipo.py — Nombres personalizados de los agentes de Maximus
#
# Ricardo puede ponerle nombre a cada agente ("ponle Alfredo al Advisor").
# Guardamos solo el override: clave del agente → nombre elegido. Comparte la
# misma base de datos que agent/memory.py.

import logging

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, select

from agent.memory import Base, async_session, engine

logger = logging.getLogger("agentkit")


class AgenteNombre(Base):
    __tablename__ = "agente_nombres"

    clave: Mapped[str] = mapped_column(String(60), primary_key=True)  # id estable del agente
    nombre: Mapped[str] = mapped_column(String(60))                   # nombre elegido por Ricardo


async def inicializar_equipo():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_nombres() -> dict:
    async with async_session() as session:
        filas = list((await session.execute(select(AgenteNombre))).scalars().all())
        return {f.clave: f.nombre for f in filas}


async def set_nombre(clave: str, nombre: str) -> None:
    clave = (clave or "").strip().lower()[:60]
    nombre = (nombre or "").strip()[:60]
    if not clave or not nombre:
        return
    async with async_session() as session:
        fila = (await session.execute(
            select(AgenteNombre).where(AgenteNombre.clave == clave)
        )).scalars().first()
        if fila:
            fila.nombre = nombre
        else:
            session.add(AgenteNombre(clave=clave, nombre=nombre))
        await session.commit()
        logger.info(f"[EQUIPO] {clave} → \"{nombre}\"")
