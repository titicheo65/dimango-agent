# agent/puente_cuadratura.py — lleva la cuadratura de DiMangoToGo a DiMangoWorking
#
# Por que existe (2-oct-2026): la CuadraturaDiaria de Working se llenaba a mano
# con datos que DiMangoToGo ya tenia. Dos turnos por local, dos locales: unas
# 120 transcripciones al mes. En una sola noche se vieron las consecuencias: un
# digito faltante que invento $400.000 de descuadre, un punto decimal que
# invento $3,4 millones, y dos campos que no coincidian entre los sistemas.
# Ninguno era un problema de caja: los tres eran de transcripcion.
#
# Y no muere ahi: de CuadraturaDiaria salen el Dashboard, la Utilidad y el
# resumen semanal de Working. Un numero mal tecleado se propaga a todos.
#
# POR QUE EL PUENTE VIVE ACA y no dentro de DiMangoToGo:
#   - No hay que poner el secreto de Working dentro de ToGo. Cruzar credenciales
#     entre apps es como se filtran.
#   - Corre a hora fija, no en cada cierre. Un turno se puede reabrir y volver a
#     cerrar; escribir en Working cada vez ensucia mas de lo que ayuda.
#   - Si falla, se reintenta sin tocar ninguna de las dos apps.
#
# El que recibe (`recibirCuadraturaTogo`) tiene sus propias defensas: lista
# blanca de campos, no pisa lo que no se le envia, y NO escribe el total de
# tarjeta cuando Working ya lo tiene repartido entre maquinas y garzones
# --escribirlo ahi lo duplicaria--.

import os
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("agentkit")

URL_TOGO = "https://dimangotogo.base44.app/functions/cuadraturaParaWorking"
URL_WORKING = "https://dimangoworking.base44.app/functions/recibirCuadraturaTogo"
TZ = ZoneInfo("America/Santiago")


# Las variables se leen DENTRO de las funciones: main.py hace los imports antes
# de load_dotenv(), asi que un os.getenv() a nivel de modulo devuelve vacio en
# produccion (ya paso con el canario de pagos).
def _secreto_togo() -> str:
    return (os.getenv("DIMANGOTOGO_MAXIMUS_SECRET", "") or "").strip()


def _secreto_working() -> str:
    return (os.getenv("MAXIMUS_API_SECRET", "") or "").strip()


