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
lo hiciste."""


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


async def responder(mensaje: str, historial: list[dict]) -> str:
    """
    Genera la respuesta de Maximus. Misma firma que brain.generar_respuesta,
    para que main.py pueda enrutar sin cambiar nada más.
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return "¿Me repites? No me llegó nada legible."

    mensajes = [{"role": m["role"], "content": m["content"]} for m in historial]
    mensajes.append({"role": "user", "content": mensaje})

    # Camino nuevo: core + índice (cacheable) y notas recuperadas (variable).
    # La separación importa — si se mezcla, el prompt cambia entero y el cache
    # nunca acierta. Camino viejo: los seis archivos completos, en un solo bloque.
    atomico = construir_prompt_atomico(mensaje)
    if atomico:
        fija, variable = atomico
        system_bloques = [
            {"type": "text", "text": fija, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": variable},
        ]
    else:
        system_bloques = [{
            "type": "text",
            "text": construir_system_prompt(),
            "cache_control": {"type": "ephemeral"},
        }]

    for modelo in (MODELO, MODELO_FALLBACK):
        try:
            respuesta = await client.messages.create(
                model=modelo,
                max_tokens=1500,
                system=system_bloques,
                messages=mensajes,
            )
            texto = respuesta.content[0].text
            logger.info(
                f"[MAXIMUS] {modelo} — {respuesta.usage.input_tokens} in / "
                f"{respuesta.usage.output_tokens} out"
            )
            return texto
        except Exception as e:
            logger.error(f"[MAXIMUS] Falló con {modelo}: {e}")
            if modelo == MODELO_FALLBACK:
                return "Se me cayó la conexión con el modelo. Reviso y te aviso."

    return "Se me cayó la conexión con el modelo. Reviso y te aviso."
