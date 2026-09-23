"""
banco_fotos — las fotos reales de DiMango, guardadas y descritas.

Por qué existe: la decisión de fondo del proyecto es que la IA NO invente
comida. Un plato generado se nota, y el cliente que llega al local compara con
lo que vio. Entonces la materia prima son las fotos de verdad, y para poder
usarlas hay que saber qué hay en cada una.

Cómo entran las fotos (v1): Ricardo las deja en una carpeta. Se eligió carpeta
y no chat a propósito — un bot nuevo en un grupo obliga a crear bot, webhook y
credencial, y el objetivo de la v1 es funcionar esta semana, no en tres. El
ingreso por chat queda para la v2.

Cada foto se describe UNA vez con visión y la descripción queda guardada. Sin
ese catálogo, cada propuesta tendría que volver a mirar todas las fotos: caro y
lento para un dato que no cambia.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import pathlib
from datetime import datetime

logger = logging.getLogger("agentkit")

CARPETA = pathlib.Path(os.getenv("MARKETING_FOTOS_DIR", pathlib.Path.home() / "marketing-fotos"))
CATALOGO = CARPETA / "_catalogo.json"
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp"}
LIMITE_BYTES = 5 * 1024 * 1024   # Claude acepta hasta ~5 MB por imagen

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

PROMPT_CATALOGO = """Mira esta foto de un restaurante y devuelve SOLO un JSON, sin texto alrededor:

{"producto": "qué se ve, en 2-4 palabras",
 "categoria": "helados|pizzas|pastas|sandwich|postres|cafeteria|bebidas|local|equipo|otro",
 "calidad": "buena|regular|mala",
 "sirve_para": ["feed","historia","carrusel"],
 "descripcion": "una frase de lo que muestra, útil para elegirla después",
 "problema": "qué le falta para publicarse, o null si está lista"}

Sé exigente con "calidad": juzga luz, foco, encuadre y si el plato se ve apetitoso.
Si la foto está movida, oscura o el plato se ve poco atractivo, dilo."""


def _huella(ruta: pathlib.Path) -> str:
    """Identifica la foto por contenido: renombrarla no la vuelve nueva."""
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()[:16]


def cargar_catalogo() -> dict:
    try:
        return json.loads(CATALOGO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guardar_catalogo(cat: dict) -> None:
    CARPETA.mkdir(parents=True, exist_ok=True)
    CATALOGO.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")


def fotos_en_carpeta() -> list[pathlib.Path]:
    """
    Todas las fotos, INCLUIDAS LAS DE SUBCARPETAS.

    Antes solo miraba el primer nivel (`iterdir`), y eso escondía trabajo ya
    hecho: las 11 fotos de helado estaban en `dimango-marketing-fotos/helados/`
    y el agente no las veía. Como el helado es el producto número uno del
    negocio (D-019), el banco quedaba sin justo lo que más se vende y las
    propuestas salían de tortas y pizzas.

    Organizar por carpetas es lo natural para quien sube las fotos, así que se
    acomoda el código, no la persona. La huella es por contenido, así que dos
    archivos con el mismo nombre en carpetas distintas no se pisan.
    """
    if not CARPETA.exists():
        return []
    return sorted(
        p for p in CARPETA.rglob("*")
        if p.is_file()
        and p.suffix.lower() in EXTENSIONES
        and not p.name.startswith(".")
        and p.stat().st_size <= LIMITE_BYTES
    )


async def catalogar_nuevas(client, modelo: str, maximo: int = 10) -> dict:
    """
    Describe las fotos que todavía no están en el catálogo.

    `maximo` acota el gasto: si un día aparecen 200 fotos, se catalogan de a
    poco en vez de quemar la cuota en una corrida. Las que faltan entran mañana.
    """
    cat = cargar_catalogo()
    nuevas, errores = [], []

    for ruta in fotos_en_carpeta():
        if len(nuevas) >= maximo:
            break
        clave = _huella(ruta)
        if clave in cat:
            continue
        try:
            b64 = base64.b64encode(ruta.read_bytes()).decode("ascii")
            r = await client.messages.create(
                model=modelo,
                max_tokens=400,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": MIME.get(ruta.suffix.lower(), "image/jpeg"), "data": b64}},
                    {"type": "text", "text": PROMPT_CATALOGO},
                ]}],
            )
            texto = "".join(b.text for b in r.content if b.type == "text").strip()
            texto = texto.strip("`").removeprefix("json").strip()
            datos = json.loads(texto)
            datos.update({"archivo": ruta.name, "catalogada": datetime.now().isoformat()[:19]})
            cat[clave] = datos
            nuevas.append(datos)
        except Exception as e:
            errores.append(f"{ruta.name}: {e}")
            logger.warning(f"[MARKETING] no se pudo catalogar {ruta.name}: {e}")

    if nuevas:
        guardar_catalogo(cat)
    return {"nuevas": nuevas, "errores": errores, "total_catalogo": len(cat)}


def disponibles(categoria: str | None = None, solo_buenas: bool = True) -> list[dict]:
    """Fotos listas para usar, opcionalmente de una categoría."""
    fotos = list(cargar_catalogo().values())
    if solo_buenas:
        fotos = [f for f in fotos if f.get("calidad") in ("buena", "regular")]
    if categoria:
        fotos = [f for f in fotos if (f.get("categoria") or "").lower() == categoria.lower()]
    return fotos


def resumen() -> str:
    """Texto corto del banco, para meter en el prompt de la propuesta."""
    fotos = list(cargar_catalogo().values())
    if not fotos:
        return ("BANCO DE FOTOS: vacío. Sin fotos no se puede proponer contenido con imagen real — "
                f"hay que dejar fotos en {CARPETA}.")
    por_cat: dict[str, list[str]] = {}
    for f in fotos:
        por_cat.setdefault(f.get("categoria") or "otro", []).append(
            f"{f.get('producto', '?')} [{f.get('calidad', '?')}]"
        )
    lineas = [f"BANCO DE FOTOS ({len(fotos)} fotos catalogadas):"]
    for cat, items in sorted(por_cat.items()):
        lineas.append(f"  · {cat}: " + ", ".join(items[:8]))
    return "\n".join(lineas)
