# agent/maximus.py — Rol privado: Maximus, el gerente virtual de Ricardo

"""
Cuando escribe Ricardo (y solo Ricardo), el agente deja de ser atención al
cliente y pasa a ser Maximus: su estratega de negocio, con la memoria completa
de ~/harvey cargada como system prompt.

Diseño deliberado:
- Si MAXIMUS_OWNER_PHONES está vacío, este módulo no se activa nunca y el
  comportamiento del agente es idéntico al de siempre. Falla cerrado.
- La memoria NO se copia acá. Se lee del directorio fuente (L-004: una sola
  fuente de verdad). En el servidor eso es un clon de solo lectura del repo.
"""

import os
import logging
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

TZ_CHILE = ZoneInfo("America/Santiago")

# Números autorizados a hablar con Maximus. Coma-separados en .env.
# Vacío = rol desactivado.
_OWNERS_RAW = os.getenv("MAXIMUS_OWNER_PHONES", "")
OWNERS = {
    t.strip().lstrip("+").replace(" ", "").replace("-", "")
    for t in _OWNERS_RAW.split(",")
    if t.strip()
}

# Directorio de la memoria (los seis archivos). En el Mac de Ricardo: ~/harvey
MEMORY_DIR = Path(os.getenv("MAXIMUS_MEMORY_DIR", str(Path.home() / "harvey")))

# Orden de carga definido en CLAUDE.md. MEMORY.md manda sobre los demás.
ARCHIVOS_MEMORIA = [
    "IDENTITY.md",
    "SOUL.md",
    "USER.md",
    "BRAIN.md",
    "MEMORY.md",
    "MENTORS.md",
]

# Default: el mismo modelo que ya usa el agente y que sabemos que funciona con
# esta API key. Para subirlo a Opus, cambiar MAXIMUS_MODEL en .env — no lo pongo
# por defecto porque no verifiqué que la cuenta tenga acceso.
MODELO = os.getenv("MAXIMUS_MODEL", "claude-sonnet-4-6")
MODELO_FALLBACK = "claude-sonnet-4-6"

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Cache de la memoria: se recarga sola cuando cambia algún archivo
_cache_texto: str | None = None
_cache_firma: tuple | None = None


def normalizar_telefono(telefono: str) -> str:
    """Deja el número en el formato que manda Meta: solo dígitos, sin +."""
    return "".join(c for c in (telefono or "") if c.isdigit())


def es_maximus(telefono: str) -> bool:
    """¿Este número tiene derecho a hablar con Maximus?"""
    if not OWNERS:
        return False
    return normalizar_telefono(telefono) in {normalizar_telefono(o) for o in OWNERS}


def _firma_archivos() -> tuple:
    """Huella (nombre, mtime, tamaño) de los seis archivos, para invalidar el cache."""
    firma = []
    for nombre in ARCHIVOS_MEMORIA:
        ruta = MEMORY_DIR / nombre
        try:
            st = ruta.stat()
            firma.append((nombre, st.st_mtime, st.st_size))
        except OSError:
            firma.append((nombre, 0, 0))
    return tuple(firma)


def cargar_memoria() -> str:
    """
    Lee los seis archivos y los devuelve concatenados.
    Se cachea hasta que alguno cambie en disco.
    """
    global _cache_texto, _cache_firma

    firma = _firma_archivos()
    if _cache_texto is not None and firma == _cache_firma:
        return _cache_texto

    partes = []
    faltantes = []
    for nombre in ARCHIVOS_MEMORIA:
        ruta = MEMORY_DIR / nombre
        try:
            contenido = ruta.read_text(encoding="utf-8")
            partes.append(f"===== {nombre} =====\n{contenido}")
        except OSError:
            faltantes.append(nombre)

    if faltantes:
        logger.warning(f"[MAXIMUS] Archivos de memoria no encontrados en {MEMORY_DIR}: {faltantes}")

    _cache_texto = "\n\n".join(partes)
    _cache_firma = firma
    logger.info(f"[MAXIMUS] Memoria cargada desde {MEMORY_DIR} ({len(_cache_texto)} caracteres)")
    return _cache_texto


# ── Herramientas: los datos vivos NO se guardan en memoria, se consultan ──
# Capa 3 del diseño. Solo fuentes sin autenticación: las que la necesitan
# (Gmail, Calendar, DiMangoToGo) viven donde están sus llaves, no acá.

