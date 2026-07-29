"""Agenten-Verwaltung: Frontmatter-Parser/Serializer mit Roundtrip-Garantie + CRUD.

MD-Format (Claude-Code-Subagent):
---
name: recherche
description: ...
tools: WebSearch, WebFetch, Read
model: haiku
---
<Body = Systemprompt>
"""
import json
import os
import re

import sys as _sys; _sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
import platform_compat as _plat   # noqa: E402
AGENTS_DIR = os.path.join(_plat.workspace(), ".claude", "agents")   # #106
NAME_RE = re.compile(r"^[a-z0-9-]{2,32}$")
KNOWN_MODELS = ("haiku", "sonnet", "opus", "inherit")
KNOWN_TOOLS = ("Bash", "Read", "Write", "WebFetch", "WebSearch", "Agent", "Skill", "Browser")


def parse(text: str) -> dict:
    """Zerlegt eine Agenten-MD in Frontmatter-Dict + Body. Unbekannte Keys bleiben erhalten."""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {"frontmatter": {}, "body": text, "extra_keys": []}
    fm, body = {}, m.group(2)
    extra = []
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        fm[k] = v
        if k not in ("name", "description", "tools", "model"):
            extra.append(k)
    return {"frontmatter": fm, "body": body, "extra_keys": extra}


def serialize(fm: dict, body: str) -> str:
    """Kanonische Reihenfolge, unbekannte Keys hinten angehängt."""
    lines = ["---"]
    for k in ("name", "description", "tools", "model"):
        if fm.get(k):
            lines.append(f"{k}: {fm[k]}")
    for k, v in fm.items():
        if k not in ("name", "description", "tools", "model"):
            lines.append(f"{k}: {v}")
    lines.append("---")
    if not body.startswith("\n"):
        lines.append("")
    return "\n".join(lines) + body if body.startswith("\n") else "\n".join(lines) + body


def _path(name: str) -> str:
    return os.path.join(AGENTS_DIR, name + ".md")


def _atomic_write(path: str, content: str) -> None:
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.replace(path + ".tmp", path)


def list_agents() -> list:
    out = []
    if not os.path.isdir(AGENTS_DIR):
        return out
    for fn in sorted(os.listdir(AGENTS_DIR)):
        if not fn.endswith(".md"):
            continue
        name = fn[:-3]
        p = parse(open(_path(name)).read())
        fm = p["frontmatter"]
        out.append({
            "name": name,
            "description": fm.get("description", ""),
            "tools": [t.strip() for t in fm.get("tools", "").split(",") if t.strip()],
            "model": fm.get("model", "inherit"),
        })
    return out


def get_agent(name: str) -> dict | None:
    if not NAME_RE.match(name) or not os.path.exists(_path(name)):
        return None
    p = parse(open(_path(name)).read())
    fm = p["frontmatter"]
    return {
        "name": name,
        "description": fm.get("description", ""),
        "tools": [t.strip() for t in fm.get("tools", "").split(",") if t.strip()],
        "model": fm.get("model", "inherit"),
        "body": p["body"].lstrip("\n"),
        "extra_keys": p["extra_keys"],
    }


def _model_ok(model: str) -> bool:
    """Claude-Alias/inherit, volle Claude-ID (claude-…) oder Fremd-Modell <provider>/<name>."""
    return (model in KNOWN_MODELS or model.startswith("claude")
            or ("/" in model and model.split("/", 1)[0] in ("ollama", "openai", "azure")))


def save_agent(name: str, description: str, tools: list, model: str, body: str) -> tuple[bool, str]:
    if not NAME_RE.match(name):
        return False, "Name muss ^[a-z0-9-]{2,32}$ entsprechen"
    if not _model_ok(model):
        return False, f"Unbekanntes Modell: {model}"
    bad = [t for t in tools if t not in KNOWN_TOOLS]
    if bad:
        return False, f"Unbekannte Werkzeuge: {bad}"
    os.makedirs(AGENTS_DIR, exist_ok=True)
    # Unbekannte Frontmatter-Keys handeditierter Dateien erhalten
    fm = {}
    if os.path.exists(_path(name)):
        fm = parse(open(_path(name)).read())["frontmatter"]
    fm["name"] = name
    fm["description"] = description
    fm["tools"] = ", ".join(tools)
    if model == "inherit":
        fm.pop("model", None)
    else:
        fm["model"] = model
    _atomic_write(_path(name), serialize(fm, "\n\n" + body.strip() + "\n"))
    return True, "ok"


def delete_agent(name: str) -> bool:
    if NAME_RE.match(name) and os.path.exists(_path(name)):
        os.remove(_path(name))
        return True
    return False
