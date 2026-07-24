# EINFACHHEIT.md — Leitbild Barrierefreiheit & einfache Sprache

**Verbindlich für das ganze Projekt.** Der Operator wird nicht von Technikern bedient,
sondern im Normalfall von Büromitarbeitenden ohne IT-Hintergrund. Jede Oberfläche, jede
Meldung und jeder Ablauf wird für diese Menschen gebaut — nicht für uns Entwickler.

---

## 1. Leit-Persona: »Petra«

Petra, 52, Sachbearbeitung. Sie nutzt täglich Outlook, Word und WhatsApp. Sie weiß nicht,
was ein Terminal, ein Server, ein Token oder »localhost« ist — und **muss es auch nie wissen**.
Wenn etwas nicht klappt, gibt sie nach zwei Fehlversuchen auf oder ruft jemanden an.

**Jedes Feature gilt erst als fertig, wenn Petra es ohne Hilfe schafft.** (»Petra-Test«,
siehe Abschnitt 6.)

## 2. Die zehn Regeln (gelten überall)

1. **Einfache Sprache.** Kurze Sätze. Keine Fachwörter. Nicht »Homeserver-Admin«, sondern
   »dein Anmelde-Name«. Nicht »OTT-Token«, sondern »Einmal-Link«.
2. **Nie ein Terminal voraussetzen.** Für jeden Pflicht-Schritt gibt es einen Klick- oder
   Chat-Weg. Terminal-Wege sind höchstens ein aufklappbarer Experten-Fallback.
3. **Jede Eingabe verzeiht Schreibweisen.** `michael`, `@michael:server`,
   `michael@firma.de` — wir normalisieren, statt Petra zu korrigieren.
4. **Fehler = drei Teile:** Was ist passiert (ein Satz) → Woran liegt es wahrscheinlich →
   **👉 Was tue ich jetzt.** Nie rohe Technik (`M_FORBIDDEN`, `Errno 61`, HTTP-Codes) zeigen.
5. **Ein Schritt pro Bildschirm.** Kein Formular fragt Dinge, die Petra nicht kennt.
   Vorbelegen, was wir wissen; erklären, was wir fragen müssen — direkt am Feld.
6. **Der nächste Schritt ist immer sichtbar.** Nach jedem Erfolg steht da, was man jetzt
   tun kann (»Öffne Element — dort wartet dein neuer Kontakt«).
7. **Geheimnisse sind immer verdeckt** (Punkte statt Klartext), werden nie im Chat
   erfragt, nie gespeichert, nie in Logs geschrieben. Und das steht auch sichtbar dabei.
8. **Status als Ampel.** 🟢 läuft / 🟡 ein Schritt fehlt / 🔴 Problem — mit einem Satz und
   dem nächsten Schritt. Automatisch geprüft, nicht erst auf Klick.
9. **Der Assistent ist der Hauptweg.** Wer nicht weiß, wo etwas ist, tippt es einfach in
   den 🧭 Assistenten (»richte Kimi ein«). Der Assistent führt, klickt aber nichts
   Verändervolles ohne Bestätigung an.
10. **Barrierefreiheit klassisch:** Bedienbar nur mit Tastatur (Tab/Enter/Esc), sichtbarer
    Fokus, Beschriftungen an jedem Feld (auch für Vorlese-Software), ausreichender
    Kontrast, keine Information nur über Farbe (Ampel hat immer auch Text).

## 3. Referenz-Usecase (barrierefrei, Schritt für Schritt)

**»Petra gibt dem Büro einen Coding-Helfer«** — so muss sich der Ablauf anfühlen:

1. Petra öffnet ihre Chat-App und schreibt dem Operator: **»dashboard«**.
   → Sie bekommt einen Link: *»Hier klicken — gilt 10 Minuten.«* Sie klickt. Fertig
   angemeldet. (Kein Passwort, kein Terminal.)
2. Im Dashboard klickt sie **🧭 Assistent** und tippt: *»Ich möchte einen Helfer, der
   Programmier-Fragen beantwortet.«*
3. Der Assistent antwortet in einfacher Sprache: *»Dafür gibt es ›coder‹. Ich prüfe kurz,
   ob alles bereit ist …«* — prüft selbst (Ampel), und sagt dann z. B.:
   *»Ein Schritt fehlt: Die Modell-Anmeldung. Klick auf ›Ausführen‹, ich richte es ein.«*