HERRAMIENTAS = [
    {
        "name": "indicadores_chile",
        "description": (
            "Valor de HOY del dólar observado, euro, UF, UTM, IPC y otros "
            "indicadores económicos chilenos. Úsala SIEMPRE que te pregunten por "
            "el precio del dólar, del euro, la UF o la UTM: son datos vivos que "
            "cambian a diario y NO están en tu memoria. Fuente: mindicador.cl "
            "(Banco Central)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "clima",
        "description": (
            "Clima actual y del día para una ciudad. Úsala cuando pregunten por "
            "el tiempo, la temperatura o si va a llover. Por defecto Arica."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ciudad": {
                    "type": "string",
                    "enum": ["arica", "santiago", "madrid", "milano", "washington"],
                    "description": "Ciudad. Si no la dicen, usa arica.",
                }
            },
        },
    },
    {
        "name": "ventas_dimango",
        "description": (
            "Ventas reales de DiMangoToGo: monto total, medios de pago, "
            "productos vendidos con cantidad y monto, y stock por producto y "
            "local (solo productos con control de stock activado). Úsala "
            "SIEMPRE que pregunten cuánto se vendió, qué se vendió, cuánto "
            "stock queda, o pidan un número de venta de hoy o de un rango de "
            "fechas — son datos vivos, no están en tu memoria. Por defecto: "
            "hoy, ambos locales."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicio": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Si no la dicen, usa hoy.",
                },
                "fecha_fin": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Si no la dicen, igual a fecha_inicio.",
                },
                "local": {
                    "type": "string",
                    "enum": ["playa", "mall"],
                    "description": "Si no lo dicen, trae ambos locales.",
                },
            },
        },
    },
    {
        "name": "checklist_dimango",
        "description": (
            "Insumos a reponer según lo vendido — mismo cálculo que la "
            "pantalla /Checklist de DiMangoToGo (venta × receta). Úsala "
            "cuando pregunten qué pedir, qué reponer, o cuánto insumo se "
            "consumió, por área (COCINA/BAR/PASTELERIA/DESPACHO) y por "
            "local. También devuelve qué % de lo vendido tiene receta "
            "vinculada y qué productos NO la tienen — sin receta, esos "
            "insumos no se están contando, dilo si preguntan por qué falta "
            "algo. Por defecto: hoy, Playa, todas las áreas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicio": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Si no la dicen, usa hoy.",
                },
                "fecha_fin": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Si no la dicen, igual a fecha_inicio.",
                },
                "local": {
                    "type": "string",
                    "enum": ["playa", "mall"],
                    "description": "Si no lo dicen, usa playa.",
                },
                "area": {
                    "type": "string",
                    "enum": ["COCINA", "BAR", "PASTELERIA", "DESPACHO"],
                    "description": "Si no la dicen, trae todas las áreas.",
                },
            },
        },
    },
    {
        "name": "crear_alerta_venta",
        "description": (
            "Crea una alerta que avisa por WhatsApp cuando se vende cierta "
            "cantidad de un producto. Úsala cuando Ricardo diga 'avísame "
            "cuando se venda X' o 'avísame cuando se vendan N de X'. Queda "
            "activa todos los días hasta que la cancele — no es de una sola "
            "vez. Avisa como máximo una vez por día por alerta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "producto": {
                    "type": "string",
                    "description": "Nombre o parte del nombre del producto a vigilar, ej: 'fettuccine'.",
                },
                "umbral": {
                    "type": "integer",
                    "description": "Cuántas unidades vendidas disparan el aviso. Si no lo dicen, usa 1.",
                },
                "local": {
                    "type": "string",
                    "enum": ["playa", "mall"],
                    "description": "Si no lo dicen, cuenta ambos locales juntos.",
                },
            },
            "required": ["producto"],
        },
    },
    {
        "name": "listar_alertas_venta",
        "description": "Lista las alertas de venta activas ahora mismo.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancelar_alerta_venta",
        "description": (
            "Cancela una o más alertas de venta activas. Úsala cuando "
            "Ricardo diga 'deja de avisarme de X' o 'cancela la alerta de X'. "
            "Para cancelarlas todas, usa producto='todas'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "producto": {
                    "type": "string",
                    "description": "Texto que identifica la alerta a cancelar, o 'todas'.",
                },
            },
            "required": ["producto"],
        },
    },
    {
        "name": "guardar_nota_personal",
        "description": (
            "Guarda algo de la vida de Ricardo — no es de DiMango. Úsala "
            "cuando cuente algo de su día, algo que quiere mejorar, una "
            "idea suelta, te pida que le recuerdes algo a una hora concreta, "
            "o te dé una tarea (algo que hacer, sin hora fija). Vuelve a "
            "aparecer sola en conversaciones futuras, no hace falta que la pidan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contenido": {
                    "type": "string",
                    "description": "Qué guardar, en una o dos frases.",
                },
                "categoria": {
                    "type": "string",
                    "enum": ["nota", "mejora", "recordatorio", "tarea"],
                    "description": (
                        "'nota' para algo del día a día, 'mejora' para algo que "
                        "quiere trabajar en sí mismo, 'recordatorio' si pidió que "
                        "le avises a una hora concreta, 'tarea' si es algo que hay "
                        "que hacer pero sin hora fija (un pendiente). Si no queda "
                        "claro, usa 'nota'."
                    ),
                },
                "recordar_en": {
                    "type": "string",
                    "description": (
                        "Solo si categoria='recordatorio': fecha y hora ISO "
                        "(YYYY-MM-DDTHH:MM:SS) en hora de Chile, calculada por ti a "
                        "partir de la fecha de hoy que ya tienes en tu contexto."
                    ),
                },
            },
            "required": ["contenido"],
        },
    },
    {
        "name": "listar_notas_personales",
        "description": "Lista las notas, mejoras, recordatorios y tareas personales activos de Ricardo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "categoria": {
                    "type": "string",
                    "enum": ["nota", "mejora", "recordatorio", "tarea"],
                    "description": "Si no la dicen, trae todas las categorías.",
                },
            },
        },
    },
    {
        "name": "cerrar_nota_personal",
        "description": (
            "Marca una nota, mejora, recordatorio o tarea personal como cumplido. "
            "Úsala cuando Ricardo diga 'ya lo hice', 'olvídalo', o 'listo con eso'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "texto": {
                    "type": "string",
                    "description": "Texto que identifica la nota a cerrar, o 'todas'.",
                },
            },
            "required": ["texto"],
        },
    },
    {
        "name": "gastos_dimango",
        "description": (
            "Gastos y egresos reales de DiMangoWorking (GastionFinanciera → "
            "Gastos): monto total, pendiente vs pagado, por tipo de gasto y "
            "por proveedor. Úsala cuando pregunten cuánto se ha gastado, en "
            "qué, a quién se le debe, o pidan un gasto de un proveedor o "
            "rango de fechas. Por defecto: el mes en curso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicio": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Si no la dicen, el día 1 del mes en curso.",
                },
                "fecha_fin": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Si no la dicen, usa hoy.",
                },
                "estado": {
                    "type": "string",
                    "enum": ["PENDIENTE", "PAGADO"],
                    "description": "Si no lo dicen, trae ambos.",
                },
                "tipo_gasto": {
                    "type": "string",
                    "description": "Ej: ALIMENTOS, NO ALIMENTOS, GASTOS FIJOS, SUELDOS. Si no lo dicen, trae todos.",
                },
            },
        },
    },
    {
        "name": "bodega_dimango",
        "description": (
            "Bodega general de DiMangoWorking: costo y stock de un insumo "
            "puntual (buscar), valorización del inventario por bodega, e "
            "ítems bajo stock mínimo agrupados por proveedor — la base "
            "para armar el pedido a proveedores. Úsala cuando pregunten "
            "cuánto cuesta o cuánto stock hay de un insumo (pechuga, "
            "harina, etc.), qué hay que pedir, a qué proveedor, cuánto "
            "vale el inventario, o qué se está por acabar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "buscar": {
                    "type": "string",
                    "description": (
                        "Nombre o parte del nombre de un insumo puntual, ej: "
                        "'pechuga', 'harina'. Si lo mandas, la respuesta es SOLO "
                        "ese insumo (costo, stock, proveedor) — no la valorización "
                        "completa ni los bajo mínimo."
                    ),
                },
                "bodega": {
                    "type": "string",
                    "description": "Ej: 'Bodega 1', 'Camara -18', 'Mall'. Si no la dicen, trae todas.",
                },
                "solo_bajo_minimo": {
                    "type": "boolean",
                    "description": "true si solo quieren lo que hay que pedir, sin la valorización completa.",
                },
            },
        },
    },
    {
        "name": "calendario_ricardo",
        "description": (
            "Agenda de Ricardo (calendario de Apple/iCloud). Solo lectura — "
            "no crea ni modifica eventos. Úsala cuando pregunten qué tiene "
            "agendado, si tiene algo hoy/mañana/esta semana, o a qué hora es "
            "algo. Por defecto: desde hoy hasta 7 días adelante."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicio": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Si no la dicen, hoy.",
                },
                "fecha_fin": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Si no la dicen, 7 días desde fecha_inicio.",
                },
            },
        },
    },
    {
        "name": "controlar_pantalla",
        "description": (
            "Controla el Command Center: la pantalla dedicada frente a Ricardo. Úsala SIEMPRE "
            "que Ricardo pida VER, ABRIR, MOSTRAR, CERRAR, AMPLIAR/MAXIMIZAR o VOLVER AL INICIO "
            "de algo visual — ventas, productos más vendidos, checklist, alertas, correos o "
            "calendario. Abre el panel Y ADEMÁS respondes por voz con normalidad.\n"
            "Ejemplos: 'muéstrame las ventas' → abrir ventas · 'abre el checklist de Playa' → "
            "abrir checklist local=playa · 'cierra las cámaras' → cerrar · 'amplía las ventas' → "
            "maximizar ventas · 'vuelve al inicio' → inicio.\n"
            "No reemplaza a las herramientas de datos: si además tienes que DECIR el número, "
            "consulta ventas_dimango/checklist_dimango en la misma vuelta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "accion": {
                    "type": "string",
                    "enum": ["abrir", "cerrar", "maximizar", "restaurar", "inicio"],
                    "description": "Qué hacer con la pantalla.",
                },
                "panel": {
                    "type": "string",
                    "enum": ["ventas", "top_productos", "checklist", "alertas", "correo", "calendario", "memoria", "todos"],
                    "description": (
                        "Qué panel. 'ventas' del día por local, 'top_productos' los más vendidos, "
                        "'checklist' insumos a reponer, 'alertas', 'correo', 'calendario', y 'memoria' "
                        "(tu grafo de memoria: se ilumina con las notas que usas al responder — ábrelo "
                        "si Ricardo pregunta qué recuerdas, de dónde sacas algo, o pide ver tu cerebro/memoria). "
                        "Para accion 'cerrar' usa 'todos' para cerrar todo."
                    ),
                },
                "local": {
                    "type": "string",
                    "enum": ["playa", "mall"],
                    "description": "Solo si Ricardo especifica un local (útil sobre todo para checklist).",
                },
            },
            "required": ["accion"],
        },
    },
]

