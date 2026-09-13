# agent/personal_autorizado.py — Personal de confianza que puede pedirle a
# Maximus dos acciones concretas en DiMangoToGo desde su propio WhatsApp.
#
# Por qué existe: hoy cada egreso de caja y cada comanda que hay que eliminar
# termina en una llamada a Ricardo pidiéndole la clave de Modo Jefe (E-001:
# un egreso de $10.550 le escaló). Esto delega esas dos cosas, acotadas y con
# registro, sin que la clave del jefe salga de sus manos.
#
# Reglas duras, en orden:
#   1. Falla cerrado. Si la tabla está vacía, esto no existe para nadie.
#   2. Solo dos acciones: EGRESO y ELIMINAR (comanda). Nada más.
#   3. Formato fijo, sin interpretación de IA. Una acción que borra una
#      comanda definitivamente no se decide adivinando qué quiso decir.
#   4. Topes de R-001: $50.000 por egreso, $200.000 al día por persona.
#   5. Tres claves erradas y el número queda bloqueado hasta que Ricardo lo
#      libere. Las claves son de 4 dígitos y siguen un patrón parecido entre
#      sí, así que el límite de intentos es lo que realmente las protege.
#   6. Todo queda registrado acá y se avisa por Telegram, siempre.

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import String, Integer, DateTime, Boolean, select, func
from sqlalchemy.orm import Mapped, mapped_column

from agent.memory import Base, async_session, engine

logger = logging.getLogger("agentkit")

TZ = ZoneInfo("America/Santiago")

# Topes de R-001, aprobados por Ricardo el 12-sep-2026.
EGRESO_MAX = 50_000
EGRESO_DIA_MAX = 200_000
INTENTOS_MAX = 3


class PersonalAutorizado(Base):
    __tablename__ = "personal_autorizado"

    telefono: Mapped[str] = mapped_column(String(20), primary_key=True)  # solo dígitos, con 56
    nombre: Mapped[str] = mapped_column(String(60))
    clave: Mapped[str] = mapped_column(String(12))
    local: Mapped[str] = mapped_column(String(20), default="playa")
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AccionPersonal(Base):
    """Bitácora de lo que hizo cada uno. Es la fuente del tope diario y la
    única forma de distinguir después un error de un abuso."""

    __tablename__ = "acciones_personal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(20))
    nombre: Mapped[str] = mapped_column(String(60))
    accion: Mapped[str] = mapped_column(String(20))          # egreso | eliminar | rechazo
    detalle: Mapped[str] = mapped_column(String(300), default="")
    monto: Mapped[int] = mapped_column(Integer, default=0)
    fecha_chile: Mapped[str] = mapped_column(String(10))     # YYYY-MM-DD en Arica
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def inicializar_personal():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _solo_digitos(t: str) -> str:
    return re.sub(r"\D", "", t or "")


def hoy_chile() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- consultas

async def buscar(telefono: str) -> PersonalAutorizado | None:
    """Devuelve la persona autorizada para ese número, o None. None significa
    que este módulo no aplica: el mensaje sigue su camino normal."""
    tel = _solo_digitos(telefono)
    if not tel:
        return None
    async with async_session() as s:
        p = await s.get(PersonalAutorizado, tel)
        return p if (p and p.activo) else None


async def egresos_del_dia(telefono: str) -> int:
    tel = _solo_digitos(telefono)
    async with async_session() as s:
        total = (await s.execute(
            select(func.coalesce(func.sum(AccionPersonal.monto), 0)).where(
                AccionPersonal.telefono == tel,
                AccionPersonal.accion == "egreso",
                AccionPersonal.ok.is_(True),
                AccionPersonal.fecha_chile == hoy_chile(),
            )
        )).scalar_one()
        return int(total or 0)


# ------------------------------------------------------------------ escritura

