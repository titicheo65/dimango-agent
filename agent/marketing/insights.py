"""
insights — métricas reales de Instagram y Facebook. SOLO LECTURA.

Por qué esto va primero (etapa 1 de la propuesta del 19-sep-2026): el gasto de
marketing de DiMango es 0,145% de la venta y no hay atribución de ninguna
clase. Sin una línea base, cualquier contenido que produzcamos después es
imposible de juzgar: en tres meses habría publicaciones bonitas y cero forma de
saber si trajeron un cliente. Primero se mide, después se publica.

Credenciales: usa las que YA existen para la mensajería —`IG_ACCESS_TOKEN` y
`MESSENGER_PAGE_TOKEN`— y no pide ninguna nueva. Si al token le falta un
permiso, se dice CUÁL falta y dónde se pide, en vez de fallar mudo: la lección
de siempre es que un error claro ahorra una tarde.

Nada de esto escribe ni publica. Publicar es otra etapa y otro permiso.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 20.0

# Permisos que hacen falta para LEER métricas. Publicar necesita otros y pasa
# por revisión de Meta; acá no se usan.
PERMISOS_LECTURA = (
    "instagram_basic",
    "instagram_manage_insights",
    "pages_read_engagement",
    "pages_show_list",
)


def _token() -> str:
    """El de la Página sirve para ambas cosas cuando Instagram está vinculado."""
    return os.getenv("MESSENGER_PAGE_TOKEN") or os.getenv("IG_ACCESS_TOKEN") or ""


async def _get(url: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(url, params=params)
        try:
            data = r.json()
        except Exception:
            return {"error": {"message": f"respuesta no-JSON ({r.status_code})"}}
        return data


def _explicar_error(data: dict) -> str | None:
    """Traduce el error de Meta a algo accionable. None si no hubo error."""
    err = (data or {}).get("error")
    if not err:
        return None
    msg = err.get("message", "error desconocido")
    code = err.get("code")
    if code in (190, 102):
        return f"El token de Meta no sirve o venció ({msg}). Hay que renovarlo en el panel de Meta."
    if code in (10, 200, 3):
        return (
            f"Falta permiso en el token ({msg}). Para leer métricas hacen falta: "
            + ", ".join(PERMISOS_LECTURA)
            + ". Se piden en el panel de Meta, sección Permisos de la app."
        )
    return f"Meta respondió: {msg}"


async def descubrir_cuentas() -> dict:
    """
    Encuentra la Página de Facebook y la cuenta de Instagram vinculada.

    Se descubren en vez de configurarse a mano: un id pegado en el código es un
    id que queda mal el día que cambie la cuenta, y nadie se entera.
    """
    token = _token()
    if not token:
        return {"ok": False, "error": "No hay token de Meta configurado en el servidor."}

    data = await _get(f"{GRAPH}/me/accounts", {
        "access_token": token,
        "fields": "id,name,instagram_business_account{id,username,followers_count}",
    })
    problema = _explicar_error(data)
    if problema:
        return {"ok": False, "error": problema}

    paginas = []
    for p in data.get("data", []):
        ig = p.get("instagram_business_account") or {}
        paginas.append({
            "page_id": p.get("id"),
            "nombre": p.get("name"),
            "ig_id": ig.get("id"),
            "ig_usuario": ig.get("username"),
            "ig_seguidores": ig.get("followers_count"),
        })
    if not paginas:
        return {"ok": False, "error": "El token no ve ninguna Página. Revisar que sea token de Página y no personal."}
    return {"ok": True, "paginas": paginas}


async def publicaciones_instagram(ig_id: str, limite: int = 50) -> dict:
    """
    Las últimas publicaciones con sus números. Esta es la línea base.

    `limite` alto a propósito: para saber qué funciona hace falta un año, no la
    semana pasada. Meta pagina; acá se trae una sola página porque 50 alcanza
    para ver el patrón sin gastar la cuota de la API.
    """
    token = _token()
    campos = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count"
    data = await _get(f"{GRAPH}/{ig_id}/media", {
        "access_token": token, "fields": campos, "limit": limite,
    })
    problema = _explicar_error(data)
    if problema:
        return {"ok": False, "error": problema}

    posts = []
    for m in data.get("data", []):
        texto = (m.get("caption") or "").strip().replace("\n", " ")
        posts.append({
            "id": m.get("id"),
            "fecha": (m.get("timestamp") or "")[:10],
            "tipo": m.get("media_type"),
            "likes": m.get("like_count") or 0,
            "comentarios": m.get("comments_count") or 0,
            "texto": texto[:120],
            "link": m.get("permalink"),
        })
    return {"ok": True, "publicaciones": posts}


async def resumen_cuenta(ig_id: str, dias: int = 30) -> dict:
    """Alcance y visitas al perfil de los últimos días."""
    token = _token()
    desde = int((datetime.now(timezone.utc) - timedelta(days=dias)).timestamp())
    data = await _get(f"{GRAPH}/{ig_id}/insights", {
        "access_token": token,
        "metric": "reach,profile_views,accounts_engaged",
        "period": "day",
        "metric_type": "total_value",
        "since": desde,
        "until": int(datetime.now(timezone.utc).timestamp()),
    })
    problema = _explicar_error(data)
    if problema:
        return {"ok": False, "error": problema}

    valores = {}
    for m in data.get("data", []):
        v = m.get("total_value", {}) or {}
        valores[m.get("name")] = v.get("value")
    return {"ok": True, "dias": dias, "metricas": valores}


def analizar(posts: list[dict]) -> dict:
    """
    Qué se puede concluir de las publicaciones, sin llamar a ningún modelo.

    A propósito es aritmética y no IA: son promedios y conteos. Gastar tokens en
    esto sería pagar por una división.
    """
    if not posts:
        return {"total": 0}

    def interaccion(p):
        return (p.get("likes") or 0) + (p.get("comentarios") or 0)

    ordenados = sorted(posts, key=interaccion, reverse=True)
    total = len(posts)
    suma = sum(interaccion(p) for p in posts)

    por_tipo: dict[str, list[int]] = {}
    for p in posts:
        por_tipo.setdefault(p.get("tipo") or "?", []).append(interaccion(p))

    por_dia: dict[str, list[int]] = {}
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    for p in posts:
        try:
            d = datetime.fromisoformat(p["fecha"]).weekday()
            por_dia.setdefault(dias_es[d], []).append(interaccion(p))
        except Exception:
            pass

    prom = lambda xs: round(sum(xs) / len(xs), 1) if xs else 0
    return {
        "total": total,
        "promedio_interaccion": prom([interaccion(p) for p in posts]),
        "total_interaccion": suma,
        "mejores": ordenados[:5],
        "peores": ordenados[-3:],
        "por_tipo": {k: {"publicaciones": len(v), "promedio": prom(v)} for k, v in por_tipo.items()},
        "por_dia": dict(sorted(
            ((k, {"publicaciones": len(v), "promedio": prom(v)}) for k, v in por_dia.items()),
            key=lambda kv: -kv[1]["promedio"],
        )),
        "desde": min(p["fecha"] for p in posts if p.get("fecha")),
        "hasta": max(p["fecha"] for p in posts if p.get("fecha")),
    }


async def linea_base() -> dict:
    """Todo junto: cuentas, publicaciones, métricas y el análisis. Nunca lanza."""
    try:
        cuentas = await descubrir_cuentas()
        if not cuentas.get("ok"):
            return cuentas

        salida = {"ok": True, "cuentas": [], "generado": datetime.now(timezone.utc).isoformat()[:19]}
        for pag in cuentas["paginas"]:
            bloque = {"pagina": pag["nombre"], "ig_usuario": pag.get("ig_usuario"),
                      "seguidores": pag.get("ig_seguidores")}
            if pag.get("ig_id"):
                posts = await publicaciones_instagram(pag["ig_id"])
                if posts.get("ok"):
                    bloque["analisis"] = analizar(posts["publicaciones"])
                else:
                    bloque["error_publicaciones"] = posts["error"]
                res = await resumen_cuenta(pag["ig_id"])
                bloque["ultimos_30_dias"] = res.get("metricas") if res.get("ok") else res.get("error")
            else:
                bloque["nota"] = "Esta Página no tiene cuenta de Instagram vinculada."
            salida["cuentas"].append(bloque)
        return salida
    except Exception as e:
        logger.error(f"[MARKETING] línea base falló: {e}")
        return {"ok": False, "error": f"No se pudo consultar Meta: {e}"}
