# agent/canario_pagos.py — vigila que los caminos de pago sigan abiertos
#
# Por que existe (26-sep-2026): el pago con Klap desde la mesa estuvo caido y
# nadie se entero hasta que Ricardo lo probo. La causa era un `if (!user) 401`
# en crearPagoMesaKlap y configKlap: el cliente que paga por QR NO tiene sesion,
# asi que el boton aparecia y siempre respondia "No se pudo generar el pago".
# Era el unico de los cinco caminos de pago con ese candado.
#
# El sintoma de este tipo de falla no es un error en ninguna pantalla nuestra:
# es un cliente que se rinde y llama al garzon. Se pierde plata en silencio.
#
# Como prueba sin cobrar nada: llama a cada funcion SIN sesion y con el cuerpo
# vacio. Sin datos no se crea ninguna orden — la funcion responde un error de
# validacion. Lo que se vigila es el CODIGO de respuesta:
#
#     401  -> el candado volvio. El cliente NO puede pagar. Se avisa.
#     otro -> la funcion es alcanzable sin sesion. Bien.
#
# Falla en silencio hacia el negocio: si Telegram no responde, no rompe nada.

import os
import asyncio
import logging

import httpx

logger = logging.getLogger("agentkit")

APP_TOGO = "69b4f4b6d8a5cb6ca598d9e2"
BASE = f"https://base44.app/api/apps/{APP_TOGO}/functions"

# Las funciones que un cliente SIN sesion tiene que poder alcanzar para pagar.
CAMINOS = [
    ("configKlap", "config de Klap (carga el formulario de tarjeta)"),
    ("crearPagoMesaKlap", "pagar la mesa con tarjeta"),
    ("crearPagoMesaKhipu", "pagar la mesa por transferencia"),
    ("crearPagoMesaMP", "pagar la mesa con Mercado Pago"),
    ("crearPagoTortaKlap", "abonar una torta con tarjeta"),
    ("crearPagoTortaKhipu", "abonar una torta por transferencia"),
    ("obtenerPedidoTortaPublico", "ver el pedido de torta desde el link"),
]

# Se leen DENTRO de la funcion, no al importar: main.py importa antes de
# load_dotenv(), asi que a nivel de modulo llegan vacias y el canario nunca
# habria podido avisar.
def _chat() -> str:
    return os.getenv("TELEGRAM_CHAT_CONTROL", "").strip()


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


async def revisar() -> list[tuple[str, str, int]]:
    """Devuelve los caminos cerrados: [(funcion, descripcion, status)]."""
    cerrados: list[tuple[str, str, int]] = []
    async with httpx.AsyncClient(timeout=25) as cli:
        for nombre, desc in CAMINOS:
            try:
                r = await cli.post(f"{BASE}/{nombre}", json={})
            except Exception as e:
                logger.warning("[CANARIO] %s no respondio: %s", nombre, e)
                continue
            # 401/403 = exige sesion -> el cliente no puede pagar.
            if r.status_code in (401, 403):
                cerrados.append((nombre, desc, r.status_code))
                logger.error("[CANARIO] %s devolvio %s — camino de pago CERRADO", nombre, r.status_code)
            else:
                logger.info("[CANARIO] %s ok (%s)", nombre, r.status_code)
    return cerrados


async def avisar(cerrados: list[tuple[str, str, int]]) -> bool:
    chat, token = _chat(), _token()
    if not cerrados or not chat or not token:
        return False
    lineas = ["🚨 PAGO CAÍDO — el cliente no puede pagar", ""]
    for nombre, desc, status in cerrados:
        lineas.append(f"• {desc}")
        lineas.append(f"  ({nombre} responde {status}: exige sesión)")
    lineas += [
        "",
        "El botón aparece igual en la pantalla del cliente,",
        "pero siempre falla. Se pierde la venta sin aviso.",
    ]
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            await cli.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": "\n".join(lineas)},
            )
        return True
    except Exception as e:
        logger.warning("[CANARIO] no pude avisar por Telegram: %s", e)
        return False


async def revisar_y_avisar() -> dict:
    cerrados = await revisar()
    avisado = await avisar(cerrados)
    return {
        "revisados": len(CAMINOS),
        "cerrados": [{"funcion": n, "que_rompe": d, "status": s} for n, d, s in cerrados],
        "avisado": avisado,
    }


async def loop_canario(intervalo_horas: int = 12):
    """Revision periodica. Cada 12h: suficiente para no descubrirlo por un cliente."""
    logger.info("[CANARIO] vigilancia de caminos de pago iniciada")
    while True:
        try:
            r = await revisar_y_avisar()
            if r["cerrados"]:
                logger.error("[CANARIO] %s camino(s) de pago cerrados", len(r["cerrados"]))
        except Exception as e:
            logger.error("[CANARIO] error en la revision: %s", e)
        await asyncio.sleep(intervalo_horas * 3600)


if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(asyncio.run(revisar_y_avisar()), indent=2, ensure_ascii=False))
