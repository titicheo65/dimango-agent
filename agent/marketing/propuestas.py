"""
propuestas — qué conviene publicar hoy, y por qué.

La idea que ordena este módulo: la decisión de QUÉ promocionar no sale del
gusto de nadie, sale de los datos. Qué se vendió poco, qué sobra, qué horario
está flojo, qué funcionó el mes pasado en redes. El modelo escribe el texto y
elige el ángulo; los hechos los pone el negocio.

Nada de esto publica. Arma una propuesta y la manda a aprobación por Telegram.
Publicar es otra etapa y otro permiso de Meta.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger("agentkit")

# Fechas que mueven venta en Arica. Se listan explícitas porque un modelo
# preguntado por "fechas comerciales" inventa efemérides gringas que acá no
# significan nada.
FECHAS_CHILE = {
    (2, 14): "San Valentín", (3, 1): "vuelta a clases",
    (5, 1): "Día del Trabajador", (5, 21): "Glorias Navales",
    (6, 21): "Día del Padre (tercer domingo de junio, confirmar)",
    (6, 29): "Año Nuevo Aymara / We Tripantu",
    (8, 15): "Asunción", (9, 18): "Fiestas Patrias", (9, 19): "Glorias del Ejército",
    (10, 12): "Encuentro de Dos Mundos", (10, 31): "Halloween",
    (11, 1): "Día de Todos los Santos", (12, 8): "Inmaculada Concepción",
    (12, 24): "Nochebuena", (12, 25): "Navidad", (12, 31): "Año Nuevo",
}


def fechas_proximas(dias: int = 21) -> list[str]:
    hoy = date.today()
    cerca = []
    for (m, d), nombre in FECHAS_CHILE.items():
        try:
            f = date(hoy.year, m, d)
            if f < hoy:
                f = date(hoy.year + 1, m, d)
        except ValueError:
            continue
        faltan = (f - hoy).days
        if 0 <= faltan <= dias:
            cerca.append(f"{nombre} — en {faltan} día(s), el {f.strftime('%d-%m')}")
    return sorted(cerca, key=lambda s: int(s.split("en ")[1].split(" ")[0]))


INSTRUCCION = """Eres el encargado de marketing de DiMango, con dos locales en Arica:
Playa Chinchorro (casa matriz) y Mall Plaza Arica.

===== QUIÉN ES DIMANGO (definido por Ricardo, 23-sep-2026 — D-019) =====

DiMango es una HELADERÍA que creció hasta ser restaurante. La razón social es
"Gelatería DiMango Limitada" y los números le dan la razón: los helados son la
categoría número uno, y los tres productos más vendidos del negocio son helado.
Ricardo lo dice así: "gracias a los helados tenemos todo".
→ El helado es la puerta de entrada, no el relleno. Va adelante, no al final.

EL CLIENTE es LA FAMILIA, y lo que la trae es LA VARIEDAD: en la misma mesa el
niño pide helado, el papá pizza y la abuela té. DiMango gana porque nadie tiene
que ceder.
→ Una mesa con cinco cosas distintas comunica más que un plato solo y perfecto.

EL NEGOCIO VIVE DE NOCHE. Las ventas se ponen fuertes desde las 18:30; el
almuerzo es flojo.
→ El momento de decisión del cliente es LA TARDE. Publicar de mañana es hablarle
   a nadie. Propón horarios de tarde salvo que un dato diga lo contrario.

DIMANGO ES DONDE EMPIEZA LA NOCHE, NO DONDE TERMINA. Se puede hablar de la
previa, del grupo de amigos, del after de la playa.
→ NUNCA hablar de carrete, fiesta, trago barato ni música fuerte. Eso espanta a
   la familia, que es el cliente que vuelve.

EL SLOGAN YA EXISTE y no se reemplaza: "La calidad de mi negocio la hacen mis
clientes". Habla de la gente, no del producto — igual que todo lo de arriba.
→ No inventes slogans nuevos.

