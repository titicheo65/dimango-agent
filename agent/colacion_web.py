# agent/colacion_web.py — Pantalla en vivo de colación + gestión de personal
# Generado por AgentKit

"""
Tablero web del control de colación. Se abre en un tablet/pantalla en cada local.
- Muestra en vivo quién está en colación y cuánto le queda.
- Alerta sonora y visual cuando alguien se pasa de los 30 min (para el encargado).
- Historial de colaciones del día (control de disciplina).
- Formulario para registrar/editar el personal (nombre, WhatsApp, local, encargado).

Acceso: https://TU-URL/colacion
"""

import os
import logging
import secrets
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from agent.colacion import (
    listar_activas,
    historial_hoy,
    listar_empleados,
    guardar_empleado,
    eliminar_empleado,
    terminar_colacion,
    buscar_empleado,
    colacion_activa,
    iniciar_colacion,
    colaciones_rango,
    set_activo_empleado,
    estado_cumplimiento_hoy,
    DIAS_SEMANA,
    LIMITE_MIN,
)
# Reusa el login del panel /admin (misma usuario/contraseña del .env)
from agent.admin import verificar_admin

logger = logging.getLogger("agentkit")

router = APIRouter(prefix="/colacion", tags=["colacion"])

# Token para que la app Base44 (dimangotogo.base44.app/Colacion) consuma el API
# sin la contraseña del panel. Se configura en el .env del servidor.
COLACION_TOKEN = os.getenv("COLACION_TOKEN", "")


def verificar_token(token: str = "") -> None:
    """Valida el token de acceso del API público (usado por la página Base44)."""
    if not COLACION_TOKEN or not secrets.compare_digest(token, COLACION_TOKEN):
        raise HTTPException(status_code=401, detail="Token inválido")


class EmpleadoRequest(BaseModel):
    nombre: str
    telefono: str
    local: str
    es_encargado: bool = False
    dias_libres: list[int] = []


class TelefonoRequest(BaseModel):
    telefono: str


class ActivoRequest(BaseModel):
    telefono: str
    activo: bool


@router.get("", response_class=HTMLResponse)
async def pantalla(_: str = Depends(verificar_admin)):
    """Sirve la pantalla en vivo de colación (privada, requiere login)."""
    return HTML_COLACION


@router.get("/api/estado")
async def api_estado(_: str = Depends(verificar_admin)):
    """Estado completo para la pantalla: activas, historial, personal y cumplimiento."""
    return {
        "limite_min": LIMITE_MIN,
        "activas": await listar_activas(),
        "historial": await historial_hoy(),
        "empleados": await listar_empleados(),
        "cumplimiento": await estado_cumplimiento_hoy(),
    }


@router.post("/api/empleados")
async def api_guardar_empleado(req: EmpleadoRequest, _: str = Depends(verificar_admin)):
    """Registra o actualiza un empleado."""
    try:
        await guardar_empleado(req.nombre, req.telefono, req.local,
                               req.es_encargado, req.dias_libres)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"[COLACIÓN] Empleado guardado: {req.nombre} ({req.local})")
    return {"ok": True}


@router.post("/api/empleados/eliminar")
async def api_eliminar_empleado(req: TelefonoRequest, _: str = Depends(verificar_admin)):
    """Elimina un empleado del registro."""
    await eliminar_empleado(req.telefono)
    return {"ok": True}


@router.post("/api/terminar")
async def api_terminar(req: TelefonoRequest, _: str = Depends(verificar_admin)):
    """Permite al encargado cerrar manualmente una colación desde la pantalla."""
    col = await terminar_colacion(req.telefono)
    return {"ok": col is not None}


async def _iniciar_manual(telefono: str) -> dict:
    """Lógica común para iniciar una colación a mano (encargado / trabajador sin celular)."""
    emp = await buscar_empleado(telefono)
    if not emp:
        raise HTTPException(status_code=404, detail="Trabajador no encontrado")
    if await colacion_activa(emp.telefono):
        return {"ok": False, "detail": f"{emp.nombre} ya está en colación"}
    await iniciar_colacion(emp)
    logger.info(f"[COLACIÓN] Inicio manual (encargado) para {emp.nombre} ({emp.local})")
    return {"ok": True}


@router.post("/api/iniciar")
async def api_iniciar(req: TelefonoRequest, _: str = Depends(verificar_admin)):
    """El encargado inicia manualmente la colación de un trabajador (sin WhatsApp)."""
    return await _iniciar_manual(req.telefono)


# ════════════════════════════════════════════════════════════
# Recordatorio diario — "libre por hoy" y estado de cumplimiento
# ════════════════════════════════════════════════════════════

@router.get("/api/cumplimiento")
async def api_cumplimiento(_: str = Depends(verificar_admin)):
    """Estado de cumplimiento del día (tomadas/pendientes/libres + % logro)."""
    return await estado_cumplimiento_hoy()


@router.post("/api/activo")
async def api_activo(req: ActivoRequest, _: str = Depends(verificar_admin)):
    """Activa o desactiva a un trabajador (desactivado = de vacaciones, no se le escribe)."""
    ok = await set_activo_empleado(req.telefono, req.activo)
    if not ok:
        raise HTTPException(status_code=404, detail="Trabajador no encontrado")
    logger.info(f"[COLACIÓN] {req.telefono} {'activado' if req.activo else 'desactivado'}")
    return {"ok": True}