# Herramienta de servidor de Anthropic — Claude busca y trae los
# resultados solo, dentro de la misma llamada. No pasa por
# ejecutar_herramienta() como las de arriba: la API la resuelve sola.
# Para noticias, deportes, o cualquier cosa de HOY que no esté en la
# memoria de Maximus ni en las herramientas de negocio.
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

DIMANGOTOGO_URL = "https://dimangotogo.base44.app/functions/maximusVentas"
DIMANGOTOGO_CHECKLIST_URL = "https://dimangotogo.base44.app/functions/maximusChecklist"
DIMANGOTOGO_SECRET = os.getenv("DIMANGOTOGO_MAXIMUS_SECRET", "")

DIMANGOWORKING_GASTOS_URL = "https://dimangoworking.base44.app/functions/maximusGastos"
DIMANGOWORKING_BODEGA_URL = "https://dimangoworking.base44.app/functions/maximusBodega"
DIMANGOWORKING_SECRET = os.getenv("DIMANGOWORKING_MAXIMUS_SECRET", "")

# URLs secretas de los calendarios de Ricardo (iCloud, "dirección pública/
# privada en formato iCal"), separadas por coma — tiene varios calendarios
# (Casa, Personal, Calendario) y cada uno tiene su propia URL. Son secretos
# igual que una contraseña — viven solo en el .env del servidor, nunca en
# el código ni en git.
ICLOUD_CALENDAR_URLS = [
    u.strip() for u in os.getenv("ICLOUD_CALENDAR_URLS", "").split(",") if u.strip()
]

