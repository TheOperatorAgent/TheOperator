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
curl -fsSL https://operator.bayern/install.sh | bash
```

**Windows** (PowerShell):
```powershell
irm https://operator.bayern/install.ps1 | iex
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

## Neu in 1.8 — der Operator fragt nach, bevor er etwas Riskantes tut

Die größte Sorge bei einem Assistenten mit Shell-Zugriff ist einfach: *Was, wenn er etwas
tut, das ich nicht wollte?* Genau dafür gibt es jetzt eine Antwort.

**🔐 Rückfrage im Chat, bevor etwas passiert.** Will der Operator Dateien löschen,
Administrator-Rechte nutzen, Systemdateien ändern, eine E-Mail versenden oder ein Skript aus
dem Netz ausführen, fragt er dich vorher — in einem Satz, in normaler Sprache. Du antwortest
**ja** oder **nein** (oder tippst auf ✅ / ❌). Ohne dein Ja passiert nichts. Keine Antwort,
ein Zeitablauf, eine Störung oder ein fremder Account bedeuten immer *nicht ausführen*.
Jede Freigabe gilt genau einmal und genau für diese eine Aktion.

**Und im Alltag merkst du davon nichts.** Lesen, Suchen, Recherchieren, normale Befehle,
Arbeiten im Arbeitsordner — alles läuft ohne eine einzige Rückfrage weiter. Gefragt wird nur
bei echtem Risiko. Dass das so bleibt, sichern elf automatische Tests ab.

**🛡️ Dein Heimnetz ist tabu.** Der Operator darf ins öffentliche Internet — aber nicht auf
dein Dashboard, deinen Router, deinen NAS oder deine internen Server. Auch nicht über eine
Weiterleitung von einer präparierten Webseite: Jede einzelne Anfrage wird geprüft, nicht nur
die erste.

**🧹 Deine Daten räumen sich selbst auf.** Gesprächsverlauf 30 Tage, Protokolle 14, das
Sicherheits-Audit 90 — danach wird automatisch gelöscht. Fristen änderbar, alles einsehbar,
exportierbar und auf Knopfdruck löschbar. Und im Protokoll stehen keine Gesprächsinhalte
mehr, nur noch technische Kennzahlen.

**🔑 Kein böses Erwachen.** Läuft dein Claude-Zugang ab, sagt der Operator dir das *einmal*
freundlich vorher — statt dich mitten in einer Frage auflaufen zu lassen. Mit hinterlegtem
Reserve-Schlüssel arbeitet er einfach weiter.

**🛟 Fair-Use-Schutz.** Automatische Läufe haben eine Obergrenze, damit ein falsch gesetzter
Zeitplan dein Kontingent nicht leerlaufen kann. Deine eigenen Nachrichten sind davon nie
betroffen.

## Neu in 1.7 — Browser-Agent (im Web navigieren)

- Agenten können jetzt nicht nur Webseiten *lesen*, sondern im **Browser navigieren**: Seiten
  öffnen, Links/Buttons klicken, mehrstufig recherchieren und Daten extrahieren — mitgeliefert
  als Agent **»websurfer«**.
- **Bewusst eingegrenzt (v1):** nur Lesen/Navigieren — **kein** Absenden von Formularen, keine
  Käufe, keine Zugangsdaten-Eingabe. Headless, mit Timeouts, jede Navigation im Log. (Aktionen
  mit Bestätigung sind als spätere, gegatete Ausbaustufe vorgesehen.)

## Neu in 1.6 — Persona & Profil (eigene Persönlichkeit)

- **Gib deinem Operator eine Persönlichkeit:** Name, Ton, Förmlichkeit (du/Sie) und Auftreten
  (neutral / androgyn / weiblich / männlich) — im Tab **»🎭 Persona«** mit Live-Vorschau
  „So klingt dein Operator".
- **Er lernt dich kennen:** ein kurzes, **überspringbares** Willkommens-Interview im 🧭 Assistenten
  fragt Ansprache, Rolle und Vorlieben ab und legt ein Profil an.
- **Transparent by design:** alles, was er über dich weiß und *ist*, bleibt sichtbar, editierbar
  und **jederzeit löschbar** — keine verdeckten Bindungs-Mechaniken. Profil & Persona liegen
  rein lokal (`profile.json`/`persona.json`), auditiert, im Datenschutz-Tab gelistet.

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
- **Shell-Zugriff ist Opt-in — und abgesichert.** Standardmäßig darf der Assistent nur
  lesen und im Web recherchieren. Erlaubst du ihm Kommandos, greift seit 1.8 die
  **Rückfrage vor riskanten Aktionen**: Löschen, Administrator-Rechte, Systemdateien,
  Skripte aus dem Netz — alles braucht dein ausdrückliches Ja im Chat. Harmlose Befehle
  laufen weiterhin ohne Unterbrechung.
- **Kein Zugriff auf dein Heimnetz.** Weder der Browser-Agent noch der Webseiten-Abruf
  erreichen interne Adressen (Dashboard, Router, NAS, interne Server) — auch nicht über
  Weiterleitungen.
