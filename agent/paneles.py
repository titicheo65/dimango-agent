# agent/paneles.py — Datos para los paneles del Maximus Command Center
#
# Por qué existe: el Command Center (pantalla dedicada) pinta paneles con datos
# en vivo. Estas funciones consultan las MISMAS fuentes que ya usa Maximus
# (DiMangoToGo, iCloud, Gmail), pero devuelven JSON limpio para la pantalla.
# El secreto de DiMangoToGo y las claves de correo viven solo acá en el
# servidor — el navegador nunca los ve, solo tiene el MAXIMUS_CHAT_TOKEN.

import os
import asyncio
import logging

logger = logging.getLogger("agentkit")


# ── Ventas / Top productos / Stock (DiMangoToGo) ──────────────────────────
async def ventas_panel() -> dict:
    """Ventas de hoy por local + medios de pago + top productos + stock bajo mínimo."""
    from agent.maximus import DIMANGOTOGO_URL, DIMANGOTOGO_SECRET
    if not DIMANGOTOGO_SECRET:
        return {"error": "DiMangoToGo no configurado en el servidor."}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(DIMANGOTOGO_URL, json={}, headers={"x-maximus-secret": DIMANGOTOGO_SECRET})
    except httpx.RequestError as e:
        return {"error": f"No pude conectar con DiMangoToGo: {e}"}
    if r.status_code != 200:
        return {"error": f"DiMangoToGo respondió {r.status_code}"}

    d = r.json()
    resumen = d.get("resumen", {})
    por_local_raw = resumen.get("por_local", {})
    por_local = {
        "playa": por_local_raw.get("playa", {"monto": 0, "ventas": 0}),
        "mall": por_local_raw.get("mall", {"monto": 0, "ventas": 0}),
    }

    top = [
        {"nombre": p.get("nombre", "—"), "cantidad": p.get("cantidad", 0), "monto": p.get("monto", 0)}
        for p in d.get("productos_vendidos", [])[:12]
    ]

    stock_bajo = []
    for s in d.get("stock", []):
        for local in ("playa", "mall"):
            v = s.get(local)
            if v and v.get("cantidad") is not None and v.get("minimo") is not None and v["cantidad"] < v["minimo"]:
                stock_bajo.append({"nombre": s.get("nombre", "—"), "local": local,
                                   "cantidad": v["cantidad"], "minimo": v["minimo"]})

    return {
        "por_local": por_local,
        "medios_pago": resumen.get("por_medio_pago", {}),
        "total": resumen.get("monto_total", 0),
        "propinas": resumen.get("propinas_total", 0),
        "top_productos": top,
        "stock_bajo": stock_bajo,
    }


# ── Checklist de reposición (DiMangoToGo) ─────────────────────────────────
async def checklist_panel(local: str = "playa") -> dict:
    from agent.maximus import DIMANGOTOGO_CHECKLIST_URL, DIMANGOTOGO_SECRET
    if not DIMANGOTOGO_SECRET:
        return {"error": "DiMangoToGo no configurado en el servidor."}

    import httpx
    payload = {"local": local} if local else {}
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(DIMANGOTOGO_CHECKLIST_URL, json=payload,
                             headers={"x-maximus-secret": DIMANGOTOGO_SECRET})
    except httpx.RequestError as e:
        return {"error": f"No pude conectar con DiMangoToGo: {e}"}
    if r.status_code != 200:
        return {"error": f"DiMangoToGo respondió {r.status_code}"}

    d = r.json()
    insumos = []
    for i in d.get("insumos_a_reponer", [])[:20]:
        cant = i.get("a_reponer", 0)
        try:
            cant = int(cant) if float(cant) == int(float(cant)) else round(float(cant), 2)
        except (ValueError, TypeError):
            pass
        insumos.append({"cantidad": cant, "unidad": i.get("unidad", ""), "insumo": i.get("insumo", "—"),
                        "area": i.get("area", "")})
    return {"local": local, "cobertura_pct": d.get("cobertura_recetas_pct"), "insumos_a_reponer": insumos}


# ── Alertas de venta activas ──────────────────────────────────────────────
async def alertas_panel() -> dict:
    try:
        from agent.alertas_venta import listar_alertas_activas
        activas = await listar_alertas_activas()
        alertas = [{"producto": a.producto, "umbral": a.umbral, "local": a.local or ""} for a in activas]
    except Exception as e:
        logger.warning(f"[PANELES] alertas: {e}")
        alertas = []
    return {"alertas": alertas}