CIUDADES = {
    "arica": (-18.4783, -70.3126), "santiago": (-33.4489, -70.6693),
    "madrid": (40.4168, -3.7038), "milano": (45.4642, 9.1900),
    "washington": (38.9072, -77.0369),
}


async def _http_json(url: str):
    import httpx
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url)
        return r.json() if r.status_code == 200 else None


async def ejecutar_herramienta(nombre: str, args: dict) -> str:
    """Devuelve texto plano. Si la fuente falla, lo dice: no inventa."""
    try:
        if nombre == "indicadores_chile":
            d = await _http_json("https://mindicador.cl/api")
            if not d:
                return "No pude consultar mindicador.cl."

            from datetime import date
            hoy = date.today()
            partes = []
            for k in ("dolar", "euro", "uf", "utm", "ipc"):
                if k not in d:
                    continue
                v = d[k]
                fecha = v["fecha"][:10]
                monto = f"{v['valor']:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
                nota = ""
                try:
                    dias = (hoy - date.fromisoformat(fecha)).days
                    if k in ("dolar", "euro") and dias > 0:
                        nota = (f" — es el último publicado: el Banco Central no publica "
                                f"dólar ni euro sábados, domingos ni festivos, y el "
                                f"observado se calcula con el día hábil anterior. "
                                f"Este valor rige hasta la próxima publicación.")
                    elif k == "utm":
                        nota = " — la UTM es mensual, no cambia hasta el próximo mes."
                except ValueError:
                    pass
                partes.append(f"{v['nombre']}: ${monto} (dato del {fecha}){nota}")

            partes.append(f"\nHoy es {hoy.isoformat()}. Si un valor trae fecha anterior, "
                          "NO es un error ni un dato desactualizado: es el último vigente. "
                          "Dilo así en vez de disculparte.")
            return "\n".join(partes)

        if nombre == "clima":
            ciudad = (args.get("ciudad") or "arica").lower()
            lat, lon = CIUDADES.get(ciudad, CIUDADES["arica"])
            d = await _http_json(
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,relative_humidity_2m,weather_code"
                "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                "&timezone=auto&forecast_days=1")
            if not d:
                return "No pude consultar el clima."
            c, dia = d["current"], d["daily"]
            return (f"{ciudad.capitalize()}: {c['temperature_2m']}°C ahora, "
                    f"humedad {c['relative_humidity_2m']}%. "
                    f"Hoy mínima {dia['temperature_2m_min'][0]}° y máxima "
                    f"{dia['temperature_2m_max'][0]}°, "
                    f"probabilidad de lluvia {dia['precipitation_probability_max'][0]}%.")

        if nombre == "ventas_dimango":
            if not DIMANGOTOGO_SECRET:
                return ("No puedo consultar DiMangoToGo: falta DIMANGOTOGO_MAXIMUS_SECRET "
                        "en el .env del servidor. Avísale a Ricardo.")
            import httpx
            payload = {k: args[k] for k in ("fecha_inicio", "fecha_fin", "local") if args.get(k)}
            try:
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.post(
                        DIMANGOTOGO_URL,
                        json=payload,
                        headers={"x-maximus-secret": DIMANGOTOGO_SECRET},
                    )
            except httpx.RequestError as e:
                return f"No pude conectar con DiMangoToGo: {e}"
            if r.status_code != 200:
                return f"DiMangoToGo respondió {r.status_code}: {r.text[:300]}"
            d = r.json()
            rango, resumen = d["rango"], d["resumen"]
            partes = [
                f"Ventas {rango['fecha_inicio']} a {rango['fecha_fin']} "
                f"({rango['local']}): {resumen['total_ventas']} ventas, "
                f"${resumen['monto_total']:,.0f} totales, "
                f"${resumen['propinas_total']:,.0f} en propinas."
                .replace(",", "."),
            ]
            if resumen.get("por_local"):
                for loc, v in resumen["por_local"].items():
                    partes.append(f"  {loc}: {v['ventas']} ventas, ${v['monto']:,.0f}".replace(",", "."))
            if resumen.get("por_medio_pago"):
                for mp, monto in resumen["por_medio_pago"].items():
                    partes.append(f"  {mp}: ${monto:,.0f}".replace(",", "."))
            top = d.get("productos_vendidos", [])[:15]
            if top:
                partes.append("\nProductos más vendidos:")
                for p in top:
                    partes.append(f"  {p['cantidad']}x {p['nombre']} — ${p['monto']:,.0f}".replace(",", "."))
            stock = d.get("stock", [])
            if stock:
                partes.append("\nStock con control activo:")
                for s in stock:
                    ubic = []
                    if s.get("playa"):
                        ubic.append(f"Playa {s['playa']['cantidad']} (mín. {s['playa']['minimo']})")
                    if s.get("mall"):
                        ubic.append(f"Mall {s['mall']['cantidad']} (mín. {s['mall']['minimo']})")
                    partes.append(f"  {s['nombre']}: {', '.join(ubic)}")
            return "\n".join(partes)

        if nombre == "checklist_dimango":
            if not DIMANGOTOGO_SECRET:
                return ("No puedo consultar el checklist: falta DIMANGOTOGO_MAXIMUS_SECRET "
                        "en el .env del servidor. Avísale a Ricardo.")
            import httpx
            payload = {k: args[k] for k in ("fecha_inicio", "fecha_fin", "local", "area") if args.get(k)}
            try:
                async with httpx.AsyncClient(timeout=25) as c:
                    r = await c.post(
                        DIMANGOTOGO_CHECKLIST_URL,
                        json=payload,
                        headers={"x-maximus-secret": DIMANGOTOGO_SECRET},
                    )
            except httpx.RequestError as e:
                return f"No pude conectar con DiMangoToGo: {e}"
            if r.status_code != 200:
                return f"DiMangoToGo respondió {r.status_code}: {r.text[:300]}"
            d = r.json()
            rango = d["rango"]
            partes = [
                f"Checklist {rango['fecha_inicio']} a {rango['fecha_fin']} "
                f"({rango['local']}, área: {rango['area']}) — "
                f"{d['cobertura_recetas_pct']}% de lo vendido tiene receta vinculada."
            ]
            sin_receta = d.get("productos_sin_receta", [])
            if sin_receta:
                partes.append(
                    f"\n{len(sin_receta)} productos SIN receta (sus insumos no se "
                    f"están contando): {', '.join(sin_receta[:20])}"
                    + ("..." if len(sin_receta) > 20 else "")
                )
            insumos = d.get("insumos_a_reponer", [])
            if insumos:
                partes.append("\nInsumos a reponer (mayor a menor):")
                for i in insumos[:25]:
                    stock = ""
                    if i.get("stock_actual") is not None:
                        stock = f" — stock actual {i['stock_actual']}"
                        if i.get("stock_minimo") is not None:
                            stock += f" (mín. {i['stock_minimo']})"
                    cant = i["a_reponer"]
                    cant_fmt = int(cant) if cant == int(cant) else round(cant, 2)
                    partes.append(f"  {cant_fmt} {i['unidad']} · {i['insumo']} ({i['area']}){stock}")
            else:
                partes.append("\nNo hay insumos con receta vinculada en este rango.")
            return "\n".join(partes)

        if nombre == "crear_alerta_venta":
            from agent.alertas_venta import crear_alerta
            producto = (args.get("producto") or "").strip()
            if not producto:
                return "Falta el nombre del producto."
            umbral = int(args.get("umbral") or 1)
            local = args.get("local") or ""
            alerta = await crear_alerta(producto, umbral, local)
            loc_txt = f" en {local}" if local else " (ambos locales)"
            return (f"Alerta creada: te aviso cuando se vendan {umbral}x \"{producto}\"{loc_txt}. "
                    f"Queda activa todos los días hasta que me digas que pare.")

        if nombre == "listar_alertas_venta":
            from agent.alertas_venta import listar_alertas_activas
            activas = await listar_alertas_activas()
            if not activas:
                return "No tienes alertas de venta activas."
            partes = ["Alertas activas:"]
            for a in activas:
                loc_txt = f" en {a.local}" if a.local else ""
                partes.append(f"  {a.umbral}x \"{a.producto}\"{loc_txt}")
            return "\n".join(partes)

        if nombre == "cancelar_alerta_venta":
            from agent.alertas_venta import cancelar_alertas
            producto = (args.get("producto") or "").strip()
            if not producto:
                return "Falta indicar qué alerta cancelar (o 'todas')."
            n = await cancelar_alertas(producto)
            if n == 0:
                return f"No encontré ninguna alerta activa que coincida con \"{producto}\"."
            return f"Cancelada{'s' if n > 1 else ''} {n} alerta{'s' if n > 1 else ''}."

        if nombre == "guardar_nota_personal":
            from agent.notas_personales import guardar_nota
            contenido = (args.get("contenido") or "").strip()
            if not contenido:
                return "Falta el contenido de la nota."
            categoria = args.get("categoria") or "nota"
            recordar_en = None
            if categoria == "recordatorio":
                crudo = (args.get("recordar_en") or "").strip()
                if crudo:
                    try:
                        # la tabla guarda UTC naive; el modelo manda hora de Chile
                        local = datetime.fromisoformat(crudo).replace(tzinfo=TZ_CHILE)
                        recordar_en = local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
                    except ValueError:
                        return f"No entendí la fecha \"{crudo}\". Debe ser YYYY-MM-DDTHH:MM:SS."
            await guardar_nota(contenido, categoria, recordar_en)
            if categoria == "recordatorio" and recordar_en:
                return f"Guardado. Te aviso: \"{contenido}\"."
            return "Guardado."

        if nombre == "listar_notas_personales":
            from agent.notas_personales import listar_notas
            categoria = args.get("categoria")
            notas = await listar_notas(categoria)
            if not notas:
                return "No tienes notas personales activas."
            etq = {"nota": "📝", "mejora": "🎯", "recordatorio": "⏰", "tarea": "☐"}
            partes = ["Notas personales activas:"]
            for n in notas:
                cuando = f" ({n.recordar_en.strftime('%d-%b %H:%M')})" if n.recordar_en else ""
                partes.append(f"  {etq.get(n.categoria,'📝')} {n.contenido}{cuando}")
            return "\n".join(partes)

        if nombre == "cerrar_nota_personal":
            from agent.notas_personales import marcar_nota
            texto = (args.get("texto") or "").strip()
            if not texto:
                return "Falta indicar qué nota cerrar (o 'todas')."
            n = await marcar_nota(texto, "cumplida")
            if n == 0:
                return f"No encontré ninguna nota activa que coincida con \"{texto}\"."
            return f"Cerrada{'s' if n > 1 else ''} {n} nota{'s' if n > 1 else ''}."

        if nombre == "gastos_dimango":
            if not DIMANGOWORKING_SECRET:
                return ("No puedo consultar DimangoWorking: falta DIMANGOWORKING_MAXIMUS_SECRET "
                        "en el .env del servidor. Avísale a Ricardo.")
            import httpx
            payload = {k: args[k] for k in ("fecha_inicio", "fecha_fin", "estado", "tipo_gasto") if args.get(k)}
            try:
                async with httpx.AsyncClient(timeout=25) as c:
                    r = await c.post(
                        DIMANGOWORKING_GASTOS_URL,
                        json=payload,
                        headers={"x-maximus-secret": DIMANGOWORKING_SECRET},
                    )
            except httpx.RequestError as e:
                return f"No pude conectar con DimangoWorking: {e}"
            if r.status_code != 200:
                return f"DimangoWorking respondió {r.status_code}: {r.text[:300]}"
            d = r.json()
            rango, resumen = d["rango"], d["resumen"]
            partes = [
                f"Gastos {rango['fecha_inicio']} a {rango['fecha_fin']} "
                f"(estado: {rango['estado']}): {resumen['total_gastos']} gastos, "
                f"${resumen['monto_total']:,.0f} en total.".replace(",", "."),
                f"  Pendiente: ${resumen['pendiente']:,.0f} · Pagado: ${resumen['pagado']:,.0f}".replace(",", "."),
            ]
            if resumen.get("por_tipo"):
                partes.append("\nPor tipo:")
                for t, monto in sorted(resumen["por_tipo"].items(), key=lambda x: -x[1]):
                    partes.append(f"  {t}: ${monto:,.0f}".replace(",", "."))
            top_prov = sorted(resumen.get("por_proveedor", {}).items(), key=lambda x: -x[1])[:10]
            if top_prov:
                partes.append("\nMayores proveedores:")
                for p, monto in top_prov:
                    partes.append(f"  {p}: ${monto:,.0f}".replace(",", "."))
            return "\n".join(partes)

        if nombre == "bodega_dimango":
            if not DIMANGOWORKING_SECRET:
                return ("No puedo consultar DimangoWorking: falta DIMANGOWORKING_MAXIMUS_SECRET "
                        "en el .env del servidor. Avísale a Ricardo.")
            import httpx
            payload = {k: args[k] for k in ("buscar", "bodega", "solo_bajo_minimo") if args.get(k) is not None}
            try:
                async with httpx.AsyncClient(timeout=25) as c:
                    r = await c.post(
                        DIMANGOWORKING_BODEGA_URL,
                        json=payload,
                        headers={"x-maximus-secret": DIMANGOWORKING_SECRET},
                    )
            except httpx.RequestError as e:
                return f"No pude conectar con DimangoWorking: {e}"
            if r.status_code != 200:
                return f"DimangoWorking respondió {r.status_code}: {r.text[:300]}"
            d = r.json()

            if "encontrados" in d:
                items = d["encontrados"]
                if not items:
                    return f"No encontré ningún insumo que coincida con \"{d['busqueda']}\"."
                partes = [f"Encontrado{'s' if len(items)>1 else ''} para \"{d['busqueda']}\":"]
                for it in items:
                    costo = f"${it['costo_unitario']:,.0f}/{it['unidad']}".replace(",", ".") if it.get('costo_unitario') is not None else "sin costo cargado"
                    prov = f" — proveedor: {it['proveedor_1']}" if it.get('proveedor_1') else ""
                    partes.append(f"  {it['item']} ({it['bodega']}): {costo}, stock {it['stock_real']} {it['unidad']}{prov}")
                return "\n".join(partes)

            partes = []
            if d.get("valorizacion"):
                v = d["valorizacion"]
                partes.append(f"Inventario: {v['total_items']} ítems, ${v['valor_total']:,.0f} valorizados.".replace(",", "."))
                for b, monto in sorted(v.get("por_bodega", {}).items(), key=lambda x: -x[1]):
                    partes.append(f"  {b}: ${monto:,.0f}".replace(",", "."))
            bajo = d.get("bajo_minimo", [])
            if bajo:
                partes.append(f"\n{len(bajo)} ítems bajo el mínimo:")
                for it in bajo[:25]:
                    partes.append(f"  {it['item']} ({it['bodega']}): {it['stock_real']}/{it['stock_minimo']} {it['unidad']}"
                                  + (f" — pedir a {it['proveedor_1']}" if it.get('proveedor_1') else ""))
            else:
                partes.append("\nNada bajo el mínimo.")
            return "\n".join(partes)

        if nombre == "calendario_ricardo":
            if not ICLOUD_CALENDAR_URLS:
                return ("No puedo consultar el calendario: falta ICLOUD_CALENDAR_URLS "
                        "en el .env del servidor. Avísale a Ricardo.")
            import httpx
            import icalendar
            import recurring_ical_events
            from datetime import date, timedelta

            hoy = date.today()
            try:
                fecha_inicio = date.fromisoformat(args["fecha_inicio"]) if args.get("fecha_inicio") else hoy
            except ValueError:
                return f"Fecha de inicio inválida: {args.get('fecha_inicio')}. Usa formato YYYY-MM-DD."
            try:
                fecha_fin = (
                    date.fromisoformat(args["fecha_fin"]) if args.get("fecha_fin")
                    else fecha_inicio + timedelta(days=7)
                )
            except ValueError:
                return f"Fecha de fin inválida: {args.get('fecha_fin')}. Usa formato YYYY-MM-DD."

            eventos = []
            errores = []
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                for calendar_url in ICLOUD_CALENDAR_URLS:
                    url = calendar_url.replace("webcal://", "https://", 1)
                    try:
                        r = await c.get(url)
                    except httpx.RequestError as e:
                        errores.append(f"no pude conectar ({e})")
                        continue
                    if r.status_code != 200:
                        errores.append(f"respondió {r.status_code}")
                        continue
                    try:
                        cal = icalendar.Calendar.from_ical(r.content)
                        eventos.extend(
                            recurring_ical_events.of(cal).between(fecha_inicio, fecha_fin + timedelta(days=1))
                        )
                    except Exception as e:
                        errores.append(f"no pude leerlo ({e})")

            if not eventos:
                base = f"No hay nada agendado entre {fecha_inicio} y {fecha_fin}."
                if errores:
                    base += f" (aviso: {len(errores)} de {len(ICLOUD_CALENDAR_URLS)} calendarios fallaron: {'; '.join(errores)})"
                return base

            def _clave(ev):
                dt = ev.get("DTSTART").dt
                return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)

            eventos.sort(key=_clave)
            partes = [f"Agenda del {fecha_inicio} al {fecha_fin}:"]
            for ev in eventos:
                titulo = str(ev.get("SUMMARY", "(sin título)"))
                dt = ev.get("DTSTART").dt
                cuando = dt.strftime("%d-%m %H:%M") if hasattr(dt, "hour") else f"{dt.strftime('%d-%m')} (todo el día)"
                lugar = ev.get("LOCATION")
                partes.append(f"  {cuando} — {titulo}" + (f" ({lugar})" if lugar else ""))
            if errores:
                partes.append(f"\n(aviso: {len(errores)} de {len(ICLOUD_CALENDAR_URLS)} calendarios fallaron al consultarse — puede faltar algo)")
            return "\n".join(partes)

        if nombre == "controlar_pantalla":
            # No consulta datos: solo le dice a la pantalla qué panel abrir/cerrar/mover.
            # El panel, ya en el navegador, pide sus propios datos a /maximus/panel/*.
            from agent import eventos
            accion = (args.get("accion") or "abrir").lower()
            panel = (args.get("panel") or "").lower()
            local = (args.get("local") or "").lower()
            payload = {"accion": accion, "panel": panel}
            if local:
                payload["args"] = {"local": local}
            await eventos.publicar("panel", **payload)
            if accion == "inicio":
                return "Listo, dejé la pantalla en inicio."
            if accion == "cerrar":
                return f"Cerré {'todo' if panel in ('todos', '') else panel} en la pantalla."
            if accion in ("maximizar", "restaurar"):
                return f"{'Maximicé' if accion == 'maximizar' else 'Restauré'} el panel de {panel}."
            return f"Abrí el panel de {panel} en la pantalla" + (f" ({local})." if local else ".")

    except Exception as e:
        logger.error(f"[MAXIMUS] Herramienta {nombre} falló: {e}")
        return f"La consulta falló: {e}"
    return f"Herramienta desconocida: {nombre}"


