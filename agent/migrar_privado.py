#!/usr/bin/env python3
"""
Mueve las conversaciones privadas de Ricardo fuera de la base del agente.

Desde la separación, lo que Ricardo habla con Maximus se guarda en otra base
(`maximus_privado.db`). Pero lo conversado ANTES quedó en la base de clientes,
que es la que alimenta el panel /admin. Esto lo traslada.

    python3 -m agent.migrar_privado            # muestra qué haría, no toca nada
    python3 -m agent.migrar_privado --aplicar  # mueve de verdad

Copia primero y borra después, dentro de la misma corrida: si algo falla al
copiar, no se borró nada todavía.
"""

import asyncio
import sys

from sqlalchemy import select

from agent.memory import (
    Mensaje,
    async_session,
    session_privada,
    es_privada,
    inicializar_db,
)


async def migrar(aplicar: bool):
    await inicializar_db()

    async with async_session() as origen:
        filas = (await origen.execute(select(Mensaje))).scalars().all()
        privados = [m for m in filas if es_privada(m.telefono)]

        if not privados:
            print("No hay conversaciones privadas en la base de clientes. Nada que mover.")
            return

        porsesion = {}
        for m in privados:
            porsesion[m.telefono] = porsesion.get(m.telefono, 0) + 1

        print(f"Mensajes privados en la base de clientes: {len(privados)}")
        for sesion, n in sorted(porsesion.items(), key=lambda x: -x[1]):
            print(f"   {sesion:<24} {n} mensajes")

        if not aplicar:
            print("\nEsto fue una simulación. Para moverlos de verdad:")
            print("   python3 -m agent.migrar_privado --aplicar")
            return

        # 1. Copiar a la base privada
        async with session_privada() as destino:
            for m in privados:
                destino.add(Mensaje(
                    telefono=m.telefono,
                    role=m.role,
                    content=m.content,
                    timestamp=m.timestamp,
                ))
            await destino.commit()
        print(f"\nCopiados {len(privados)} mensajes a la base privada.")

        # 2. Recién ahora, borrar del origen
        for m in privados:
            await origen.delete(m)
        await origen.commit()
        print(f"Borrados {len(privados)} mensajes de la base de clientes.")
        print("\nListo. El panel /admin ya no puede mostrarlos.")


if __name__ == "__main__":
    asyncio.run(migrar("--aplicar" in sys.argv))
