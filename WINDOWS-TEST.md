# Windows-Abnahme-Checkliste (End-to-End)

Der Code ist plattformübergreifend gebaut und auf macOS (live) + Linux (echt auf dem Pi)
bewiesen. Windows ist über cross-platform-Primitive (`webbrowser`, `tempfile`, `sys.executable`,
DPAPI) und plattform-gemockte Tests abgesichert; der **echte End-to-End-Lauf** braucht einen
Windows-Rechner. Diese Liste führt Schritt für Schritt durch die Abnahme.

## Voraussetzungen auf dem Windows-Rechner
- [ ] Python 3.11+ installiert (python.org, Haken **„Add python.exe to PATH"**) — `py --version` zeigt die Version
- [ ] Claude-Abo vorhanden; entweder Node/npm für `npm i -g @anthropic-ai/claude-code` oder den nativen Claude-Installer
- [ ] Ein separater Matrix-Bot-Account (Localpart + Passwort) + deine eigene Matrix-ID

## Installation
- [ ] PowerShell öffnen (kein Admin nötig) und ausführen:
      `irm http://192.168.178.53:3000/root/the-operator/raw/branch/main/install.ps1 | iex`
- [ ] Falls Claude noch nicht angemeldet: in einem Terminal `claude /login` (Browser-Login), dann Installer erneut
- [ ] Wizard beantworten (Homeserver, Bot-User+Passwort, eigene Matrix-ID, Shell-Freigabe ja/nein, Dashboard ja)

## Erwartetes Ergebnis — abhaken
- [ ] **Task Scheduler**: drei Aufgaben `OperatorListener`, `OperatorDashboard`, `OperatorPseudonym` vorhanden und „Wird ausgeführt"
- [ ] **Chat**: Testnachricht „Operator einsatzbereit auf Windows!" kommt in Element an; eine eigene Nachricht wird beantwortet
- [ ] **Secret-Store (DPAPI)**: `dir %USERPROFILE%\.claude\matrix-bot\secrets` zeigt `*.dpapi`-Dateien; `credentials.json` enthält `"access_token": "keychain"` (kein Klartext)
- [ ] **Dashboard**: `<venv>\Scripts\python.exe %USERPROFILE%\.claude\matrix-bot\dashboard\open.py` öffnet den Browser; Status-Kacheln grün
- [ ] **Tresor**: im Dashboard anlegen + entsperren (Master-Passwort); ein Eintrag speichern; im Chat `{{tresor:name}}` in einem `curl`-Kommando testen → Wert wird eingesetzt, in der Antwort geschwärzt
- [ ] **FIDO**: der Sicherheitsschlüssel-Bereich ist auf Windows **ausgeblendet** (bewusst) — Master-Passwort/Recovery/Vaultwarden funktionieren
- [ ] **Pseudonymisierung**: eine Nachricht mit einem erfundenen Namen + Mail schreiben → Antwort enthält den echten Namen zurück (Rückübersetzung), der Verlauf zeigt einen Fake-Namen
- [ ] **Autostart**: einmal ab-/anmelden → die drei Aufgaben laufen wieder
- [ ] **Deinstallation** (Gegenprobe): `.\install.ps1 -Uninstall` entfernt die Aufgaben und widerruft die Tokens

## Bekannte Windows-Grenzen (kein Fehler)
- Sicherheitsschlüssel-Entsperrung ist auf Windows vorerst deaktiviert (WebAuthn-API-Umstellung offen).
- Dateirechte: Klartext-Sitzungsdateien werden per `icacls`-ACL geschützt (best effort); der starke
  Schutz liegt bei DPAPI (Tokens) bzw. dem Tresor-Master-Passwort (Krypto).
- Tresor-`run` nutzt `cmd /c` mit `%VAR%`-Injektion; komplexe Shell-Pipelines sind unter cmd
  anders als unter bash — für Standardkommandos (curl/git/ssh) getestet.

## Bei Problemen
Logs: `%USERPROFILE%\.claude\matrix-bot\listener.log` bzw. `dashboard.log`. Jedes reproduzierbare
Problem als Gitea-Issue mit dem Log-Auszug melden.