# ── Calendario (iCloud iCal) ──────────────────────────────────────────────
async def calendario_panel() -> dict:
    from agent.maximus import ICLOUD_CALENDAR_URLS
    if not ICLOUD_CALENDAR_URLS:
        return {"error": "Calendario no configurado en el servidor.", "eventos": []}

    import httpx
    import icalendar
    import recurring_ical_events
    from datetime import date, timedelta

    hoy = date.today()
    fin = hoy + timedelta(days=14)
    eventos_raw = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        for cal_url in ICLOUD_CALENDAR_URLS:
            url = cal_url.replace("webcal://", "https://", 1)
            try:
                r = await c.get(url)
                if r.status_code != 200:
                    continue
                cal = icalendar.Calendar.from_ical(r.content)
                eventos_raw.extend(recurring_ical_events.of(cal).between(hoy, fin + timedelta(days=1)))
            except Exception as e:
                logger.warning(f"[PANELES] calendario {url[:40]}: {e}")

    def clave(ev):
        dt = ev.get("DTSTART").dt
        return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)

    try:
        eventos_raw.sort(key=clave)
    except Exception:
        pass

    _DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    eventos = []
    for ev in eventos_raw[:12]:
        titulo = str(ev.get("SUMMARY", "(sin título)"))
        dt = ev.get("DTSTART").dt
        if hasattr(dt, "hour"):
            cuando = f"{_DIAS[dt.weekday()]} {dt.strftime('%d-%m %H:%M')}"
        else:
            cuando = f"{_DIAS[dt.weekday()]} {dt.strftime('%d-%m')}"
        eventos.append({"cuando": cuando, "titulo": titulo})
    return {"eventos": eventos}


# ── Correos recientes (Gmail IMAP, solo lectura) ──────────────────────────
def _leer_correos_sync(max_por_cuenta: int = 6) -> list:
    import imaplib
    import email
    from email.header import decode_header

    def decodificar(valor):
        if not valor:
            return ""
        out = ""
        for txt, cod in decode_header(valor):
            out += txt.decode(cod or "utf-8", errors="replace") if isinstance(txt, bytes) else txt
        return out

    cuentas = [
        ("titicheo", os.getenv("CORREO_TITICHEO_USER", ""), os.getenv("CORREO_TITICHEO_APP_PASSWORD", "")),
        ("presupuesto", os.getenv("CORREO_PRESUPUESTO_USER", ""), os.getenv("CORREO_PRESUPUESTO_APP_PASSWORD", "")),
    ]
    correos = []
    for etiqueta, user, pwd in cuentas:
        if not user or not pwd:
            continue
        conexion = None
        try:
            conexion = imaplib.IMAP4_SSL("imap.gmail.com")
            conexion.login(user, pwd)
            conexion.select("INBOX", readonly=True)
            estado, datos = conexion.uid("search", None, "ALL")
            if estado != "OK" or not datos or not datos[0]:
                continue
            uids = datos[0].split()[-max_por_cuenta:]
            for uid in reversed(uids):
                est, msg_data = conexion.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if est != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                de = decodificar(msg.get("From", ""))
                # "Nombre <correo>" → solo el nombre si viene
                if "<" in de:
                    de = de.split("<")[0].strip().strip('"') or de
                correos.append({
                    "de": de[:40] or "(sin remitente)",
                    "asunto": decodificar(msg.get("Subject", "(sin asunto)"))[:90],
                    "cuenta": etiqueta,
                })
        except Exception as e:
            logger.warning(f"[PANELES] correo {etiqueta}: {e}")
        finally:
            if conexion is not None:
                try:
                    conexion.logout()
                except Exception:
                    pass
    return correos[:10]


async def correo_panel() -> dict:
    correos = await asyncio.to_thread(_leer_correos_sync)
    return {"correos": correos}


# ── Grafo de memoria (memoria atómica de Maximus) ─────────────────────────
def _resumen_cuerpo(cuerpo: str) -> str:
    """Primera línea con contenido del cuerpo, para el tooltip del nodo."""
    for linea in (cuerpo or "").splitlines():
        t = linea.strip().lstrip("#").strip()
        if len(t) > 3:
            return t[:160]
    return ""


async def grafo_panel() -> dict:
    """Nodos + aristas de la memoria atómica de Maximus, para el grafo visual.
    Devuelve una versión LIGERA (sin el cuerpo completo) — solo lo que la
    pantalla necesita para dibujar y mostrar un tooltip."""
    import json
    from agent.maximus import MEMORY_DIR

    ruta = MEMORY_DIR / "memoria" / "indice.json"
    try:
        data = await asyncio.to_thread(lambda: json.loads(ruta.read_text(encoding="utf-8")))
    except Exception as e:
        logger.warning(f"[PANELES] grafo: {e}")
        return {"error": "No pude leer la memoria de Maximus.", "nodos": [], "aristas": []}

    nodos = [
        {
            "id": n.get("id"),
            "titulo": n.get("titulo", ""),
            "tipo": n.get("tipo", "otro"),
            "color": n.get("color"),
            "grado": n.get("grado", 1),
            "tags": n.get("tags", [])[:5],
            "resumen": _resumen_cuerpo(n.get("cuerpo", "")),
        }
        for n in data.get("nodos", [])
        if n.get("id")
    ]
    aristas = [
        {"de": a.get("de"), "a": a.get("a"), "rel": a.get("rel", "")}
        for a in data.get("aristas", [])
        if a.get("de") and a.get("a")
    ]
    return {"nodos": nodos, "aristas": aristas}


