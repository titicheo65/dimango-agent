# agent/alerta_stock.py — avisa por Telegram antes de que un producto quede en cero
#
# Por que existe (26-sep-2026): el stock se descubria agotado con el cliente ya
# esperando. Le paso a Ricardo con una torta de 7 sabores: "esta con stock 10
# und pero sale agotada". En TelegramConfig habia un campo
# `notificar_alerta_stock` que NADIE usaba -- estaba el interruptor y nunca se
# construyo el aparato.
#
# Mira INSUMOS, no productos (corregido 26-sep-2026): la primera version leia
# el stock de Product y solo avisaba de tortas enteras. Solo entran los que
# tienen 'Descuenta' (controla_stock) y/o 'Corta venta' (bloquea_venta)
# encendido, que es lo que Ricardo controla de verdad.
#
# Umbral: `stock_minimo` del insumo en ese local si esta cargado; si no, 2.
#
# Lo dificil de una alerta asi no es mandarla: es NO repetirla. Un aviso cada
# 20 minutos del mismo producto se ignora en un dia, y despues se ignoran
# todos. Por eso:
#   - de cada producto se avisa UNA vez, y no se vuelve a nombrar hasta que
#     suba del umbral (se repuso) o pasen SILENCIO_HORAS;
#   - los avisos se juntan en un solo mensaje por local, no uno por producto;
#   - si un producto PASA a cero habiendo avisado antes en 1 o 2, se vuelve a
#     avisar: quedarse sin nada es noticia distinta de estar por acabarse.

import os
import asyncio
import logging
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger("agentkit")

URL_STOCK_BAJO = "https://dimangotogo.base44.app/functions/stockBajoDimango"


# Las variables se leen DENTRO de las funciones, no al importar el modulo:
# main.py hace los imports (linea ~40) ANTES de load_dotenv() (linea ~47), asi
# que cualquier os.getenv() a nivel de modulo devuelve vacio en produccion.
# El nombre real en el .env es DIMANGOTOGO_MAXIMUS_SECRET (asi lo lee maximus.py).
def _secret() -> str:
    return (os.getenv("DIMANGOTOGO_MAXIMUS_SECRET", "")
            or os.getenv("DIMANGOTOGO_SECRET", "")
            or os.getenv("MAXIMUS_API_SECRET", "")).strip()


def _chat() -> str:
    return os.getenv("TELEGRAM_CHAT_CONTROL", "").strip()


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

SILENCIO_HORAS = 12
LOCAL_LEGIBLE = {"playa": "Playa Chinchorro", "mall": "Mall Plaza Arica"}

# producto_id+local -> (cuando se aviso, con cuanta cantidad)
_avisados: dict[str, tuple[datetime, int]] = {}


async def _consultar() -> list[dict]:
    secret = _secret()
    if not secret:
        logger.error("[STOCK] falta DIMANGOTOGO_MAXIMUS_SECRET — no puedo consultar el stock")
        return []
    async with httpx.AsyncClient(timeout=40) as cli:
        r = await cli.post(URL_STOCK_BAJO, headers={"x-maximus-secret": secret}, json={})
    if r.status_code != 200:
        logger.error("[STOCK] stockBajoDimango devolvio %s", r.status_code)
        return []
    d = r.json()
    if not d.get("ok"):
        logger.error("[STOCK] %s", d.get("error"))
        return []
    return d.get("insumos", [])


def _hay_que_avisar(p: dict, ahora: datetime) -> bool:
    clave = f"{p['id']}:{p['local']}"
    previo = _avisados.get(clave)
    if previo is None:
        return True
    cuando, cantidad_avisada = previo
    # Se quedo en cero despues de haber avisado con 1 o 2: es noticia nueva.
    if p["stock"] <= 0 < cantidad_avisada:
        return True
    return ahora - cuando > timedelta(hours=SILENCIO_HORAS)


async def _enviar(texto: str) -> bool:
    chat, token = _chat(), _token()
    if not chat or not token:
        logger.error("[STOCK] AVISO NO ENVIADO: falta TELEGRAM_CHAT_CONTROL o TELEGRAM_BOT_TOKEN")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": texto},
            )
        if r.status_code != 200:
            logger.error("[STOCK] Telegram devolvio %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        logger.error("[STOCK] error enviando a Telegram: %s", e)
        return False


async def revisar_y_avisar() -> dict:
    ahora = datetime.utcnow()
    productos = await _consultar()

    # Lo que ya se repuso deja de estar en la lista: se olvida, para que la
    # proxima vez que baje vuelva a avisar.
    vigentes = {f"{p['id']}:{p['local']}" for p in productos}
    for clave in list(_avisados):
        if clave not in vigentes:
            del _avisados[clave]

    nuevos = [p for p in productos if _hay_que_avisar(p, ahora)]
    if not nuevos:
        return {"revisados": len(productos), "avisados": 0}

    enviados = 0
    for local in ("playa", "mall"):
        delLocal = [p for p in nuevos if p["local"] == local]
        if not delLocal:
            continue

        # Lo primero no es lo que esta en cero: es lo que al estar en cero
        # TUMBA PLATOS de la carta (corta_venta). Eso no es un numero feo,
        # es parte del menu que el cliente ya no puede pedir.
        tumbando = [p for p in delLocal if p["agotado"] and p["corta_venta"]]
        agotados = [p for p in delLocal if p["agotado"] and not p["corta_venta"]]
        porAcabarse = [p for p in delLocal if not p["agotado"]]

        lineas = [f"📦 STOCK — {LOCAL_LEGIBLE.get(local, local)}", ""]
        if tumbando:
            lineas.append("🔴 EN CERO y CORTA VENTA — hay platos agotados ahora:")
            for p in tumbando:
                lineas.append(f"   • {p['nombre']}" + (f"  [{p['area']}]" if p["area"] else ""))
            lineas.append("")
        if agotados:
            lineas.append("🟠 En cero:")
            for p in agotados:
                lineas.append(f"   • {p['nombre']}" + (f"  [{p['area']}]" if p["area"] else ""))
            lineas.append("")
        if porAcabarse:
            lineas.append("🟡 Por acabarse:")
            for p in porAcabarse:
                u = f" {p['unidad']}" if p.get("unidad") else ""
                lineas.append(f"   • {p['nombre']} — quedan {p['stock']}{u}")
        if await _enviar("\n".join(lineas).rstrip()):
            enviados += len(delLocal)
            for p in delLocal:
                _avisados[f"{p['id']}:{p['local']}"] = (ahora, p["stock"])

    return {"revisados": len(productos), "avisados": enviados}


async def loop_alerta_stock(intervalo_minutos: int = 30):
    logger.info("[STOCK] vigilancia de stock bajo iniciada (cada %s min)", intervalo_minutos)
    while True:
        try:
            r = await revisar_y_avisar()
            if r["avisados"]:
                logger.info("[STOCK] %s producto(s) avisados", r["avisados"])
        except Exception as e:
            logger.error("[STOCK] error en la revision: %s", e)
        await asyncio.sleep(intervalo_minutos * 60)


if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(asyncio.run(revisar_y_avisar()), indent=2, ensure_ascii=False))
