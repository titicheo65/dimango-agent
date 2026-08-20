#!/usr/bin/env python3
"""
Recuperación selectiva sobre la memoria atómica.

Tres partes en el contexto que se le entrega a Maximus:
  1. core/          — siempre, sin excepción (identidad, conducta, perfil)
  2. índice compacto — id + título de las 61 notas, para que sepa qué existe
                       aunque no se haya recuperado (le permite decir "no lo sé,
                       pero está en tal nota" o "eso es dato vivo, se consulta en…")
  3. notas relevantes — por coincidencia léxica, expandidas por el grafo

Lo tercero es lo que hace valiosa la estructura de grafo: si una pregunta trae
H-015, también llegan sus vecinos (H-008, H-003, D-003…), que es exactamente lo
que se necesita para responder preguntas que conectan varias notas.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

BASE = Path(os.getenv("MAXIMUS_MEMORY_DIR", str(Path.home() / "harvey")))
CORE = BASE / "core"
MEM = BASE / "memoria"

STOP = set("""a al algo alguna algunas alguno algunos ante antes como con contra cual
cuales cuando de del desde donde dos el ella ellas ellos en entre era eran es esa esas
ese eso esos esta estan estas este esto estos fue fueron ha hace hacer han hasta hay la
las le les lo los mas me mi mientras mucho muy no nos o os otra otras otro otros para
pero poco por porque que quien se ser si sin sobre son su sus tambien tan tanto te
tiene tienen todo todos tu un una uno unos y ya""".split())


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


SUFIJOS = ("imientos", "amientos", "aciones", "iciones", "imiento", "amiento",
           "acion", "icion", "ando", "endo", "aron", "ieron", "imos", "amos",
           "emos", "ados", "idos", "ada", "ado", "ida", "ido", "aba", "ian",
           "aron", "ar", "er", "ir", "es", "as", "os", "s")


def raiz(w: str) -> str:
    """Stemmer mínimo para español: acerca 'vendimos', 'ventas' y 'vender'."""
    for s in SUFIJOS:
        if len(w) - len(s) >= 4 and w.endswith(s):
            return w[: -len(s)]
    return w


def tokens(t: str) -> list[str]:
    brutos = [w for w in re.findall(r"[a-z0-9%$.,]{3,}", normalizar(t)) if w not in STOP]
    return list({*brutos, *(raiz(w) for w in brutos)})


class Cerebro:
    def __init__(self):
        self.idx = json.loads((MEM / "indice.json").read_text(encoding="utf-8"))
        self.nodos = {n["id"]: n for n in self.idx["nodos"]}
        self.vecinos = {k: set() for k in self.nodos}
        for a in self.idx["aristas"]:
            self.vecinos[a["de"]].add(a["a"])
            self.vecinos[a["a"]].add(a["de"])
        self.rel = {}
        for a in self.idx["aristas"]:
            self.rel.setdefault(a["de"], []).append((a["rel"], a["a"]))

    # ── capa 1 ──
    def core(self) -> str:
        partes = []
        for f in ["IDENTITY.md", "SOUL.md", "PERFIL.md"]:
            p = CORE / f
            if p.exists():
                partes.append(f"===== core/{f} =====\n{p.read_text(encoding='utf-8')}")
        return "\n\n".join(partes)

    # ── índice compacto: qué existe en el cerebro ──
    def indice_compacto(self) -> str:
        filas = []
        for n in sorted(self.idx["nodos"], key=lambda x: x["id"]):
            marca = "" if n["estado"] in ("vigente", "") else f" [{n['estado']}]"
            filas.append(f"{n['id']} · {n['titulo']}{marca}")
        return ("ÍNDICE DE LA MEMORIA (lo que existe; pide por id lo que necesites)\n"
                + "\n".join(filas))

    # ── búsqueda ──
    def buscar(self, consulta: str, top: int = 8) -> list[tuple[str, float]]:
        q = tokens(consulta)
        if not q:
            return []
        puntajes = {}
        for nid, n in self.nodos.items():
            tit = set(tokens(n["titulo"]))
            tags = set(tokens(" ".join(n.get("tags", []))))
            cuerpo = set(tokens(n.get("cuerpo", "")))
            s = 0.0
            for w in q:
                if w in tit:
                    s += 5
                if w in tags:
                    s += 3
                if w in cuerpo:
                    s += 1
                if w.upper() == nid.lower() or w == nid.lower():
                    s += 20
            if n.get("estado") == "conflicto":
                s *= 1.15          # los conflictos deben salir a la superficie
            if s:
                puntajes[nid] = s
        return sorted(puntajes.items(), key=lambda x: -x[1])[:top]

    def nota_texto(self, nid: str) -> str:
        n = self.nodos[nid]
        rels = self.rel.get(nid, [])
        rel_txt = " · ".join(f"{r}→{d}" for r, d in rels) or "sin enlaces salientes"
        return (f"===== {nid} · {n['titulo']} =====\n"
                f"tipo: {n['tipo']} | estado: {n['estado']} | fuente: {n['fuente']} "
                f"| autoridad: {n['autoridad']} | fecha: {n['fecha']}\n"
                f"origen: {n.get('origen','—')}\n"
                f"enlaces: {rel_txt}\n\n{n['cuerpo']}")

    def contexto(self, consulta: str, top: int = 8, expandir: int = 3) -> tuple[str, str, list[str]]:
        """
        Devuelve (parte_fija, parte_variable, ids).

        La separación importa: la parte fija —core + índice— es idéntica en cada
        pregunta y se puede cachear. Si se mezcla con las notas recuperadas, el
        prompt cambia entero cada vez y el cache nunca acierta. Medido: eso hacía
        la recuperación MÁS lenta que cargar la memoria completa.
        """
        hits = self.buscar(consulta, top)
        elegidos = [nid for nid, _ in hits]
        # expandir por el grafo desde los mejores: trae lo que la nota necesita para
        # ser entendida (lo que la contradice, lo que la cierra, lo que la bloquea)
        for nid, _ in hits[:expandir]:
            for v in self.vecinos[nid]:
                if v not in elegidos:
                    elegidos.append(v)
        fija = self.core() + "\n\n" + self.indice_compacto()
        variable = ("===== NOTAS RECUPERADAS PARA ESTA PREGUNTA =====\n\n"
                    + "\n\n".join(self.nota_texto(n) for n in elegidos))
        return fija, variable, elegidos


if __name__ == "__main__":
    import sys
    c = Cerebro()
    q = " ".join(sys.argv[1:]) or "¿el Mall es rentable?"
    fija, variable, ids = c.contexto(q)
    print(f"consulta: {q}")
    print(f"notas recuperadas ({len(ids)}): {', '.join(ids)}")
    print(f"fija (cacheable): {len(fija):,} car ≈{len(fija)//4:,} tok | "
          f"variable: {len(variable):,} car ≈{len(variable)//4:,} tok")