# ── Agentes de Maximus (núcleo + canales + automatizaciones) ──────────────
def _estado_tareas_windows(nombres: list) -> dict:
    """Estado real de las Tareas Programadas de Windows (schtasks). En Mac/Linux
    o si falla, devuelve vacío y esas tareas quedan como 'desconocido'."""
    import subprocess
    import csv
    import io
    out = {}
    try:
        r = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/nh"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return out
        mapa = {"ready": "activo", "running": "corriendo", "disabled": "apagado"}
        for row in csv.reader(io.StringIO(r.stdout)):
            if len(row) >= 3:
                nombre = row[0].lstrip("\\").strip()
                estado = row[2].strip().lower()
                if nombre in nombres:
                    out[nombre] = mapa.get(estado, estado or "desconocido")
    except Exception as e:
        logger.info(f"[PANELES] schtasks no disponible: {e}")
    return out


async def agentes_panel() -> dict:
    """Roster de los 'agentes' de Maximus con su estado."""
    agentes = [
        {"nombre": "Cerebro", "rol": "Orquestador — Claude + herramientas", "estado": "activo", "grupo": "Núcleo"},
        {"nombre": "Voz", "rol": "Escucha y habla (STT + TTS)", "estado": "activo", "grupo": "Núcleo"},
        {"nombre": "Memoria", "rol": "Grafo de conocimiento", "estado": "activo", "grupo": "Núcleo"},
        {"nombre": "Visión", "rol": "Lee pantallas e imágenes", "estado": "activo", "grupo": "Núcleo"},
    ]

    def canal(nombre, envkey, rol):
        return {"nombre": nombre, "rol": rol, "estado": "activo" if os.getenv(envkey) else "apagado", "grupo": "Canales"}

    agentes += [
        {"nombre": "WhatsApp", "rol": "Clientes + Maximus", "estado": "activo", "grupo": "Canales"},
        canal("Instagram", "IG_ACCESS_TOKEN", "DM Instagram"),
        canal("Messenger", "MESSENGER_PAGE_TOKEN", "Mensajes Facebook"),
        canal("Telegram", "TELEGRAM_BOT_TOKEN", "Canal remoto de Maximus"),
    ]

    agentes += [
        {"nombre": "Alertas de venta", "rol": "Vigila productos y umbrales", "estado": "activo", "grupo": "Automatizaciones"},
        {"nombre": "Colación", "rol": "Control de descansos del personal", "estado": "activo", "grupo": "Automatizaciones"},
        {"nombre": "Checklist operativo", "rol": "Reenvíos y escalamiento", "estado": "activo", "grupo": "Automatizaciones"},
    ]

    # Tareas programadas de Windows — estado real
    tareas = {
        "Maximus-Advisor": "Advisor diario (3 recomendaciones)",
        "Maximus-Correo": "Aviso de correo nuevo",
        "Maximus-Vigilante": "Vigilante del sistema",
        "Maximus-Sync-Memoria": "Sincroniza memoria",
        "DimangoChecklistReposicion": "Checklist de reposición",
    }
    estados = await asyncio.to_thread(_estado_tareas_windows, list(tareas.keys()))
    for tn, rol in tareas.items():
        agentes.append({"nombre": rol, "rol": "Tarea programada", "estado": estados.get(tn, "desconocido"), "grupo": "Automatizaciones"})

    activos = sum(1 for a in agentes if a["estado"] in ("activo", "corriendo"))
    return {"agentes": agentes, "activos": activos, "total": len(agentes)}


# ── Delegaciones (tareas que Maximus mandó a sus agentes) ─────────────────
async def delegaciones_panel() -> dict:
    from datetime import timezone
    from agent.maximus import TZ_CHILE
    from agent.delegaciones import listar_delegaciones
    ds = await listar_delegaciones(30)
    out = []
    for d in ds:
        try:
            cuando = d.creado_en.replace(tzinfo=timezone.utc).astimezone(TZ_CHILE).strftime("%d-%m %H:%M")
        except Exception:
            cuando = ""
        out.append({"agente": d.agente, "tarea": d.tarea, "estado": d.estado,
                    "resultado": d.resultado, "cuando": cuando})
    return {"delegaciones": out}