def contexto_fecha() -> str:
    ahora = datetime.now(TZ_CHILE)
    return (
        f"Hoy es {_DIAS[ahora.weekday()]} {ahora.day} de {_MESES[ahora.month - 1]} "
        f"de {ahora.year}, {ahora.strftime('%H:%M')} hrs (hora de Chile)."
    )


def _cerebro_atomico():
    """
    Devuelve el recuperador si la memoria atómica existe y está completa.
    Si falta cualquier cosa, devuelve None y se usan los seis archivos.
    Falla cerrado: preferimos memoria completa y lenta antes que memoria a medias.
    """
    try:
        if not (MEMORY_DIR / "memoria" / "indice.json").exists():
            return None
        if not (MEMORY_DIR / "core" / "SOUL.md").exists():
            return None
        from agent.memoria_atomica import Cerebro
        return Cerebro()
    except Exception as e:
        logger.warning(f"[MAXIMUS] Memoria atómica no disponible, uso los archivos completos: {e}")
        return None


def construir_prompt_atomico(mensaje: str) -> tuple[str, str] | None:
    """(bloque fijo cacheable, bloque variable) o None si no hay memoria atómica."""
    c = _cerebro_atomico()
    if c is None:
        return None
    try:
        fija, variable, ids = c.contexto(mensaje)
        logger.info(f"[MAXIMUS] {len(ids)} notas recuperadas: {', '.join(ids[:8])}")
        return _encabezado() + "\n\n" + fija, variable
    except Exception as e:
        logger.error(f"[MAXIMUS] Falló la recuperación, uso los archivos completos: {e}")
        return None


