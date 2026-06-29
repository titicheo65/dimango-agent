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

import logging
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent.colacion import (
    listar_activas,
    historial_hoy,
    listar_empleados,
    guardar_empleado,
    eliminar_empleado,
    terminar_colacion,
    LIMITE_MIN,
)
# Reusa el login del panel /admin (misma usuario/contraseña del .env)
from agent.admin import verificar_admin

logger = logging.getLogger("agentkit")

router = APIRouter(prefix="/colacion", tags=["colacion"])


class EmpleadoRequest(BaseModel):
    nombre: str
    telefono: str
    local: str
    es_encargado: bool = False


class TelefonoRequest(BaseModel):
    telefono: str


@router.get("", response_class=HTMLResponse)
async def pantalla(_: str = Depends(verificar_admin)):
    """Sirve la pantalla en vivo de colación (privada, requiere login)."""
    return HTML_COLACION


@router.get("/api/estado")
async def api_estado(_: str = Depends(verificar_admin)):
    """Estado completo para la pantalla: activas, historial del día y personal."""
    return {
        "limite_min": LIMITE_MIN,
        "activas": await listar_activas(),
        "historial": await historial_hoy(),
        "empleados": await listar_empleados(),
    }


@router.post("/api/empleados")
async def api_guardar_empleado(req: EmpleadoRequest, _: str = Depends(verificar_admin)):
    """Registra o actualiza un empleado."""
    try:
        await guardar_empleado(req.nombre, req.telefono, req.local, req.es_encargado)
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
  @media (max-width: 720px) { .form { grid-template-columns: 1fr 1fr; } }
</style>
</head>
<body>
<header>
  <span class="dot"></span>
  <h1>Colación Dimango — en vivo</h1>
  <button class="btn-sonido" id="btnSonido" onclick="activarSonido()">🔔 Activar alerta</button>
</header>
<div class="wrap">

  <h2>En colación ahora</h2>
  <div class="activas" id="activas"></div>

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
      <button class="btn btn-add" onclick="agregarEmpleado()">Agregar</button>
    </div>
    <div class="lista-emp" id="empleados"></div>
  </div>

</div>
<script>
let LIMITE = 30;
let activasCache = [];   // estado anclado al servidor; el timer corre localmente
let audioCtx = null;
let yaAlerto = {};       // id de colación -> ya sonó la alerta de excedido

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
  renderHistorial(data.historial);
  renderEmpleados(data.empleados);
  renderActivas();  // pinta de inmediato
}

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
  cont.innerHTML = emps.map(e => `
    <div class="emp-fila">
      <span class="local ${e.local}">${escapar(e.local)}</span>
      <span class="crece">${escapar(e.nombre)} · ${escapar(e.telefono)} ${e.es_encargado?'<span class="jefe">ENCARGADO</span>':''}</span>
      <button class="btn-del" onclick="eliminar('${e.telefono}')">Eliminar</button>
    </div>`).join('');
}

// ──────────── Acciones ────────────
async function agregarEmpleado(){
  const nombre = document.getElementById('fNombre').value.trim();
  const telefono = document.getElementById('fTelefono').value.trim();
  const local = document.getElementById('fLocal').value;
  const es_encargado = document.getElementById('fEncargado').checked;
  if(!nombre || !telefono){ alert('Pon el nombre y el WhatsApp.'); return; }
  const r = await fetch('/colacion/api/empleados', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({nombre, telefono, local, es_encargado})
  });
  if(!r.ok){ const e = await r.json().catch(()=>({})); alert('Error: ' + (e.detail||'no se pudo guardar')); return; }
  document.getElementById('fNombre').value = '';
  document.getElementById('fTelefono').value = '';
  document.getElementById('fEncargado').checked = false;
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