async def upsert_persona(telefono: str, nombre: str, clave: str, local: str = "playa") -> None:
    """Alta o cambio de clave. Es lo que Ricardo edita cuando entra o sale
    alguien del grupo."""
    tel = _solo_digitos(telefono)
    if not tel or not (clave or "").strip():
        return
    async with async_session() as s:
        p = await s.get(PersonalAutorizado, tel)
        if p:
            p.nombre = (nombre or p.nombre).strip()[:60]
            p.clave = clave.strip()[:12]
            p.local = (local or p.local).strip().lower()[:20]
            p.activo = True
            p.intentos_fallidos = 0
            p.bloqueado = False
        else:
            s.add(PersonalAutorizado(
                telefono=tel, nombre=(nombre or "").strip()[:60],
                clave=clave.strip()[:12], local=(local or "playa").strip().lower()[:20],
            ))
        await s.commit()


async def desactivar(telefono: str) -> bool:
    tel = _solo_digitos(telefono)
    async with async_session() as s:
        p = await s.get(PersonalAutorizado, tel)
        if not p:
            return False
        p.activo = False
        await s.commit()
        return True


async def desbloquear(telefono: str) -> bool:
    tel = _solo_digitos(telefono)
    async with async_session() as s:
        p = await s.get(PersonalAutorizado, tel)
        if not p:
            return False
        p.bloqueado = False
        p.intentos_fallidos = 0
        await s.commit()
        return True


async def registrar(telefono: str, nombre: str, accion: str, detalle: str,
                    monto: int = 0, ok: bool = True) -> None:
    async with async_session() as s:
        s.add(AccionPersonal(
            telefono=_solo_digitos(telefono), nombre=(nombre or "")[:60],
            accion=accion[:20], detalle=(detalle or "")[:300], monto=int(monto or 0),
            fecha_chile=hoy_chile(), ok=ok,
        ))
        await s.commit()


async def _sumar_intento_fallido(telefono: str) -> int:
    """Devuelve cuántos intentos fallidos lleva; al llegar a INTENTOS_MAX
    deja el número bloqueado."""
    tel = _solo_digitos(telefono)
    async with async_session() as s:
        p = await s.get(PersonalAutorizado, tel)
        if not p:
            return 0
        p.intentos_fallidos = (p.intentos_fallidos or 0) + 1
        if p.intentos_fallidos >= INTENTOS_MAX:
            p.bloqueado = True
        await s.commit()
        return p.intentos_fallidos


async def _limpiar_intentos(telefono: str) -> None:
    async with async_session() as s:
        p = await s.get(PersonalAutorizado, _solo_digitos(telefono))
        if p and p.intentos_fallidos:
            p.intentos_fallidos = 0
            await s.commit()


# -------------------------------------------------------------------- parser

AYUDA = (
    "Puedo hacerte dos cosas. Escríbelas tal cual, con el local y tu clave:\n\n"
    "1) Registrar un egreso de caja\n"
    "   EGRESO LOCAL PLAYA 12000 pan clave 1234\n"
    "   EGRESO LOCAL MALL 12000 pan clave 1234\n\n"
    "2) Eliminar una comanda\n"
    "   ELIMINAR LOCAL PLAYA 3F2A9C clave 1234\n\n"
    "El local va siempre. El código de la comanda son los 6 caracteres que\n"
    "aparecen en el panel."
)

# El local va opcional despues del verbo (EGRESO MALL 12000 ...). Es obligatorio
# para quien trabaja en los dos: sin eso la plata saldria de la caja equivocada
# y descuadraria las dos.
_RE_EGRESO = re.compile(
    r"^\s*egreso\s+(?:local\s+)?(?:(playa|mall)\s+)?\$?\s*([\d.\s]+)\s+(.+?)\s+clave\s*:?\s*(\w+)\s*$", re.IGNORECASE)
_RE_ELIMINAR = re.compile(
    r"^\s*eliminar\s+(?:local\s+)?(?:(playa|mall)\s+)?#?\s*([A-Za-z0-9]{4,12})\s+clave\s*:?\s*(\w+)\s*$", re.IGNORECASE)


