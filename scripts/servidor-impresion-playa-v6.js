/**
 * DIMANGO PRINT SERVER v6.0 (PLAYA)
 * = v5.1 + COLA DE IMPRESION (PrintJob en Base44, modelo PULL)
 *
 * NOVEDAD v6.0:
 *   Ademas de recibir el push directo (como siempre), ahora CONSULTA la cola
 *   PrintJob en Base44 cada 4 seg e imprime los pendientes de PLAYA. Asi imprime
 *   aunque el dispositivo de venta NO este en el WiFi del local (red inestable / 4G).
 *   El push directo se mantiene intacto (doble via de seguridad).
 *
 * Boletas    -> 192.168.1.139:9100
 * Precuentas -> 192.168.1.132:9100
 *
 * REQUISITO: en esta carpeta correr  ->  npm install @base44/sdk
 */
var http = require('http');
var net = require('net');
var url = require('url');
var JimpModule = require('jimp');
var Jimp = JimpModule.Jimp || JimpModule.default || JimpModule;

var PORT = 3001;
var BOLETA = { host: '192.168.1.139', port: 9100 };
var PRECUENTA = { host: '192.168.1.132', port: 9100 };

// ===== COLA Base44 (PULL) =====
var BASE44_APP_ID = '69b4f4b6d8a5cb6ca598d9e2';
var BASE44_API_KEY = 'cf05dc9ec6d149c58382b3c341a6d0bc';
var LOCAL_ESTE_SERVER = 'playa';   // este PC imprime los PrintJob de PLAYA
var POLL_MS = 4000;   // ritmo de consulta de la cola. 2000 generaba MAS 429 (peor); 4000 es mas estable.
// ==============================

var ESC = '\x1B', GS = '\x1D';
var INIT = ESC+'@', CUT = GS+'V\x01';
var BOLD_ON = ESC+'E\x01', BOLD_OFF = ESC+'E\x00';
var CENTER = ESC+'a\x01', LEFT = ESC+'a\x00';
var BIG = GS+'!\x11', NORMAL = GS+'!\x00';
var LINE = '----------------------------------------\n';
function fmtCLP(n){return '$'+Number(n||0).toLocaleString('es-CL');}
function pad(l,r,w){w=w||40;l=String(l||'');r=String(r||'');return l+' '.repeat(Math.max(1,w-l.length-r.length))+r+'\n';}

// Convertir imagen PNG base64 a bytes ESC/POS raster
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

function enviar(printer, data) {
  return new Promise(function(resolve, reject) {
    var c = new net.Socket();
    var to = setTimeout(function(){c.destroy();reject(new Error('Timeout '+printer.host));},5000);
    c.connect(printer.port, printer.host, function(){
      clearTimeout(to);
      if (Buffer.isBuffer(data)) {
        c.write(data, function(){c.end();resolve(true);});
      } else {
        c.write(Buffer.from(data,'latin1'), function(){c.end();resolve(true);});
      }
    });
    c.on('error',function(e){clearTimeout(to);reject(new Error(printer.host+': '+e.message));});
  });
}

function testPrinter(printer) {
  return new Promise(function(resolve) {
    var c = new net.Socket();
    var to = setTimeout(function(){c.destroy();resolve({ok:false,error:'Timeout',host:printer.host});},3000);
    c.connect(printer.port, printer.host, function(){clearTimeout(to);c.end();resolve({ok:true,host:printer.host});});
    c.on('error',function(e){clearTimeout(to);resolve({ok:false,error:e.message,host:printer.host});});
  });
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
//  COLA DE IMPRESION (PULL desde Base44) — NUEVO en v6.0
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

// Imprime un PrintJob. Acepta contenido como JSON (payload normal) o como texto RAW.
async function imprimirJob(d, tipo) {
  if (tipo === 'boleta') {
    var buf = await buildBoletaWithTimbre(d);
    return enviar(BOLETA, buf);
  } else {
    return enviar(PRECUENTA, ticketPrecuenta(d));
  }
}

var polling = false;
var POLL_MIN = POLL_MS;        // ritmo normal (2s)
var POLL_MAX = 6000;           // techo BAJO: una boleta nunca espera mas de ~6s, aunque haya 429
var POLL_STEP = 1000;          // backoff SUAVE: sube de a 1s (aditivo), NO se duplica
var pollDelay = POLL_MIN;      // intervalo actual (sube con backoff, baja al normalizarse)
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
          await enviar(job.tipo === 'boleta' ? BOLETA : PRECUENTA, job.contenido);
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
    // bajo (6s). Asi alivia a Base44 SIN que una boleta quede esperando 16-60s.
    pollDelay = Math.min(pollDelay + POLL_STEP, POLL_MAX);
    console.error('[COLA] Backoff: proxima consulta en ' + (pollDelay / 1000) + 's');
  } finally {
    polling = false;
  }
  if (!huboError) {
    pollDelay = POLL_MIN;   // todo OK -> volver al ritmo normal (4s)
  }
  // auto-agenda la proxima vuelta (en vez de setInterval fijo) para respetar el backoff
  setTimeout(pollQueue, pollDelay);
}
// OJO: la cola NO se arranca aqui. Se arranca dentro de server.listen (mas abajo),
// asi SOLO consulta la cola el proceso que logro tomar el puerto 3001.
// Un segundo 'node' no podra tomar el puerto, se cerrara solo y nunca imprimira duplicado.
// ============================================================