# ════════════════════════════════════════════════════════════
# Reportes — resumen por rango de fechas y descarga en PDF
# ════════════════════════════════════════════════════════════

LOCAL_NOMBRE = {"mall": "Mall", "playa": "Playa"}


def _resumen_por_trabajador(filas: list[dict]) -> list[dict]:
    """Agrupa las colaciones por trabajador y calcula totales para el reporte."""
    agg: dict = {}
    for f in filas:
        clave = (f["nombre"], f["local"])
        a = agg.setdefault(clave, {
            "nombre": f["nombre"], "local": f["local"],
            "n": 0, "excedidas": 0, "suma": 0, "exceso_total": 0,
        })
        a["n"] += 1
        a["suma"] += f["duracion_min"]
        if f["excedido"]:
            a["excedidas"] += 1
            a["exceso_total"] += f["exceso_min"]
    filas_r = []
    for a in agg.values():
        a["promedio_min"] = round(a["suma"] / a["n"]) if a["n"] else 0
        filas_r.append(a)
    filas_r.sort(key=lambda x: (x["local"], -x["excedidas"], -x["n"], x["nombre"]))
    return filas_r


def _txt(s) -> str:
    """Sanitiza texto a latin-1 (fuentes base de fpdf2 no soportan Unicode completo)."""
    return str(s if s is not None else "").encode("latin-1", "replace").decode("latin-1")


def _construir_pdf(subtitulo: str, filas: list[dict], resumen: list[dict]) -> bytes:
    """Arma el PDF del reporte de colaciones (detalle + resumen por trabajador)."""
    from fpdf import FPDF

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Encabezado
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, _txt("Reporte de Colaciones — Dimango"), ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 6, _txt(subtitulo), ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # ── Resumen por trabajador ──
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, _txt("Resumen por trabajador"), ln=True)
    cols_r = [("Trabajador", 80), ("Local", 25), ("Colaciones", 30),
              ("Excedidas", 28), ("Prom (min)", 30), ("Exceso total (min)", 40)]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(235, 238, 241)
    for titulo, w in cols_r:
        pdf.cell(w, 7, _txt(titulo), border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    if not resumen:
        pdf.cell(sum(w for _, w in cols_r), 7, _txt("Sin colaciones en el período."),
                 border=1, align="C", ln=True)
    for r in resumen:
        pdf.cell(80, 6, _txt(r["nombre"]), border=1)
        pdf.cell(25, 6, _txt(LOCAL_NOMBRE.get(r["local"], r["local"])), border=1, align="C")
        pdf.cell(30, 6, str(r["n"]), border=1, align="C")
        pdf.cell(28, 6, str(r["excedidas"]), border=1, align="C")
        pdf.cell(30, 6, str(r["promedio_min"]), border=1, align="C")
        pdf.cell(40, 6, str(r["exceso_total"]), border=1, align="C", ln=True)
    pdf.ln(6)

    # ── Detalle de colaciones ──
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, _txt("Detalle de colaciones"), ln=True)
    cols_d = [("Fecha", 30), ("Trabajador", 75), ("Local", 25), ("Inicio", 25),
              ("Fin", 25), ("Duración", 30), ("Estado", 33)]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(235, 238, 241)
    for titulo, w in cols_d:
        pdf.cell(w, 7, _txt(titulo), border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    if not filas:
        pdf.cell(sum(w for _, w in cols_d), 7, _txt("Sin colaciones en el período."),
                 border=1, align="C", ln=True)
    for f in filas:
        excedido = f["excedido"]
        if excedido:
            pdf.set_text_color(200, 30, 60)
        pdf.cell(30, 6, _txt(f["fecha"]), border=1, align="C")
        pdf.cell(75, 6, _txt(f["nombre"]), border=1)
        pdf.cell(25, 6, _txt(LOCAL_NOMBRE.get(f["local"], f["local"])), border=1, align="C")
        pdf.cell(25, 6, _txt(f["inicio"]), border=1, align="C")
        pdf.cell(25, 6, _txt(f["fin"]), border=1, align="C")
        pdf.cell(30, 6, f"{f['duracion_min']} min", border=1, align="C")
        estado = f"EXCEDIO +{f['exceso_min']}" if excedido else "OK"
        pdf.cell(33, 6, _txt(estado), border=1, align="C", ln=True)
        if excedido:
            pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())