def _encabezado() -> str:
    return f"""Eres **Maximus**, el estratega y operador de negocio de Ricardo Vinet (DiMango, Arica, Chile).

{contexto_fecha()}

Estás respondiendo por **WhatsApp**, no por consola. Eso cambia el formato, no el criterio:
- Respuestas cortas. Un mensaje de WhatsApp, no un informe. Si necesitas más de 8 líneas, es porque el tema lo merece de verdad.
- Nada de tablas markdown ni encabezados: no se ven bien en WhatsApp. Usa listas simples con guiones.
- Negrita con *un asterisco*, que es lo que entiende WhatsApp.
- Conclusión primero, siempre.
- **Nunca cierres con "¿algo más?", "¿en qué te ayudo?" ni fórmulas de asistente.** No eres un asistente esperando órdenes. Si el tema queda abierto, propón el siguiente movimiento concreto. Si está cerrado, cierra y calla.

Tu carácter y tus prohibiciones están en SOUL.md, que manda sobre tu conducta.
Entre notas en conflicto manda la de mayor **autoridad de fuente** (1 sistema
oficial > 2 exportación directa > 3 planilla interna > 4 informado > 5 estimación).
Si dos notas del mismo período se contradicen con la misma autoridad, **decláralo
en vez de elegir en silencio.**

**Los montos son SIEMPRE pesos chilenos (CLP), nunca dólares.** En Chile el punto
separa los miles y la coma los decimales: `$40.464.040` son cuarenta millones
cuatrocientos sesenta y cuatro mil cuarenta pesos, y `$1.073,94` son mil setenta
y tres pesos con noventa y cuatro. Si un monto viene sin símbolo, igual es CLP.
Cuando cites cifras grandes, redondea a millones para que se entiendan
("$40,5 millones"), pero nunca conviertas a dólares salvo que te lo pidan.

Regla que no se negocia: **nunca inventes un número.** Si el dato no está en tu
memoria, di "no lo tengo" y ofrece dónde consultarlo. Toda estimación se etiqueta
como estimación.

Si Ricardo te pide escribir en la memoria, editar archivos o ejecutar código:
dile que eso se hace en la sesión de Claude Code, no por WhatsApp. No finjas que
lo hiciste.

Tienes búsqueda web real. Úsala para noticias, deportes, o cualquier cosa de
HOY que no esté en tu memoria ni en tus herramientas de negocio — no digas
"no tengo internet", sí lo tienes. Cuando la respuesta venga de una búsqueda,
incluye la URL de la fuente tal cual (sin acortarla ni inventarla) para que el
cerebro visual pueda mostrarla como link. Si encuentras un video de YouTube
relevante, incluye esa URL exacta — el cerebro lo embebe solo."""