var server = http.createServer(function(req, res) {
  res.setHeader('Access-Control-Allow-Origin','*');
  res.setHeader('Access-Control-Allow-Methods','GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers','Content-Type');
  if(req.method==='OPTIONS'){res.writeHead(200);res.end();return;}
  var p = url.parse(req.url,true).pathname;
  if(p==='/health'){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,v:'6.0',boleta:BOLETA.host,precuenta:PRECUENTA.host,timbre:true,telegram:true,cola:true,local:LOCAL_ESTE_SERVER}));return;}
  if(p==='/test-132'){testPrinter(PRECUENTA).then(function(r){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(r));});return;}
  if(p==='/test-139'){testPrinter(BOLETA).then(function(r){res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(r));});return;}

  // BOLETA con timbre -> .139
  if(p==='/imprimir'&&req.method==='POST'){
    var body='';req.on('data',function(c){body+=c;});req.on('end',function(){
      try{
        var d=JSON.parse(body);
        buildBoletaWithTimbre(d).then(function(fullBuffer){
          return enviar(BOLETA, fullBuffer);
        }).then(function(){
          console.log('[BOLETA] OK .139 Mesa:'+d.mesa_numero+' '+fmtCLP(d.total)+(d.timbreBase64?' +TIMBRE':''));
          res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,printer:BOLETA.host}));
        }).catch(function(e){
          console.error('[BOLETA] ERR',e.message);res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:e.message}));
        });
      }catch(e){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:'JSON invalido'}));}
    });return;
  }

  // PRECUENTA -> .132
  if(p==='/imprimir-precuenta'&&req.method==='POST'){
    var body2='';req.on('data',function(c){body2+=c;});req.on('end',function(){
      try{var d=JSON.parse(body2);enviar(PRECUENTA,ticketPrecuenta(d)).then(function(){
        console.log('[PRECUENTA] OK .132 Mesa:'+d.mesa_numero+' '+fmtCLP(d.total));
        res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,printer:PRECUENTA.host}));
      }).catch(function(e){console.error('[PRECUENTA] ERR',e.message);res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:e.message}));});
      }catch(e){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:'JSON invalido'}));}
    });return;
  }

  // PRECUENTA-UI -> .132 (via GET/window.open)
  if(p==='/precuenta-ui'){
    var dp3=url.parse(req.url,true).query.data;
    var d3={};
    try{d3=JSON.parse(decodeURIComponent(escape(atob(decodeURIComponent(dp3)))));}catch(e){try{d3=JSON.parse(atob(decodeURIComponent(dp3)));}catch(e2){try{d3=JSON.parse(dp3);}catch(e3){}}}
    enviar(PRECUENTA,ticketPrecuenta(d3)).then(function(){console.log('[PRECUENTA-UI] OK .132 Mesa:'+d3.mesa_numero);}).catch(function(e){console.error('[PRECUENTA-UI] ERR',e.message);});
    res.writeHead(200,{'Content-Type':'text/html'});
    res.end('<html><body style="background:#000;color:#0f0;font:14px monospace;padding:20px"><h2>Precuenta enviada a '+PRECUENTA.host+'</h2><p>Mesa: '+(d3.mesa_numero||'-')+' | '+fmtCLP(d3.total)+'</p><script>setTimeout(function(){window.close()},3000)</script></body></html>');
    return;
  }

  // PRINT-UI con timbre -> .139 (via GET/window.open)
  if(p==='/print-ui'){
    var dp=url.parse(req.url,true).query.data;
    var d2={};
    try{d2=JSON.parse(decodeURIComponent(escape(atob(decodeURIComponent(dp)))));}catch(e){try{d2=JSON.parse(atob(decodeURIComponent(dp)));}catch(e2){try{d2=JSON.parse(dp);}catch(e3){}}}
    buildBoletaWithTimbre(d2).then(function(fullBuffer){
      return enviar(BOLETA, fullBuffer);
    }).then(function(){
      console.log('[PRINT-UI] OK .139 Mesa:'+d2.mesa_numero+(d2.timbreBase64?' +TIMBRE':''));
    }).catch(function(e){console.error('[PRINT-UI] ERR',e.message);});
    res.writeHead(200,{'Content-Type':'text/html'});
    res.end('<html><body style="background:#000;color:#0f0;font:14px monospace;padding:20px"><h2>Boleta enviada a '+BOLETA.host+'</h2><p>Mesa: '+(d2.mesa_numero||'-')+' | '+fmtCLP(d2.total)+'</p><p>Folio: '+(d2.folio||'Sin folio')+'</p><script>setTimeout(function(){window.close()},3000)</script></body></html>');
    return;
  }

  // TELEGRAM via GET (window.open, evita CORS)
  if(p==='/telegram-send'){
    var query=url.parse(req.url,true).query;
    var mesa=query.mesa||'?';
    var msg=query.msg||'Cliente solicita atencion';
    var token=query.token||'8888158418:AAF6wMt_QmPaaip5eMzzfYAB8ujm1tHfmqk';
    var chatid=query.chatid||'8208785474';
    var hora=new Date().toLocaleTimeString('es-CL',{timeZone:'America/Santiago'});
    var text='MESA '+mesa+'\n'+hora+'\n'+msg;
    var https=require('https');
    var tgUrl='https://api.telegram.org/bot'+token+'/sendMessage';
    var postData=JSON.stringify({chat_id:chatid,text:text,parse_mode:'HTML'});
    var tgReq=https.request(tgUrl,{method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(postData)}},function(tgRes){
      var rb='';tgRes.on('data',function(c){rb+=c;});tgRes.on('end',function(){
        console.log('[TELEGRAM] OK Mesa:'+mesa+' msg:'+msg.substring(0,30));
        res.writeHead(200,{'Content-Type':'text/html'});
        res.end('<html><body style="background:#000;color:#0f0;font:16px monospace;padding:20px;text-align:center"><h2>Notificacion enviada</h2><p>Mesa '+mesa+'</p><script>setTimeout(function(){window.close()},2000)</script></body></html>');
      });
    });
    tgReq.on('error',function(e){
      console.error('[TELEGRAM] ERR',e.message);
      res.writeHead(200,{'Content-Type':'text/html'});
      res.end('<html><body style="background:#000;color:red;font:16px monospace;padding:20px;text-align:center"><h2>Error enviando</h2><p>'+e.message+'</p><script>setTimeout(function(){window.close()},3000)</script></body></html>');
    });
    tgReq.write(postData);tgReq.end();
    return;
  }

  // TELEGRAM PROXY POST (para fetch)
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
  console.log('=== DIMANGO PRINT SERVER v6.0 (PLAYA) ===');
  console.log('Puerto     : '+PORT);
  console.log('Boletas    : '+BOLETA.host+':'+BOLETA.port);
  console.log('Precuentas : '+PRECUENTA.host+':'+PRECUENTA.port);
  console.log('Timbre     : HABILITADO (jimp)');
  console.log('Telegram   : HABILITADO (proxy)');
  console.log('Cola PULL  : HABILITADA (local='+LOCAL_ESTE_SERVER+', cada '+POLL_MS+'ms, backoff hasta '+(POLL_MAX/1000)+'s)');
  console.log('=========================================');
  // Arranca la cola SOLO ahora que este proceso es duenio del puerto 3001.
  pollQueue();
});
server.on('error',function(e){
  if(e.code==='EACCES')console.error('ERROR: Ejecuta como Administrador');
  else if(e.code==='EADDRINUSE'){
    console.error('==========================================================');
    console.error('ERROR: Puerto 3001 EN USO. Ya hay otro print server corriendo.');
    console.error('Este proceso se CIERRA para no imprimir duplicado.');
    console.error('Si quieres reiniciar limpio:  Stop-Process -Name node -Force');
    console.error('y luego arranca UNA sola instancia.');
    console.error('==========================================================');
    process.exit(1);   // <- clave: el duplicado muere y nunca llega a leer la cola
  }
  else console.error('ERROR:',e.message);
});
