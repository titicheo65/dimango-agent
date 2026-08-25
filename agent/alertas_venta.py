# agent/alertas_venta.py — Alertas de venta creadas por conversación
#
# Ricardo le pide a Maximus "avísame cuando se venda un fettuccine" o "cuando
# se vendan 5 bife chorizo", y queda corriendo sola hasta que le diga que pare.
# Comparte la misma base de datos que agent/memory.py (mismo Base / async_session).

import asyncio
import logging
from datetime import datetime, date

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, select

from agent.memory import Base, async_session, engine

logger = logging.getLogger("agentkit")

# Las alertas avisan por WhatsApp al primer número de MAXIMUS_OWNER_PHONES —
# el canal confirmado funcionando hoy. Si algún día hace falta que respeten
# el canal de origen (Telegram también), hay que pasar el chat_id al crear
# la alerta en vez de resolverlo acá.


class AlertaVenta(Base):
    __tablename__ = "alertas_venta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    producto: Mapped[str] = mapped_column(String(200))       # texto a buscar, substring, sin mayúsculas
    umbral: Mapped[int] = mapped_column(Integer, default=1)  # cuántas unidades disparan el aviso
    local: Mapped[str] = mapped_column(String(20), default="")  # "playa" | "mall" | "" = ambos
    estado: Mapped[str] = mapped_column(String(20), default="activa")  # activa | cancelada
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ultimo_aviso_fecha: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD, evita repetir el mismo día


async def inicializar_alertas():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def crear_alerta(producto: str, umbral: int = 1, local: str = "") -> AlertaVenta:
    async with async_session() as session:
        alerta = AlertaVenta(producto=producto.strip().lower(), umbral=max(1, umbral), local=local)
        session.add(alerta)
        await session.commit()
        await session.refresh(alerta)
        return alerta


async def listar_alertas_activas() -> list[AlertaVenta]:
    async with async_session() as session:
        q = select(AlertaVenta).where(AlertaVenta.estado == "activa").order_by(AlertaVenta.creado_en.desc())
        return list((await session.execute(q)).scalars().all())


async def cancelar_alertas(texto: str) -> int:
    """Cancela por coincidencia de texto en el producto (o 'todas'). Devuelve cuántas canceló."""
    texto = texto.strip().lower()
    async with async_session() as session:
        activas = list((await session.execute(
            select(AlertaVenta).where(AlertaVenta.estado == "activa")
        )).scalars().all())
        afectadas = [a for a in activas if texto == "todas" or texto in a.producto]
        for a in afectadas:
            a.estado = "cancelada"
        await session.commit()
        return len(afectadas)


async def marcar_avisada_hoy(alerta_id: int):
    async with async_session() as session:
        alerta = (await session.execute(
            select(AlertaVenta).where(AlertaVenta.id == alerta_id)
        )).scalars().first()
        if alerta:
            alerta.ultimo_aviso_fecha = date.today().isoformat()
            await session.commit()


# ════════════════════════════════════════════════════════════
# Loop en segundo plano: revisa las alertas activas contra la venta real
# ════════════════════════════════════════════════════════════

async def _venta_de_hoy(local: str = "") -> dict:
    """Cantidad vendida hoy por producto (nombre en minúsculas -> cantidad)."""
    import httpx
    from agent.maximus import DIMANGOTOGO_URL, DIMANGOTOGO_SECRET

    if not DIMANGOTOGO_SECRET:
        return {}
    payload = {"local": local} if local else {}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(DIMANGOTOGO_URL, json=payload, headers={"x-maximus-secret": DIMANGOTOGO_SECRET})
        if r.status_code != 200:
            logger.warning(f"[ALERTA VENTA] DiMangoToGo respondió {r.status_code}")
            return {}
        d = r.json()
    except Exception as e:
        logger.warning(f"[ALERTA VENTA] No se pudo consultar venta: {e}")
        return {}

    totales: dict[str, int] = {}
    for p in d.get("productos_vendidos", []):
        nombre = (p.get("nombre") or "").strip().lower()
        totales[nombre] = totales.get(nombre, 0) + int(p.get("cantidad") or 0)
    return totales


async def loop_alertas_venta(proveedor, intervalo: int = 90):
    """Revisa cada `intervalo` segundos si alguna alerta activa se cumplió."""
    logger.info("Loop de alertas de venta iniciado")
    while True:
        try:
            activas = await listar_alertas_activas()
            hoy = date.today().isoformat()
            pendientes_hoy = [a for a in activas if a.ultimo_aviso_fecha != hoy]

            if pendientes_hoy:
                from agent.maximus import OWNERS
                destino = next(iter(OWNERS), None)

                cache_venta: dict[str, dict] = {}
                for alerta in pendientes_hoy:
                    if alerta.local not in cache_venta:
                        cache_venta[alerta.local] = await _venta_de_hoy(alerta.local)
                    venta = cache_venta[alerta.local]

                    cantidad = sum(v for nombre, v in venta.items() if alerta.producto in nombre)
                    if cantidad >= alerta.umbral and destino:
                        loc_txt = f" en {alerta.local}" if alerta.local else ""
                        mensaje = (
                            f"🔔 Se cumplió tu alerta: {cantidad}x \"{alerta.producto}\" "
                            f"vendidos hoy{loc_txt} (umbral {alerta.umbral}).\n"
                            f"Sigue activa — dime \"deja de avisarme de {alerta.producto}\" para pararla."
                        )
                        ok = await proveedor.enviar_mensaje(destino, mensaje)
                        if ok:
                            await marcar_avisada_hoy(alerta.id)
                            logger.info(f"[ALERTA VENTA] Avisado: {alerta.producto} ({cantidad}/{alerta.umbral})")
        except Exception as e:
            logger.error(f"[ALERTA VENTA] Error en loop: {e}")
        await asyncio.sleep(intervalo)