def parsear(texto: str) -> dict | None:
    """Traduce el mensaje a una orden, o None si no es una orden para este
    módulo. Deliberadamente rígido: no adivina."""
    t = (texto or "").strip()
    if not t:
        return None

    m = _RE_EGRESO.match(t)
    if m:
        monto_txt = re.sub(r"[.\s]", "", m.group(2))
        if not monto_txt.isdigit():
            return {"accion": "error", "mensaje": "No entendí el monto. Ejemplo: EGRESO 12000 pan clave 1234"}
        return {"accion": "egreso", "monto": int(monto_txt),
                "local": (m.group(1) or "").lower(),
                "motivo": m.group(3).strip()[:120], "clave": m.group(4).strip()}

    m = _RE_ELIMINAR.match(t)
    if m:
        return {"accion": "eliminar", "codigo": m.group(2).strip().upper(),
                "local": (m.group(1) or "").lower(),
                "clave": m.group(3).strip()}

    primera = t.split()[0].lower().strip(":,.")
    if primera in ("egreso", "eliminar", "ayuda", "help", "menu", "menú"):
        return {"accion": "error", "mensaje": AYUDA}

    return None


# ------------------------------------------------------------- autorización

async def autorizar(persona: PersonalAutorizado, orden: dict) -> tuple[bool, str]:
    """Valida clave, bloqueo y topes. Devuelve (autorizado, motivo del rechazo)."""
    if persona.bloqueado:
        return False, ("Tu número está bloqueado por claves erradas. "
                       "Habla con Ricardo para reactivarlo.")

    if (orden.get("clave") or "").strip() != (persona.clave or "").strip():
        n = await _sumar_intento_fallido(persona.telefono)
        quedan = max(0, INTENTOS_MAX - n)
        if quedan == 0:
            return False, "Clave incorrecta. Tu número quedó bloqueado; avísale a Ricardo."
        return False, f"Clave incorrecta. Te quedan {quedan} intento(s) antes de que se bloquee."

    await _limpiar_intentos(persona.telefono)

    # El local es OBLIGATORIO siempre, decision de Ricardo: nunca se asume.
    # Un egreso en la caja equivocada descuadra las dos, y con dos locales el
    # que escribe no siempre esta donde el sistema cree que esta.
    verbo = "EGRESO" if orden["accion"] == "egreso" else "ELIMINAR"
    resto = ("12000 pan" if orden["accion"] == "egreso" else "3F2A9C")
    if not orden.get("local"):
        return False, ("Falta el local. Escríbelo siempre:\n"
                       f"  {verbo} LOCAL PLAYA {resto} clave ****\n"
                       f"  {verbo} LOCAL MALL {resto} clave ****")

    # Y tiene que ser el suyo. Si alguien cambia de local, lo cambia Ricardo
    # ("quitar a X" y alta de nuevo), no se decide desde el mensaje.
    if persona.local != "ambos" and orden["local"] != persona.local:
        return False, (f"Estás registrado en {persona.local}, no en {orden['local']}. "
                       "Si cambiaste de local, avísale a Ricardo.")

    if orden["accion"] == "egreso":
        monto = orden["monto"]
        if monto <= 0:
            return False, "El monto tiene que ser mayor a cero."
        if monto > EGRESO_MAX:
            return False, (f"Ese egreso es de ${monto:,.0f}".replace(",", ".") +
                           f" y tu tope por egreso es ${EGRESO_MAX:,.0f}".replace(",", ".") +
                           ". Este lo tiene que autorizar Ricardo.")
        ya = await egresos_del_dia(persona.telefono)
        if ya + monto > EGRESO_DIA_MAX:
            return False, (f"Hoy ya llevas ${ya:,.0f}".replace(",", ".") +
                           f" en egresos y el tope diario es ${EGRESO_DIA_MAX:,.0f}".replace(",", ".") +
                           ". Este lo tiene que autorizar Ricardo.")

    return True, ""


# -------------------------------------------------------------------- ejecución

DIMANGOTOGO_EGRESO_URL = "https://dimangotogo.base44.app/functions/maximusEgreso"
DIMANGOTOGO_ELIMINAR_URL = "https://dimangotogo.base44.app/functions/maximusEliminarOrden"