def construir_system_prompt() -> str:
    """Camino de respaldo: los seis archivos completos, como antes de la migración."""
    memoria = cargar_memoria()
    if not memoria:
        return (
            "Eres Maximus, el estratega de negocio de Ricardo Vinet. "
            "ADVERTENCIA: no pudiste cargar tu memoria. Dilo en la primera línea "
            "y no respondas nada que dependa de datos que no tienes."
        )
    return _encabezado() + "\n\n===== TU MEMORIA =====\n\n" + memoria


async def responder(
    mensaje: str, historial: list[dict],
    imagen_b64: str = "", imagen_mime: str = "",
) -> str:
    """
    Genera la respuesta de Maximus. Misma firma que brain.generar_respuesta
    (más imagen_b64/imagen_mime, opcionales), para que main.py pueda enrutar
    sin cambiar nada más.
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return "¿Me repites? No me llegó nada legible."

    from agent import eventos
    await eventos.publicar("pensando", mensaje=mensaje[:200])

    mensajes = [{"role": m["role"], "content": m["content"]} for m in historial]
    if imagen_b64:
        mensajes.append({"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": imagen_mime or "image/jpeg", "data": imagen_b64,
            }},
            {"type": "text", "text": mensaje},
        ]})
    else:
        mensajes.append({"role": "user", "content": mensaje})

    from agent.notas_personales import contexto_notas_recientes
    notas_ctx = await contexto_notas_recientes()

    # Camino nuevo: core + índice (cacheable) y notas recuperadas (variable).
    # La separación importa — si se mezcla, el prompt cambia entero y el cache
    # nunca acierta. Camino viejo: los seis archivos completos, en un solo bloque.
    # Las notas personales van SIEMPRE en la parte variable/sin caché: cambian
    # con el tiempo, y si entraran al bloque fijo el cache nunca acertaría.
    atomico = construir_prompt_atomico(mensaje)
    if atomico:
        fija, variable = atomico
        if notas_ctx:
            variable = variable + "\n\n" + notas_ctx
        system_bloques = [
            {"type": "text", "text": fija, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": variable},
        ]
    else:
        base = construir_system_prompt()
        system_bloques = [{
            "type": "text",
            "text": base,
            "cache_control": {"type": "ephemeral"},
        }]
        if notas_ctx:
            system_bloques.append({"type": "text", "text": notas_ctx})

    for modelo in (MODELO, MODELO_FALLBACK):
        try:
            # Hasta dos vueltas: la primera puede pedir una herramienta, la
            # segunda responde con el dato ya en mano. Más vueltas serían un
            # bucle en un canal de chat, y no vale la pena.
            for _ in range(2):
                respuesta = await client.messages.create(
                    model=modelo,
                    max_tokens=2048,   # con búsqueda web, 1500 se quedaba corto y cortaba la respuesta
                    system=system_bloques,
                    tools=HERRAMIENTAS + [WEB_SEARCH_TOOL],
                    messages=mensajes,
                )
                logger.info(
                    f"[MAXIMUS] {modelo} — {respuesta.usage.input_tokens} in / "
                    f"{respuesta.usage.output_tokens} out — {respuesta.stop_reason}"
                )

                if respuesta.stop_reason != "tool_use":
                    partes = [b.text for b in respuesta.content if b.type == "text"]
                    texto_final = "\n".join(partes).strip() or "No supe qué responder."
                    await eventos.publicar("respondiendo", texto=texto_final[:500])
                    return texto_final

                mensajes.append({"role": "assistant", "content": respuesta.content})
                resultados = []
                for bloque in respuesta.content:
                    if bloque.type == "tool_use":
                        await eventos.publicar("ejecutando", herramienta=bloque.name)
                        salida = await ejecutar_herramienta(bloque.name, bloque.input or {})
                        logger.info(f"[MAXIMUS] herramienta {bloque.name} → {salida[:70]}")
                        resultados.append({
                            "type": "tool_result",
                            "tool_use_id": bloque.id,
                            "content": salida,
                        })
                mensajes.append({"role": "user", "content": resultados})

            return "Me quedé dando vueltas consultando datos. Pregúntame de nuevo."

        except Exception as e:
            logger.error(f"[MAXIMUS] Falló con {modelo}: {e}")
            if modelo == MODELO_FALLBACK:
                return "Se me cayó la conexión con el modelo. Reviso y te aviso."

    return "Se me cayó la conexión con el modelo. Reviso y te aviso."
