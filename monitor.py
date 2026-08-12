#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("state.json")

SOURCES = [
    {
        "name": "Ayuntamiento — promoción Actur/Valdespartera",
        "url": "https://www.zaragoza.es/sede/servicio/noticia/341908",
        "always_notify": False,
        "parser": "zaragoza_news",
    },
    {
        "name": "Zaragoza Vivienda",
        "url": "https://www.zaragozavivienda.es/",
        "always_notify": False,
        "parser": "generic",
    },
    {
        "name": "Alquiler Asequible Zaragoza",
        "url": "https://alquilerasequiblezaragoza.com/",
        "always_notify": False,
        "parser": "generic",
    },
    {
        "name": "Ayuntamiento — Vivienda Joven",
        "url": "https://www.zaragoza.es/sede/portal/juventud/vivienda/",
        "always_notify": False,
        "parser": "generic",
    },
    {
        "name": "Suelo y Vivienda de Aragón",
        "url": "https://svaragon.com/",
        "always_notify": False,
        "parser": "generic",
    },
]

KEYWORDS = [
    "actur",
    "maría zambrano",
    "maria zambrano",
    "608 viviendas",
    "alquiler asequible",
    "vivienda asequible",
    "vivienda joven",
    "jóvenes",
    "jovenes",
    "solicitud",
    "solicitudes",
    "inscripción",
    "inscripcion",
    "plazo",
    "adjudicación",
    "adjudicacion",
    "sorteo",
    "convocatoria",
    "alquiler",
]

CRITICAL_KEYWORDS = [
    "plazo de solicitudes",
    "plazo de solicitud",
    "presentar solicitud",
    "presentación de solicitudes",
    "presentacion de solicitudes",
    "solicitudes abiertas",
    "abierto el plazo",
    "abre el plazo",
    "inscripción abierta",
    "inscripcion abierta",
    "inscribirse",
    "formulario de solicitud",
    "participar en el sorteo",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ZaragozaViviendaMonitor/1.1; +https://github.com/)",
    "Accept-Language": "es-ES,es;q=0.9",
}

COMMON_NOISE_SELECTORS = [
    "script", "style", "noscript", "svg", "canvas",
    "nav", "footer", "header", "[role='navigation']",
    ".cookie", ".cookies", ".cookie-banner",
    ".breadcrumb", ".breadcrumbs",
    ".social", ".share", ".related", ".related-news",
    ".more-news", ".mas-noticias",
]

def clean_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def strip_common_noise(soup: BeautifulSoup) -> None:
    for selector in COMMON_NOISE_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

