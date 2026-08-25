# agent/notas_personales.py — Memoria personal de Ricardo, no de DiMango
#
# Lo que Ricardo le cuenta a Maximus de su día a día, cosas que quiere
# mejorar, ideas sueltas — y recordatorios con hora. No vive en los seis
# archivos de memoria (esos son 100% negocio): es un carril aparte, para
# que Maximus lo traiga solo a la conversación sin que se lo pidan.
#
# Comparte la misma base de datos que agent/memory.py (mismo Base / async_session).

import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, Boolean, Integer, select

from agent.memory import Base, async_session, engine

logger = logging.getLogger("agentkit")

CATEGORIAS = ("nota", "mejora", "recordatorio", "tarea")


class NotaPersonal(Base):
    __tablename__ = "notas_personales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contenido: Mapped[str] = mapped_column(Text)
    categoria: Mapped[str] = mapped_column(String(20), default="nota")  # nota | mejora | recordatorio | tarea
    estado: Mapped[str] = mapped_column(String(20), default="activa")  # activa | cumplida | archivada
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    recordar_en: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # solo si categoria=recordatorio
    avisado: Mapped[bool] = mapped_column(Boolean, default=False)


async def inicializar_notas():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def guardar_nota(contenido: str, categoria: str = "nota", recordar_en: datetime | None = None) -> NotaPersonal:
    if categoria not in CATEGORIAS:
        categoria = "nota"
    async with async_session() as session:
        nota = NotaPersonal(contenido=contenido.strip(), categoria=categoria, recordar_en=recordar_en)
        session.add(nota)
        await session.commit()
        await session.refresh(nota)
        return nota


async def listar_notas(categoria: str | None = None, limite: int = 30) -> list[NotaPersonal]:
    async with async_session() as session:
        q = select(NotaPersonal).where(NotaPersonal.estado == "activa")
        if categoria:
            q = q.where(NotaPersonal.categoria == categoria)
        q = q.order_by(NotaPersonal.creado_en.desc()).limit(limite)
        return list((await session.execute(q)).scalars().all())


async def marcar_nota(texto: str, estado: str = "cumplida") -> int:
    """Marca por coincidencia de texto (o 'todas' dentro de la categoría dada). Devuelve cuántas."""
    texto = texto.strip().lower()
    async with async_session() as session:
        activas = list((await session.execute(
            select(NotaPersonal).where(NotaPersonal.estado == "activa")
        )).scalars().all())
        afectadas = [n for n in activas if texto == "todas" or texto in n.contenido.lower()]
        for n in afectadas:
            n.estado = estado
        await session.commit()
        return len(afectadas)


async def contexto_notas_recientes(dias_notas: int = 200, max_items: int = 12) -> str:
    """
    Bloque de texto para inyectar en el prompt: notas/mejoras recientes y
    recordatorios pendientes. Vacío si no hay nada — no ensucia el prompt
    con una sección "sin notas".
    """
    activas = await listar_notas(limite=max_items)
    if not activas:
        return ""
    lineas = []
    for n in activas:
        etiqueta = {"nota": "📝", "mejora": "🎯", "recordatorio": "⏰", "tarea": "☐"}.get(n.categoria, "📝")
        cuando = f" (para {n.recordar_en.strftime('%d-%b %H:%M')})" if n.recordar_en else ""
        lineas.append(f"{etiqueta} {n.contenido}{cuando}")
    return (
        "## Memoria personal de Ricardo (no es de DiMango, es de su día a día)\n"
        + "\n".join(lineas)
        + "\n\nÚsala con naturalidad si viene al caso — no la recites entera salvo que te pregunten "
          "qué tienes anotado. Si algo ya se resolvió o dejó de aplicar, dile que se lo marques cumplido."
    )


# ════════════════════════════════════════════════════════════
# Loop en segundo plano: recordatorios con hora
# ════════════════════════════════════════════════════════════

async def loop_recordatorios_personales(proveedor, intervalo: int = 60):
    """Revisa cada `intervalo` segundos si algún recordatorio con hora ya venció."""
    logger.info("Loop de recordatorios personales iniciado")
    while True:
        try:
            ahora = datetime.utcnow()
            async with async_session() as session:
                q = select(NotaPersonal).where(
                    NotaPersonal.estado == "activa",
                    NotaPersonal.categoria == "recordatorio",
                    NotaPersonal.avisado == False,  # noqa: E712
                    NotaPersonal.recordar_en != None,  # noqa: E711
                    NotaPersonal.recordar_en <= ahora,
                )
                vencidos = list((await session.execute(q)).scalars().all())

            if vencidos:
                from agent.maximus import OWNERS
                destino = next(iter(OWNERS), None)
                for nota in vencidos:
                    if not destino:
                        break
                    ok = await proveedor.enviar_mensaje(destino, f"⏰ {nota.contenido}")
                    if ok:
                        async with async_session() as session:
                            n = (await session.execute(
                                select(NotaPersonal).where(NotaPersonal.id == nota.id)
                            )).scalars().first()
                            if n:
                                n.avisado = True
                                await session.commit()
                        logger.info(f"[RECORDATORIO PERSONAL] Avisado: {nota.contenido}")
        except Exception as e:
            logger.error(f"[RECORDATORIO PERSONAL] Error en loop: {e}")
        await asyncio.sleep(intervalo)