@router.get("/api/reporte")
async def api_reporte(desde: str, hasta: str, local: str = "", _: str = Depends(verificar_admin)):
    """Devuelve los datos del reporte (detalle + resumen) para la vista previa."""
    try:
        filas = await colaciones_rango(desde, hasta, local or None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Fechas inválidas (usa YYYY-MM-DD)")
    return {"filas": filas, "resumen": _resumen_por_trabajador(filas), "limite_min": LIMITE_MIN}


@router.get("/api/reporte.pdf")
async def api_reporte_pdf(desde: str, hasta: str, local: str = "", _: str = Depends(verificar_admin)):
    """Genera y descarga el reporte de colaciones en PDF para el rango indicado."""
    try:
        filas = await colaciones_rango(desde, hasta, local or None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Fechas inválidas (usa YYYY-MM-DD)")
    resumen = _resumen_por_trabajador(filas)
    local_txt = LOCAL_NOMBRE.get(local, "Todos los locales")
    subtitulo = (f"{desde} a {hasta}   -   {local_txt}   -   "
                 f"{len(filas)} colaciones   -   limite {LIMITE_MIN} min")
    try:
        pdf_bytes = _construir_pdf(subtitulo, filas, resumen)
    except ImportError:
        raise HTTPException(status_code=500,
                            detail="Falta instalar fpdf2 en el servidor: pip install fpdf2")
    nombre = f"colacion_{local or 'todos'}_{desde}_a_{hasta}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/reportes", response_class=HTMLResponse)
async def pantalla_reportes(_: str = Depends(verificar_admin)):
    """Página de reportes con filtros de fecha/local y descarga de PDF."""
    return HTML_REPORTES


# ════════════════════════════════════════════════════════════
# API público con token — lo consume la página /Colacion de Base44
# (dimangotogo.base44.app). No usa la contraseña del panel; se valida
# con COLACION_TOKEN del .env. CORS habilitado en main.py.
# ════════════════════════════════════════════════════════════

@router.get("/api/publico")
async def api_publico_estado(token: str = ""):
    """Estado en vivo para la página de Base44: activas, historial y personal."""
    verificar_token(token)
    return {
        "limite_min": LIMITE_MIN,
        "activas": await listar_activas(),
        "historial": await historial_hoy(),
        "empleados": await listar_empleados(),
    }


@router.post("/api/publico/empleados")
async def api_publico_guardar_empleado(req: EmpleadoRequest, token: str = ""):
    """Registra o actualiza un empleado desde Base44."""
    verificar_token(token)
    try:
        await guardar_empleado(req.nombre, req.telefono, req.local,
                               req.es_encargado, req.dias_libres)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"[COLACIÓN] (Base44) Empleado guardado: {req.nombre} ({req.local})")
    return {"ok": True}


@router.post("/api/publico/empleados/eliminar")
async def api_publico_eliminar_empleado(req: TelefonoRequest, token: str = ""):
    """Elimina un empleado desde Base44."""
    verificar_token(token)
    await eliminar_empleado(req.telefono)
    return {"ok": True}


@router.post("/api/publico/terminar")
async def api_publico_terminar(req: TelefonoRequest, token: str = ""):
    """Cierra manualmente una colación desde Base44."""
    verificar_token(token)
    col = await terminar_colacion(req.telefono)
    return {"ok": col is not None}


@router.post("/api/publico/iniciar")
async def api_publico_iniciar(req: TelefonoRequest, token: str = ""):
    """Inicia manualmente la colación de un trabajador desde Base44 (sin WhatsApp)."""
    verificar_token(token)
    return await _iniciar_manual(req.telefono)


@router.get("/api/publico/cumplimiento")
async def api_publico_cumplimiento(token: str = ""):
    """Estado de cumplimiento del día desde Base44."""
    verificar_token(token)
    return await estado_cumplimiento_hoy()


@router.post("/api/publico/activo")
async def api_publico_activo(req: ActivoRequest, token: str = ""):
    """Activa o desactiva a un trabajador desde Base44."""
    verificar_token(token)
    ok = await set_activo_empleado(req.telefono, req.activo)
    if not ok:
        raise HTTPException(status_code=404, detail="Trabajador no encontrado")
    return {"ok": True}


# ════════════════════════════════════════════════════════════
# Página HTML (todo en un archivo, sin dependencias externas)
# ════════════════════════════════════════════════════════════