- **Der Bot-Raum ist noch unverschlüsselt.** Matrix-Ende-zu-Ende-Verschlüsselung braucht
  eine zusätzliche Bibliothek; der Listener kommt bewusst mit der Python-Standard­bibliothek
  aus, damit er überall läuft und leicht prüfbar bleibt. Wir sagen das lieber ehrlich, als
  es zu verschweigen: **Schick deinem Assistenten keine Passwörter** — dafür gibt es den
  Tresor, der Geheimnisse als Platzhalter einsetzt.
- Zugangsdaten liegen lokal unter `~/.claude/matrix-bot/credentials.json` (Rechte 600),
  Tokens im Schlüsselbund deines Betriebssystems. Der Arbeitsordner der Agenten ist nur
  für dich lesbar.

## Sicherheit & Datenschutz — und wie wir uns abheben

Beim Operator sind Datenschutz und Sicherheit der **Bauplan**, kein nachträglicher Schalter.
Jede Nachricht durchläuft dieselbe Schutz-Pipeline, **bevor** sie ein Sprachmodell erreicht:

```
Nachricht → 🔑 Secret-Redaction → 🎭 Pseudonymisierung → Modell → ↩︎ Re-Identifikation → 🔑 Maskierung → Antwort
                                                      ↑
                          Werkzeug-Ergebnisse (Shell · Dateien · Browser) laufen
                          durch dieselbe Reinigung, bevor ein Modell sie sieht
```

Ein externes Modell sieht **nie** echte Namen, E-Mails oder Passwörter — nur unverfängliche
Platzhalter, die vor dem Versand automatisch zurückübersetzt werden. Wer ein lokales Modell
(Ollama) wählt, dessen Daten verlassen den eigenen Rechner überhaupt nicht.

### Operator vs. OpenClaw vs. Hermes Agent

