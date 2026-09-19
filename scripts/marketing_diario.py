"""scripts/marketing_diario.py — Marketing DiMango, la corrida de cada día.

Qué hace, en orden:
  1. cataloga las fotos nuevas que Ricardo dejó en la carpeta;
  2. lee la venta real del día anterior (qué se vendió poco, qué sobra);
  3. lee cómo vienen las redes;
  4. investiga qué está pasando afuera (búsqueda web, acotada);
  5. propone dos o tres publicaciones concretas y las manda por Telegram.

NO publica nada. Todo pasa por aprobación de Ricardo — esa es la regla del
proyecto y no se automatiza en esta etapa.

Corre por Tarea Programada de Windows, una vez al día. Mismo patrón que
`advisor_diario.py`, que lleva meses funcionando: no se inventó nada nuevo.
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

from agent.maximus import client, MODELO                      # noqa: E402
from agent.marketing import banco_fotos, propuestas           # noqa: E402
from agent.marketing.insights import linea_base               # noqa: E402

TZ = ZoneInfo("America/Santiago")
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
HISTORIAL = BASE_DIR / "marketing_historial"
HISTORIAL.mkdir(exist_ok=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Grupo propio de marketing. Si no está configurado, cae al chat privado del
# dueño: la propuesta nunca se pierde en silencio.
CHAT_MARKETING = os.getenv("TELEGRAM_CHAT_MARKETING", "") or \
    os.getenv("TELEGRAM_OWNER_CHAT_IDS", "").split(",")[0].strip()

MAX_TROZO = 3900


async def enviar_telegram(texto: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_MARKETING:
        print("[marketing] falta TELEGRAM_BOT_TOKEN o el chat de destino")
        return False
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > MAX_TROZO:
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
                print("[marketing] Telegram respondió", r.status_code, r.text[:200])
                return False
    return True


async def ventas_de_ayer() -> str:
    """Lo que ya sabe hacer Maximus: se reutiliza su herramienta, no se duplica."""
    try:
        from agent.maximus import ejecutar_herramienta
        return await ejecutar_herramienta("ventas_dimango", {"dia": "ayer"})
    except Exception as e:
        return f"(No se pudo leer la venta de ayer: {e})"


async def main() -> None:
    hoy = datetime.now(TZ)
    print(f"[marketing] corrida {hoy:%Y-%m-%d %H:%M}")

    cat = await banco_fotos.catalogar_nuevas(client, MODELO)
    if cat["nuevas"]:
        print(f"[marketing] {len(cat['nuevas'])} fotos nuevas catalogadas")

    ventas = await ventas_de_ayer()
    redes = propuestas.resumen_redes(await linea_base())
    prompt = propuestas.armar_prompt(ventas, banco_fotos.resumen(), redes)

    r = await client.messages.create(
        model=MODELO,
        max_tokens=2000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": prompt}],
    )
    texto = "\n".join(b.text for b in r.content if b.type == "text").strip()
    if not texto:
        texto = "No pude armar propuestas hoy. Revisar el log."

    encabezado = f"📣 MARKETING DIMANGO — {hoy:%A %d-%m}\n"
    if not banco_fotos.cargar_catalogo():
        encabezado += "⚠️ El banco de fotos está vacío: las propuestas van sin imagen asignada.\n"
    cuerpo = encabezado + "\n" + texto

    (HISTORIAL / f"{hoy:%Y-%m-%d}.txt").write_text(cuerpo, encoding="utf-8")
    ok = await enviar_telegram(cuerpo)
    print("[marketing] enviado" if ok else "[marketing] NO se pudo enviar")


if __name__ == "__main__":
    asyncio.run(main())
