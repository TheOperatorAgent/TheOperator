# Operator

**Your operator inside the Matrix.** Ein persönlicher Claude-Assistent in deinem
Matrix-Chat — selbst gehostet, ein Installationsbefehl.

> *Im Film ruft man den Operator an, wenn man in der Matrix Hilfe braucht — er sitzt an den
> Systemen und macht Dinge möglich. Genau das ist Operator: Du schreibst ihm im Matrix-Chat,
> er arbeitet auf deinem Rechner.*
*(English quick start below · Hauptdokumentation auf Deutsch)*

Schreib deinem Assistenten in Element (oder jedem Matrix-Client) — er liest mit deinem
eigenen Claude-Abo, denkt nach, recherchiert und antwortet in Sekunden. Läuft komplett auf
deinem eigenen Rechner (Mac, Linux-PC oder Windows): kein fremder Server, keine API-Keys,
keine Zusatzkosten außer deinem bestehenden Claude-Abo.

```
Du (Element, Handy) ──▶ dein Matrix-Server ──▶ Listener (dein Rechner) ──▶ Claude CLI ──▶ Antwort im Chat
```

## Voraussetzungen

- **macOS, Linux oder Windows** (läuft auf allen drei — ein einziger Installationsbefehl je OS)
- **Claude-Abo** (Pro/Max) — der Assistent nutzt deinen persönlichen Claude-Login
- **Zwei Matrix-Accounts**: dein eigener + ein separater für den Bot
  (z. B. kostenlos auf [matrix.org](https://matrix.org) registrieren — oder auf deinem eigenen Homeserver)

## Installation

Ein Befehl — passend zu deinem Betriebssystem:

**macOS / Linux** (Terminal):
```bash
curl -fsSL http://192.168.178.53:3000/root/the-operator/raw/branch/main/install.sh | bash
```

**Windows** (PowerShell):
```powershell
irm http://192.168.178.53:3000/root/the-operator/raw/branch/main/install.ps1 | iex
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

## Neu in 1.5 — Agenten mit Werkzeugen, Einrichtungs-Assistent & Einfachheit

- **Agenten, die wirklich arbeiten:** Fremd-Modelle (z. B. Kimi K2.7 Code über Ollama-Cloud,
  lokale Modelle, OpenAI, Azure) können jetzt Dateien anlegen, Befehle ausführen und testen —
  **sicher in einem abgeschotteten Arbeitsordner** (Pfad-Käfig, Befehls-Sperrliste, Schritt-
  und Zeit-Limits, jede Aktion protokolliert). Pro Agent über die Werkzeug-Häkchen aktivierbar.
- **🧭 Einrichtungs-Assistent:** ein Chat im Dashboard, der beim Einrichten führt, den
  Live-Zustand kennt und Aktionen **auf Bestätigung** selbst ausführt — Passwörter/Keys nur
  über sichere, maskierte Formulare, nie im Chat.
- **Login ohne Terminal:** Schreib deinem Operator im Chat »dashboard« → Ein-Klick-Link.
  Agenten werden als eigener Chat-Kontakt veröffentlicht — ein Klick, kein zweites Konto,
  kein Passwort.
- **Klartext statt Technik:** Fehlermeldungen sind einfache Sätze mit dem nächsten Schritt —
  kein `HTTP 502` oder `M_FORBIDDEN` mehr für Endnutzer.
- **Bedienbar ohne IT-Wissen:** verbindliches Leitbild [EINFACHHEIT.md](EINFACHHEIT.md)
  (»Petra-Test«) — jedes Feature muss auch eine Büromitarbeiterin ohne Hilfe schaffen.

## Neu in v2.1 — Verlauf, Automationen, Nutzung & mehr

- **Verlauf:** Jede Assistenten-Antwort wird lokal aufgezeichnet (SQLite) und ist im
  Dashboard volltextdurchsuchbar — inkl. Dauer, Tokens und Fehlern.
- **Automationen:** Zeitgesteuerte Aufträge („Jeden Morgen 7 Uhr: …") im Cron-Format,
  mit „Jetzt ausführen", Lauf-Historie und Ziel-Wahl (Operator oder ein Agent).
- **Nutzung:** Eigenverbrauch im 5-Stunden-Fenster (Abo-Limit-Logik) plus 24h/7-Tage-Charts.
- **Gedächtnis-Browser:** Fakten einsehen, ergänzen, vergessen — direkt im Dashboard.
- **Logs & Health:** Listener-/Dashboard-Logs mit Fehlerfilter; Kacheln für
  Matrix-Server-Erreichbarkeit, Disk und Datenbank-Größen.
- **MCP-Server:** Eigene MCP-Werkzeuge per UI registrieren (mit Sicherheitsabfrage) —
  der Assistent lädt sie automatisch.
- **Backups:** Ein Klick sichert Konfiguration, Agenten, Gedächtnis und verschlüsselte
  Tokens; Wiederherstellung entpackt zur Prüfung und überschreibt nie automatisch.

**Sicherheit als Standard:** Anders als verbreitete Alternativen ist das Operator-Dashboard
ab Werk gehärtet — Bearer-Token-Pflicht (kein Cookie ⇒ kein CSRF), Host-Whitelist gegen
DNS-Rebinding, reine Loopback-Bindung, verschlüsselte Token-Ablage mit Schlüssel im
macOS-Schlüsselbund und Privacy-by-Default bei allen Integrations-Reglern.

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

## Sicherheit & Datenschutz — und wie wir uns abheben

Beim Operator sind Datenschutz und Sicherheit der **Bauplan**, kein nachträglicher Schalter.
Jede Nachricht durchläuft dieselbe Schutz-Pipeline, **bevor** sie ein Sprachmodell erreicht:

```
Nachricht → 🔑 Secret-Redaction → 🎭 Pseudonymisierung → Modell → ↩︎ Re-Identifikation → 🔑 Maskierung → Antwort
```

Ein externes Modell sieht **nie** echte Namen, E-Mails oder Passwörter — nur unverfängliche
Platzhalter, die vor dem Versand automatisch zurückübersetzt werden. Wer ein lokales Modell
(Ollama) wählt, dessen Daten verlassen den eigenen Rechner überhaupt nicht.

### Operator vs. OpenClaw vs. Hermes

| Kriterium | **Operator** | OpenClaw / Hermes (typischer Cloud-Agent) |
|---|---|---|
| Wo laufen die Daten? | **Auf deiner Hardware** (Mac/Linux/Windows), Dashboard nur localhost | Überwiegend in der Anbieter-Cloud |
| PII-Schutz vor dem Modell | **Automatische Pseudonymisierung** bei jeder Nachricht | Selten — Daten gehen meist im Klartext ans Modell |
| Geheimnis-Handling | Verschlüsselter Tresor + **FIDO2** + OS-Schlüsselbund | Variiert, oft Cloud-Secrets |
| Werkzeug-Ausführung | **Abgeschottet** (Pfad-Käfig, Sperrliste, Limits, Audit) | Anbieterabhängig, oft intransparent |
| Modell-Wahl | **Frei** — Claude, lokales Ollama, OpenAI, Azure (auch rein lokal) | Meist an einen Anbieter gebunden |
| Nachvollziehbarkeit | Manipulationssicheres Audit-Log | Nicht durchgängig |
| Antwort-Qualität | Optionale **Zweitmodell-Prüfung** vor dem Senden | Selten eingebaut |
| Bedienbarkeit | **Für Nicht-Techniker** (kein Terminal, geführte Abläufe) | Häufig entwicklerzentriert |

**Unser Alleinstellungsmerkmal:** die Kombination aus *Local-First*, *automatischem
PII-Schutz* und *FIDO2-gestütztem Tresor*. Vollständiges Konzept mit Modul-Landkarte und
Datenfluss: **[docs/SICHERHEIT_UND_ARCHITEKTUR.md](docs/SICHERHEIT_UND_ARCHITEKTUR.md)**.

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

## Roadmap

- **v2.0 — Dashboard:** lokale Web-Oberfläche zur Verwaltung von Agenten (Modelle, Werkzeuge,
  Veröffentlichung als eigene Matrix-Bots) sowie geführte Anbindung von Google Drive und
  Microsoft 365 mit Lese-/Schreib-Reglern je Dienst — Datenschutz by Design, alles bleibt lokal
- Ende-zu-Ende-Verschlüsselung des Bot-Raums

## Passwort-Tresor

Der Operator kann Zugangsdaten **nutzen, ohne sie je im Klartext zu sehen**. Im Dashboard-Tab
**„Tresor"** legst du ein Master-Passwort fest und bekommst einen Wiederherstellungsschlüssel
(Notfall-Kit). Passwörter trägst du danach verschlüsselt ein und sprichst sie im Chat nur per
Referenz an: *„Logge dich mit `{{tresor:gitea-admin}}` ein"*. Der echte Wert wird erst im Moment
der Kommando-Ausführung als Umgebungsvariable eingesetzt und sofort wieder aus allen Antworten,
Logs und dem Gesprächsverlauf entfernt (Redaction). Details und Threat-Model: [SICHERHEIT.md](SICHERHEIT.md).

## Lizenz

[MIT](LICENSE)

