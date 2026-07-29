#!/usr/bin/env python3
"""Operator PII-Pseudonymisierung — ersetzt personenbezogene Daten durch konsistente,
realistische Surrogate, BEVOR Text an Claude geht; die Antwort wird zurückübersetzt.

Design (siehe PSEUDONYMISIERUNG-STUDIE.md):
- Microsoft Presidio (Analyzer, deutsches spaCy-NER + Regex) findet PII-Spans.
- Faker(de_DE) erzeugt plausible Ersatzwerte gleichen Typs (echter Name → anderer Name).
- Ein bidirektionales, konsistentes Mapping (real ⇄ surrogat) erlaubt exakte Rückübersetzung.
- Reihenfolge im System: erst Secret-Redaction (redact.py, irreversibel), DANN dies (reversibel).

Läuft im dashboard-venv (Presidio/spaCy sind nicht stdlib). Wird als Daemon gehalten
(Modell einmal laden) oder direkt importiert.
"""
import re
import sys

MODEL = "de_core_news_lg"
# Entitäten, die wir behandeln, + Faker-Erzeuger + Surrogat-Konsistenzschlüssel.
# read/write-Risiko: strukturierte PII (EMAIL/PHONE/IBAN/CREDIT_CARD/IP) = geringes
# Kontextrisiko; PERSON/LOCATION/ORG = höheres Risiko (nur in Modus >= "standard").
STRUCTURED = {"EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN_CODE", "CREDIT_CARD", "IP_ADDRESS"}
NAMED = {"PERSON", "LOCATION", "ORGANIZATION", "NRP"}
EXTRA_STRICT = {"DATE_TIME"}

# Presidio-Score-Untergrenze: darunter ignorieren (Telefon 0.4 wollen wir noch,
# aber Rauschen < 0.35 raus). fail-safe: im Zweifel eher ersetzen.
MIN_SCORE = 0.35
# #102: Orte/Firmen brauchen mehr Sicherheit als strukturierte Muster (IBAN, Mail) —
# NER rät bei unbekannten Großwörtern nach »im/in« sonst zu oft daneben.
MIN_SCORE_NAMED = 0.55

# Häufige deutsche Wörter, die NER (spaCy de) gern fälschlich als PERSON labelt —
# v. a. großgeschriebene Imperative/Funktionswörter am Satzanfang. Over-Detection hier
# würde den Prompt verwirren (reversibel, aber unschön), daher blockieren.
STOP_PERSON = {
    # Grußformeln / Füllwörter
    "hallo", "hi", "hey", "danke", "bitte", "guten", "moin", "servus", "liebe",
    "lieber", "sehr", "geehrte", "geehrter", "mit", "freundliche", "grüße", "gruß",
    "ok", "okay", "ja", "nein", "und", "oder", "der", "die", "das", "los", "übrigens",
    "hey", "na", "so", "also", "gerne", "gern", "kurz", "bzw", "usw", "etc",
    # Imperative (Aufträge an den Bot) — häufigste zuerst
    "fasse", "fass", "nenne", "nenn", "zeige", "zeig", "erstelle", "erstell",
    "schreibe", "schreib", "sende", "send", "schicke", "schick", "prüfe", "prüf",
    "checke", "check", "suche", "such", "finde", "find", "gib", "gibt", "mach",
    "mache", "hol", "hole", "lies", "öffne", "öffn", "starte", "start", "stoppe",
    "stopp", "führe", "führ", "rufe", "ruf", "antworte", "antwort", "erkläre",
    "erklär", "beschreibe", "beschreib", "liste", "list", "plane", "plan", "buche",
    "buch", "storniere", "lösche", "lösch", "ändere", "änder", "aktualisiere",
    "kopiere", "kopier", "verschiebe", "überweise", "überweis", "zahle", "zahl",
    "kaufe", "kauf", "bestelle", "bestell", "frage", "frag", "sag", "sage", "teile",
    "teil", "informiere", "erinnere", "erinner", "denk", "denke", "warte", "wart",
    "komm", "komme", "geh", "gehe", "bring", "bringe", "nimm", "setze", "setz",
    "lege", "leg", "packe", "pack", "formuliere", "übersetze", "übersetz",
    "korrigiere", "verbessere", "verbesser", "kürze", "kürz", "fülle", "füll",
    "trage", "trag", "wähle", "wähl", "klicke", "klick", "drücke", "drück", "tippe",
    "tipp", "kannst", "könntest", "würdest", "soll", "sollst", "hilf", "hilfst",
    "gehe", "lass", "schau", "wie", "was", "wann", "wer", "wo", "warum", "welche",
    "sprich", "sprechen", "red", "rede", "melde", "meld", "kontaktiere", "ruf",
    "vereinbare", "verschiebe", "sortiere", "ordne", "fasse", "notiere", "merke",
    # Pronomen (spaCy de labelt sie am Satzanfang gern als PERSON)
    "ich", "du", "er", "sie", "es", "wir", "ihr", "mich", "dich", "ihn", "uns",
    "euch", "mir", "dir", "ihm", "ihnen", "mein", "dein", "sein", "unser", "euer",
    "man", "wen", "wem", "dem", "den", "dessen", "diese", "dieser", "dieses",
}

