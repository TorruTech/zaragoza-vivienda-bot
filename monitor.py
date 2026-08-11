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
        "always_notify": True,
    },
    {
        "name": "Zaragoza Vivienda",
        "url": "https://www.zaragozavivienda.es/",
        "always_notify": False,
    },
    {
        "name": "Alquiler Asequible Zaragoza",
        "url": "https://alquilerasequiblezaragoza.com/",
        "always_notify": True,
    },
    {
        "name": "Ayuntamiento — Vivienda Joven",
        "url": "https://www.zaragoza.es/sede/portal/juventud/vivienda/",
        "always_notify": False,
    },
    {
        "name": "Suelo y Vivienda de Aragón",
        "url": "https://svaragon.com/",
        "always_notify": False,
    },
]

# Palabras que hacen que un cambio sea interesante.
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

# Si aparecen en el texto NUEVO, el aviso sube a nivel rojo.
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
    "User-Agent": (
        "Mozilla/5.0 (compatible; ZaragozaViviendaMonitor/1.0; "
        "+https://github.com/)"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

def normalize_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
        tag.decompose()

    # Conservamos el texto visible y reducimos ruido de espacios.
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

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
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def changed_fragments(old: str, new: str, max_chars: int = 3500) -> str:
    """Devuelve sobre todo el texto añadido/reemplazado para reducir falsos positivos."""
    if not old:
        return new[:max_chars]

    matcher = SequenceMatcher(None, old, new, autojunk=False)
    pieces = []
    used = 0
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
    positions = []
    for h in hits:
        p = lower.find(h.lower())
        if p >= 0:
            positions.append(p)
    if positions:
        p = min(positions)
        start = max(0, p - radius)
        end = min(len(text), p + radius)
        snippet = text[start:end]
    else:
        snippet = text[:1000]
    return snippet.strip()[:1000]

def send_discord(webhook_url: str, title: str, description: str, source_name: str, url: str, critical: bool):
    # Discord embed colors are decimal integers.
    color = 0xE74C3C if critical else 0xF1C40F
    payload = {
        "username": "Zaragoza Vivienda Bot",
        "embeds": [
            {
                "title": title,
                "description": description[:3900],
                "color": color,
                "fields": [
                    {"name": "Fuente", "value": source_name[:1024], "inline": False},
                    {"name": "Enlace oficial", "value": url[:1024], "inline": False},
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
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
        name = src["name"]
        url = src["url"]
        print(f"Revisando: {name} -> {url}")

        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            text = normalize_text(response.text)
            new_hash = digest(text)

            old_data = previous_sources.get(url)
            if old_data is None:
                previous_sources[url] = {
                    "name": name,
                    "hash": new_hash,
                    "text": text,
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
            hits = keyword_hits(delta, KEYWORDS)
            critical_hits = keyword_hits(delta, CRITICAL_KEYWORDS)

            should_notify = src.get("always_notify", False) or bool(hits) or bool(critical_hits)
            critical = bool(critical_hits)

            print(f"  Cambio detectado. Keywords: {hits or 'ninguna'}")
            if should_notify and not first_global_run:
                snippet = excerpt_around_keyword(delta, critical_hits or hits)
                if critical:
                    title = "🚨 POSIBLE APERTURA / CAMBIO DE SOLICITUDES"
                else:
                    title = "🟡 Nueva información sobre vivienda detectada"

                desc = (
                    f"Se ha detectado contenido nuevo o modificado en una fuente oficial.\n\n"
                    f"**Texto nuevo relevante:**\n{snippet or '(El contenido cambió, pero no se pudo aislar un fragmento corto.)'}"
                )
                send_discord(webhook, title, desc, name, url, critical)
                notifications += 1
                print("  Aviso enviado a Discord.")
            else:
                print("  Cambio guardado sin aviso (no relevante o inicialización).")

            previous_sources[url] = {
                "name": name,
                "hash": new_hash,
                # Guardamos texto para comparar el próximo cambio.
                "text": text,
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
    # No hacemos fallar todo el workflow por una sola web temporalmente caída.
    if failures == len(SOURCES):
        sys.exit(1)

if __name__ == "__main__":
    main()