HTML_COLACION = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Colación — Dimango</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0f1419; color: #e7e9ea; min-height: 100vh; }
  header { background: #16202a; padding: 14px 20px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid #2a3540; position: sticky; top: 0; z-index: 5; }
  header h1 { font-size: 18px; font-weight: 700; }
  header .dot { width: 10px; height: 10px; border-radius: 50%; background: #2ecc71; }
  .btn-sonido { margin-left: auto; background: #2a3540; color: #e7e9ea; border: none; border-radius: 8px; padding: 9px 14px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .btn-sonido.on { background: #1d6f42; color: #fff; }
  .link-rep { background: #1d9bf0; color: #fff; text-decoration: none; border-radius: 8px; padding: 9px 14px; font-size: 13px; font-weight: 600; }
  .wrap { padding: 18px; max-width: 1100px; margin: 0 auto; }
  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .5px; color: #8b98a5; margin: 22px 0 12px; }
  .activas { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
  .card { background: #18222d; border: 1px solid #2a3540; border-radius: 14px; padding: 16px; position: relative; }
  .card.warn { border-color: #e0a800; }
  .card.over { border-color: #e0245e; background: #2a1620; animation: pulso 1s infinite; }
  @keyframes pulso { 0%,100% { box-shadow: 0 0 0 0 rgba(224,36,94,.5); } 50% { box-shadow: 0 0 0 8px rgba(224,36,94,0); } }
  .card .nombre { font-size: 18px; font-weight: 700; }
  .card .local { display: inline-block; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 2px 8px; border-radius: 10px; margin-top: 4px; }
  .local.mall { background: #1d4e89; color: #cfe3ff; }
  .local.playa { background: #1d6f42; color: #cfffe0; }
  .card .timer { font-size: 38px; font-weight: 800; font-variant-numeric: tabular-nums; margin: 10px 0 2px; }
  .card.over .timer { color: #ff6b8a; }
  .card .sub { font-size: 12px; color: #8b98a5; }
  .card .cerrar { position: absolute; top: 12px; right: 12px; background: #2a3540; color: #e7e9ea; border: none; border-radius: 8px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
  .vacio { color: #8b98a5; font-size: 14px; padding: 10px 0; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e2832; }
  th { color: #8b98a5; font-size: 12px; text-transform: uppercase; }
  .badge { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; }
  .badge.ok { background: #1d6f42; color: #cfffe0; }
  .badge.over { background: #e0245e; color: #fff; }
  .panel { background: #18222d; border: 1px solid #2a3540; border-radius: 14px; padding: 16px; }
  .form { display: grid; grid-template-columns: 1.4fr 1.4fr 1fr auto auto; gap: 10px; align-items: center; }
  .form input, .form select { background: #0f1722; border: 1px solid #2a3540; color: #e7e9ea; padding: 9px 11px; border-radius: 8px; font-size: 14px; }
  .form label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #cfd6dd; white-space: nowrap; }
  .btn { border: none; border-radius: 8px; padding: 9px 16px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .btn-add { background: #1d9bf0; color: #fff; }
  .lista-emp { margin-top: 14px; }
  .emp-fila { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid #1e2832; font-size: 14px; }
  .emp-fila .crece { flex: 1; }
  .emp-fila .jefe { font-size: 11px; background: #e0a800; color: #000; padding: 1px 7px; border-radius: 10px; font-weight: 700; }
  .btn-del { background: #2a3540; color: #ff6b8a; border: none; border-radius: 8px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
  .btn-ini { background: #1d6f42; color: #fff; border: none; border-radius: 8px; padding: 5px 12px; font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .btn-ini[disabled] { background: #2a3540; color: #8b98a5; cursor: default; }
  .btn-edit { background: #2a3540; color: #cfe3ff; border: none; border-radius: 8px; padding: 5px 10px; font-size: 12px; cursor: pointer; white-space: nowrap; }
  .btn-off { background: #2a3540; color: #ffb3b3; border: none; border-radius: 8px; padding: 5px 10px; font-size: 12px; cursor: pointer; white-space: nowrap; }
  .btn-on { background: #1d6f42; color: #fff; border: none; border-radius: 8px; padding: 5px 10px; font-size: 12px; cursor: pointer; white-space: nowrap; }
  .emp-fila.inactivo { opacity: .5; }
  .dias-chips { display: flex; gap: 4px; flex-wrap: wrap; }
  .dia-chip { font-size: 10px; font-weight: 700; background: #3a2a10; color: #ffd98a; padding: 1px 6px; border-radius: 8px; }
  .dias-form { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; padding-top: 12px; border-top: 1px solid #1e2832; }
  .dias-form .lbl { font-size: 13px; color: #8b98a5; }
  .dias-form label { display: flex; align-items: center; gap: 4px; font-size: 13px; color: #cfd6dd; background: #0f1722; border: 1px solid #2a3540; padding: 5px 9px; border-radius: 8px; cursor: pointer; }
  .est { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
  .est.tomada { background: #1d6f42; color: #cfffe0; }
  .est.en_colacion { background: #1d4e89; color: #cfe3ff; }
  .est.pendiente { background: #3a2a10; color: #ffd98a; }
  .est.libre { background: #444; color: #cfd6dd; }
  .est.inactivo { background: #5a1d1d; color: #ffb3b3; }
  /* Panel de cumplimiento */
  .cumpl-top { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
  .cumpl-pct { font-size: 46px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .cumpl-pct.full { color: #2ecc71; }
  .cumpl-pct.partial { color: #e0a800; }
  .cumpl-counts { display: flex; gap: 10px; flex-wrap: wrap; }
  .pill { background: #0f1722; border: 1px solid #2a3540; border-radius: 12px; padding: 8px 14px; font-size: 13px; }
  .pill b { font-size: 18px; display: block; }
  .pend-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .pend-chip { background: #3a2a10; color: #ffd98a; border-radius: 10px; padding: 4px 10px; font-size: 13px; }
  @media (max-width: 720px) { .form { grid-template-columns: 1fr 1fr; } }
</style>
</head>
<body>
<header>
  <span class="dot"></span>
  <h1>Colación Dimango — en vivo</h1>
  <button class="btn-sonido" id="btnSonido" onclick="activarSonido()">🔔 Activar alerta</button>
  <a class="link-rep" href="/colacion/reportes">📊 Reportes</a>
</header>
<div class="wrap">

  <h2>En colación ahora</h2>
  <div class="activas" id="activas"></div>

  <h2>Cumplimiento de hoy</h2>
  <div class="panel" id="cumplimiento"></div>

  <h2>Historial de hoy</h2>
  <div class="panel">
    <table>
      <thead><tr><th>Trabajador</th><th>Local</th><th>Inicio</th><th>Fin</th><th>Duración</th><th></th></tr></thead>
      <tbody id="historial"></tbody>
    </table>
  </div>

  <h2>Personal registrado</h2>
  <div class="panel">
    <div class="form">
      <input type="text" id="fNombre" placeholder="Nombre">
      <input type="text" id="fTelefono" placeholder="WhatsApp (ej: +56 9 1234 5678)">
      <select id="fLocal">
        <option value="mall">Mall</option>
        <option value="playa">Playa</option>
      </select>
      <label><input type="checkbox" id="fEncargado"> Encargado</label>
      <button class="btn btn-add" id="btnGuardar" onclick="agregarEmpleado()">Agregar</button>
      <button class="btn" id="btnCancelar" onclick="cancelarEdicion()" style="display:none;background:#2a3540;color:#e7e9ea;">Cancelar</button>
    </div>
    <div class="dias-form">
      <span class="lbl">Días libres fijos:</span>
      <label><input type="checkbox" class="fDia" value="0"> Lun</label>
      <label><input type="checkbox" class="fDia" value="1"> Mar</label>
      <label><input type="checkbox" class="fDia" value="2"> Mié</label>
      <label><input type="checkbox" class="fDia" value="3"> Jue</label>
      <label><input type="checkbox" class="fDia" value="4"> Vie</label>
      <label><input type="checkbox" class="fDia" value="5"> Sáb</label>
      <label><input type="checkbox" class="fDia" value="6"> Dom</label>
    </div>
    <div class="lista-emp" id="empleados"></div>
  </div>

</div>
<script>
let LIMITE = 30;
let activasCache = [];   // estado anclado al servidor; el timer corre localmente
let audioCtx = null;
let yaAlerto = {};       // id de colación -> ya sonó la alerta de excedido
let estadoPorTel = {};   // telefono -> estado de cumplimiento de hoy (libre/tomada/...)
let editandoTel = null;  // telefono original en edición (null = modo agregar)
const DIAS_CORTOS = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'];  // 0=lunes ... 6=domingo

function escapar(t){ const d=document.createElement('div'); d.textContent=t??''; return d.innerHTML; }
function dos(n){ return String(n).padStart(2,'0'); }

// ──────────── Carga de estado ────────────
async function cargar(){
  let data;
  try { const r = await fetch('/colacion/api/estado'); if(!r.ok) return; data = await r.json(); }
  catch(e){ return; }
  LIMITE = data.limite_min;
  // anclar el tiempo transcurrido al momento de la respuesta
  const t0 = Date.now();
  activasCache = data.activas.map(a => ({...a, ancla: t0}));
  renderCumplimiento(data.cumplimiento);
  renderHistorial(data.historial);
  renderEmpleados(data.empleados);
  renderActivas();  // pinta de inmediato
}

// ──────────── Cumplimiento del día (meta 100%) ────────────
const ESTADO_TXT = { tomada:'Tomó', en_colacion:'En colación', pendiente:'Pendiente', libre:'Libre' };
function renderCumplimiento(c){
  const cont = document.getElementById('cumplimiento');
  if(!c){ cont.innerHTML = ''; return; }
  estadoPorTel = {};
  (c.trabajadores||[]).forEach(t => estadoPorTel[t.telefono] = t.estado);
  const full = c.porcentaje >= 100;
  const pendientes = (c.trabajadores||[]).filter(t => t.estado === 'pendiente');
  let listaPend = '';
  if(pendientes.length){
    listaPend = '<div class="pend-list">' + pendientes.map(t =>
      `<span class="pend-chip">${escapar(t.nombre)} · ${escapar(LOCAL_TXT(t.local))}</span>`).join('') + '</div>';
  } else if(c.considerados > 0){
    listaPend = '<div class="pend-list"><span class="pend-chip" style="background:#153d24;color:#8affc0;">✓ Todos tomaron su colación</span></div>';
  }
  cont.innerHTML = `
    <div class="cumpl-top">
      <div class="cumpl-pct ${full?'full':'partial'}">${c.porcentaje}%</div>
      <div class="cumpl-counts">
        <div class="pill"><b>${c.tomadas}</b>Tomaron</div>
        <div class="pill"><b style="color:#ffd98a;">${c.pendientes}</b>Pendientes</div>
        <div class="pill"><b style="color:#9aa7b2;">${c.libres}</b>Libres hoy</div>
      </div>
    </div>${listaPend}`;
}
function LOCAL_TXT(l){ return l==='mall'?'Mall':(l==='playa'?'Playa':l); }

// ──────────── Pantalla en vivo (timer local cada segundo) ────────────
function renderActivas(){
  const cont = document.getElementById('activas');
  if(!activasCache.length){ cont.innerHTML = '<div class="vacio">Nadie en colación ahora mismo.</div>'; return; }
  const ahora = Date.now();
  let hayExcedido = false;
  cont.innerHTML = activasCache.map(a => {
    const transcurrido = a.transcurrido_seg + Math.floor((ahora - a.ancla)/1000);
    const restante = a.limite_seg - transcurrido;
    const excedido = restante < 0;
    let clase = 'card', timer, sub;
    if(excedido){
      clase += ' over'; hayExcedido = true;
      const e = Math.abs(restante);
      timer = '+' + dos(Math.floor(e/60)) + ':' + dos(e%60);
      sub = 'PASADO del límite';
      if(!yaAlerto[a.id]){ yaAlerto[a.id] = true; sonar(); }
    } else {
      if(restante <= 300) clase += ' warn';
      timer = dos(Math.floor(restante/60)) + ':' + dos(restante%60);
      sub = 'restante';
    }
    return `
      <div class="${clase}">
        <button class="cerrar" onclick="cerrar('${a.telefono}')">Cerrar</button>
        <div class="nombre">${escapar(a.nombre)}</div>
        <span class="local ${a.local}">${escapar(a.local)}</span>
        <div class="timer">${timer}</div>
        <div class="sub">${sub} · inició ${a.inicio}</div>
      </div>`;
  }).join('');
  // limpiar alertas de quienes ya no están
  const ids = new Set(activasCache.map(a=>a.id));
  Object.keys(yaAlerto).forEach(id => { if(!ids.has(parseInt(id))) delete yaAlerto[id]; });
}

function renderHistorial(hist){
  const tb = document.getElementById('historial');
  if(!hist.length){ tb.innerHTML = '<tr><td colspan="6" class="vacio">Sin colaciones registradas hoy.</td></tr>'; return; }
  tb.innerHTML = hist.map(h => `
    <tr>
      <td>${escapar(h.nombre)}</td>
      <td><span class="local ${h.local}">${escapar(h.local)}</span></td>
      <td>${h.inicio}</td>
      <td>${h.fin}</td>
      <td>${h.duracion_min} min</td>
      <td>${h.excedido ? '<span class="badge over">EXCEDIÓ</span>' : '<span class="badge ok">OK</span>'}</td>
    </tr>`).join('');
}

function renderEmpleados(emps){
  const cont = document.getElementById('empleados');
  if(!emps.length){ cont.innerHTML = '<div class="vacio">Aún no hay personal registrado. Agrega al equipo arriba.</div>'; return; }
  const enColacion = new Set(activasCache.map(a => a.telefono));
  cont.innerHTML = emps.map(e => {
    const inactivo = e.activo === false;
    const est = inactivo ? 'inactivo' : (estadoPorTel[e.telefono] || 'pendiente');
    const badge = `<span class="est ${est}">${inactivo?'Desactivado':(ESTADO_TXT[est]||est)}</span>`;
    const dias = (e.dias_libres||[]).map(d => `<span class="dia-chip">${DIAS_CORTOS[d]}</span>`).join('');
    const chips = dias ? `<span class="dias-chips">${dias}</span>` : '';
    const tel = escapar(e.telefono).replace(/'/g, "\\'");
    const accion = (inactivo || enColacion.has(e.telefono))
      ? `<button class="btn-ini" disabled>${inactivo?'—':'En colación'}</button>`
      : `<button class="btn-ini" onclick="iniciar('${tel}')">▶ Iniciar</button>`;
    const onoff = inactivo
      ? `<button class="btn-on" onclick="toggleActivo('${tel}', true)">Activar</button>`
      : `<button class="btn-off" onclick="toggleActivo('${tel}', false)">Desactivar</button>`;
    return `
    <div class="emp-fila ${inactivo?'inactivo':''}">
      <span class="local ${e.local}">${escapar(e.local)}</span>
      <span class="crece">${escapar(e.nombre)} · ${escapar(e.telefono)} ${e.es_encargado?'<span class="jefe">ENCARGADO</span>':''}</span>
      ${chips}
      ${badge}
      ${accion}
      <button class="btn-edit" onclick='editarEmpleado(${JSON.stringify(e)})'>Editar</button>
      ${onoff}
      <button class="btn-del" onclick="eliminar('${tel}')">Eliminar</button>
    </div>`;
  }).join('');
}

// ──────────── Acciones ────────────
function leerDias(){
  return Array.from(document.querySelectorAll('.fDia:checked')).map(c => parseInt(c.value));
}
function ponerDias(dias){
  const set = new Set(dias||[]);
  document.querySelectorAll('.fDia').forEach(c => { c.checked = set.has(parseInt(c.value)); });
}

async function agregarEmpleado(){
  const nombre = document.getElementById('fNombre').value.trim();
  const telefono = document.getElementById('fTelefono').value.trim();
  const local = document.getElementById('fLocal').value;
  const es_encargado = document.getElementById('fEncargado').checked;
  const dias_libres = leerDias();
  if(!nombre || !telefono){ alert('Pon el nombre y el WhatsApp.'); return; }
  const r = await fetch('/colacion/api/empleados', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({nombre, telefono, local, es_encargado, dias_libres})
  });
  if(!r.ok){ const e = await r.json().catch(()=>({})); alert('Error: ' + (e.detail||'no se pudo guardar')); return; }
  // Si estaba editando y cambió el WhatsApp, borrar el registro viejo (el teléfono es la llave)
  if(editandoTel && editandoTel !== telefono){
    await fetch('/colacion/api/empleados/eliminar', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({telefono: editandoTel})
    });
  }
  cancelarEdicion();
  cargar();
}

function editarEmpleado(e){
  editandoTel = e.telefono;
  document.getElementById('fNombre').value = e.nombre;
  document.getElementById('fTelefono').value = e.telefono;
  document.getElementById('fLocal').value = e.local;
  document.getElementById('fEncargado').checked = !!e.es_encargado;
  ponerDias(e.dias_libres);
  document.getElementById('btnGuardar').textContent = 'Guardar cambios';
  document.getElementById('btnCancelar').style.display = '';
  document.getElementById('fNombre').focus();
  document.querySelector('.form').scrollIntoView({behavior:'smooth', block:'center'});
}

function cancelarEdicion(){
  editandoTel = null;
  document.getElementById('fNombre').value = '';
  document.getElementById('fTelefono').value = '';
  document.getElementById('fLocal').value = 'mall';
  document.getElementById('fEncargado').checked = false;
  ponerDias([]);
  document.getElementById('btnGuardar').textContent = 'Agregar';
  document.getElementById('btnCancelar').style.display = 'none';
}

async function toggleActivo(telefono, activo){
  if(!activo && !confirm('¿Desactivar a este trabajador? No se le enviarán recordatorios (ej: vacaciones) hasta que lo actives de nuevo.')) return;
  await fetch('/colacion/api/activo', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({telefono, activo})
  });
  cargar();
}

async function eliminar(telefono){
  if(!confirm('¿Eliminar a este trabajador del registro?')) return;
  await fetch('/colacion/api/empleados/eliminar', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({telefono})
  });
  cargar();
}

async function cerrar(telefono){
  if(!confirm('¿Cerrar manualmente esta colación?')) return;
  await fetch('/colacion/api/terminar', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({telefono})
  });
  cargar();
}

async function iniciar(telefono){
  const r = await fetch('/colacion/api/iniciar', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({telefono})
  });
  const e = await r.json().catch(()=>({}));
  if(!r.ok || e.ok===false){ alert(e.detail || 'No se pudo iniciar la colación'); }
  cargar();
}

// ──────────── Sonido ────────────
function initAudio(){
  if(!audioCtx){ try{ audioCtx = new (window.AudioContext||window.webkitAudioContext)(); }catch(e){} }
  if(audioCtx && audioCtx.state==='suspended') audioCtx.resume();
}
function beep(freq, inicio, dur){
  if(!audioCtx) return;
  const o = audioCtx.createOscillator(), g = audioCtx.createGain();
  o.connect(g); g.connect(audioCtx.destination); o.type='sine'; o.frequency.value=freq;
  const t = audioCtx.currentTime + inicio;
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.5, t+0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, t+dur);
  o.start(t); o.stop(t+dur+0.03);
}
function sonar(){ initAudio(); beep(880,0,0.25); beep(1175,0.18,0.32); beep(880,0.4,0.25); }
function activarSonido(){
  initAudio();
  const btn = document.getElementById('btnSonido');
  btn.textContent = '🔔 Alerta activada'; btn.classList.add('on');
  sonar();
}

// ──────────── Bucles ────────────
cargar();
setInterval(cargar, 5000);     // re-sincroniza con el servidor
setInterval(renderActivas, 1000); // timer fluido cada segundo
</script>
</body>
</html>"""


HTML_REPORTES = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reportes de Colación — Dimango</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0f1419; color: #e7e9ea; min-height: 100vh; }
  header { background: #16202a; padding: 14px 20px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid #2a3540; position: sticky; top: 0; z-index: 5; }
  header h1 { font-size: 18px; font-weight: 700; }
  header a.volver { margin-left: auto; background: #2a3540; color: #e7e9ea; text-decoration: none; border-radius: 8px; padding: 9px 14px; font-size: 13px; font-weight: 600; }
  .wrap { padding: 18px; max-width: 1100px; margin: 0 auto; }
  .panel { background: #18222d; border: 1px solid #2a3540; border-radius: 14px; padding: 16px; margin-bottom: 18px; }
  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .5px; color: #8b98a5; margin: 6px 0 12px; }
  .rapidos { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
  .chip { background: #0f1722; border: 1px solid #2a3540; color: #cfd6dd; border-radius: 20px; padding: 7px 14px; font-size: 13px; cursor: pointer; }
  .chip:hover { border-color: #1d9bf0; color: #fff; }
  .filtros { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
  .campo { display: flex; flex-direction: column; gap: 4px; }
  .campo label { font-size: 12px; color: #8b98a5; }
  input, select { background: #0f1722; border: 1px solid #2a3540; color: #e7e9ea; padding: 9px 11px; border-radius: 8px; font-size: 14px; }
  .btn { border: none; border-radius: 8px; padding: 10px 18px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .btn-ver { background: #2a3540; color: #e7e9ea; }
  .btn-pdf { background: #1d6f42; color: #fff; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-bottom: 4px; }
  .kpi { background: #0f1722; border: 1px solid #2a3540; border-radius: 12px; padding: 14px; }
  .kpi .n { font-size: 30px; font-weight: 800; }
  .kpi .t { font-size: 12px; color: #8b98a5; margin-top: 2px; }
  .kpi.alerta .n { color: #ff6b8a; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e2832; }
  th { color: #8b98a5; font-size: 12px; text-transform: uppercase; }
  .local { display: inline-block; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 2px 8px; border-radius: 10px; }
  .local.mall { background: #1d4e89; color: #cfe3ff; }
  .local.playa { background: #1d6f42; color: #cfffe0; }
  .badge { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 10px; }
  .badge.ok { background: #1d6f42; color: #cfffe0; }
  .badge.over { background: #e0245e; color: #fff; }
  .vacio { color: #8b98a5; font-size: 14px; padding: 10px 0; }
</style>
</head>
<body>
<header>
  <h1>📊 Reportes de Colación</h1>
  <a class="volver" href="/colacion">← Volver a en vivo</a>
</header>
<div class="wrap">

  <div class="panel">
    <h2>Período</h2>
    <div class="rapidos">
      <button class="chip" onclick="rango('hoy')">Hoy</button>
      <button class="chip" onclick="rango('ayer')">Ayer</button>
      <button class="chip" onclick="rango('semana')">Últimos 7 días</button>
      <button class="chip" onclick="rango('mes')">Este mes</button>
      <button class="chip" onclick="rango('mespasado')">Mes pasado</button>
    </div>
    <div class="filtros">
      <div class="campo"><label>Desde</label><input type="date" id="desde"></div>
      <div class="campo"><label>Hasta</label><input type="date" id="hasta"></div>
      <div class="campo"><label>Local</label>
        <select id="local">
          <option value="">Todos</option>
          <option value="mall">Mall</option>
          <option value="playa">Playa</option>
        </select>
      </div>
      <button class="btn btn-ver" onclick="ver()">Ver</button>
      <button class="btn btn-pdf" onclick="descargarPDF()">⬇ Descargar PDF</button>
    </div>
  </div>

  <div class="panel">
    <h2>Resumen</h2>
    <div class="kpis" id="kpis"></div>
  </div>

  <div class="panel">
    <h2>Por trabajador</h2>
    <table>
      <thead><tr><th>Trabajador</th><th>Local</th><th>Colaciones</th><th>Excedidas</th><th>Prom.</th><th>Exceso total</th></tr></thead>
      <tbody id="resumen"></tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Detalle</h2>
    <table>
      <thead><tr><th>Fecha</th><th>Trabajador</th><th>Local</th><th>Inicio</th><th>Fin</th><th>Duración</th><th>Estado</th></tr></thead>
      <tbody id="detalle"></tbody>
    </table>
  </div>

</div>
<script>
let LIMITE = 30;
function escapar(t){ const d=document.createElement('div'); d.textContent=t??''; return d.innerHTML; }
function iso(d){ return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }

function rango(tipo){
  const hoy = new Date();
  let d = new Date(hoy), h = new Date(hoy);
  if(tipo==='hoy'){ /* d=h=hoy */ }
  else if(tipo==='ayer'){ d.setDate(d.getDate()-1); h.setDate(h.getDate()-1); }
  else if(tipo==='semana'){ d.setDate(d.getDate()-6); }
  else if(tipo==='mes'){ d = new Date(hoy.getFullYear(), hoy.getMonth(), 1); }
  else if(tipo==='mespasado'){ d = new Date(hoy.getFullYear(), hoy.getMonth()-1, 1); h = new Date(hoy.getFullYear(), hoy.getMonth(), 0); }
  document.getElementById('desde').value = iso(d);
  document.getElementById('hasta').value = iso(h);
  ver();
}

function params(){
  const desde = document.getElementById('desde').value;
  const hasta = document.getElementById('hasta').value;
  const local = document.getElementById('local').value;
  return { desde, hasta, local };
}

async function ver(){
  const p = params();
  if(!p.desde || !p.hasta){ alert('Elige el rango de fechas.'); return; }
  const url = '/colacion/api/reporte?desde='+p.desde+'&hasta='+p.hasta+'&local='+encodeURIComponent(p.local);
  let data;
  try { const r = await fetch(url); if(!r.ok){ alert('Error al cargar el reporte.'); return; } data = await r.json(); }
  catch(e){ alert('Error de conexión.'); return; }
  LIMITE = data.limite_min;
  renderKpis(data);
  renderResumen(data.resumen);
  renderDetalle(data.filas);
}

function renderKpis(data){
  const total = data.filas.length;
  const excedidas = data.filas.filter(f=>f.excedido).length;
  const prom = total ? Math.round(data.filas.reduce((s,f)=>s+f.duracion_min,0)/total) : 0;
  const exceso = data.filas.reduce((s,f)=>s+f.exceso_min,0);
  document.getElementById('kpis').innerHTML =
    kpi(total,'Colaciones',false) + kpi(excedidas,'Excedieron 30 min',excedidas>0) +
    kpi(prom+' min','Duración promedio',false) + kpi(exceso+' min','Exceso acumulado',exceso>0);
}
function kpi(n,t,alerta){ return '<div class="kpi'+(alerta?' alerta':'')+'"><div class="n">'+escapar(String(n))+'</div><div class="t">'+escapar(t)+'</div></div>'; }

function renderResumen(res){
  const tb = document.getElementById('resumen');
  if(!res.length){ tb.innerHTML = '<tr><td colspan="6" class="vacio">Sin datos en el período.</td></tr>'; return; }
  tb.innerHTML = res.map(r=>`
    <tr>
      <td>${escapar(r.nombre)}</td>
      <td><span class="local ${r.local}">${escapar(r.local)}</span></td>
      <td>${r.n}</td>
      <td>${r.excedidas>0?'<span class="badge over">'+r.excedidas+'</span>':'0'}</td>
      <td>${r.promedio_min} min</td>
      <td>${r.exceso_total} min</td>
    </tr>`).join('');
}

function renderDetalle(filas){
  const tb = document.getElementById('detalle');
  if(!filas.length){ tb.innerHTML = '<tr><td colspan="7" class="vacio">Sin colaciones en el período.</td></tr>'; return; }
  tb.innerHTML = filas.map(f=>`
    <tr>
      <td>${f.fecha}</td>
      <td>${escapar(f.nombre)}</td>
      <td><span class="local ${f.local}">${escapar(f.local)}</span></td>
      <td>${f.inicio}</td>
      <td>${f.fin}</td>
      <td>${f.duracion_min} min</td>
      <td>${f.excedido?'<span class="badge over">EXCEDIÓ +'+f.exceso_min+'</span>':'<span class="badge ok">OK</span>'}</td>
    </tr>`).join('');
}

function descargarPDF(){
  const p = params();
  if(!p.desde || !p.hasta){ alert('Elige el rango de fechas.'); return; }
  const url = '/colacion/api/reporte.pdf?desde='+p.desde+'&hasta='+p.hasta+'&local='+encodeURIComponent(p.local);
  window.open(url, '_blank');
}

// Al abrir, mostrar el reporte de hoy
rango('hoy');
</script>
</body>
</html>"""
