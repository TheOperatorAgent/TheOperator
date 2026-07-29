#!/usr/bin/env python3
"""Operator — Netz-Wächter gegen Zugriffe ins eigene Netz (#82, stdlib-only).

Das Problem: Der Operator läuft IN deinem Netz. Ein Web-Agent könnte deshalb
Adressen erreichen, die von außen niemand erreicht — dein Dashboard auf
127.0.0.1, dein Gitea auf 192.168.x, deine FritzBox, Cloud-Metadaten-Dienste.
Eine präparierte Webseite könnte den Agenten genau dorthin schicken
(»Server Side Request Forgery«, SSRF).

Diese Wache prüft VOR dem Verbinden, wohin eine Adresse wirklich zeigt:
  * Schema muss http/https sein (kein file://, gopher://, data: …)
  * Der Hostname wird aufgelöst und JEDE dabei gefundene IP geprüft
  * Gesperrt sind: eigener Rechner (127.x, ::1), private Netze (10.x, 172.16–31.x,
    192.168.x, fc00::/7), Link-Local (169.254.x — enthält den Cloud-Metadaten-
    Dienst 169.254.169.254), sowie reservierte und Multicast-Bereiche

Wichtig gegen Umleitungen: Der Aufrufer muss JEDEN Sprung prüfen, nicht nur den
ersten — eine harmlose Seite kann per Weiterleitung nach innen zeigen.
"""
import ipaddress
import socket
import urllib.parse

ERLAUBTE_SCHEMATA = ("http", "https")

# Namen, hinter denen praktisch immer ein Gerät im eigenen Netz steckt — auch wenn sie
# gerade öffentlich auflösen. (».box« ist inzwischen eine echte Top-Level-Domain: auf
# manchen Rechnern zeigt »fritz.box« ins Internet statt auf den Router. Wer diesen Namen
# ansteuert, meint aber den Router — deshalb hier hart gesperrt, unabhängig vom DNS.)
GERAETE_NAMEN = {
    "fritz.box", "www.fritz.box", "fritzbox", "fritz", "speedport.ip", "easy.box",
    "router", "router.box", "gateway", "modem", "nas", "openwrt", "pi.hole",
}


def _ip_gesperrt(ip_text):
    """→ Grund (str) wenn gesperrt, sonst None."""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return "keine gültige IP-Adresse"
    if ip.is_loopback:
        return "zeigt auf diesen Rechner selbst"
    if ip.is_link_local:                     # enthält 169.254.169.254 (Cloud-Metadaten)
        return "zeigt auf eine Link-Local-Adresse (z. B. Cloud-Metadaten)"
    if ip.is_private:
        return "zeigt in dein privates Netz"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "zeigt auf einen reservierten Adressbereich"
    # IPv6-Adressen, die eine IPv4 einbetten, über die eingebettete Adresse prüfen
    for attr in ("ipv4_mapped", "sixtofour"):
        eingebettet = getattr(ip, attr, None)
        if eingebettet is not None:
            grund = _ip_gesperrt(str(eingebettet))
            if grund:
                return grund
    return None


def aufloesen(host):
    """Alle IPs eines Hostnamens. Leere Liste, wenn nicht auflösbar."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    return sorted({i[4][0] for i in infos})


def check_url(url):
    """→ (erlaubt: bool, grund: str). Fail-closed: im Zweifel gesperrt."""
    try:
        u = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return False, "Adresse nicht lesbar"
    if u.scheme.lower() not in ERLAUBTE_SCHEMATA:
        return False, f"nur http/https erlaubt (nicht »{u.scheme or '—'}«)"
    host = u.hostname
    if not host:
        return False, "kein Hostname in der Adresse"
    # Direkt notierte IP? Dann sofort prüfen (kein DNS nötig).
    try:
        ipaddress.ip_address(host)
        grund = _ip_gesperrt(host)
        return (False, grund) if grund else (True, "")
    except ValueError:
        pass
    h = host.lower().rstrip(".")
    if h in ("localhost", "localhost.localdomain") or h.endswith(
            (".localhost", ".local", ".internal", ".home", ".lan", ".fritz.box")):
        return False, "zeigt auf einen Namen im eigenen Netz"
    if h in GERAETE_NAMEN:
        return False, "ist der übliche Name eines Geräts in deinem Netz (z. B. Router)"
    ips = aufloesen(host)
    if not ips:
        return False, "Adresse ließ sich nicht auflösen"
    for ip in ips:                       # ALLE Ergebnisse müssen sauber sein
        grund = _ip_gesperrt(ip)
        if grund:
            return False, grund
    return True, ""


def hinweis(url, grund):
    """Freundliche Klartext-Meldung für Chat und Agenten-Rückgabe."""
    return (f"🚫 Diese Adresse habe ich aus Sicherheitsgründen nicht geöffnet: {url[:120]}\n"
            f"Grund: Sie {grund}. Ich darf nur ins öffentliche Internet — nicht in dein "
            f"eigenes Netz. Das schützt dein Dashboard, deinen Router und deine Server "
            f"davor, über eine präparierte Webseite ausgelesen zu werden.")


if __name__ == "__main__":
    import sys
    for a in sys.argv[1:]:
        ok, grund = check_url(a)
        print(f"{'✅ erlaubt ' if ok else '🚫 gesperrt'}  {a}" + (f"  ({grund})" if grund else ""))
