/**
 * DIMANGO PRINT SERVER v6.0 (MALL PLAZA)
 * = equivalente a la v6 de PLAYA, pero adaptado a MALL:
 *     - Consume la cola PrintJob de Base44 con  local: 'mall'   (modelo PULL)
 *     - Imprime por USB share  \\localhost\PRECUENTA  con  copy /b  (RAW),
 *       NO por IP de red 9100 (Mall tiene una sola termica por USB).
 *
 * POR QUE ESTE ARCHIVO:
 *   La app DimangoToGo dejo de mandar la impresion por HTTP al puerto 3001
 *   (modelo PUSH de la v5.2). Ahora SOLO crea registros PrintJob en Base44 y
 *   espera que el servidor del local los lea (modelo PULL). La v5.2 no lee esa
 *   cola, por eso Mall no imprimia. Esta v6 lee la cola y vuelve a imprimir,
 *   ademas aunque el dispositivo de venta NO este en el WiFi del local.
 *
 * REQUISITO: en esta carpeta correr  ->  npm install @base44/sdk jimp
 */
var http = require('http');
var url = require('url');
var fs = require('fs');
var os = require('os');
var path = require('path');
var exec = require('child_process').exec;
var JimpModule = require('jimp');
var Jimp = JimpModule.Jimp || JimpModule.default || JimpModule;

var PORT = 3001;

// ===== IMPRESORA MALL (USB share, RAW via copy /b) =====
// Mismo destino para boleta y precuenta (Mall tiene una sola termica USB).
var SHARE = '\\\\localhost\\PRECUENTA';   // USB003 compartida como "PRECUENTA"
// =======================================================

// ===== COLA Base44 (PULL) =====
var BASE44_APP_ID = '69b4f4b6d8a5cb6ca598d9e2';
var BASE44_API_KEY = 'cf05dc9ec6d149c58382b3c341a6d0bc';
var LOCAL_ESTE_SERVER = 'mall';   // este PC imprime los PrintJob de MALL
var POLL_MS = 4000;   // ritmo de consulta de la cola (4000 es mas estable que 2000 ante 429)
// ==============================

var ESC = '\x1B', GS = '\x1D';
var INIT = ESC+'@', CUT = GS+'V\x01';
var BOLD_ON = ESC+'E\x01', BOLD_OFF = ESC+'E\x00';
var CENTER = ESC+'a\x01', LEFT = ESC+'a\x00';
var BIG = GS+'!\x11', NORMAL = GS+'!\x00';
var LINE = '----------------------------------------\n';
function fmtCLP(n){return '$'+Number(n||0).toLocaleString('es-CL');}
function pad(l,r,w){w=w||40;l=String(l||'');r=String(r||'');return l+' '.repeat(Math.max(1,w-l.length-r.length))+r+'\n';}

// Convertir imagen PNG base64 a bytes ESC/POS raster (timbre electronico)
async function imageToEscPos(base64Data, maxWidth) {
  maxWidth = maxWidth || 384;
  try {
    var buf = Buffer.from(base64Data, 'base64');
    var image;
    if (typeof Jimp.read === 'function') {
      image = await Jimp.read(buf);
    } else if (typeof Jimp.fromBuffer === 'function') {
      image = await Jimp.fromBuffer(buf);
    } else {
      console.error('[IMAGE] No se encontro metodo read/fromBuffer en Jimp');
      return null;
    }
    if (image.width > maxWidth) {
      image.resize({w: maxWidth});
    }
    image.greyscale().contrast(0.5);
    var w = image.width || image.bitmap?.width;
    var h = image.height || image.bitmap?.height;
    var bytesPerLine = Math.ceil(w / 8);
    var header = Buffer.from([0x1D, 0x76, 0x30, 0x00,
      bytesPerLine & 0xFF, (bytesPerLine >> 8) & 0xFF,
      h & 0xFF, (h >> 8) & 0xFF]);
    var data = Buffer.alloc(bytesPerLine * h);
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        var color = image.getPixelColor(x, y);
        var r, g, b;
        if (typeof Jimp.intToRGBA === 'function') {
          var rgba = Jimp.intToRGBA(color);
          r = rgba.r; g = rgba.g; b = rgba.b;
        } else {
          r = (color >> 24) & 0xFF;
          g = (color >> 16) & 0xFF;
          b = (color >> 8) & 0xFF;
        }
        var brightness = (r + g + b) / 3;
        if (brightness < 128) {
          var bytePos = y * bytesPerLine + Math.floor(x / 8);
          var bitPos = 7 - (x % 8);
          data[bytePos] |= (1 << bitPos);
        }
      }
    }
    return Buffer.concat([header, data]);
  } catch(e) {
    console.error('[IMAGE] Error procesando imagen:', e.message);
    return null;
  }
}

