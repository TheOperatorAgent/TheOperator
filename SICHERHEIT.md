# Sicherheit & Datenschutz — Operator

Operator läuft vollständig lokal auf deinem Rechner. Es gibt keinen Hersteller-Server, keine
Telemetrie. Dieses Dokument beschreibt ehrlich, **was geschützt ist und was nicht**.

## Speicherorte für Geheimnisse

| Was | Wo | Schutz |
|---|---|---|
| Matrix-/Dashboard-Tokens | macOS-Schlüsselbund (Service `the-operator`) | OS-Schlüsselbund, entsperrt mit Mac-Login |
| OAuth-Tokens (Google/M365) | `secrets/*.enc` | AES-256-GCM, Schlüssel im Schlüsselbund (`token-key`) |
| **Passwort-Tresor** | `secrets/vault.enc` | AES-256-GCM, Schlüssel aus **Master-Passwort** (nicht im Schlüsselbund) |
| Sitzungs-Schlüssel des Tresors | nutzer-privates Temp-Verzeichnis, `0600` | reboot-flüchtig, Owner-geprüft |

## Passwort-Tresor — Design

**Ziel:** Der Operator kann Zugangsdaten *nutzen*, ohne sie je im Klartext zu sehen. Passwörter
tauchen nicht im Chat, im LLM-Kontext, im Verlauf (sessions.db) oder in Logs auf.

### Verschlüsselung (Envelope, nach dem `age`-Muster)
- Eine Datei `secrets/vault.enc` (JSON, versioniert). Ein zufälliger 32-Byte-**Datenschlüssel
  (DEK)** verschlüsselt die gesamte Eintrags-DB mit AES-256-GCM (AAD-gebunden). Auch die
  Eintrags-**Namen** liegen verschlüsselt (Dateiname verrät nichts).
- Der DEK ist **zweifach gewrappt**: einmal mit einem Schlüssel aus dem **Master-Passwort**,
  einmal mit einem Schlüssel aus dem **Wiederherstellungsschlüssel**. Beide Schlüssel werden
  per **scrypt** (N=2¹⁷, r=8, p=1 — OWASP-Empfehlung) abgeleitet.
- Master-Passwort ändern oder Recovery = nur der DEK wird neu gewrappt; die Einträge werden
  nie neu verschlüsselt. Der Tresor ist selbstenthaltend: `vault.enc` + Master-Passwort
  genügen zum Öffnen auf einem neuen Mac (backup-portabel, keine Schlüsselbund-Abhängigkeit).

### Wiederherstellungsschlüssel
- Format `XXXXX-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX` (Crockford Base32, 29 Datenzeichen ≈ 145 bit
  Entropie + 1 Prüfsymbol). Verwechselbare Zeichen (I/L/O/U) sind ausgeschlossen; das
  Prüfsymbol erkennt Tippfehler **vor** dem teuren scrypt-Lauf.
- Wird bei Anlage und bei jeder Recovery **genau einmal** angezeigt (Notfall-Kit zum Download),
  nie gespeichert oder geloggt. Nach einer Recovery wird immer ein **neuer** Schlüssel
  ausgestellt — der alte verfällt.

### Nutzung nur per Referenz
- Der Operator arbeitet mit Platzhaltern `{{tresor:name}}` und führt Kommandos über den
  Wrapper aus: `vault.py run -- <kommando>`. Der Wrapper injiziert echte Werte als
  **Umgebungsvariablen** (nie als Kommandozeilen-Argument → nicht in `ps` sichtbar), führt aus
  und **entfernt bekannte Tresor-Werte aus stdout/stderr**, bevor die Ausgabe an den Operator
  zurückgeht.

### Redaction als zweite Verteidigungslinie
Zusätzlich läuft jede Operator-Antwort und jede Chat-Nachricht vor dem Speichern durch eine
Redaction-Schicht (`redact.py`), die bekannte Tresor-Werte und generische Secret-Muster
(Bearer-Token, JWT, AWS/GitHub/Slack-Keys, Matrix-`syt_`-Token, `passwort=…`-Zuweisungen)
durch `[REDACTED:…]` ersetzt — vor dem Schreiben in `sessions.db`, in Logs und beim
Wiedereinspielen alter Runden in den Prompt.

## Sperr-Modell
Der Tresor wird pro Sitzung mit dem Master-Passwort entsperrt. Der Sitzungs-Schlüssel liegt im
nutzer-privaten Temp-Verzeichnis und **verschwindet beim Reboot** → nach jedem Neustart ist der
Tresor gesperrt. Optionaler Auto-Lock nach Leerlauf (`vault_autolock_minutes` in
`dashboard.json`, Standard aus).

## Threat-Model — was geschützt ist / was NICHT

**Geschützt:**
- **At rest / Diebstahl der Platte / Backups:** Ohne Master-Passwort ist `vault.enc` wertlos.
  Backups enthalten die verschlüsselte Datei, nicht den Sitzungs-Schlüssel.
- **Gesperrter Zustand:** Nach Reboot kein Zugriff, bis entsperrt wird.
- **Versehentliche Leaks:** Passwörter landen durch Wrapper + Redaction nicht im Chat, im
  Verlauf oder in Logs.

**NICHT geschützt (ehrlich):**
- **Kompromittierter Mac / Schadsoftware unter deinem Benutzer:** Jeder Prozess, der als du
  läuft — inklusive der Shell des Operators — kann den Sitzungs-Schlüssel lesen, **solange der
  Tresor entsperrt ist**. Der Tresor schützt at rest, nicht gegen einen aktiv bösartigen
  lokalen Prozess in einer offenen Sitzung.
- **Vorsätzliche Umgehung durch das Modell:** Der Operator könnte einen Wert theoretisch
  umkodieren (z. B. base64) und so an der Redaction vorbeischleusen. Der Wrapper verhindert
  *versehentliche* Leaks zuverlässig, *vorsätzliche* nicht — dafür sind Verhaltensregeln und
  das Audit-Log die Leitplanken; echtes Sandboxing ist geplant (Folge-Issue).
- **Klartext-Altbestand:** `sessions.db`-Zeilen aus der Zeit *vor* der Redaction bleiben, wie
  sie sind (die Wieder-Einspielung in den Prompt wird gefiltert; die DB-Datei selbst wird in
  einem Folge-Issue nachbehandelt).
- **Vom Nutzer im Chat gesendete Passwörter** liegen im Matrix-Raumverlauf auf dem
  Homeserver — außerhalb der Kontrolle des Operators. Deshalb: Zugangsdaten immer direkt im
  Tresor anlegen, nie in den Chat schreiben.

## Dashboard-Sicherheit
Bindet nur an `127.0.0.1`, Bearer-Token-Pflicht (SHA-256-Hash gespeichert, Token via
URL-Fragment), Host-Header-Whitelist gegen DNS-Rebinding, kein Cookie ⇒ kein CSRF. Der
Tresor-Entsperr-Endpunkt hat zusätzlich eine Brute-Force-Bremse (5 Fehlversuche → 30 s Sperre);
scrypt bremst jeden Versuch ohnehin auf ~1 s.