async def _avisar_a_ricardo(texto: str) -> None:
    """Aviso directo al Telegram de Ricardo. Las funciones de Base44 ya avisan
    al grupo de garzones; esto es aparte y a propósito: Ricardo pidió enterarse
    siempre, y un grupo no es lo mismo que su chat."""
    try:
        from agent import telegram_maximus as tg
        if not tg.configurado():
            return
        for chat_id in tg.OWNER_CHAT_IDS:
            await tg.enviar_mensaje(chat_id, texto)
    except Exception as e:
        logger.warning("personal_autorizado: no pude avisar por Telegram: %s", e)


async def _llamar_togo(url: str, payload: dict) -> tuple[bool, str]:
    import os
    import httpx

    secreto = os.getenv("DIMANGOTOGO_MAXIMUS_SECRET", "")
    if not secreto:
        return False, ("No puedo escribir en DiMangoToGo: falta "
                       "DIMANGOTOGO_MAXIMUS_SECRET en el servidor. Avísale a Ricardo.")
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(url, json=payload, headers={"x-maximus-secret": secreto})
    except httpx.RequestError as e:
        return False, f"No pude conectar con DiMangoToGo: {e}"
    if r.status_code != 200:
        return False, f"DiMangoToGo respondió {r.status_code}."
    d = r.json()
    if not d.get("ok"):
        return False, d.get("error", "No se pudo.")
    return True, d.get("mensaje", "Listo.")


async def procesar(telefono: str, texto: str) -> str | None:
    """Punto de entrada. Devuelve la respuesta para el trabajador, o None si
    este mensaje no es para este módulo (y entonces sigue su camino normal).

    Falla cerrado: si el número no está en la tabla, siempre devuelve None."""
    persona = await buscar(telefono)
    if not persona:
        return None

    orden = parsear(texto)
    if orden is None:
        # Está autorizado pero no pidió ninguna de las dos acciones: que el
        # mensaje siga al flujo normal en vez de comerse la conversación.
        return None

    if orden["accion"] == "error":
        return orden["mensaje"]

    ok, motivo = await autorizar(persona, orden)
    if not ok:
        await registrar(persona.telefono, persona.nombre, "rechazo",
                        f"{orden['accion']}: {motivo}", ok=False)
        await _avisar_a_ricardo(
            "🚫 INTENTO RECHAZADO\n"
            f"👤 {persona.nombre} ({persona.telefono})\n"
            f"📋 Pidió: {orden['accion']}\n"
            f"❌ Motivo: {motivo}")
        return motivo

    if orden["accion"] == "egreso":
        exito, mensaje = await _llamar_togo(DIMANGOTOGO_EGRESO_URL, {
            "local": orden.get("local") or persona.local,
            "monto": orden["monto"],
            "motivo": orden["motivo"],
            "autorizado_por": persona.nombre,
        })
        await registrar(persona.telefono, persona.nombre, "egreso",
                        f"{orden['motivo']} — {mensaje[:150]}",
                        monto=orden["monto"] if exito else 0, ok=exito)
        if exito:
            queda = EGRESO_DIA_MAX - await egresos_del_dia(persona.telefono)
            monto_fmt = f"${orden['monto']:,.0f}".replace(",", ".")
            queda_fmt = f"${queda:,.0f}".replace(",", ".")
            await _avisar_a_ricardo(
                "💸 EGRESO DE CAJA (personal autorizado)\n"
                f"👤 {persona.nombre}\n"
                f"💵 {monto_fmt} · {orden['motivo']}\n"
                f"🏪 {persona.local}\n"
                f"📊 Le queda {queda_fmt} de su tope diario")
            return f"{mensaje}\n\nTe quedan {queda_fmt} de tu tope de hoy."
        return f"No se pudo registrar el egreso: {mensaje}"

    if orden["accion"] == "eliminar":
        exito, mensaje = await _llamar_togo(DIMANGOTOGO_ELIMINAR_URL, {
            "codigo": orden["codigo"],
            "local": orden.get("local") or persona.local,
            "autorizado_por": persona.nombre,
            "telefono": persona.telefono,
        })
        await registrar(persona.telefono, persona.nombre, "eliminar",
                        f"comanda {orden['codigo']} — {mensaje[:150]}", ok=exito)
        # El aviso a Ricardo de la eliminación lo manda maximusEliminarOrden con
        # el detalle completo de la comanda; acá no lo duplicamos.
        return mensaje if exito else f"No se pudo eliminar: {mensaje}"

    return None


