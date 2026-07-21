# Operator

**Your operator inside the Matrix.** Ein persönlicher Claude-Assistent in deinem
Matrix-Chat — selbst gehostet, ein Installationsbefehl.

> *Im Film ruft man den Operator an, wenn man in der Matrix Hilfe braucht — er sitzt an den
> Systemen und macht Dinge möglich. Genau das ist Operator: Du schreibst ihm im Matrix-Chat,
> er arbeitet auf deinem Rechner.*
*(English quick start below · Hauptdokumentation auf Deutsch)*

Schreib deinem Assistenten in Element (oder jedem Matrix-Client) — er liest mit deinem
eigenen Claude-Abo, denkt nach, recherchiert und antwortet in Sekunden. Läuft komplett auf
deinem eigenen Mac: kein fremder Server, keine API-Keys, keine Zusatzkosten außer deinem
bestehenden Claude-Abo.

```
Du (Element, Handy) ──▶ dein Matrix-Server ──▶ Listener (dein Mac) ──▶ Claude CLI ──▶ Antwort im Chat
```

## Voraussetzungen

- **macOS** (Apple Silicon oder Intel)
- **Claude-Abo** (Pro/Max) — der Assistent nutzt deinen persönlichen Claude-Login
- **Zwei Matrix-Accounts**: dein eigener + ein separater für den Bot
  (z. B. kostenlos auf [matrix.org](https://matrix.org) registrieren — oder auf deinem eigenen Homeserver)

## Installation

```bash
curl -fsSL http://192.168.178.53:3000/root/the-operator/raw/branch/main/install.sh | bash
```

Der Wizard führt durch alles:

1. prüft die Voraussetzungen und installiert den Claude CLI, falls er fehlt
2. **öffnet deinen Browser für die Claude-Anmeldung** und verifiziert sie automatisch
3. fragt Homeserver, Bot-Account und deine Matrix-ID ab
4. verbindet sich mit Matrix und legt euren gemeinsamen Chat-Raum an (oder findet den bestehenden)
5. richtet alle Dateien ein — inklusive deiner persönlichen Verhaltens-Datei
6. startet den Hintergrunddienst (Autostart beim Login, Neustart bei Absturz)
7. schickt dir eine Testnachricht in den Raum

Erneutes Ausführen ist gefahrlos: Das Skript repariert/aktualisiert, statt doppelt zu
installieren. Deinstallation: `bash install.sh --uninstall`.

## Agenten & Gedächtnis (v2)

- **Agenten per MD-Datei:** In `~/.claude/matrix-bot/workspace/.claude/agents/` definieren
  einfache Markdown-Dateien Spezialisten (mitgeliefert: `recherche`, `schreiber`, bei
  Shell-Freigabe auch `sysadmin`). Der Haupt-Operator delegiert selbstständig — und kann auf
  Zuruf im Chat neue Agenten anlegen („Leg mir einen Übersetzer-Agenten an").
  Günstige Modelle (`model: haiku`) schonen dein Abo-Kontingent.
- **Tokensparendes Gedächtnis:** `memory.py` speichert Fakten in einer lokalen
  SQLite-Volltext-Datenbank. Vor jedem Wecken werden nur die zur Nachricht passenden
  Einträge in den Prompt gelegt — dein Assistent erinnert sich, ohne dass der Prompt mit
  wachsendem Wissen immer teurer wird. Der Operator speichert Merkenswertes selbst
  (Shell-Freigabe nötig) und kann Einträge korrigieren oder vergessen.

## Verhalten anpassen

`~/.claude/matrix-bot/VERHALTEN.md` ist das Gehirn-Briefing deines Assistenten: Wer er ist,
was er über dich und deine Systeme wissen soll, was er darf. Die Datei wird bei **jeder**
Nachricht frisch geladen — Änderungen wirken sofort, ohne Neustart.

## Sicherheit — bitte lesen

- **Nur du wirst beantwortet.** Der Bot reagiert ausschließlich auf die Matrix-ID, die du
  bei der Installation angibst. Nachrichten aller anderen werden ignoriert.
- **Shell-Zugriff ist Opt-in.** Standardmäßig darf der Assistent nur lesen und im Web
  recherchieren. Erst wenn du es im Wizard ausdrücklich erlaubst, darf er Kommandos auf
  deinem Mac ausführen — dann kann jede deiner Chat-Nachrichten Kommandos auslösen.
  Aktiviere das nur, wenn dir klar ist, was das bedeutet.
- **Der Bot-Raum ist unverschlüsselt** (Ende-zu-Ende-Verschlüsselung steht auf der
  Roadmap). Schick deinem Assistenten keine Passwörter oder Geheimnisse.
- Zugangsdaten liegen lokal unter `~/.claude/matrix-bot/credentials.json` (Rechte 600).

## Fehlerbehebung

| Symptom | Lösung |
|---|---|
| „tippt…", aber keine Antwort | Der Bot meldet abgelaufene Claude-Logins selbst im Chat. Falls nicht: `claude /login` im Terminal |
| Gar keine Reaktion | `launchctl list \| grep matrix-claude` prüfen; Log: `tail -f ~/.claude/matrix-bot/listener.log` |
| Neu aufsetzen | Installer einfach erneut ausführen |

## English quick start

Operator is a self-hosted personal Claude assistant living in your Matrix chat — the operator you call from inside the Matrix. Requirements: macOS, a
Claude subscription, and a dedicated Matrix account for the bot. Run the install command
above — the wizard walks you through the Claude browser login and all Matrix setup, then
starts a background service. Your assistant answers within seconds, powered by your own
Claude login on your own machine. Customize its behavior in
`~/.claude/matrix-bot/VERHALTEN.md` (hot-reloaded per message). Security: only your Matrix
ID is answered; shell access is opt-in; the bot room is not end-to-end encrypted yet.

## Lizenz

[MIT](LICENSE)
