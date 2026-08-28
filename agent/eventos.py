"""agent/eventos.py — bus de eventos en memoria para Maximus Display.

Por qué existe: Maximus Display es una pantalla separada (para TV, tablet,
mini PC en la red local) que muestra en tiempo real qué está haciendo
Maximus — sin ser un segundo cerebro. Este módulo no decide nada, no
reemplaza nada de `responder()`: solo publica lo que ya está pasando, para
que quien esté mirando `/maximus/eventos` (Server-Sent Events) lo vea.

Regla de diseño, no negociable: publicar() JAMÁS debe bloquear ni romper
el flujo real de Maximus. Si nadie está mirando la pantalla, o un cliente
se quedó atrás, el evento simplemente se pierde — nunca se espera, nunca
se reintenta, nunca lanza una excepción hacia quien llama.
"""

import asyncio
import json
import logging
import time

logger = logging.getLogger("agentkit")

_suscriptores: set[asyncio.Queue] = set()


def suscribirse() -> asyncio.Queue:
    """Cada cliente SSE (cada pantalla conectada) tiene su propia cola."""
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _suscriptores.add(q)
    return q


def desuscribirse(q: asyncio.Queue) -> None:
    _suscriptores.discard(q)


async def publicar(tipo: str, **datos) -> None:
    """
    Envía un evento a todas las pantallas conectadas ahora mismo.
    Nunca espera, nunca lanza — un error acá no debe tumbar una respuesta
    real de Maximus.
    """
    try:
        evento = {"tipo": tipo, "ts": time.time(), **datos}
        linea = json.dumps(evento, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[EVENTOS] No se pudo serializar el evento '{tipo}': {e}")
        return

    for q in list(_suscriptores):
        try:
            q.put_nowait(linea)
        except asyncio.QueueFull:
            pass  # pantalla lenta o desconectada -- se pierde un evento, no se bloquea Maximus
        except Exception:
            pass