function ticketBoleta(d) {
  var t = INIT+CENTER+BOLD_ON+BIG+'DIMANGO\n'+NORMAL;
  t+='Gelateria & Restaurante\n';
  t+='RUT: 76.056.870-8\n';
  t+='Avenida Raul Pey 2592 - Arica\n';
  t+='www.dimango.cl | +56 9 5667 3866\n';
  t+=BOLD_OFF+CENTER+BOLD_ON+'CUENTA\n'+BOLD_OFF+LEFT;
  var ahora = new Date();
  t+='Fecha: '+ahora.toLocaleDateString('es-CL',{timeZone:'America/Santiago'})+' Hora: '+ahora.toLocaleTimeString('es-CL',{timeZone:'America/Santiago'})+'\n';
  if(d.mesa_numero) t+='Mesa: '+d.mesa_numero+'\n';
  if(d.garzon) t+='Garzon: '+d.garzon+'\n';
  if(d.turno) t+=BOLD_ON+'TURNO # '+d.turno+'\n'+BOLD_OFF;
  if(d.local) t+='Local: '+(d.local==='playa'?'Playa Chinchorro':'Mall Plaza')+'\n';
  t+=LINE;
  t+=BOLD_ON+pad('PRODUCTO','TOTAL')+BOLD_OFF;
  t+=LINE;
  if(d.productos&&d.productos.length>0){
    d.productos.forEach(function(p){
      var q=p.quantity||p.cantidad||1;
      var name=p.product_name||p.nombre||'Producto';
      var sub=fmtCLP(p.subtotal||(p.price||p.precio_unitario||0)*q);
      t+=name+'\n';
      t+=pad('  '+q+' x '+fmtCLP(p.price||p.precio_unitario||0),sub);
    });
  }
  t+=LINE;
  t+=BOLD_ON+BIG+pad('TOTAL',fmtCLP(d.total))+NORMAL+BOLD_OFF;
  if(d.folio){
    t+='\n'+CENTER+BOLD_ON+'BOLETA ELECTRONICA N '+d.folio+'\n'+BOLD_OFF;
  }
  t+='\n'+CENTER+'Gracias por su preferencia!\nwww.dimango.cl\n';
  return t;
}

function ticketPrecuenta(d) {
  var total = d.total || 0;
  var p10 = Math.round(total * 0.10);
  var p15 = Math.round(total * 0.15);
  var p20 = Math.round(total * 0.20);
  var t = INIT+CENTER+BOLD_ON+BIG+'DIMANGO\n'+NORMAL;
  t+='Gelateria & Restaurante\n';
  t+='RUT: 76.056.870-8\n';
  t+='Avenida Raul Pey 2592 - Arica\n';
  t+='www.dimango.cl | +56 9 5667 3866\n';
  t+=BOLD_ON+'*** PRECUENTA ***\n'+BOLD_OFF+LEFT;
  var ahora = new Date();
  t+='Fecha: '+ahora.toLocaleDateString('es-CL',{timeZone:'America/Santiago'})+' Hora: '+ahora.toLocaleTimeString('es-CL',{timeZone:'America/Santiago'})+'\n';
  if(d.mesa_numero) t+='Mesa: '+d.mesa_numero+'\n';
  if(d.garzon) t+='Garzon: '+d.garzon+'\n';
  if(d.local) t+='Local: '+(d.local==='playa'?'Playa Chinchorro':'Mall Plaza')+'\n';
  t+=LINE;
  t+=BOLD_ON+pad('PRODUCTO','TOTAL')+BOLD_OFF;
  t+=LINE;
  if(d.productos&&d.productos.length>0){
    d.productos.forEach(function(p){
      var q=p.quantity||1;
      var name=p.product_name||p.nombre||'Producto';
      var sub=fmtCLP(p.subtotal||(p.price||0)*q);
      t+=name+'\n';
      t+=pad('  '+q+' x '+fmtCLP(p.price||0),sub);
      if(Array.isArray(p.modifiers)&&p.modifiers.length>0){
        p.modifiers.forEach(function(m){t+='  + '+(m.item_name||m.name||'')+'\n';});
      }
    });
  }
  t+=LINE;
  t+=BOLD_ON+pad('CONSUMO',fmtCLP(total))+BOLD_OFF;
  t+='\n';
  t+=CENTER+BOLD_ON+'PROPINA SUGERIDA\n'+BOLD_OFF+LEFT;
  t+=LINE;
  t+=pad('  10%  '+fmtCLP(p10),'Total: '+fmtCLP(total+p10));
  t+=pad('  15%  '+fmtCLP(p15),'Total: '+fmtCLP(total+p15));
  t+=pad('  20%  '+fmtCLP(p20),'Total: '+fmtCLP(total+p20));
  t+=LINE;
  t+='\n';
  t+=BOLD_ON+BIG+CENTER;
  t+='TOTAL c/10%\n';
  t+=fmtCLP(total+p10)+'\n';
  t+=NORMAL+BOLD_OFF;
  t+='\n\n';
  t+=CENTER+'Este NO es un documento tributario\n';
  t+='Solicite su boleta al momento de pagar\n';
  t+='\n';
  t+='Gracias por su preferencia!\n';
  t+='www.dimango.cl\n';
  t+='\n\n\n\n\n\n\n\n'+CUT;
  return t;
}

