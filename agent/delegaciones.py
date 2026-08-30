# agent/delegaciones.py — Tareas que Maximus delega a sus agentes
#
# Cuando Ricardo le dice "pídele a X que...", "corre el advisor", "revisa los
# correos ahora" o "delega esto", Maximus lo registra acá y —si el agente es
# una automatización real— la lanza al toque. Comparte la misma base de datos
# que agent/memory.py.

import logging
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, Integer, select

from agent.memory import Base, async_session, engine

logger = logging.getLogger("agentkit")


class Delegacion(Base):
    __tablename__ = "delegaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agente: Mapped[str] = mapped_column(String(80))          # a quién se delegó
    tarea: Mapped[str] = mapped_column(Text)                 # qué se pidió
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # lanzado | pendiente | hecho | error
    resultado: Mapped[str] = mapped_column(Text, default="")  # nota corta del resultado
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def inicializar_delegaciones():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def registrar_delegacion(agente: str, tarea: str, estado: str = "pendiente", resultado: str = "") -> Delegacion:
    async with async_session() as session:
        d = Delegacion(agente=(agente or "").strip()[:80], tarea=(tarea or "").strip(),
                       estado=estado, resultado=(resultado or "").strip()[:300])
        session.add(d)
        await session.commit()
        await session.refresh(d)
        logger.info(f"[DELEGACION] {agente} → {tarea} ({estado})")
        return d


async def listar_delegaciones(limite: int = 30) -> list[Delegacion]:
    async with async_session() as session:
        q = select(Delegacion).order_by(Delegacion.creado_en.desc()).limit(limite)
        return list((await session.execute(q)).scalars().all())