# Anrede/Titel-Tokens, die bei der Teilnamen-Zuordnung ignoriert werden.
TITLES = {"herr", "herrn", "frau", "dr", "dr.", "prof", "prof.", "dipl", "dipl.",
          "ing", "ing.", "mba", "mba.", "bsc", "b.sc", "b.sc.", "ba", "b.a", "b.a.",
          "msc", "m.sc", "m.sc.", "beng", "b.eng", "b.eng.", "med", "med."}

# Anrede + Name — zuverlässiger deutscher PII-Indikator, fängt Namen, die NER übersieht
# („Frau Wagner"). Die Anrede selbst wird beim Trimming wieder freigelegt.
ANREDE_RE = re.compile(
    r"\b(?:Herrn?|Frau|Fräulein)\s+(?:(?:Dr|Prof|Dipl)\.?\s+)*"
    r"[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?")

_analyzer = None


def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "de", "model_name": MODEL}]})
        _analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(),
                                   supported_languages=["de"])
    return _analyzer


def _faker():
    from faker import Faker
    f = Faker("de_DE")
    Faker.seed()  # nicht deterministisch über Läufe (jeder Lauf eigene Surrogate)
    return f


def _guess_gender(text: str, start: int) -> str:
    """Geschlecht aus der Anrede unmittelbar vor dem Namen ableiten (für passende Fake-Vornamen)."""
    prefix = text[max(0, start - 14):start].lower()
    if "fräulein" in prefix or "frau" in prefix:
        return "female"
    if "herrn" in prefix or "herr" in prefix:
        return "male"
    return ""


def _make_surrogate(fake, entity_type: str, gender: str = "") -> str:
    if entity_type == "EMAIL_ADDRESS":
        return fake.ascii_email()
    if entity_type == "PHONE_NUMBER":
        return fake.phone_number()
    if entity_type == "IBAN_CODE":
        return fake.iban()
    if entity_type == "CREDIT_CARD":
        return fake.credit_card_number()
    if entity_type == "IP_ADDRESS":
        return fake.ipv4()
    if entity_type == "PERSON":
        first = (fake.first_name_female() if gender == "female"
                 else fake.first_name_male() if gender == "male"
                 else fake.first_name())
        return first + " " + fake.last_name()   # sauber, 2 Tokens
    if entity_type in ("LOCATION",):
        return fake.city()
    if entity_type in ("ORGANIZATION", "NRP"):
        return fake.company()
    if entity_type == "DATE_TIME":
        return fake.date(pattern="%d.%m.%Y")
    return fake.word()


def _active_entities(mode: str) -> set:
    if mode == "structured":
        return set(STRUCTURED)
    if mode == "strict":
        return STRUCTURED | NAMED | EXTRA_STRICT
    return STRUCTURED | NAMED  # "standard" (Default): sicher & genau


def _resolve_overlaps(results):
    """Überlappende Spans auflösen: höhere Konfidenz gewinnt, bei Gleichstand der längere.
    (Presidio labelt z. B. eine E-Mail zusätzlich als PERSON.)"""
    chosen = []
    for r in sorted(results, key=lambda x: (-x.score, -(x.end - x.start), x.start)):
        if all(r.end <= c.start or r.start >= c.end for c in chosen):
            chosen.append(r)
    return sorted(chosen, key=lambda x: x.start)


