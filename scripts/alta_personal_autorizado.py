"""Alta inicial del personal de confianza (12-sep-2026).

Se corre UNA vez en ServidorPlaya, desde C:\\dimango-agent:

    python -m scripts.alta_personal_autorizado

Después de esto, Ricardo administra todo desde su WhatsApp:
    autorizados · clave de Noemi 7821 · bloquear a Carlos · quitar a Carlos

Nota deliberada: las claves están acá porque este archivo es el alta inicial y
el repositorio es privado. Si Ricardo las cambia por WhatsApp —que es lo que
debería pasar— este archivo queda obsoleto y no vuelve a correrse. No es la
fuente de verdad: la fuente es la tabla.
"""

import asyncio

from agent.personal_autorizado import inicializar_personal, upsert_persona, listar_texto

# telefono, nombre, clave, local
PERSONAL = [
    ("+56977962181", "Carlos",   "3592", "playa"),
    ("+56974596595", "Alejandra", "4592", "playa"),
    ("+56967210490", "Angelica", "5592", "playa"),
    ("+56997716349", "Noemi",    "6592", "playa"),
]


async def main():
    await inicializar_personal()
    for telefono, nombre, clave, local in PERSONAL:
        await upsert_persona(telefono, nombre, clave, local)
        print(f"  alta: {nombre} ({telefono}) · {local}")
    print()
    print(await listar_texto())


if __name__ == "__main__":
    asyncio.run(main())
