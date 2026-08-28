"""scripts/advisor_diario.py — el Advisor: 3 recomendaciones rankeadas al día.

Por qué existe: idea tomada de la guía "How to Build a Personal Jarvis with
Claude" — el Scout junta datos, el Operator hace trabajo, y el Advisor lee
todo eso y dice qué hacer, con evidencia, y recuerda qué se recomendó la
semana pasada y sigue sin resolverse. Maximus ya tiene algo de Scout
(`gastos_dimango`, `bodega_dimango`, todo lectura) y de Operator (el
checklist operativo). Esta era la pieza que faltaba.

A diferencia de todo lo demás construido hoy (vigilante, correo,
calendario), ESTO SÍ LLAMA A CLAUDE — no es un script determinista gratis.
Una vez al día, mensaje corto: el costo es bajo pero no es cero.

Corre vía Tarea Programada de Windows (una vez al día, ej. 08:00 hora de
Chile), lee la misma memoria que ya usa Maximus (los 6 archivos de
~/harvey), llama a Claude una sola vez, y manda el resultado por Telegram.
Guarda cada día lo que recomendó en un archivo de texto simple en
advisor_historial/, para poder recordarte en el siguiente run qué sigue
sin resolverse.
"""

import asyncio
import os
import pathlib
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent.maximus import cargar_memoria, client, MODELO  # noqa: E402

TZ_CHILE = ZoneInfo("America/Santiago")
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "advisor_historial"
LOG_DIR.mkdir(exist_ok=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_OWNER_CHAT_IDS", "").split(",")[0].strip()

PROMPT_SISTEMA = """Eres el Advisor de Ricardo, dueño de DiMango (gastronomía, dos locales en Arica). Lees su memoria completa (decisiones, hallazgos, prioridades) y le dices qué hacer HOY.

Produce EXACTAMENTE 3 recomendaciones, rankeadas. Cada una con:
- La acción en una frase, algo que pueda empezar hoy mismo.
- La evidencia específica de la memoria que la respalda (cita el hallazgo, decisión o prioridad — por su nombre, ej. H-015, P-007).
- Qué pasa si la ignora esta semana.

Prioriza en este orden: lo que bloquea venta, margen o una decisión ya tomada; lo que está funcionando mejor de lo esperado y merece más atención; lo que se está degradando y se pone caro si se deja.

Reglas: nunca inventes una acción vaga como "mejorar la gestión". Apunta a lo específico: el local, el proveedor, la tarea, la nota. Si la memoria no sustenta una recomendación fuerte, dilo — "el día se ve rutinario" en vez de inventar una prioridad. Nunca trates un número que la memoria marca como estimado o pendiente como si fuera un hecho confirmado.

Al final, en una sección aparte, lista cualquier recomendación tuya de los últimos 7 días que sigue sin resolverse según la memoria actual — si no hay ninguna, dilo.

Formato para Telegram: texto plano, sin markdown pesado, corto, directo. Máximo 300 palabras."""


def _historial_reciente(dias: int = 7) -> str:
    hoy = datetime.now(TZ_CHILE).date()
    partes = []
    for i in range(1, dias + 1):
        fecha = hoy - timedelta(days=i)
        archivo = LOG_DIR / f"{fecha.isoformat()}.txt"
        if archivo.exists():
            partes.append(f"--- {fecha.isoformat()} ---\n{archivo.read_text(encoding='utf-8')}")
    return "\n\n".join(partes) if partes else "(sin historial previo — primera corrida)"


async def avisar(texto: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[advisor] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_OWNER_CHAT_IDS, no se puede avisar")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": texto},
            )
    except Exception as e:
        print(f"[advisor] Error avisando por Telegram: {e}")


async def main():
    memoria = cargar_memoria()
    historial = _historial_reciente()
    hoy = datetime.now(TZ_CHILE)

    mensaje_usuario = (
        f"MEMORIA ACTUAL:\n{memoria}\n\n"
        f"TUS RECOMENDACIONES DE LOS ÚLTIMOS 7 DÍAS:\n{historial}\n\n"
        f"Dame las 3 recomendaciones de hoy, {hoy.strftime('%A %d de %B, %Y')}."
    )

    respuesta = await client.messages.create(
        model=MODELO,
        max_tokens=800,
        system=PROMPT_SISTEMA,
        messages=[{"role": "user", "content": mensaje_usuario}],
    )
    texto = "".join(b.text for b in respuesta.content if hasattr(b, "text"))

    hoy_str = hoy.date().isoformat()
    (LOG_DIR / f"{hoy_str}.txt").write_text(texto, encoding="utf-8")

    await avisar(f"📋 Advisor — {hoy_str}\n\n{texto}")
    print(f"[advisor] Enviado, {len(texto)} caracteres")


if __name__ == "__main__":
    asyncio.run(main())