def pseudonymize(text: str, mapping: dict, mode: str = "standard",
                 allow=(), deny=()):
    """PII → konsistente Surrogate. `mapping` ist der bidirektionale Zustand
    (wird über eine ganze Konversation weitergereicht). Gibt (text', mapping, stats) zurück."""
    if not text:
        return text, mapping, {}
    mapping.setdefault("r2s", {})   # (typ, realwert_lower) -> surrogat
    mapping.setdefault("s2r", {})   # surrogat -> realwert
    allow_l = {a.lower() for a in allow}
    deny_l = {d.lower() for d in deny}
    fake = _faker()
    active = _active_entities(mode)

    analyzer = _get_analyzer()
    results = analyzer.analyze(text=text, language="de")
    spans = [r for r in _resolve_overlaps(results)
             if r.entity_type in active
             and r.score >= (MIN_SCORE_NAMED if r.entity_type in ("LOCATION", "ORGANIZATION")
                             else MIN_SCORE)]
    # #102: Orte/Firmen nur ersetzen, wenn das Sprachmodell die Wörter überhaupt kennt.
    # NER hielt das Tippfehler-Wort »Satelitenmodus« für einen Ort und machte
    # »Dinkelsbühl« daraus — der Chat wurde wirr. Echte Orte (München, Dinkelsbühl)
    # haben Wortvektoren in de_core_news_lg; erfundene/vertippte Wörter nicht.
    # Fail-safe: schlägt die Prüfung selbst fehl, wird weiter ersetzt (Datenschutz gewinnt).
    try:
        _doc = analyzer.nlp_engine.nlp["de"](text)
        _nlp = analyzer.nlp_engine.nlp["de"]

        def _bekannt(sp):
            toks = [t for t in _doc if t.idx < sp.end and t.idx + len(t.text) > sp.start]
            return (not toks) or any(t.has_vector for t in toks)

        def _alltagswort(sp):
            """#107: GROSSGESCHRIEBENE Alltagswörter sind keine Firmennamen.
            »Schreib FERTIG in die Datei« wurde zu »Schreib Bien AG & Co. OHG …« —
            wörtliche Inhalte wurden still verfälscht, und der Nutzer hielt das
            Ergebnis für einen Fehler des Assistenten.
            Unterscheidungsmerkmal (gemessen, nicht geraten): Bei Alltagswörtern ist
            die Kleinschreibung ein Adverb/Verb/Adjektiv (fertig, erledigt, gut),
            bei echten Marken ein Eigenname (bmw, rewe, adac). Bewusst NUR für
            durchgehend großgeschriebene Einzelwörter — »Siemens« und »München«
            bleiben damit unangetastet."""
            wort = text[sp.start:sp.end].strip()
            if not wort.isupper() or " " in wort or len(wort) < 2:
                return False
            try:
                t = _nlp(wort.lower())[0]
            except Exception:
                return False
            return (not t.is_oov) and t.pos_ in ("ADV", "VERB", "ADJ", "AUX", "INTJ")

        spans = [sp for sp in spans
                 if sp.entity_type not in ("LOCATION", "ORGANIZATION")
                 or (_bekannt(sp) and not _alltagswort(sp))]
    except Exception:
        pass
    # POS-Check: Verben/Hilfsverben nie als PERSON (zusätzlich zur Stopwort-Liste)
    try:
        doc = analyzer.nlp_engine.nlp["de"](text)
        verb_at = {t.idx for t in doc if t.pos_ in ("VERB", "AUX")}
    except Exception:
        verb_at = set()

    stats = {}
    # Deny-List erzwingt zusätzliche Ersetzungen (Namen, die NER übersieht)
    forced = []
    for term in deny:
        for m in re.finditer(re.escape(term), text):
            forced.append((m.start(), m.end(), "PERSON"))
    if "PERSON" in active:
        for m in ANREDE_RE.finditer(text):    # „Frau Wagner" auch wenn NER es übersieht
            forced.append((m.start(), m.end(), "PERSON"))

    def _covered(s, e):
        return any(not (e <= x.start or s >= x.end) for x in spans)

    class _F:
        def __init__(self, s, e, t):
            self.start, self.end, self.entity_type = s, e, t
    for s, e, t in forced:
        if not _covered(s, e):
            spans.append(_F(s, e, t))
    spans = sorted(spans, key=lambda x: x.start)

    out, last = [], 0
    for sp in spans:
        # Führende Imperative/Verben aus PERSON-Spans trimmen (Presidio bündelt
        # „Sprich Thomas Müller" oft zu EINEM Span) — nur der echte Name wird ersetzt.
        if getattr(sp, "entity_type", "") == "PERSON":
            while sp.start < sp.end:
                seg = text[sp.start:sp.end]
                first = seg.split(None, 1)[0] if seg.split() else ""
                if first and (first.lower() in STOP_PERSON
                              or first.lower().strip(".,") in TITLES
                              or sp.start in verb_at):
                    adv = len(first)
                    ws = len(seg[adv:]) - len(seg[adv:].lstrip())
                    sp.start += adv + ws
                else:
                    break
            if sp.start >= sp.end:
                continue
        real = text[sp.start:sp.end]
        low = real.lower().strip()
        # Allowlist (Eigen-Identität) und Stopwörter für PERSON: nicht ersetzen
        if low in allow_l:
            continue
        if sp.entity_type == "PERSON" and low not in deny_l and (
                low in STOP_PERSON or " ".join(low.split()) in STOP_PERSON
                or (len(real.split()) == 1 and sp.start in verb_at)):
            continue
        key = sp.entity_type + "\x1f" + low   # String-Key (JSON-serialisierbar)
        surrogat = mapping["r2s"].get(key)
        if not surrogat:
            gender = _guess_gender(text, sp.start) if sp.entity_type == "PERSON" else ""
            # eindeutigen Surrogat erzeugen (keine Kollision mit vergebenen/echten)
            for _ in range(20):
                cand = _make_surrogate(fake, sp.entity_type, gender)
                if cand != real and cand not in mapping["s2r"]:
                    surrogat = cand
                    break
            surrogat = surrogat or (real[::-1] + "_x")
            mapping["r2s"][key] = surrogat
            mapping["s2r"][surrogat] = real
            # Teilnamen-Mapping (Claude nutzt oft nur den Vor-/Nachnamen): Titel strippen,
            # dann positionsweise Vorname↔Vorname, Nachname↔Nachname (nur klare 2-Token-Fälle)
            if sp.entity_type == "PERSON":
                rt = [w for w in real.split() if w.lower().strip(".,") not in TITLES and len(w) >= 3]
                st = surrogat.split()
                if len(rt) >= 1 and len(st) >= 1:
                    if len(st[-1]) >= 3 and st[-1] not in mapping["s2r"]:
                        mapping["s2r"][st[-1]] = rt[-1]           # Nachname
                    if len(rt) >= 2 and len(st) >= 2 and len(st[0]) >= 3 and st[0] not in mapping["s2r"]:
                        mapping["s2r"][st[0]] = rt[0]             # Vorname
        out.append(text[last:sp.start])
        out.append(surrogat)
        last = sp.end
        stats[sp.entity_type] = stats.get(sp.entity_type, 0) + 1
    out.append(text[last:])
    return "".join(out), mapping, stats


