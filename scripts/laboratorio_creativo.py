"""scripts/laboratorio_creativo.py — Laboratorio Creativo, una vez por semana.

Para qué: Ricardo quiere APRENDER estas herramientas mientras construye su
agencia, no solo consumirlas. Este módulo investiga qué hay nuevo en IA para
imagen, video, publicidad y automatización, y propone UNA prueba concreta
aplicable a DiMango, con su costo y qué se aprendería.

Tres reglas que lo hacen útil en vez de ruido:

1. **Una prueba por semana, no cinco.** Una lista de diez herramientas no se
   prueba nunca. Una sola, con pasos, se hace el sábado en la tarde.
2. **Aplicable a un local de comida en Arica**, no a una agencia de Nueva York.
   Si necesita un equipo de tres personas, no sirve.
3. **Con costo al frente.** Ricardo aprueba antes de contratar cualquier
   servicio: esa fue una condición explícita del proyecto.

No contrata nada, no gasta nada fuera de su propia búsqueda. Propone.
"""
import asyncio
import os
import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent.maximus import client, MODELO   # noqa: E402

TZ = ZoneInfo("America/Santiago")
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
HISTORIAL = BASE_DIR / "laboratorio_historial"
HISTORIAL.mkdir(exist_ok=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_MARKETING = os.getenv("TELEGRAM_CHAT_MARKETING", "") or \
    os.getenv("TELEGRAM_OWNER_CHAT_IDS", "").split(",")[0].strip()

PROMPT = """Eres el Laboratorio Creativo de DiMango: un restaurante-heladería-pizzería con
dos locales en Arica, Chile. Quien te lee es el dueño, que quiere APRENDER a
usar estas herramientas mientras arma su propia agencia de marketing con IA.

Busca en internet qué hay de nuevo o poco explotado en:
· generación y edición de imágenes para gastronomía,
· video corto y reels,
· publicidad y automatización de contenido.

Después entrega EXACTAMENTE esto:

1) LO NUEVO DE LA SEMANA — tres hallazgos, una línea cada uno, con el nombre
   real de la herramienta. Nada de tendencias abstractas.

2) LA PRUEBA DE ESTA SEMANA — UNA sola, la que más rinda para DiMango:
   · Qué es y qué resuelve
   · Para qué la usaría DiMango, concreto (ej: "los conos de helado en el feed")
   · Costo real: plan gratis o cuánto sale probarla. Si no encuentras el precio,
     dilo: "precio no verificado", nunca lo inventes
   · Cómo probarla en menos de 30 minutos, paso a paso
   · Qué se aprende haciéndola
   · Qué puede salir mal

3) LO QUE DESCARTÉ — dos herramientas que miraste y por qué NO valen para
   DiMango. Esto importa tanto como lo que recomiendas.

Reglas: español de Chile. Nada de superlativos de vendedor. Si algo exige un
equipo grande o un presupuesto alto, descártalo y dilo. Si una herramienta
promete resultados "imposibles de distinguir de una foto real", desconfía y
acláralo: DiMango publica fotos reales y no quiere que su publicidad se vea
artificial."""


async def enviar(texto: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_MARKETING:
        print("[laboratorio] falta token o chat de destino")
        return False
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > 3900:
            trozos.append(actual); actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        trozos.append(actual)
    async with httpx.AsyncClient(timeout=30) as cli:
        for t in trozos:
            r = await cli.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                               json={"chat_id": CHAT_MARKETING, "text": t})
            if r.status_code != 200:
                print("[laboratorio] Telegram:", r.status_code, r.text[:200])
                return False
    return True


def ya_probadas() -> str:
    """Lo recomendado antes, para que no repita la misma herramienta cada semana."""
    previos = sorted(HISTORIAL.glob("*.txt"))[-6:]
    if not previos:
        return ""
    nombres = []
    for p in previos:
        for linea in p.read_text(encoding="utf-8").split("\n"):
            if "PRUEBA DE ESTA SEMANA" in linea.upper():
                idx = p.read_text(encoding="utf-8").split("\n")
                pos = idx.index(linea)
                nombres.append(" ".join(idx[pos + 1: pos + 3]).strip()[:80])
                break
    if not nombres:
        return ""
    return ("\n\nYA RECOMENDADO ANTES (no repitas, salvo que haya algo nuevo de verdad):\n"
            + "\n".join(f"· {n}" for n in nombres if n))


async def main() -> None:
    hoy = datetime.now(TZ)
    print(f"[laboratorio] corrida {hoy:%Y-%m-%d}")

    r = await client.messages.create(
        model=MODELO,
        max_tokens=2200,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        messages=[{"role": "user", "content": PROMPT + ya_probadas()}],
    )
    texto = "\n".join(b.text for b in r.content if b.type == "text").strip()
    if not texto:
        texto = "No pude completar la investigación de esta semana. Revisar el log."

    cuerpo = f"🧪 LABORATORIO CREATIVO — semana del {hoy:%d-%m-%Y}\n\n{texto}"
    (HISTORIAL / f"{hoy:%Y-%m-%d}.txt").write_text(cuerpo, encoding="utf-8")
    print("[laboratorio] enviado" if await enviar(cuerpo) else "[laboratorio] NO se pudo enviar")


if __name__ == "__main__":
    asyncio.run(main())