def _chat() -> str:
    return os.getenv("TELEGRAM_CHAT_CONTROL", "").strip()


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def fecha_de_ayer() -> str:
    """La fecha comercial que se sincroniza. Se corre de madrugada, asi que lo
    que interesa es el dia que acaba de cerrar."""
    return (datetime.now(tz=TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


async def _leer_togo(cli: httpx.AsyncClient, local: str, fecha: str) -> dict | None:
    r = await cli.post(URL_TOGO,
                       headers={"x-maximus-secret": _secreto_togo()},
                       json={"local": local, "fecha": fecha})
    if r.status_code != 200:
        logger.error("[CUADRATURA] ToGo %s %s devolvio %s", local, fecha, r.status_code)
        return None
    d = r.json()
    if not d.get("ok"):
        logger.error("[CUADRATURA] ToGo %s: %s", local, d.get("error"))
        return None
    return d


async def _escribir_working(cli: httpx.AsyncClient, cuerpo: dict) -> dict:
    r = await cli.post(URL_WORKING,
                       headers={"x-maximus-secret": _secreto_working()},
                       json=cuerpo)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "error": f"HTTP {r.status_code}"}


def _cuerpo(local: str, fecha: str, t: dict) -> dict:
    """Un turno de ToGo traducido a los campos de Working.

    Se mandan los DECLARADOS, no los totales del sistema: Working registra lo
    que el cajero conto, y esa diferencia ES la cuadratura. El unico que sale
    del sistema es `venta_sb`, que no se declara.
    """
    dec = t.get("declarado") or {}
    sis = t.get("sistema") or {}
    pro = t.get("propinas") or {}
    deliv = t.get("delivery") or {}
    return {
        "fecha": fecha,
        "local": local.upper(),
        "turno": (t.get("turno") or "AM").upper(),
        "cajera": t.get("cajero") or "",
        "efectivo_entrega": dec.get("efectivo_entrega") or 0,
        "tarjeta_caja_total": dec.get("vouchers") or 0,
        "transferencias_caja": dec.get("transferencias_caja") or 0,
        "online_caja": dec.get("online") or 0,
        "t_alim": dec.get("t_alim") or 0,
        "propina_tarjeta": pro.get("propina_tarjeta") or 0,
        "propina_efectivo": pro.get("propina_efectivo") or 0,
        "venta_sb": sis.get("venta_sb") or 0,
        "pedidosya": deliv.get("pedidosya") or 0,
        "ubereats_postres": deliv.get("ubereats_postres") or 0,
        "ubereats_rest": deliv.get("ubereats_rest") or 0,
    }


async def _avisar(texto: str) -> None:
    chat, token = _chat(), _token()
    if not chat or not token:
        logger.error("[CUADRATURA] aviso NO enviado: falta TELEGRAM_CHAT_CONTROL o TELEGRAM_BOT_TOKEN")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            await cli.post(f"https://api.telegram.org/bot{token}/sendMessage",
                           json={"chat_id": chat, "text": texto})
    except Exception as e:
        logger.error("[CUADRATURA] error avisando: %s", e)


async def sincronizar(fecha: str | None = None, avisar: bool = True) -> dict:
    fecha = fecha or fecha_de_ayer()
    if not _secreto_togo() or not _secreto_working():
        msg = "[CUADRATURA] falta DIMANGOTOGO_MAXIMUS_SECRET o MAXIMUS_API_SECRET"
        logger.error(msg)
        return {"ok": False, "error": msg}

    resultados: list[dict] = []
    async with httpx.AsyncClient(timeout=60) as cli:
        for local in ("playa", "mall"):
            datos = await _leer_togo(cli, local, fecha)
            if not datos:
                resultados.append({"local": local, "ok": False, "error": "sin datos de ToGo"})
                continue

            for t in datos.get("turnos", []):
                # Un turno sin nada declarado no se manda: escribir ceros encima
                # de una cuadratura cargada a mano la borraria.
                dec = t.get("declarado") or {}
                if not any(float(v or 0) for v in dec.values()):
                    resultados.append({"local": local, "turno": t.get("turno"),
                                       "ok": True, "accion": "omitido (sin declarar)"})
                    continue
                r = await _escribir_working(cli, _cuerpo(local, fecha, t))
                resultados.append({"local": local, "turno": t.get("turno"), **r})

    escritos = [r for r in resultados if r.get("ok") and r.get("accion") in ("creada", "actualizada")]
    fallidos = [r for r in resultados if not r.get("ok")]

    if avisar:
        lineas = [f"📋 Cuadratura → Working · {fecha}", ""]
        for r in resultados:
            loc = str(r.get("local", "?")).upper()
            tur = r.get("turno", "?")
            if not r.get("ok"):
                lineas.append(f"   ❌ {loc} {tur}: {r.get('error')}")
                continue
            acc = r.get("accion", "?")
            cambios = r.get("cambios") or {}
            detalle = f" · {len(cambios)} cambio(s)" if cambios else " · sin cambios"
            lineas.append(f"   ✅ {loc} {tur}: {acc}{detalle}")
            # Si no se escribio el total de tarjeta, hay que decirlo: significa
            # que alguien lo repartio a mano y ese reparto manda.
            if r.get("tarjeta_no_escrita"):
                tn = r["tarjeta_no_escrita"]
                lineas.append(f"      ⚠️ tarjeta no escrita: Working tiene ${tn.get('suma_actual_working'):,}"
                              .replace(",", ".") + f" repartido · ToGo dice ${tn.get('total_enviado'):,}".replace(",", "."))
        if fallidos:
            lineas.append("")
            lineas.append("Revisar: alguna cuadratura no se pudo escribir.")
        await _avisar("\n".join(lineas))

    return {"ok": not fallidos, "fecha": fecha, "escritos": len(escritos), "resultados": resultados}


async def loop_puente_cuadratura(hora: int = 3, minuto: int = 30):
    """Corre una vez al dia, de madrugada: a esa hora los turnos del dia
    anterior ya cerraron (el ultimo cierre observado fue 00:58)."""
    logger.info("[CUADRATURA] puente iniciado — corre a las %02d:%02d de Arica", hora, minuto)
    while True:
        ahora = datetime.now(tz=TZ)
        objetivo = ahora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
        if objetivo <= ahora:
            objetivo += timedelta(days=1)
        await asyncio.sleep((objetivo - ahora).total_seconds())
        try:
            r = await sincronizar()
            logger.info("[CUADRATURA] %s cuadratura(s) escritas", r.get("escritos"))
        except Exception as e:
            logger.error("[CUADRATURA] error en la sincronizacion: %s", e)


if __name__ == "__main__":
    import json
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    fecha = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(asyncio.run(sincronizar(fecha, avisar=False)), indent=2, ensure_ascii=False))
