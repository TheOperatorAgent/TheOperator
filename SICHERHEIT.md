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
| Vaultwarden-Session (optionales Backend) | nutzer-privates Temp-Verzeichnis, `0600` | reboot-flüchtig, Owner-geprüft; Master-PW nie gespeichert |

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

## Pseudonymisierung — PII erreicht das Sprachmodell nie im Klartext

Eine dritte, davon getrennte Ebene (standardmäßig AN, im Dashboard → Datenschutz abschaltbar):
Bevor Nutzerdaten an Claude gehen, werden **personenbezogene Daten** (Namen, E-Mail, Telefon,
IBAN, Orte, Firmen) durch **konsistente, realistische Ersatzwerte** ersetzt; in der Antwort und
in Tool-Aktionen werden die echten Werte wieder eingesetzt.

- **Erkennung:** Microsoft Presidio + deutsches spaCy-NER (`de_core_news_lg`) + Regex-Recognizer
  (E-Mail/Telefon/IBAN/…) + eigener Anrede-Recognizer („Frau Wagner"). Over-Detection von
  Imperativen/Pronomen wird per Stopwort-Liste, POS-Check und Span-Trimming vermieden.
- **Ersatzwerte:** Faker(`de_DE`) erzeugt plausible Namen/Adressen; ein konsistentes,
  bidirektionales Mapping (gleicher Wert → gleicher Ersatz, auch über mehrere Segmente) erlaubt
  die exakte Rückübersetzung, inkl. Teilnamen (nur Vorname).
- **Reihenfolge:** erst Secret-Redaction (`redact.py`, irreversibel), **dann** Pseudonymisierung
  (reversibel) — so wandert nie ein Token in das PII-Mapping.
- **Tool-Brücke:** Der Operator „denkt" in Ersatzwerten; `send.py`, die m365-/n8n-Tools lösen sie
  über `reid.py` erst **an der Ausführungsgrenze** auf (Mapping-Pfad flüchtig via
  `$OPERATOR_PII_MAP`, `0600`, nur für die Dauer eines Laufs). Michi sieht in der Chat-Antwort
  echte Namen; Anthropic sieht nur Ersatzwerte.
- **fail-safe:** Ist der Dienst nicht verfügbar und Pseudonymisierung AN, wird die Nachricht
  **nicht** an Claude geschickt (Meldung im Chat), statt ungeschützt zu senden.

**Grenzen (ehrlich):** NER ist nicht perfekt (~85–92 % Trefferquote) — einzelne unübliche Namen
können durchrutschen; die **Deny-Liste** im Dashboard fängt bekannte Kontakte zusätzlich ab. Das
lokale Mapping enthält Klartext-PII → nur im Arbeitsspeicher / flüchtig, **nie** in `sessions.db`
oder Logs. Ein an das Sprachmodell durchgereichter Ersatzwert ist als solcher nicht markiert
(realistische Namen statt Tokens) — das ist der bewusste Preis für hohe Antwortqualität.

### Entsperren mit FIDO2-Hardware-Key (optional)
Als bequeme Alternative zum Master-Passwort kann der Tresor mit einem FIDO2-Hardware-Key
(YubiKey o. ä.) entsperrt werden — einstecken und antippen.
- **Mechanismus:** CTAP2-`hmac-secret`-Extension (via `python-fido2`, BSD-Lizenz). Der Key
  liefert für ein gespeichertes `salt` einen deterministischen 32-Byte-Wert
  (`HMAC(CredRandom, salt)`), der das Secure Element nie verlässt und nur mit **genau diesem
  physischen Key** reproduzierbar ist. Dieser Wert wird als KEK ein **weiterer DEK-Wrap** —
  gleichwertig neben Master-Passwort und Recovery-Key. Es findet **keine** Re-Encryption statt.
- **Mehrere Keys:** Es können mehrere Keys registriert werden (Haupt + Backup), jeder ein
  eigener Wrap; alle teilen ein gemeinsames `salt`, sodass ein einziges Antippen beim Öffnen
  den passenden Key findet. Gespeichert werden nur `credential_id` + `salt` (öffentlich).
- **Touch-only (bewusste Wahl):** Es wird **nur User-Presence (Antippen)** verlangt, keine
  Key-PIN (`CredRandomWithoutUV`). Bequem — aber: **wer den physischen Key besitzt UND Zugriff
  auf die `vault.enc`-Datei hat, kann den Tresor öffnen.** Gegenmittel: bei Verlust den Key im
  Dashboard entfernen; Master-Passwort und Recovery-Key funktionieren immer weiter. Wer stärkere
  Bindung will, kann später auf PIN+Touch umstellen (Format ist vorbereitet).
- **Key verloren?** Recovery-Key nutzen (oder Master-Passwort), dann den verlorenen Key im
  Dashboard entfernen. Der Key allein enthält keine Tresordaten.

### Optionales Vaultwarden-Backend (Issue #19)
Statt des lokalen `vault.enc` kann `{{tresor:name}}` optional aus einer selbst gehosteten
**Vaultwarden-Instanz** (Bitwarden-kompatibel) aufgelöst werden. Umschaltbar im Dashboard-Tresor-Tab;
**Standard bleibt lokal** (Produkt-Nutzer ohne Vaultwarden brauchen nichts weiter).
- **Mechanismus:** Die offizielle `bw`-CLI wird über ein eigenes, isoliertes Datenverzeichnis
  (`secrets/bw-data`) angesprochen. `bw unlock` liefert ein `BW_SESSION`-Token, das — **genau wie
  der lokale DEK** — als flüchtige `0600`-Datei im nutzer-privaten Temp liegt (reboot-flüchtig,
  Owner-geprüft, gleicher Auto-Lock über `vault_autolock_minutes`).
- **Master-Passwort:** wird **nie persistiert** und nur transient per Umgebungsvariable
  (`BW_MASTERPW`) an `bw` gereicht — **nicht via argv** (nicht in `ps` sichtbar). Beim ersten Mal
  ist zusätzlich die E-Mail nötig (Login), danach genügt das Passwort (Unlock).
- **Nutzung identisch:** Der Operator sieht weiterhin nur Referenzen; der `run`-Wrapper löst sie
  bei aktivem Vaultwarden-Backend über `bw get password` auf. Allowlist-Härtung (#22) und Output-
  Redaction gelten unverändert. Die Einträge werden **in Vaultwarden** gepflegt (Dashboard zeigt
  sie read-only, ohne Passwörter).
- **Grenzen (ehrlich):** Netz-Abhängigkeit (Vaultwarden muss erreichbar sein); `bw` ist eine
  externe Node-CLI (größere Angriffsfläche als der stdlib-lokale Tresor) → bleibt bewusst optional.
  Das `BW_SESSION`-Token ist so sensibel wie der lokale DEK (gleiche at-rest-Grenze). Bei aktiver
  CLI-Zwei-Faktor-Anmeldung schlägt der Login mit klarer Meldung fehl (CLI-2FA deaktivieren oder
  API-Key nutzen). Die Listener-Redaction kennt bei Vaultwarden nur die **referenzierten** Werte
  (nicht die ganze Vault-Liste); generische Secret-Muster greifen weiterhin.

## Sperr-Modell
Der Tresor wird pro Sitzung mit dem Master-Passwort entsperrt. Der Sitzungs-Schlüssel liegt im
nutzer-privaten Temp-Verzeichnis und **verschwindet beim Reboot** → nach jedem Neustart ist der
Tresor gesperrt. Optionaler Auto-Lock nach Leerlauf (`vault_autolock_minutes` in
`dashboard.json`, Standard aus).

## Plattformen (macOS / Linux / Windows)
Der Operator läuft auf allen drei Systemen; OS-Unterschiede sind in `platform_compat.py`,
`secretstore.py` und `servicemgr.py` (alle stdlib) gekapselt. Auf macOS verhält sich alles
bitidentisch zum bisherigen Stand.

| Aspekt | macOS | Linux | Windows |
|---|---|---|---|
| Secret-Store (Tokens, Master-Key) | Schlüsselbund (`security`) | Secret-Service (`secret-tool`) sonst 0600-Datei | **DPAPI** (user-gebunden, `ConvertFrom/To-SecureString`) sonst 0600-Datei |
| Sitzungsdateien (DEK, Vaultwarden-Session) | nutzer-privates Temp `0600` | `$XDG_RUNTIME_DIR` `0600` | `%LOCALAPPDATA%\Temp` + ACL |
| Daemon-IPC (Pseudonym) | AF_UNIX-Socket `0600` | AF_UNIX-Socket `0600` | TCP-Loopback 127.0.0.1 **+ Zufallstoken** (verhindert Fremdzugriff) |
| Autostart-Dienst | launchd (LaunchAgent) | systemd-user (`enable-linger`) | Task Scheduler (onlogon, Restart-on-fail) |
| Sicherheitsschlüssel (FIDO2) | ✅ | ✅ (udev-Regel nötig) | ⏸ vorerst deaktiviert (WebAuthn-API-Umstellung nötig) |

**Ehrliche Grenzen:**
- **Windows-Dateirechte:** POSIX-`0o600` schützt unter Windows nicht wie erwartet (NTFS-ACLs
  statt Mode-Bits). Der Master-Passwort-Schutz des Tresors ist plattformunabhängig (Krypto);
  für Klartext-Sitzungsdateien setzt `platform_compat.secure_chmod()` best-effort eine
  Nutzer-ACL via `icacls`. Der stärkste Windows-Secret-Schutz ist DPAPI (an Windows-Login gebunden).
- **FIDO2 auf Windows:** Der direkte HID-Zugriff ist für nicht-elevierte Prozesse gesperrt; die
  Entsperrung per Sicherheitsschlüssel ist dort vorerst deaktiviert (klarer Hinweis im Dashboard).
  Master-Passwort, Wiederherstellungsschlüssel und Vaultwarden funktionieren überall.
- **Datei-Fallback:** Fehlt ein OS-Secret-Store (z. B. headless-Linux ohne Secret-Service), landen
  Tokens als `0600`-Datei unter `secrets/` — dieselbe Grenze wie beim bisherigen `.token`-Fallback.

## Fremd-Sprachmodelle (Ollama · OpenAI · Azure) — optional
Einzelne Agenten können ein Fremd-Modell nutzen. Sicherheitsrelevant:
- **PII bleibt geschützt:** Text an Fremd-Modelle durchläuft dieselbe **Pseudonymisierung** wie
  bei Claude (das Fremd-LLM sieht nie echte Namen/Mails — bei Cloud-Anbietern besonders wichtig);
  die Antwort wird erst nach der Reidentifikation in den Chat gesendet.
- **Keine lokalen Werkzeuge:** Fremd-Agenten liefern nur Text — kein Bash/Datei/MCP-Zugriff.
  Der volle Werkzeugkasten bleibt Claude-Agenten vorbehalten (bewusst, siehe ARCHITEKTUR.md).
- **API-Keys** (openai/azure/anthropic) liegen im **OS-Secret-Store** (secretstore, wie die
  Matrix-Tokens); Ollama braucht keinen. `connections/models.json` enthält nur URLs/Modellnamen.
- **Claude-API-Key als Reserve:** optional, standardmäßig aus; springt nur bei Abo-Limit ein
  (mit Chat-Hinweis) — verursacht dann echte API-Kosten, daher bewusst opt-in.

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

## Backup & Restore — was wiederherstellbar ist (Issue #21)

Das Dashboard-Backup (`~/OperatorBackups/*.tar.gz`) enthält Konfiguration, Agenten, Skills,
Gedächtnis, Verlauf und die **verschlüsselten** Secret-Dateien (`secrets/*.enc`) — aber
bewusst **nicht** den macOS-Schlüsselbund. Daraus folgt für einen Restore auf einem **neuen**
Mac (ohne den ursprünglichen Schlüsselbund):

| Was | Aus dem tar-Backup allein wiederherstellbar? |
|---|---|
| **Passwort-Tresor** (`vault.enc`) | ✅ **Ja** — Master-Passwort (oder Recovery-Key) genügt, schlüsselbund-unabhängig |
| Gedächtnis, Verlauf, Skills, Agenten, Config | ✅ Ja (Klartext-Dateien bzw. tresor-unabhängig) |
| OAuth-Tokens (Google/M365) + n8n-API-Key | ❌ **Nein** — verschlüsselt mit dem Schlüsselbund-Schlüssel `token-key`, der nicht im Backup liegt → die `.enc` sind ohne ihn wertlos; die Dienste einfach im Dashboard neu verbinden |
| Matrix-Tokens (Owner/Bots) | ❌ Nein — liegen im Schlüsselbund; auf neuem Mac neu anmelden (`claude`-CLI + Bot-Login) |

**Kernaussage:** Der Tresor ist voll portabel, die *Dienst-Anbindungen* (Google/M365/n8n/Matrix)
sind es bewusst nicht — sie werden auf einem neuen Rechner in wenigen Klicks neu verbunden.
Das ist eine Sicherheitseigenschaft (Tokens verlassen den Schlüsselbund nie), kein Datenverlust.
Wer echte Voll-Portabilität will, kann den Schlüsselbund-Eintrag `the-operator/token-key`
manuell exportieren und getrennt sichern — dann sind auch die OAuth-`.enc` woanders nutzbar.

## Dashboard-Sicherheit
Bindet nur an `127.0.0.1`, Bearer-Token-Pflicht (SHA-256-Hash gespeichert, Token via
URL-Fragment), Host-Header-Whitelist gegen DNS-Rebinding, kein Cookie ⇒ kein CSRF. Der
Tresor-Entsperr-Endpunkt hat zusätzlich eine Brute-Force-Bremse (5 Fehlversuche → 30 s Sperre);
scrypt bremst jeden Versuch ohnehin auf ~1 s.


## Nutzungsmodell & Grenzen (Claude-Abo)

Operator läuft über dein **eigenes Claude-Abo** — und zwar über die **offizielle Claude-Code-CLI**
(`claude -p`), den von Anthropic unterstützten Weg. Er extrahiert **keine** Abo-OAuth-Tokens und
betreibt **keinen** reverse-engineerten Zugang. Damit fällt Operator **nicht** unter Anthropics
Sperre vom 04.04.2026, die Abo-OAuth-Tokens in Drittanbieter-Harnessen (wie OpenClaw) außerhalb
von Claude Code/Claude.ai untersagt.

**Ehrliche Grenze:** Anthropic hat zeitweise geplant, headless/automatisierten `claude -p`-Betrieb
(Agent-SDK-Klasse) separat zu bepreisen (angekündigt 14.05.2026, **pausiert** 15./16.06.2026 —
aktuell **nicht** in Kraft, alles läuft weiter aus dem Abo-Kontingent). Sollte Anthropic eine
überarbeitete Fassung reaktivieren, könnte **schwere Dauer-Automation** aus einem kleineren
Kontingent laufen. Deshalb:
- Wir versprechen **nicht** „unbegrenzt/keine Kosten", sondern: *läuft über dein Abo (offizielle
  CLI), optionaler Claude-API-Key als Reserve, Fremd-/lokale Modelle (Ollama) als abo-unabhängige
  Alternative.*
- Ist ein `ANTHROPIC_API_KEY` gesetzt, nutzt die CLI **diesen** statt des Abos (kostet API-Preise).
  Operator setzt den Key **nur im Auto-Fallback** (Abo am Limit), nie im Normalbetrieb.
- Eine **Fair-Use-Drossel** für Automationen ist vorgesehen, um „outsized strain" zu vermeiden.

Stand der Prüfung: 2026-07-22. Primärquelle: support.claude.com/en/articles/11145838.
