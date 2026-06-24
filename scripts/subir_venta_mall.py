# scripts/subir_venta_mall.py
# Automatizacion: subir venta presencial diaria al Portal Tiendas de Mallplaza.
#
# Flujo (mapeado en vivo el 2026-06-24):
#   1. Login en https://portaltiendas.mallplaza.com/login
#   2. Menu "Ventas"
#   3. Lapiz "Editar venta" de la fila del dia de HOY
#   4. Llenar "Ventas presenciales": Transacciones + Total ventas netas en pesos
#   5. Boton MODIFICAR -> dialogo "¿Esta seguro...?" -> CONFIRMAR
#
# NOTA del portal: las ventas de HOY solo se pueden ingresar despues de las 18:00.
# Por eso esta tarea se programa a las 22:30.
#
# Toda la configuracion sensible (correo, clave) sale de variables de entorno / .env.
# NUNCA se hardcodean credenciales aqui.

import os
import sys
import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# --- Carga de .env (si python-dotenv esta disponible) ---
try:
    from dotenv import load_dotenv
    # Busca un .env junto a este script
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # Funciona igual si las variables ya estan en el entorno

# --- Configuracion ---
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
SHOT_DIR = BASE_DIR / "capturas"
LOG_DIR.mkdir(exist_ok=True)
SHOT_DIR.mkdir(exist_ok=True)

EMAIL = os.getenv("MALL_EMAIL", "")
PASSWORD = os.getenv("MALL_PASSWORD", "")
TRANSACCIONES = os.getenv("MALL_TRANSACCIONES", "35")
TOTAL = os.getenv("MALL_TOTAL", "650000")

# Flags de control
HEADLESS = os.getenv("MALL_HEADLESS", "1") == "1"      # 1 = sin ventana (para servidor)
DRY_RUN = os.getenv("MALL_DRY_RUN", "0") == "1"        # 1 = NO confirma (solo prueba)
FORCE = os.getenv("MALL_FORCE", "0") == "1"            # 1 = sobrescribe aunque ya haya venta

LOGIN_URL = "https://portaltiendas.mallplaza.com/login"

# --- Logging (a archivo por dia + consola) ---
HOY = datetime.now()
HOY_TABLA = HOY.strftime("%d-%m-%Y")  # formato de la tabla: 24-06-2026
log_file = LOG_DIR / f"venta_{HOY.strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("mall")


def captura(page, nombre: str):
    """Guarda una captura de pantalla para poder verificar despues."""
    ruta = SHOT_DIR / f"{HOY.strftime('%Y-%m-%d')}_{nombre}.png"
    try:
        page.screenshot(path=str(ruta))
        log.info(f"Captura guardada: {ruta.name}")
    except Exception as e:
        log.warning(f"No se pudo guardar captura {nombre}: {e}")


