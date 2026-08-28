"""run_maximus.py - arranque directo para PM2, sin pasar por cmd.exe/.bat.

Por que existe: el arranque anterior (`pm2 start iniciar_maximus.bat
--interpreter cmd.exe --interpreter-args "/c"`) se rompio solo, varias
veces (ver H-021 y su recurrencia del 27-ago-2026): PM2 en Windows no
guarda de forma confiable esa configuracion de interprete en su archivo
de respaldo, y con el tiempo intenta correr el .bat como si fuera
JavaScript ("SyntaxError: Invalid or unexpected token" en "@echo off").

Este wrapper elimina esa capa entera. PM2 corre este archivo Python
directo con --interpreter python (sin cmd.exe, sin .bat, sin argumentos
de linea de comando que PM2 pueda confundir con los suyos propios).
Host y puerto quedan fijos aca adentro, no como argumentos externos.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("agent.main:app", host="0.0.0.0", port=8000)