// ===== TRANSPORTE MALL: escribe los bytes a un archivo temporal y los manda
//       crudos (RAW) al share USB con  copy /b . Reemplaza al net.Socket de Playa.
function enviar(data) {
  return new Promise(function(resolve, reject) {
    var buf = Buffer.isBuffer(data) ? data : Buffer.from(data, 'latin1');
    var tmp = path.join(os.tmpdir(), 'dimango_print_' + Date.now() + '_' + Math.floor(Math.random()*1e6) + '.bin');
    fs.writeFile(tmp, buf, function(err) {
      if (err) return reject(err);
      // exec usa  cmd.exe /d /s /c "..."  -> maneja bien las comillas anidadas
      // (execFile rompia la ruta UNC del share). copy /b = copia binaria (RAW).
      exec('copy /b "' + tmp + '" "' + SHARE + '"', function(e, stdout, stderr) {
        try { fs.unlinkSync(tmp); } catch(x) { /* limpieza best-effort */ }
        if (e) return reject(new Error('copy /b fallo: ' + (stderr || e.message).toString().trim()));
        resolve(true);
      });
    });
  });
}

// Test de impresora: imprime una linea de prueba al share.
function testPrinter() {
  return enviar(INIT+CENTER+'== TEST MALL ==\n'+new Date().toLocaleString('es-CL',{timeZone:'America/Santiago'})+'\n\n\n'+CUT)
    .then(function(){return {ok:true,share:SHARE};})
    .catch(function(e){return {ok:false,error:e.message,share:SHARE};});
}

// Construir boleta completa con timbre como Buffer
async function buildBoletaWithTimbre(d) {
  var textPart = ticketBoleta(d);
  var textBuffer = Buffer.from(textPart, 'latin1');
  var timbreBuffer = null;
  if (d.timbreBase64) {
    console.log('[TIMBRE] Procesando imagen...');
    timbreBuffer = await imageToEscPos(d.timbreBase64, 350);
    if (timbreBuffer) {
      console.log('[TIMBRE] Imagen lista:', timbreBuffer.length, 'bytes');
    }
  }
  var centerCmd = Buffer.from([0x1B, 0x61, 0x01]); // CENTER
  var cutCmd = Buffer.from([0x1D, 0x56, 0x01]); // CUT
  var feed = Buffer.from('\n\n\n\n\n', 'latin1');
  if (timbreBuffer) {
    return Buffer.concat([textBuffer, centerCmd, timbreBuffer, feed, cutCmd]);
  } else {
    return Buffer.concat([textBuffer, feed, cutCmd]);
  }
}

