#!/usr/bin/env python3
"""Operator — Persona (»Soul«) + Nutzerprofil (stdlib-only).

Zwei getrennte, für den Nutzer transparente Dateien:
- persona.json  : WIE der Operator auftritt (Name, Geschlechts-Präsentation, Ton, …). Kein PII.
- profile.json  : WEN er bedient (Ansprache, Rolle, Interessen, Grenzen). Enthält PII des Owners.

Beide werden pro Nachricht frisch in den Owner-Prompt gerendert (analog VERHALTEN.md) — der
Nutzer sieht/ändert/löscht sie im Dashboard. Kein verstecktes Verhalten, keine Bindungs-
Mechanik: nur, was der Nutzer bewusst eingestellt hat.

stdlib-only, damit der Listener (stdlib) es genauso laden kann wie providers.py.
"""
import json
import os

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
PERSONA_FILE = os.path.join(BOT_DIR, "persona.json")
PROFILE_FILE = os.path.join(BOT_DIR, "profile.json")

GENDER_PRESENTATIONS = ("neutral", "androgyn", "weiblich", "männlich")
TONES = ("freundlich", "professionell", "locker", "humorvoll", "direkt", "warmherzig")
FORMALITY = ("du", "sie")
HUMOR = ("keiner", "dezent", "viel")
VERBOSITY = ("knapp", "mittel", "ausführlich")

PERSONA_DEFAULTS = {
    "name": "Operator",
    "gender_presentation": "neutral",   # bewusst neutral als Default (Bias-Falle vermeiden)
    "tone": "freundlich",
    "formality": "du",
    "humor": "dezent",
    "emoji": True,
    "verbosity": "knapp",
    "soul": "",                         # Freitext: »so soll er sich anfühlen«
    "onboarded": False,
}
PROFILE_DEFAULTS = {
    "preferred_name": "", "pronouns": "", "language": "Deutsch", "role": "",
    "interests": [], "work_context": "", "comm_prefs": "", "boundaries": [],
}


# ---------------------------------------------------------------- I/O --
def _load(path: str, defaults: dict) -> dict:
    out = dict(defaults)
    try:
        data = json.load(open(path))
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k in defaults})
    except (OSError, ValueError):
        pass
    return out


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def load_persona() -> dict:
    return _load(PERSONA_FILE, PERSONA_DEFAULTS)


def load_profile() -> dict:
    return _load(PROFILE_FILE, PROFILE_DEFAULTS)


def save_persona(patch: dict) -> dict:
    p = load_persona()
    for k in PERSONA_DEFAULTS:
        if k in patch and patch[k] is not None:
            p[k] = patch[k]
    # sanfte Validierung (nur bekannte Werte, sonst Default)
    if p["gender_presentation"] not in GENDER_PRESENTATIONS:
        p["gender_presentation"] = "neutral"
    if p["formality"] not in FORMALITY:
        p["formality"] = "du"
    p["emoji"] = bool(p["emoji"])
    p["onboarded"] = True
    _save(PERSONA_FILE, p)
    return p


def save_profile(patch: dict) -> dict:
    pr = load_profile()
    for k in PROFILE_DEFAULTS:
        if k in patch and patch[k] is not None:
            pr[k] = patch[k]
    for k in ("interests", "boundaries"):
        v = pr[k]
        if isinstance(v, str):
            pr[k] = [x.strip() for x in v.split(",") if x.strip()]
        elif isinstance(v, list):
            pr[k] = [str(x).strip() for x in v if str(x).strip()]
        else:
            pr[k] = []
    _save(PROFILE_FILE, pr)
    return pr


def delete_profile() -> None:
    try:
        os.remove(PROFILE_FILE)
    except OSError:
        pass


def is_onboarded() -> bool:
    return bool(load_persona().get("onboarded"))


# ---------------------------------------------------------------- Prompt-Rendering --
def render_persona(p: dict = None) -> str:
    """Persona → Prompt-Block (»## Wer du bist / ## Ton«)."""
    p = p or load_persona()
    gp = {"neutral": "geschlechtsneutral", "androgyn": "androgyn",
          "weiblich": "weiblich", "männlich": "männlich"}.get(p["gender_presentation"], "geschlechtsneutral")
    anrede = "per Du" if p["formality"] == "du" else "per Sie (höflich)"
    emoji = "Emojis sparsam einsetzen" if p.get("emoji") else "keine Emojis"
    laenge = {"knapp": "kurz und chat-tauglich", "mittel": "mittellang",
              "ausführlich": "ausführlich, wenn es hilft"}.get(p["verbosity"], "kurz")
    lines = ["## Wer du bist",
             f"Du bist »{p['name']}«, der persönliche Operator deines Nutzers. "
             f"Du trittst {gp} auf."]
    if p.get("soul", "").strip():
        lines.append(p["soul"].strip())
    lines += ["", "## Ton",
              f"- Sprich {anrede}.",
              f"- Grundton: {p['tone']}. Humor: {p['humor']}. {emoji}. Antworten {laenge}.",
              "- Bleib ehrlich, dass du eine KI bist; keine gespielten Gefühle vortäuschen."]
    return "\n".join(lines)


def render_profile(pr: dict = None) -> str:
    """Nutzerprofil → Prompt-Block (»## Über deinen Nutzer«). Nur befüllte Felder."""
    pr = pr or load_profile()
    out = []
    if pr.get("preferred_name"):
        anr = pr["preferred_name"] + (f" ({pr['pronouns']})" if pr.get("pronouns") else "")
        out.append(f"- Ansprache: {anr}")
    if pr.get("language") and pr["language"] != "Deutsch":
        out.append(f"- Sprache: {pr['language']}")
    if pr.get("role") or pr.get("work_context"):
        out.append(f"- Rolle/Kontext: {', '.join(x for x in (pr.get('role'), pr.get('work_context')) if x)}")
    if pr.get("interests"):
        out.append(f"- Interessen: {', '.join(pr['interests'])}")
    if pr.get("comm_prefs"):
        out.append(f"- Kommunikationswunsch: {pr['comm_prefs']}")
    if pr.get("boundaries"):
        out.append(f"- Grenzen (bitte beachten): {', '.join(pr['boundaries'])}")
    if not out:
        return ""
    return "## Über deinen Nutzer\n" + "\n".join(out)


def render_block() -> str:
    """Kombinierter Persona+Profil-Block für den Owner-Prompt.

    Leer, solange der Nutzer nichts gesetzt hat: die Persona fließt erst nach dem Onboarding
    ein (sonst würde sie VERHALTEN.md doppeln); das Profil, sobald es befüllt ist.
    """
    parts = []
    if is_onboarded():
        parts.append(render_persona())
    parts.append(render_profile())
    return "\n\n".join(p for p in parts if p.strip())
