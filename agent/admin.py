# agent/admin.py — Panel de administración web
# Generado por AgentKit

"""
Panel web para ver las conversaciones en vivo e intervenir manualmente.
- Protegido con usuario/contraseña (HTTP Basic).
- Permite "tomar control" de una conversación (pausa el agente) y responder a mano.
- Acceso en: https://TU-URL/admin
"""

import os
import secrets
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from agent.memory import (
    listar_conversaciones,
    obtener_conversacion_completa,
    pausar_conversacion,
    guardar_mensaje,
)
from agent.providers import obtener_proveedor

logger = logging.getLogger("agentkit")

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic()

# Credenciales del panel (configurables en .env)
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "dimango2026")

# Proveedor para enviar mensajes manuales
_proveedor = obtener_proveedor()


def verificar_admin(credenciales: HTTPBasicCredentials = Depends(security)) -> str:
    """Valida usuario y contraseña del panel."""
    usuario_ok = secrets.compare_digest(credenciales.username, ADMIN_USER)
    clave_ok = secrets.compare_digest(credenciales.password, ADMIN_PASSWORD)
    if not (usuario_ok and clave_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credenciales.username


class PausaRequest(BaseModel):
    telefono: str
    pausado: bool


class EnviarRequest(BaseModel):
    telefono: str
    mensaje: str


@router.get("", response_class=HTMLResponse)
async def panel(_: str = Depends(verificar_admin)):
    """Sirve la página HTML del panel."""
    return HTML_PANEL


@router.get("/api/conversaciones")
async def api_listar_conversaciones(_: str = Depends(verificar_admin)):
    """Devuelve todas las conversaciones con su último mensaje y estado."""
    return await listar_conversaciones()


@router.get("/api/conversaciones/{telefono}")
async def api_ver_conversacion(telefono: str, _: str = Depends(verificar_admin)):
    """Devuelve todos los mensajes de una conversación."""
    return await obtener_conversacion_completa(telefono)


@router.post("/api/pausar")
async def api_pausar(req: PausaRequest, _: str = Depends(verificar_admin)):
    """Pausa o reactiva el agente para una conversación."""
    await pausar_conversacion(req.telefono, req.pausado)
    logger.info(f"[PANEL] {req.telefono} {'pausado (atención humana)' if req.pausado else 'reactivado (agente)'}")
    return {"ok": True, "pausado": req.pausado}


@router.post("/api/enviar")
async def api_enviar(req: EnviarRequest, _: str = Depends(verificar_admin)):
    """Envía un mensaje manual al cliente por WhatsApp y lo guarda en el historial."""
    mensaje = req.mensaje.strip()
    if not mensaje:
        return {"ok": False, "error": "Mensaje vacío"}
    enviado = await _proveedor.enviar_mensaje(req.telefono, mensaje)
    if enviado:
        await guardar_mensaje(req.telefono, "assistant", mensaje)
        logger.info(f"[PANEL] Respuesta manual a {req.telefono}: {mensaje}")
    return {"ok": enviado}


# ════════════════════════════════════════════════════════════
# Página HTML del panel (todo en un archivo, sin dependencias)
# ════════════════════════════════════════════════════════════

HTML_PANEL = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel — AgentKit</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0f1419; color: #e7e9ea; height: 100vh; display: flex; flex-direction: column; }
  header { background: #16202a; padding: 12px 18px; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid #2a3540; }
  header h1 { font-size: 16px; font-weight: 600; }
  header .dot { width: 9px; height: 9px; border-radius: 50%; background: #2ecc71; }
  .layout { flex: 1; display: flex; overflow: hidden; }
  .sidebar { width: 320px; border-right: 1px solid #2a3540; background: #121a22; display: flex; flex-direction: column; }
  .toolbar { padding: 10px; border-bottom: 1px solid #1e2832; display: flex; flex-direction: column; gap: 8px; }
  .buscar { background: #0f1722; border: 1px solid #2a3540; color: #e7e9ea; padding: 8px 10px; border-radius: 8px; font-size: 14px; width: 100%; }
  .filtros { display: flex; gap: 6px; }
  .fbtn { flex: 1; background: #1a2530; color: #8b98a5; border: 1px solid #2a3540; border-radius: 8px; padding: 6px 4px; font-size: 12px; font-weight: 600; cursor: pointer; }
  .fbtn.activo { background: #1d9bf0; color: #fff; border-color: #1d9bf0; }
  .lista { flex: 1; overflow-y: auto; }
  .btn-volver { display: none; background: #2a3540; color: #e7e9ea; border: none; border-radius: 8px; padding: 6px 11px; font-size: 16px; cursor: pointer; }
  .punto { width: 9px; height: 9px; border-radius: 50%; background: #1d9bf0; display: inline-block; margin-left: auto; flex-shrink: 0; }
  .conv { padding: 12px 16px; border-bottom: 1px solid #1e2832; cursor: pointer; }
  .conv:hover { background: #182230; }
  .conv.activa { background: #1d2b3a; }
  .conv .tel { font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px; }
  .conv .subtel { font-size: 11px; color: #8b98a5; margin-top: 1px; }
  .conv .ult { font-size: 12px; color: #8b98a5; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .badge { font-size: 10px; background: #e0a800; color: #000; padding: 1px 6px; border-radius: 10px; font-weight: 700; }
  .chat { flex: 1; display: flex; flex-direction: column; }
  .chat-header { padding: 12px 18px; border-bottom: 1px solid #2a3540; display: flex; align-items: center; justify-content: space-between; gap: 10px; background: #16202a; }
  .chat-header .tel { font-weight: 600; }
  .mensajes { flex: 1; overflow-y: auto; padding: 18px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 70%; padding: 9px 13px; border-radius: 14px; font-size: 14px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; }
  .msg.user { align-self: flex-start; background: #243340; border-bottom-left-radius: 4px; }
  .msg.assistant { align-self: flex-end; background: #1d6f42; border-bottom-right-radius: 4px; }
  .msg .hora { display: block; font-size: 10px; color: rgba(255,255,255,.5); margin-top: 4px; }
  .vacio { color: #8b98a5; text-align: center; margin-top: 40px; font-size: 14px; }
  .responder { padding: 12px 16px; border-top: 1px solid #2a3540; background: #16202a; }
  .aviso { font-size: 12px; color: #8b98a5; margin-bottom: 8px; }
  .fila { display: flex; gap: 8px; }
  input[type=text] { flex: 1; background: #0f1722; border: 1px solid #2a3540; color: #e7e9ea; padding: 10px 12px; border-radius: 8px; font-size: 14px; }
  input[type=text]:disabled { opacity: .5; }
  button { border: none; border-radius: 8px; padding: 9px 14px; font-size: 13px; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .btn-enviar { background: #1d9bf0; color: #fff; }
  .btn-control { background: #e0a800; color: #000; }
  .btn-control.activo { background: #2ecc71; color: #000; }
  .btn-sonido { margin-left: auto; background: #2a3540; color: #e7e9ea; }
  .btn-sonido.on { background: #1d6f42; color: #fff; }
  /* ─── Vista móvil (celular) ─── */
  @media (max-width: 700px) {
    header h1 { font-size: 14px; }
    .sidebar { width: 100%; }
    .chat { display: none; }
    .layout.chat-abierto .sidebar { display: none; }
    .layout.chat-abierto .chat { display: flex; }
    .btn-volver { display: inline-block; }
    .msg { max-width: 85%; }
  }
</style>
</head>
<body>
<header>
  <span class="dot"></span>
  <h1>Panel Dimango — Conversaciones en vivo</h1>
  <button class="btn-sonido" id="btnSonido" onclick="activarSonido()">🔔 Activar sonido</button>
</header>
<div class="layout" id="layout">
  <aside class="sidebar">
    <div class="toolbar">
      <input type="text" class="buscar" id="buscar" placeholder="🔍 Buscar nombre o número..." oninput="onBuscar(this.value)">
      <div class="filtros">
        <button class="fbtn activo" onclick="setFiltro('todas', this)">Todas</button>
        <button class="fbtn" onclick="setFiltro('humano', this)">Humano</button>
        <button class="fbtn" onclick="setFiltro('nuevos', this)">Nuevos</button>
      </div>
    </div>
    <div class="lista" id="lista"></div>
  </aside>
  <div class="chat">
    <div class="chat-header" id="chatHeader" style="display:none">
      <button class="btn-volver" onclick="volver()" aria-label="Volver">←</button>
      <span class="tel" id="chatTel"></span>
      <button class="btn-control" id="btnControl" onclick="toggleControl()"></button>
    </div>
    <div class="mensajes" id="mensajes">
      <div class="vacio">Selecciona una conversación para verla</div>
    </div>
    <div class="responder" id="responder" style="display:none">
      <div class="aviso" id="aviso"></div>
      <div class="fila">
        <input type="text" id="texto" placeholder="Escribe una respuesta..." onkeydown="if(event.key==='Enter')enviar()">
        <button class="btn-enviar" onclick="enviar()">Enviar</button>
      </div>
    </div>
  </div>
</div>
<script>
let activa = null;        // telefono seleccionado
let pausadaActiva = false;
let nombres = {};         // mapa telefono -> nombre de contacto
let ultimoVisto = {};     // mapa telefono -> total de mensajes ya visto
let leidos = {};          // mapa telefono -> total de mensajes ya LEÍDOS (para "no leídos")
let ultimasConvs = [];    // última lista recibida (para filtrar/buscar sin recargar)
let busqueda = '';        // texto del buscador
let filtro = 'todas';     // filtro activo: todas | humano | nuevos
let primeraVez = true;    // en la primera carga no suena (solo fija la línea base)
let suprimirNotif = false;// evita que tu propio envío manual haga sonar
let audioCtx = null;      // contexto de audio (se activa con un clic)
let tituloOrig = document.title;
let parpadeo = null;

function fmtHora(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'Z');
  return d.toLocaleString('es-CL', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' });
}

function noLeidos(c) {
  return Math.max(0, (c.total || 0) - (leidos[c.telefono] ?? 0));
}

async function cargarLista() {
  const r = await fetch('/admin/api/conversaciones');
  if (!r.ok) return;
  const convs = await r.json();
  nombres = {};
  convs.forEach(c => { if (c.nombre) nombres[c.telefono] = c.nombre; });

  // Detectar actividad nueva (más mensajes que antes o conversación nueva)
  let convNuevo = null;
  convs.forEach(c => {
    const prevTotal = ultimoVisto[c.telefono];
    if (!primeraVez && (prevTotal === undefined || c.total > prevTotal)) {
      convNuevo = c;
    }
    // Base de "leídos": en la primera carga todo arranca leído; una conversación
    // que aparece DESPUÉS arranca como no leída (leidos = 0).
    if (leidos[c.telefono] === undefined) leidos[c.telefono] = primeraVez ? c.total : 0;
    ultimoVisto[c.telefono] = c.total;
  });
  primeraVez = false;
  if (convNuevo && !suprimirNotif) notificar(convNuevo);
  suprimirNotif = false;

  // La conversación abierta se considera leída y refresca su estado de pausa
  if (activa) {
    const c = convs.find(x => x.telefono === activa);
    if (c) { leidos[activa] = c.total; pausadaActiva = c.pausado; actualizarBotones(); }
  }

  ultimasConvs = convs;
  render();
}

function render() {
  const q = busqueda.trim().toLowerCase();
  const visibles = ultimasConvs.filter(c => {
    if (q) {
      const nom = (c.nombre || '').toLowerCase();
      if (!nom.includes(q) && !(c.telefono || '').includes(q)) return false;
    }
    if (filtro === 'humano' && !c.pausado) return false;
    if (filtro === 'nuevos' && !(noLeidos(c) > 0)) return false;
    return true;
  });

  const cont = document.getElementById('lista');
  cont.innerHTML = visibles.map(c => {
    const titulo = c.nombre ? escapar(c.nombre) : c.telefono;
    const sub = c.nombre ? `<div class="subtel">${c.telefono}</div>` : '';
    const nuevo = (noLeidos(c) > 0 && c.telefono !== activa) ? '<span class="punto"></span>' : '';
    return `
    <div class="conv ${c.telefono===activa?'activa':''}" onclick="seleccionar('${c.telefono}')">
      <div class="tel">${titulo} ${c.pausado?'<span class="badge">HUMANO</span>':''}${nuevo}</div>
      ${sub}
      <div class="ult">${c.ultimo_role==='assistant'?'Tú/Bot: ':''}${escapar((c.ultimo_mensaje||'').slice(0,40))}</div>
    </div>`;
  }).join('') || '<div class="vacio">Sin conversaciones</div>';
}

function onBuscar(v) { busqueda = v; render(); }

function setFiltro(f, btn) {
  filtro = f;
  document.querySelectorAll('.fbtn').forEach(b => b.classList.remove('activo'));
  btn.classList.add('activo');
  render();
}

function volver() {
  document.getElementById('layout').classList.remove('chat-abierto');
  activa = null;
  render();
}

async function seleccionar(tel) {
  initAudio();  // cualquier clic también desbloquea el audio
  activa = tel;
  const c = ultimasConvs.find(x => x.telefono === tel);
  if (c) { leidos[tel] = c.total; pausadaActiva = c.pausado; }
  document.getElementById('layout').classList.add('chat-abierto');  // vista móvil: mostrar el chat
  document.getElementById('chatHeader').style.display = 'flex';
  document.getElementById('chatTel').textContent = nombres[tel] || tel;
  actualizarBotones();
  await cargarConversacion();
  render();
}

async function cargarConversacion() {
  if (!activa) return;
  const r = await fetch('/admin/api/conversaciones/' + encodeURIComponent(activa));
  if (!r.ok) return;
  const msgs = await r.json();
  const cont = document.getElementById('mensajes');
  const abajo = cont.scrollHeight - cont.scrollTop - cont.clientHeight < 80;
  cont.innerHTML = msgs.map(m => `
    <div class="msg ${m.role}">${escapar(m.content)}<span class="hora">${fmtHora(m.timestamp)}</span></div>
  `).join('') || '<div class="vacio">Sin mensajes</div>';
  if (abajo) cont.scrollTop = cont.scrollHeight;
}

function actualizarBotones() {
  const btn = document.getElementById('btnControl');
  const resp = document.getElementById('responder');
  const aviso = document.getElementById('aviso');
  const input = document.getElementById('texto');
  resp.style.display = 'block';
  if (pausadaActiva) {
    btn.textContent = '✓ Devolver al agente';
    btn.classList.add('activo');
    aviso.textContent = 'Agente PAUSADO — tú estás atendiendo este chat.';
    input.disabled = false;
  } else {
    btn.textContent = 'Tomar control';
    btn.classList.remove('activo');
    aviso.textContent = 'El agente responde automáticamente. Pulsa "Tomar control" para intervenir tú.';
    input.disabled = false;
  }
}

async function toggleControl() {
  if (!activa) return;
  pausadaActiva = !pausadaActiva;
  await fetch('/admin/api/pausar', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ telefono: activa, pausado: pausadaActiva })
  });
  actualizarBotones();
  await cargarLista();
}

async function enviar() {
  const input = document.getElementById('texto');
  const texto = input.value.trim();
  if (!texto || !activa) return;
  input.value = '';
  await fetch('/admin/api/enviar', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ telefono: activa, mensaje: texto })
  });
  suprimirNotif = true;  // tu propio mensaje no debe hacer sonar la notificación
  await cargarConversacion();
  await cargarLista();
}

function escapar(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

// ──────────── Notificaciones (sonido + título + escritorio) ────────────
function initAudio() {
  if (!audioCtx) {
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch(e) {}
  }
  if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
}

function beep(freq, inicio, dur) {
  if (!audioCtx) return;
  const o = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  o.connect(g); g.connect(audioCtx.destination);
  o.type = 'sine';
  o.frequency.value = freq;
  const t = audioCtx.currentTime + inicio;
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.5, t + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.start(t); o.stop(t + dur + 0.03);
}

function sonar() {
  initAudio();
  beep(880, 0, 0.25);     // ding
  beep(1175, 0.18, 0.32); // dong
}

function flashTitulo() {
  if (parpadeo || document.hasFocus()) return;
  let on = false;
  parpadeo = setInterval(() => {
    document.title = on ? tituloOrig : '🔴 Nuevo mensaje — Dimango';
    on = !on;
  }, 1000);
}

window.addEventListener('focus', () => {
  if (parpadeo) { clearInterval(parpadeo); parpadeo = null; document.title = tituloOrig; }
});

function notificar(c) {
  sonar();
  flashTitulo();
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      new Notification('Nuevo mensaje — Dimango', {
        body: (c.nombre || c.telefono) + ': ' + (c.ultimo_mensaje || '')
      });
    } catch(e) {}
  }
}

function activarSonido() {
  initAudio();
  const btn = document.getElementById('btnSonido');
  btn.textContent = '🔔 Sonido activado';
  btn.classList.add('on');
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
  sonar();  // beep de prueba para confirmar que se escucha
}

// Refresco automático
cargarLista();
setInterval(cargarLista, 4000);
setInterval(cargarConversacion, 3000);
</script>
</body>
</html>"""
