// base44/functions/walletFixClass/entry.ts
// Arregla la clase de Google Wallet "DiMangoFidelidad": reemplaza cualquier URL
// de imagen rota (ngrok muerto) por el logo vivo, y reenvía la clase a revisión.
// Se ejecuta UNA vez (o las que quieras: es idempotente). Solo-escritura sobre la CLASE,
// no toca clientes ni datos de la app.

const ISSUER_ID = "BCR2DN5T43UNBIR7";
const CLASS_ID = `${ISSUER_ID}.DiMangoFidelidad`;
const SERVICE_ACCOUNT_EMAIL = "dimango-wallet@dimango-wallet.iam.gserviceaccount.com";

// Logo vivo y público (CDN base44, 512x512 PNG, verificado 200 image/png)
const GOOD_LOGO = "https://media.base44.com/images/public/69e6d36caf464481e4c831ff/be023580c_DiMango_logo_512x512.png";

// ── Utilidades de codificación (idénticas a generateGoogleWalletPass) ──
function base64url(data) {
  let str;
  if (data instanceof ArrayBuffer) {
    str = String.fromCharCode(...new Uint8Array(data));
  } else if (ArrayBuffer.isView(data)) {
    str = String.fromCharCode(...new Uint8Array(data.buffer, data.byteOffset, data.byteLength));
  } else {
    str = data;
  }
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function decodeBase64ToBuffer(input) {
  let b64 = input.replace(/-/g, '+').replace(/_/g, '/').replace(/\s/g, '');
  const rem = b64.length % 4;
  if (rem === 2) b64 += '==';
  else if (rem === 3) b64 += '=';
  const binaryStr = atob(b64);
  const bytes = new Uint8Array(binaryStr.length);
  for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
  return bytes.buffer;
}

async function importPrivateKey(rawKeyFromEnv) {
  const stripped = rawKeyFromEnv
    .replace(/-----BEGIN [A-Z ]+-----/g, '')
    .replace(/-----END [A-Z ]+-----/g, '')
    .replace(/\s/g, '');
  const derBuffer = decodeBase64ToBuffer(stripped);
  return await crypto.subtle.importKey(
    "pkcs8",
    derBuffer,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"]
  );
}

// ── Obtener access token OAuth2 del service account (scope wallet_object.issuer) ──
async function getAccessToken() {
  const rawKey = Deno.env.get("GOOGLE_WALLET_PRIVATE_KEY");
  if (!rawKey) throw new Error("GOOGLE_WALLET_PRIVATE_KEY no configurada");
  const privateKey = await importPrivateKey(rawKey);

  const now = Math.floor(Date.now() / 1000);
  const header = { alg: "RS256", typ: "JWT" };
  const claims = {
    iss: SERVICE_ACCOUNT_EMAIL,
    scope: "https://www.googleapis.com/auth/wallet_object.issuer",
    aud: "https://oauth2.googleapis.com/token",
    iat: now,
    exp: now + 3600,
  };
  const signingInput = `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(claims))}`;
  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    privateKey,
    new TextEncoder().encode(signingInput)
  );
  const assertion = `${signingInput}.${base64url(signature)}`;

  const resp = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion,
    }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(`OAuth token error: ${JSON.stringify(data)}`);
  return data.access_token;
}

// ── Recorre el objeto y reemplaza cualquier string con "ngrok" por el logo vivo ──
function repararUrlsRotas(obj) {
  let cambios = 0;
  function walk(node) {
    if (node && typeof node === "object") {
      for (const k of Object.keys(node)) {
        const v = node[k];
        if (typeof v === "string" && v.includes("ngrok")) {
          node[k] = GOOD_LOGO;
          cambios++;
        } else if (v && typeof v === "object") {
          walk(v);
        }
      }
    }
  }
  walk(obj);
  return cambios;
}

Deno.serve(async () => {
  try {
    const token = await getAccessToken();
    const base = "https://walletobjects.googleapis.com/walletobjects/v1/loyaltyClass";
    const url = `${base}/${encodeURIComponent(CLASS_ID)}`;
    const authHeaders = {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    };

    // 1) Leer la clase actual
    const getResp = await fetch(url, { headers: authHeaders });
    const clase = await getResp.json();
    if (!getResp.ok) {
      return Response.json({ error: "No pude leer la clase", detalle: clase }, { status: 500 });
    }

    const antes = {
      programLogo: clase?.programLogo?.sourceUri?.uri || null,
      heroImage: clase?.heroImage?.sourceUri?.uri || null,
      wideProgramLogo: clase?.wideProgramLogo?.sourceUri?.uri || null,
      reviewStatus: clase?.reviewStatus || null,
    };

    // 2) Reparar cualquier URL ngrok en cualquier campo de imagen
    const cambios = repararUrlsRotas(clase);

    // 3) Garantizar que el logo quede en el bueno (aunque no fuera ngrok)
    clase.programLogo = {
      sourceUri: { uri: GOOD_LOGO },
      contentDescription: { defaultValue: { language: "es", value: "DiMango" } },
    };

    // 4) Reenviar a revisión para que quede publicada
    clase.reviewStatus = "UNDER_REVIEW";

    // 5) Guardar la clase completa (PUT = reemplazo determinístico, sin merge)
    const putResp = await fetch(url, {
      method: "PUT",
      headers: authHeaders,
      body: JSON.stringify(clase),
    });
    const claseFinal = await putResp.json();
    if (!putResp.ok) {
      return Response.json({ error: "No pude guardar la clase", detalle: claseFinal }, { status: 500 });
    }

    const despues = {
      programLogo: claseFinal?.programLogo?.sourceUri?.uri || null,
      heroImage: claseFinal?.heroImage?.sourceUri?.uri || null,
      wideProgramLogo: claseFinal?.wideProgramLogo?.sourceUri?.uri || null,
      reviewStatus: claseFinal?.reviewStatus || null,
    };

    return Response.json({
      ok: true,
      classId: CLASS_ID,
      urls_ngrok_reparadas: cambios,
      antes,
      despues,
    });
  } catch (error) {
    return Response.json({ error: error.message, stack: error.stack }, { status: 500 });
  }
});