// ============================================================
//  COLA DE IMPRESION (PULL desde Base44)
// ============================================================
var base44Client = null;
async function getBase44() {
  if (base44Client) return base44Client;
  var mod = await import('@base44/sdk');
  var createClient = mod.createClient || (mod.default && mod.default.createClient);
  base44Client = createClient({
    appId: BASE44_APP_ID,
    headers: { 'api_key': BASE44_API_KEY }
  });
  return base44Client;
}

// Imprime un PrintJob. boleta -> con timbre ; precuenta -> texto. Mismo destino USB.
async function imprimirJob(d, tipo) {
  if (tipo === 'boleta') {
    var buf = await buildBoletaWithTimbre(d);
    return enviar(buf);
  } else {
    return enviar(ticketPrecuenta(d));
  }
}

var polling = false;
var POLL_MIN = POLL_MS;        // ritmo normal (4s)
var POLL_MAX = 6000;           // techo BAJO: un ticket nunca espera mas de ~6s, aunque haya 429
var POLL_STEP = 1000;          // backoff SUAVE: sube de a 1s (aditivo), NO se duplica
var pollDelay = POLL_MIN;      // intervalo actual
async function pollQueue() {
  if (polling) return;        // evita solaparse si una vuelta tarda
  polling = true;
  var huboError = false;
  try {
    var b = await getBase44();
    var jobs = await b.entities.PrintJob.filter({ local: LOCAL_ESTE_SERVER, estado: 'pendiente' });
    for (var i = 0; i < jobs.length; i++) {
      var job = jobs[i];
      try {
        // marcar 'imprimiendo' para que otra vuelta no lo tome dos veces
        await b.entities.PrintJob.update(job.id, { estado: 'imprimiendo' });

        var d = null;
        try { d = JSON.parse(job.contenido); } catch(e) { d = null; }

        if (d && typeof d === 'object') {
          await imprimirJob(d, job.tipo);
        } else {
          // contenido ya viene como texto/raw ESC-POS
          await enviar(job.contenido);
        }

        await b.entities.PrintJob.update(job.id, {
          estado: 'impreso',
          printed_at: new Date().toISOString()
        });
        console.log('[COLA] Impreso job ' + job.id + ' (' + job.tipo + ' mesa ' + (job.mesa_numero||'-') + ')');
      } catch (e) {
        console.error('[COLA] ERROR job ' + job.id + ':', e.message);
        try {
          await b.entities.PrintJob.update(job.id, {
            estado: 'error',
            error_mensaje: String(e.message).slice(0, 250),
            intentos: (job.intentos || 0) + 1
          });
        } catch(e2) { /* si ni siquiera puede actualizar, se reintenta luego */ }
      }
    }
  } catch (e) {
    huboError = true;
    console.error('[COLA] Error consultando la cola:', e.message);
    // BACKOFF SUAVE: ante error (sobre todo 429) subir la espera de a 1s, con techo
    // bajo (6s). Asi alivia a Base44 SIN que un ticket quede esperando 16-60s.
    pollDelay = Math.min(pollDelay + POLL_STEP, POLL_MAX);
    console.error('[COLA] Backoff: proxima consulta en ' + (pollDelay / 1000) + 's');
  } finally {
    polling = false;
  }
  if (!huboError) {
    pollDelay = POLL_MIN;   // todo OK -> volver al ritmo normal (4s)
  }
  setTimeout(pollQueue, pollDelay);
}
// La cola se arranca DENTRO de server.listen: solo consulta el proceso que tomo el puerto 3001.
// ============================================================