def reidentify(text: str, mapping: dict) -> str:
    """Surrogate → echte Werte (längste zuerst, exakt). Für Antworttext UND Tool-Argumente."""
    if not text or not mapping.get("s2r"):
        return text
    for surrogat in sorted(mapping["s2r"], key=len, reverse=True):
        if surrogat in text:
            text = text.replace(surrogat, mapping["s2r"][surrogat])
    return text


# ---------------------------------------------------------------- CLI / Selbsttest --
def _run_stdin():
    """JSON über stdin → pseudonymisieren → JSON nach stdout. Vom Listener aufgerufen.
    In: {texts:[...], mapping, mode, allow, deny}  Out: {texts:[...], mapping, stats}
    Mehrere Segmente teilen EIN Mapping (Konsistenz über Nachricht/Verlauf/Gedächtnis)."""
    import json
    req = json.load(sys.stdin)
    mapping = req.get("mapping", {})
    mode = req.get("mode", "standard")
    allow, deny = req.get("allow", []), req.get("deny", [])
    out_texts, total = [], {}
    for txt in req.get("texts", []):
        p, mapping, st = pseudonymize(txt, mapping, mode, allow, deny)
        out_texts.append(p)
        for k, v in st.items():
            total[k] = total.get(k, 0) + v
    json.dump({"texts": out_texts, "mapping": mapping, "stats": total}, sys.stdout)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        _run_stdin()
    elif len(sys.argv) > 1 and sys.argv[1] == "selftest":
        m = {}
        src = ("Hallo, ich bin Michael von Aschenbrenner. Bitte überweise 250 Euro an "
               "Thomas Müller, IBAN DE89370400440532013000, und maile lisa.becker@firma.de.")
        ps, m, st = pseudonymize(src, m, allow=["Michael von Aschenbrenner"])
        print("ORIGINAL :", src)
        print("PSEUDONYM:", ps)
        print("STATS    :", st)
        back = reidentify(ps, m)
        # Antwort-Simulation: Claude nennt Thomas Müller erneut
        ans = ps.split("an ")[1].split(",")[0]
        print("REIDENT  :", reidentify(f"Ich habe {ans} kontaktiert.", m))
        assert "Thomas Müller" not in ps and "lisa.becker@firma.de" not in ps
        assert "Michael von Aschenbrenner" in ps  # Allowlist
        print("Roundtrip identisch:", back == src)
    else:
        sys.exit(__doc__)