def extract_zaragoza_news(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
        tag.decompose()

    # Eliminamos bloques relacionados/dinámicos por encabezado.
    stop_labels = {
        "más noticias",
        "mas noticias",
        "otras noticias",
        "noticias relacionadas",
        "te puede interesar",
    }
    for node in list(soup.find_all(["h2", "h3", "h4", "h5", "strong"])):
        txt = clean_whitespace(node.get_text(" ", strip=True)).lower()
        if txt in stop_labels and node.parent:
            node.parent.decompose()

    # Buscamos el bloque principal de la noticia.
    candidates = []
    for sel in ["article", "main article", ".noticia", ".detalle-noticia", ".contenido-noticia", "main", "#content"]:
        for node in soup.select(sel):
            txt = clean_whitespace(node.get_text(" ", strip=True))
            if len(txt) > 500:
                candidates.append((len(txt), node))

    if candidates:
        _, main = max(candidates, key=lambda x: x[0])
        local = BeautifulSoup(str(main), "html.parser")
        strip_common_noise(local)
        text = clean_whitespace(local.get_text(" ", strip=True))
    else:
        strip_common_noise(soup)
        text = clean_whitespace(soup.get_text(" ", strip=True))

    # Corte adicional por texto para evitar "Más Noticias" si quedó embebido.
    lower = text.lower()
    cut_points = []
    for marker in [" más noticias ", " otras noticias ", " noticias relacionadas ", " te puede interesar "]:
        pos = lower.find(marker)
        if pos >= 0:
            cut_points.append(pos)
    if cut_points:
        text = text[:min(cut_points)].strip()

    return text

def extract_generic(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    strip_common_noise(soup)

    noisy_labels = {
        "más noticias",
        "mas noticias",
        "últimas noticias",
        "ultimas noticias",
        "noticias relacionadas",
        "te puede interesar",
    }
    for node in list(soup.find_all(["h2", "h3", "h4", "h5"])):
        txt = clean_whitespace(node.get_text(" ", strip=True)).lower()
        if txt in noisy_labels and node.parent:
            node.parent.decompose()

    return clean_whitespace(soup.get_text(" ", strip=True))

def normalize_text(html: str, parser_name: str) -> str:
    return extract_zaragoza_news(html) if parser_name == "zaragoza_news" else extract_generic(html)

def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"sources": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sources": {}}

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def changed_fragments(old: str, new: str, max_chars: int = 3500) -> str:
    if not old:
        return new[:max_chars]
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    pieces, used = [], 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            part = new[j1:j2].strip()
            if not part:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                break
            part = part[:remaining]
            pieces.append(part)
            used += len(part)
    return " … ".join(pieces)

def keyword_hits(text: str, words: list[str]) -> list[str]:
    lower = text.lower()
    return [w for w in words if w.lower() in lower]

def excerpt_around_keyword(text: str, hits: list[str], radius: int = 500) -> str:
    if not text:
        return ""
    lower = text.lower()
    positions = [lower.find(h.lower()) for h in hits if lower.find(h.lower()) >= 0]
    if positions:
        p = min(positions)
        snippet = text[max(0, p-radius):min(len(text), p+radius)]
    else:
        snippet = text[:1000]
    return snippet.strip()[:1000]

def send_discord(webhook_url: str, title: str, description: str, source_name: str, url: str, critical: bool):
    payload = {
        "username": "Zaragoza Vivienda Bot",
        "embeds": [{
            "title": title,
            "description": description[:3900],
            "color": 0xE74C3C if critical else 0xF1C40F,
            "fields": [
                {"name": "Fuente", "value": source_name[:1024], "inline": False},
                {"name": "Enlace oficial", "value": url[:1024], "inline": False},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
    r = requests.post(webhook_url, json=payload, timeout=20)
    r.raise_for_status()

def test_discord(webhook_url: str):
    send_discord(
        webhook_url,
        "✅ Monitor conectado",
        "El webhook funciona. A partir de ahora el monitor podrá enviar avisos a este canal.",
        "Prueba del sistema",
        "https://www.zaragoza.es/",
        False,
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-discord", action="store_true")
    args = parser.parse_args()

    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("ERROR: falta la variable DISCORD_WEBHOOK_URL", file=sys.stderr)
        sys.exit(2)

    if args.test_discord:
        test_discord(webhook)
        print("Mensaje de prueba enviado.")
        return

    state = load_state()
    previous_sources = state.setdefault("sources", {})
    first_global_run = not bool(previous_sources)

    notifications = 0
    failures = 0

    for src in SOURCES:
        name, url = src["name"], src["url"]
        parser_name = src.get("parser", "generic")
        print(f"Revisando: {name} -> {url}")

        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            text = normalize_text(response.text, parser_name)
            new_hash = digest(text)

            old_data = previous_sources.get(url)
            if old_data is None:
                previous_sources[url] = {
                    "name": name, "hash": new_hash, "text": text,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                }
                print("  Primera captura: guardada sin avisar.")
                continue

            old_hash = old_data.get("hash", "")
            old_text = old_data.get("text", "")

            if new_hash == old_hash:
                print("  Sin cambios.")
                continue

            delta = changed_fragments(old_text, text)

            # Un hash distinto sin texto nuevo útil es ruido técnico.
            if not delta.strip():
                print("  Cambio técnico sin texto nuevo relevante. Se ignora.")
                previous_sources[url] = {
                    "name": name, "hash": new_hash, "text": text,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                }
                continue

            hits = keyword_hits(delta, KEYWORDS)
            critical_hits = keyword_hits(delta, CRITICAL_KEYWORDS)

            # Solo avisamos si el TEXTO NUEVO contiene términos relevantes.
            should_notify = bool(hits) or bool(critical_hits)
            critical = bool(critical_hits)

            print(f"  Cambio detectado. Keywords: {hits or 'ninguna'}")

            if should_notify and not first_global_run:
                snippet = excerpt_around_keyword(delta, critical_hits or hits)
                title = "🚨 POSIBLE APERTURA / CAMBIO DE SOLICITUDES" if critical else "🟡 Nueva información sobre vivienda detectada"
                desc = (
                    "Se ha detectado contenido nuevo o modificado en una fuente oficial.\n\n"
                    f"**Texto nuevo relevante:**\n{snippet or '(El contenido cambió, pero no se pudo aislar un fragmento corto.)'}"
                )
                send_discord(webhook, title, desc, name, url, critical)
                notifications += 1
                print("  Aviso enviado a Discord.")
            else:
                print("  Cambio guardado sin aviso (no relevante o inicialización).")

            previous_sources[url] = {
                "name": name, "hash": new_hash, "text": text,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as exc:
            failures += 1
            print(f"  ERROR revisando {url}: {exc}", file=sys.stderr)

    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    state["last_notifications"] = notifications
    state["last_failures"] = failures
    save_state(state)

    print(f"Fin. Avisos: {notifications}. Errores: {failures}.")
    if failures == len(SOURCES):
        sys.exit(1)

if __name__ == "__main__":
    main()