def main() -> int:
    if not EMAIL or not PASSWORD:
        log.error("Faltan MALL_EMAIL o MALL_PASSWORD en el entorno/.env. Aborto.")
        return 2

    log.info("=" * 50)
    log.info(f"Inicio subida de venta — fecha {HOY_TABLA}")
    log.info(f"Valores: transacciones={TRANSACCIONES}, total={TOTAL}")
    log.info(f"Modo: headless={HEADLESS}, dry_run={DRY_RUN}, force={FORCE}")

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=HEADLESS)
        contexto = navegador.new_context(viewport={"width": 1440, "height": 900})
        page = contexto.new_page()
        page.set_default_timeout(30000)

        try:
            # 1) LOGIN
            log.info("Abriendo login...")
            page.goto(LOGIN_URL, wait_until="networkidle")
            page.locator('input[type="email"]').first.fill(EMAIL)
            page.locator('input[type="password"]').first.fill(PASSWORD)
            page.get_by_role("button", name="INGRESAR").click()
            page.wait_for_url("**/home", timeout=30000)
            log.info("Login OK")

            # 2) Menu VENTAS
            page.get_by_role("link", name="Ventas").click()
            page.wait_for_url("**/ventas", timeout=30000)
            page.wait_for_load_state("networkidle")
            # Espera a que cargue la tabla
            page.get_by_text("Listado de Ventas").wait_for(timeout=30000)
            log.info("Pagina Ventas cargada")
            captura(page, "1_ventas")

            # 3) Fila del dia de HOY
            fila = page.locator("tr", has_text=HOY_TABLA).first
            fila.wait_for(timeout=15000)

            # Proteccion: si la fila de hoy YA tiene venta (no es "--"), no pisar salvo FORCE
            texto_fila = (fila.inner_text() or "").strip()
            # La fila vacia muestra "--" en las columnas. Si hay un numero de transacciones, ya hay datos.
            ya_tiene_datos = ("--" not in texto_fila.replace(HOY_TABLA, "")) or any(
                c.isdigit() for c in texto_fila.replace(HOY_TABLA, "").replace("-", "")
            )
            if ya_tiene_datos and not FORCE:
                log.warning(
                    f"La fila de hoy ({HOY_TABLA}) ya parece tener venta cargada: '{texto_fila}'. "
                    "No se sobrescribe (usa MALL_FORCE=1 para forzar). Saliendo sin cambios."
                )
                captura(page, "2_fila_ya_cargada")
                return 0

            # Abrir el lapiz "Editar venta" de esa fila
            try:
                fila.get_by_role("button", name="Editar venta").click()
            except PWTimeout:
                fila.locator("button").last.click()  # respaldo: ultimo boton de la fila (Acciones)
            log.info("Modal de edicion abierto")

            # 4) Llenar Ventas presenciales (primer par de campos)
            page.get_by_text("Modificar Ventas").wait_for(timeout=15000)
            trans_inputs = page.get_by_placeholder("Transacciones")
            total_inputs = page.get_by_placeholder("Total ventas netas en pesos")
            trans_inputs.first.fill(TRANSACCIONES)        # presencial
            total_inputs.first.fill(TOTAL)                # presencial

            # Asegurar que "Ventas online" quede vacio (el portal a veces autollena 1/1)
            try:
                if trans_inputs.count() > 1:
                    trans_inputs.nth(1).fill("")
                if total_inputs.count() > 1:
                    total_inputs.nth(1).fill("")
            except Exception as e:
                log.warning(f"No se pudieron limpiar campos online: {e}")

            captura(page, "3_formulario_lleno")
            log.info(f"Formulario lleno: {TRANSACCIONES} / {TOTAL}")

            if DRY_RUN:
                log.info("DRY_RUN activo: NO se confirma. Prueba terminada OK.")
                captura(page, "4_dryrun_sin_confirmar")
                return 0

            # 5) MODIFICAR -> CONFIRMAR
            page.get_by_role("button", name="MODIFICAR").click()
            page.get_by_role("button", name="CONFIRMAR").click()
            page.wait_for_timeout(2500)  # esperar respuesta del portal
            captura(page, "4_post_confirmar")

            # Verificar resultado
            cuerpo = page.inner_text("body")
            if "18:00" in cuerpo and "despu" in cuerpo.lower():
                log.error(
                    "El portal rechazo por horario (solo despues de las 18:00). "
                    "Revisa que la tarea corra a las 22:30."
                )
                return 3

            # Releer la fila de hoy para confirmar que quedo registrada
            page.wait_for_timeout(1500)
            try:
                fila2 = page.locator("tr", has_text=HOY_TABLA).first
                txt2 = (fila2.inner_text() or "").strip()
                if TRANSACCIONES in txt2:
                    log.info(f"VENTA REGISTRADA OK. Fila hoy: '{txt2}'")
                else:
                    log.warning(f"No pude confirmar el registro en la tabla. Fila hoy: '{txt2}'")
            except Exception as e:
                log.warning(f"No pude releer la fila de hoy: {e}")

            log.info("Proceso finalizado.")
            return 0

        except PWTimeout as e:
            log.error(f"Timeout esperando un elemento: {e}")
            captura(page, "ERROR_timeout")
            return 1
        except Exception as e:
            log.error(f"Error inesperado: {e}")
            captura(page, "ERROR")
            return 1
        finally:
            contexto.close()
            navegador.close()


if __name__ == "__main__":
    sys.exit(main())