# --------------------------------------------------- administración (Ricardo)

_RE_CLAVE = re.compile(r"^\s*clave\s+(?:de\s+)?(.+?)\s+(\w{3,12})\s*$", re.IGNORECASE)
_RE_BLOQ = re.compile(r"^\s*(bloquear|desbloquear|quitar)\s+(?:a\s+)?(.+?)\s*$", re.IGNORECASE)

AYUDA_ADMIN = (
    "Personal autorizado — comandos:\n"
    "  autorizados            → ver la lista y el gasto de hoy\n"
    "  clave de Noemi 7821    → cambiar su clave\n"
    "  bloquear a Carlos      → deja de poder pedir\n"
    "  desbloquear a Carlos   → lo reactiva\n"
    "  quitar a Carlos        → lo saca de la lista"
)


async def _por_nombre(nombre: str) -> PersonalAutorizado | None:
    n = (nombre or "").strip().lower()
    async with async_session() as s:
        for p in (await s.execute(select(PersonalAutorizado))).scalars().all():
            if p.nombre.lower() == n or p.nombre.lower().startswith(n):
                return p
    return None


async def listar_texto() -> str:
    async with async_session() as s:
        gente = list((await s.execute(select(PersonalAutorizado))).scalars().all())
    if not gente:
        return "No hay nadie autorizado todavía."
    lineas = ["Personal autorizado:"]
    for p in gente:
        gasto = await egresos_del_dia(p.telefono)
        estado = "🚫 bloqueado" if p.bloqueado else ("✅" if p.activo else "⏸️ inactivo")
        lineas.append(f"  {estado} {p.nombre} (+{p.telefono}) · {p.local} · "
                      f"hoy ${gasto:,.0f}".replace(",", ".") + f" de ${EGRESO_DIA_MAX:,.0f}".replace(",", "."))
    return "\n".join(lineas)


async def procesar_admin(texto: str) -> str | None:
    """Comandos que solo corre Ricardo, desde su propio WhatsApp o Telegram.
    Devuelve None si el mensaje no es uno de estos (sigue a Maximus normal)."""
    t = (texto or "").strip()
    bajo = t.lower()

    if bajo in ("autorizados", "personal autorizado", "quien esta autorizado",
                "quién está autorizado", "lista autorizados"):
        return await listar_texto()

    m = _RE_CLAVE.match(t)
    if m:
        nombre, clave = m.group(1).strip(), m.group(2).strip()
        p = await _por_nombre(nombre)
        if not p:
            return f"No tengo a nadie llamado «{nombre}» en la lista.\n\n{AYUDA_ADMIN}"
        await upsert_persona(p.telefono, p.nombre, clave, p.local)
        return (f"Listo: la clave de {p.nombre} ahora es {clave}. "
                "Quedó desbloqueado y con los intentos en cero.")

    m = _RE_BLOQ.match(t)
    if m:
        accion, nombre = m.group(1).lower(), m.group(2).strip()
        p = await _por_nombre(nombre)
        if not p:
            return f"No tengo a nadie llamado «{nombre}» en la lista."
        if accion == "desbloquear":
            await desbloquear(p.telefono)
            return f"{p.nombre} quedó desbloqueado."
        if accion == "quitar":
            await desactivar(p.telefono)
            return f"{p.nombre} salió de la lista: ya no puede pedir nada."
        async with async_session() as s:
            fila = await s.get(PersonalAutorizado, p.telefono)
            fila.bloqueado = True
            await s.commit()
        return f"{p.nombre} quedó bloqueado."

    return None