var server = http.createServer(function(req, res) {
  res.setHeader('Access-Control-Allow-Origin','*');
  res.setHeader('Access-Control-Allow-Methods','GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers','Content-Type');
  if(req.method==='OPTIONS'){res.writeHead(200);res.end();return;}
  var p = url.parse(req.url,true).pathname;
  if(p==='/health'){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,v:'6.0-mall',share:SHARE,timbre:true,telegram:true,cola:true,local:LOCAL_ESTE_SERVER}));return;}
  if(p==='/test'){testPrinter().then(function(r){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(r));});return;}

  // BOLETA con timbre (push directo, doble via de seguridad) -> USB share
  if(p==='/imprimir'&&req.method==='POST'){
    var body='';req.on('data',function(c){body+=c;});req.on('end',function(){
      try{
        var d=JSON.parse(body);
        buildBoletaWithTimbre(d).then(function(fullBuffer){
          return enviar(fullBuffer);
        }).then(function(){
          console.log('[BOLETA] OK USB Mesa:'+d.mesa_numero+' '+fmtCLP(d.total)+(d.timbreBase64?' +TIMBRE':''));
          res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,share:SHARE}));
        }).catch(function(e){
          console.error('[BOLETA] ERR',e.message);res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:e.message}));
        });
      }catch(e){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:'JSON invalido'}));}
    });return;
  }

  // PRECUENTA (push directo) -> USB share
  if(p==='/imprimir-precuenta'&&req.method==='POST'){
    var body2='';req.on('data',function(c){body2+=c;});req.on('end',function(){
      try{var d=JSON.parse(body2);enviar(ticketPrecuenta(d)).then(function(){
        console.log('[PRECUENTA] OK USB Mesa:'+d.mesa_numero+' '+fmtCLP(d.total));
        res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,share:SHARE}));
      }).catch(function(e){console.error('[PRECUENTA] ERR',e.message);res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:e.message}));});
      }catch(e){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:'JSON invalido'}));}
    });return;
  }

  // TELEGRAM PROXY POST (para fetch) — notificacion al equipo
  if(p==='/telegram'&&req.method==='POST'){
    var body3='';req.on('data',function(c){body3+=c;});req.on('end',function(){
      try{
        var d=JSON.parse(body3);
        var botToken=d.bot_token||'8888158418:AAF6wMt_QmPaaip5eMzzfYAB8ujm1tHfmqk';
        var chatId=d.chat_id||'8208785474';
        var text=d.text||'Sin mensaje';
        var tgUrl='https://api.telegram.org/bot'+botToken+'/sendMessage';
        var postData=JSON.stringify({chat_id:chatId,text:text,parse_mode:'HTML'});
        var https=require('https');
        var tgReq=https.request(tgUrl,{method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(postData)}},function(tgRes){
          var respBody='';
          tgRes.on('data',function(chunk){respBody+=chunk;});
          tgRes.on('end',function(){
            console.log('[TELEGRAM] OK chat:'+chatId+' text:'+text.substring(0,50));
            res.writeHead(200,{'Content-Type':'application/json'});
            res.end(JSON.stringify({ok:true,telegram:JSON.parse(respBody)}));
          });
        });
        tgReq.on('error',function(e){
          console.error('[TELEGRAM] ERR',e.message);
          res.writeHead(500,{'Content-Type':'application/json'});
          res.end(JSON.stringify({ok:false,error:e.message}));
        });
        tgReq.write(postData);
        tgReq.end();
      }catch(e){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:'JSON invalido'}));}
    });return;
  }

  res.writeHead(404,{'Content-Type':'application/json'});
  res.end(JSON.stringify({error:'Ruta no encontrada: '+p}));
});
server.listen(PORT,'0.0.0.0',function(){
  console.log('');
  console.log('=== DIMANGO PRINT SERVER v6.0 (MALL PLAZA) ===');
  console.log('Puerto     : '+PORT);
  console.log('Impresora  : '+SHARE+' (USB RAW via copy /b)');
  console.log('Local      : Diego Portales 640 Local 116');
  console.log('Timbre     : HABILITADO (jimp)');
  console.log('Telegram   : HABILITADO (proxy)');
  console.log('Cola PULL  : HABILITADA (local='+LOCAL_ESTE_SERVER+', cada '+POLL_MS+'ms, backoff hasta '+(POLL_MAX/1000)+'s)');
  console.log('==============================================');
  // Arranca la cola SOLO ahora que este proceso es duenio del puerto 3001.
  pollQueue();
});
server.on('error',function(e){
  if(e.code==='EACCES')console.error('ERROR: Ejecuta como Administrador');
  else if(e.code==='EADDRINUSE'){
    console.error('==========================================================');
    console.error('ERROR: Puerto 3001 EN USO. Ya hay otro print server corriendo.');
    console.error('Este proceso se CIERRA para no imprimir duplicado.');
    console.error('Cierra la v5.2 antigua primero:  Stop-Process -Name node -Force');
    console.error('y luego arranca UNA sola instancia de esta v6.');
    console.error('==========================================================');
    process.exit(1);
  }
  else console.error('ERROR:',e.message);
});