Tu trabajo hoy: proponer DOS o TRES publicaciones concretas para Instagram y Facebook.

REGLAS QUE NO SE NEGOCIAN:
1. Solo puedes usar fotos que estén en el banco. Si no hay foto para una idea,
   dilo y pide la foto que falta — nunca propongas generar el plato con IA.
2. Cada propuesta se justifica con un DATO, no con una opinión: qué se vende
   poco, qué sobra, qué horario está flojo, qué formato rindió antes.
3. Nada de superlativos vacíos ("los mejores de Arica", "increíble"). Habla
   como habla un local de barrio a sus clientes: directo y sin inflar.
4. Precios: solo los que aparezcan en los datos. Nunca inventes uno.
5. Escribe en español de Chile, sin voseo argentino ni tuteo español.

FORMATO de cada propuesta, exacto:

━━━━━━━━━━━━━━━━━━━━
PROPUESTA N
Local: Playa / Mall / los dos
Formato: feed | historia | carrusel
Foto a usar: <nombre exacto del archivo del banco>
Cuándo: día y hora, con una razón
Texto:
<el texto listo para copiar y pegar, con sus emojis si corresponden>
Hashtags: <5 a 8, mezcla locales y de rubro>
Por qué esto: <el dato que la justifica, una línea>
━━━━━━━━━━━━━━━━━━━━

Cierra con una línea: "FALTA:" y lo que necesitas de Ricardo (una foto puntual,
un precio, una definición). Si no falta nada, escribe "FALTA: nada"."""


def armar_prompt(ventas_texto: str, banco_texto: str, redes_texto: str,
                 investigacion: str = "") -> str:
    """Junta todo lo que el modelo necesita para proponer con fundamento."""
    hoy = datetime.now()
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    partes = [
        INSTRUCCION,
        f"\n\n===== HOY =====\n{dias_es[hoy.weekday()]} {hoy.strftime('%d-%m-%Y')}, {hoy.strftime('%H:%M')} en Arica",
    ]
    proximas = fechas_proximas()
    if proximas:
        partes.append("\n===== FECHAS QUE VIENEN =====\n" + "\n".join(f"· {f}" for f in proximas))
    partes.append("\n===== QUÉ PASA EN EL NEGOCIO =====\n" + (ventas_texto or "Sin datos de venta hoy."))
    partes.append("\n===== " + banco_texto)
    if redes_texto:
        partes.append("\n===== CÓMO VAN LAS REDES =====\n" + redes_texto)
    if investigacion:
        partes.append("\n===== LO QUE ENCONTRÉ INVESTIGANDO HOY =====\n" + investigacion)
    return "\n".join(partes)


def resumen_redes(linea_base: dict) -> str:
    """Convierte las métricas en tres líneas que el modelo pueda usar."""
    if not linea_base or not linea_base.get("ok"):
        return ""
    lineas = []
    for c in linea_base.get("cuentas", []):
        a = c.get("analisis") or {}
        if not a.get("total"):
            continue
        tipos = a.get("por_tipo", {})
        mejor_tipo = max(tipos.items(), key=lambda kv: kv[1]["promedio"], default=(None, None))
        dias = list(a.get("por_dia", {}).items())
        lineas.append(
            f"· {c.get('ig_usuario') or c.get('pagina')}: {c.get('seguidores', '?')} seguidores. "
            f"{a['total']} publicaciones analizadas, interacción promedio {a['promedio_interaccion']}."
        )
        if mejor_tipo[0]:
            lineas.append(f"  El formato que más rinde: {mejor_tipo[0]} (promedio {mejor_tipo[1]['promedio']}).")
        if dias:
            lineas.append(f"  El mejor día: {dias[0][0]} (promedio {dias[0][1]['promedio']}).")
        if a.get("mejores"):
            lineas.append(f"  Lo que mejor funcionó: \"{a['mejores'][0].get('texto', '')[:70]}\".")
    return "\n".join(lineas)