Alle drei sind **selbst-gehostete** Agenten — der Unterschied liegt nicht im „lokal vs. Cloud",
sondern in **Datenschutz-Tiefe, Kanal-Philosophie und Kosten**. (Wettbewerber-Spalten nach
öffentlicher Doku, Stand 2026; „nicht dokumentiert" = kein beworbenes Feature, kein Gegenbeweis.)

| Kriterium | **Operator** | OpenClaw | Hermes Agent |
|---|---|---|---|
| Betrieb | selbst-gehostet (Mac/Linux/Windows) | selbst-gehostet | selbst-gehostet |
| Chat-Kanäle | **nur Matrix** — bewusst: E2E-fähig, selbst hostbar | viele (WhatsApp, Telegram, Signal, Discord …) | viele (WhatsApp, Telegram, Signal, Discord …) |
| Daten über Fremd-Plattformen | **nein** (kein Meta/Telegram-Server im Pfad) | ja, je nach Kanal | ja, je nach Kanal |
| PII-Schutz **vor** dem Modell | **automatische Pseudonymisierung** (Presidio + Faker) | nicht dokumentiert | nicht dokumentiert |
| Geheimnisse | verschlüsselter Tresor + **FIDO2** + Redaction + OS-Schlüsselbund | anbieterabhängig | anbieterabhängig |
| Werkzeug-Ausführung | **abgeschottet** (Pfad-Käfig, Sperrliste, Limits, Audit) | Terminal/Dateien (Sandbox anbieterabhängig) | Terminal/Dateien (dito) |
| Rückfrage vor riskanten Aktionen | **ja — im Chat, fail-closed** (ohne dein Ja passiert nichts) | Befehls-Freigabe, anbieterabhängig | Command-Approval |
| Zugriff aufs eigene Heimnetz | **technisch gesperrt** (auch über Weiterleitungen) | nicht dokumentiert | nicht dokumentiert |
| Werkzeug-Ergebnisse vor dem Modell gereinigt | **ja** (Secrets maskiert, Namen pseudonymisiert) | nicht dokumentiert | nicht dokumentiert |
| Automatische Löschfristen für lokale Daten | **ja** (30 / 14 / 90 Tage, einstellbar) | nicht dokumentiert | nicht dokumentiert |
| Modell & Kosten | **Claude-Abo — kein API-Key, keine Token-Kosten** (oder lokal Ollama/OpenAI/Azure) | API-Key eines Anbieters nötig | API-Key eines Anbieters nötig |
| Antwort-Prüfung | optionale **Zweitmodell-Prüfung** vor dem Senden | nicht dokumentiert | nicht dokumentiert |
| Bedienbarkeit | **für Nicht-Techniker** (kein Terminal, geführte Abläufe) | entwicklerzentriert | entwicklerzentriert |

**Wo wir wirklich anders sind.** OpenClaw und Hermes sind breiter bei den Messengern — das
ist ihr Vorteil. Unserer liegt eine Ebene tiefer: **was das Sprachmodell überhaupt zu sehen
bekommt**, wo Geheimnisse liegen, wie viele Türen offen stehen und ob der Assistent fragt,
bevor er handelt. Ein Kanal statt zwanzig ist dabei kein Mangel, sondern die Entscheidung
für **einen kontrollierten Eingang**, den du selbst betreibst.

Dazu kommt ein Unterschied, den man erst im Alltag merkt: Operator läuft über **dein
bestehendes Claude-Abo** — kein API-Schlüssel, keine Kosten pro Anfrage, keine Rechnung, die
mit der Nutzung wächst.

**Und das Beste daran: Du musst uns nicht glauben.** Der komplette Quellcode ist offen, und
**152 automatische Prüfungen** halten genau diese Zusagen fest — dass der Browser-Agent keine
Formulare absenden kann, dass interne Adressen gesperrt sind, dass Werkzeug-Ergebnisse
gereinigt werden, dass keine Gesprächsinhalte im Protokoll landen und dass harmlose Arbeit nie
eine Rückfrage auslöst. Nachlesbar im Repo, ausführbar auf deinem eigenen Rechner.

Vollständiges Konzept: **[docs/SICHERHEIT_UND_ARCHITEKTUR.md](docs/SICHERHEIT_UND_ARCHITEKTUR.md)**.

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
`~/.claude/matrix-bot/VERHALTEN.md` (hot-reloaded per message).

**New in 1.8 — it asks before doing anything risky.** Deleting files, admin rights, touching
system files, sending an email, running a script from the web: Operator asks you in the chat
first, in one plain sentence. You reply **yes** or **no** (or tap ✅ / ❌). Without your
explicit yes, nothing happens — no answer, a timeout, a failure or a stranger's message all
mean *don't do it*. Each approval is valid exactly once, for exactly that action. Everyday
work — reading, searching, normal commands — runs without a single interruption, and eleven
automated tests make sure it stays that way.

Also new: **your home network is off-limits** (no access to your dashboard, router or internal
servers — not even via a redirect from a prepared web page), **local data cleans itself up**
(history 30 days, logs 14, security audit 90 — adjustable, exportable, deletable), and **no
conversation content is written to the log** any more.

Security in short: only your Matrix ID is answered; shell access is opt-in and gated by the
approval flow; the bot room is not end-to-end encrypted yet — we say so plainly rather than
hiding it, because E2EE would require an extra dependency and the listener deliberately runs
on the Python standard library alone. Use the built-in vault for passwords instead of typing
them into chat.

**You don't have to take our word for it:** the full source is open and **152 automated checks**
lock in exactly these promises — that the browser agent cannot submit forms, that internal
addresses are blocked, that tool output is sanitized before any model sees it, and that safe
work never triggers a prompt.

**New in 1.5:** agents that *actually work* — foreign models (e.g. Kimi K2.7 Code via Ollama
Cloud, local models, OpenAI, Azure) can now create files, run commands and test them, safely
in a sandboxed workspace (path jail, command blocklist, step/time limits, full audit log).
A **setup assistant** in the dashboard guides you through configuration and performs actions
on confirmation (secrets only via masked forms, never in chat). Simpler login: message your
operator `dashboard` for a one-click link; publish an agent as its own chat contact with a
single click — no second account, no password. Plain-language errors instead of raw codes.
Built to be usable **without IT knowledge** (see [EINFACHHEIT.md](EINFACHHEIT.md)). How the
Operator compares to **OpenClaw** and **Hermes Agent** — and why privacy is the blueprint —
is in the security section above and in
[docs/SICHERHEIT_UND_ARCHITEKTUR.md](docs/SICHERHEIT_UND_ARCHITEKTUR.md).

## Roadmap

- **v2.0 — Dashboard:** lokale Web-Oberfläche zur Verwaltung von Agenten (Modelle, Werkzeuge,
  Veröffentlichung als eigene Matrix-Bots) sowie geführte Anbindung von Google Drive und
  Microsoft 365 mit Lese-/Schreib-Reglern je Dienst — Datenschutz by Design, alles bleibt lokal
- Ende-zu-Ende-Verschlüsselung des Bot-Raums (bedingt einen Zusatzdienst — Abwägung läuft)
- Sprachein- und -ausgabe · Formulare im Browser mit Bestätigung

## Passwort-Tresor

Der Operator kann Zugangsdaten **nutzen, ohne sie je im Klartext zu sehen**. Im Dashboard-Tab
**„Tresor"** legst du ein Master-Passwort fest und bekommst einen Wiederherstellungsschlüssel
(Notfall-Kit). Passwörter trägst du danach verschlüsselt ein und sprichst sie im Chat nur per
Referenz an: *„Logge dich mit `{{tresor:gitea-admin}}` ein"*. Der echte Wert wird erst im Moment
der Kommando-Ausführung als Umgebungsvariable eingesetzt und sofort wieder aus allen Antworten,
Logs und dem Gesprächsverlauf entfernt (Redaction). Details und Threat-Model: [SICHERHEIT.md](SICHERHEIT.md).

## Lizenz

[MIT](LICENSE)