4. Braucht ein Schritt ein Passwort, öffnet sich ein **kleines Formular mit verdeckter
   Eingabe** und einem Satz Erklärung: *»Dein Anmelde-Passwort — dasselbe wie in Element.«*
   Petra tippt es ein. Es wird nicht gespeichert, und das steht da.
5. Erfolg wird gefeiert und erklärt: *»Fertig! ›coder‹ ist jetzt ein Kontakt in deiner
   Chat-App. Öffne Element und schreib ihm z. B.: Erstell mir eine Excel-Formel für …«*
6. Geht etwas schief, liest Petra **keinen Code**, sondern: *»Die Anmeldung wurde
   abgelehnt — Name oder Passwort stimmen nicht. Es sind dieselben Daten wie in Element.
   👉 Bitte prüfe beides und versuch es noch einmal.«*

**Messlatte:** Petra braucht dafür niemanden anzurufen. Kein Schritt zeigt ihr ein
Terminal, einen HTTP-Code oder ein englisches Fachwort.

## 4. Stil-Regeln für Meldungen (Spickzettel)

| Schlecht (Technik) | Gut (Büro) |
|---|---|
| `M_FORBIDDEN: Invalid username or password` | Anmeldung abgelehnt: Name oder Passwort stimmen nicht. 👉 Es sind dieselben Daten wie in Element — bitte prüfen und nochmal versuchen. |
| `[Errno 61] Connection refused` | Ollama läuft gerade nicht. 👉 Starte die Ollama-App, dann klicke hier auf »Nochmal prüfen«. |
| `HTTP 502 Bad Gateway` | Der Chat-Server hat nicht geantwortet. 👉 In einer Minute nochmal versuchen; bleibt es so, sag dem Assistenten »Server prüfen«. |
| »Admin-Benutzer des Homeservers« | »Dein Anmelde-Name — derselbe wie in Element« |
| »Token abgelaufen« | »Deine Anmeldung ist abgelaufen. 👉 Schreib dem Operator im Chat ›dashboard‹ für einen neuen Link.« |

## 5. Ist-Stand vs. Ziel (offene Punkte)

- ✅ Einmal-Link statt Terminal (»dashboard«-Chatbefehl), Entsperr-Karte mit Chat-Weg zuerst
- ✅ Provider-Ampel mit nächstem Schritt; Kimi-/Cloud-Hinweis in Klartext
- ✅ Veröffentlichen: verdecktes Passwort-Formular, verzeihende Schreibweisen, Klartext-Fehler
- ✅ 🧭 Assistent: führt, prüft selbst, bestätigt vor Änderungen, Geheimnisse nur im Formular
- ✅ Fehlertexte projektweit durch die Drei-Teile-Regel ersetzt (zentraler `friendlyError`, 1.5.1)
- ✅ Anmeldung bleibt nach Server-Neustart erhalten (Sitzungen persistent, `secrets/dash_sessions.json`)
- ✅ Erst-Einrichtung als geführte Strecke im 🧭 Assistenten (Persona/Profil-Interview, überspringbar, 1.6.x)
- ✅ Persona & Profil sichtbar/editierbar/löschbar im Tab »🎭 Persona« + Datenschutz-Tab
- ⬜ Restliche `prompt()`/`confirm()`-Dialoge durch erklärende Formulare ersetzen
- ⬜ Tastatur-/Screenreader-Audit (Fokus-Reihenfolge, `label for`, `aria-live` für Ampeln)

## 6. Definition of Done: der Petra-Test

Ein Feature ist fertig, wenn alle vier Fragen mit **Ja** beantwortet sind:

1. Schafft Petra den Ablauf **ohne fremde Hilfe** und ohne Terminal?
2. Versteht sie **jede Meldung** beim ersten Lesen (keine Fachwörter, nächster Schritt klar)?
3. Kommt sie **nur mit Tastatur** durch — und sieht immer, wo sie gerade ist?
4. Wenn etwas schiefgeht: Weiß sie danach, **was sie jetzt tun soll** — ohne zu googeln?

*Dieses Dokument gilt wie SICHERHEIT.md: Neue Features werden dagegen geprüft.*
