/* ---------- Operator-Dock (#90): derselbe Chat wie auf dem Handy ----------
   Läuft in ZWEI Welten mit demselben Code:
   - eingebettet im Dashboard (index.html): ausklappbares Panel + 💬-Knopf
   - als eigenes Fenster / "Satellit" (dock.html): füllt das Fenster, immer offen
   SICHERHEIT (#93/#94): Nachrichten sind Fremddaten aus Matrix. Ausgabe
   AUSSCHLIESSLICH als Text (textContent/createTextNode) — nie als Markup,
   keine Link-Erkennung. Ein XSS hier hätte Vollzugriff aufs Dashboard. */
(function dock() {
  const el = document.getElementById("dock");
  if (!el) return;
  const STANDALONE = document.body.classList.contains("dock-standalone");
  const toggle = document.getElementById("dock-toggle");
  const verlauf = document.getElementById("dock-verlauf");
  const status = document.getElementById("dock-status");
  const text = document.getElementById("dock-text");
  const senden = document.getElementById("dock-senden");
  const SEITEN = ["rechts", "links", "unten"];
  const gesehen = new Set();          // event_ids — Verlauf und Stream überlappen sich
  let syncMarke = null, streamLaeuft = false, offen = false;

  // Zugangs-Token: im Dashboard kommt es aus app.js; das Satellit-Fenster bringt es
  // selbst mit (#t=… aus dem Startlink, danach localStorage — wie app.js).
  const DTOKEN = (typeof TOKEN !== "undefined" && TOKEN) || (function () {
    let t = (location.hash.match(/(?:^|[#&])t=([0-9a-f]+)/) || [])[1];
    if (t) { localStorage.setItem("op_token", t); history.replaceState(null, "", location.pathname); }
    return t || localStorage.getItem("op_token") || "";
  })();
  const dapi = (typeof api !== "undefined") ? api : async function (method, path, body) {
    const r = await fetch(path, {
      method,
      headers: { "Authorization": "Bearer " + DTOKEN, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data?.error?.message_de || ("HTTP " + r.status));
    return data;
  };

  let seite = localStorage.getItem("dock.seite") || "rechts";
  if (!SEITEN.includes(seite)) seite = "rechts";

  function setzeSeite(s) {
    seite = s;
    const zu = el.classList.contains("zu");
    el.className = zu ? "zu" : "";
    el.classList.add("seite-" + s);
    localStorage.setItem("dock.seite", s);
  }

  function setStatus(txt, klasse) {
    status.textContent = txt;
    status.className = "dock-status" + (klasse ? " " + klasse : "");
  }

  function zeige(e) {
    if (e.event_id) {
      if (gesehen.has(e.event_id)) return;
      gesehen.add(e.event_id);
    }
    const div = document.createElement("div");
    div.className = "dock-msg " + (e.wer === "du" ? "du" : e.wer === "operator" ? "operator" : "fremd");
    const meta = document.createElement("span");
    meta.className = "dock-meta";
    const zeit = e.ts ? new Date(e.ts).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "";
    meta.textContent = (e.wer === "du" ? (e.quelle === "dashboard" ? "du · 🖥️" : "du · 📱")
      : e.wer === "operator" ? "operator" : "⚠️ fremd: " + (e.quelle || "?")) + (zeit ? " · " + zeit : "");
    div.appendChild(meta);
    div.appendChild(document.createTextNode(e.text || ""));
    verlauf.appendChild(div);
    verlauf.scrollTop = verlauf.scrollHeight;
  }

  async function laden() {
    setStatus("verbinde …");
    try {
      const d = await dapi("GET", "/api/dock/verlauf?limit=50");
      verlauf.textContent = "";
      gesehen.clear();
      (d.eintraege || []).forEach(zeige);
      syncMarke = d.sync;
      setStatus("verbunden", "ok");
      stream();
    } catch (e) {
      setStatus("keine Verbindung — 👉 Tab System", "warnung");
      setTimeout(() => { if (offen) laden(); }, 8000);
    }
  }

  async function stream() {
    if (streamLaeuft || !syncMarke) return;
    streamLaeuft = true;
    try {
      // fetch statt EventSource: nur so bleibt der Zugangs-Token im Header statt in der URL.
      const r = await fetch("/api/dock/stream?sync=" + encodeURIComponent(syncMarke),
        { headers: { "Authorization": "Bearer " + DTOKEN } });
      const leser = r.body.getReader();
      const dec = new TextDecoder();
      let puffer = "";
      for (;;) {
        const { done, value } = await leser.read();
        if (done) break;
        puffer += dec.decode(value, { stream: true });
        let i;
        while ((i = puffer.indexOf("\n\n")) >= 0) {
          const zeile = puffer.slice(0, i);
          puffer = puffer.slice(i + 2);
          if (!zeile.startsWith("data: ")) continue;
          const e = JSON.parse(zeile.slice(6));
          if (e.wer === "system") throw new Error("getrennt");
          zeige(e);
        }
      }
      throw new Error("Stream endete");
    } catch (e) {
      streamLaeuft = false;
      if (offen) {
        setStatus("Verbindung verloren — verbinde neu …", "warnung");
        setTimeout(laden, 4000);
      }
    }
  }

  async function abschicken() {
    const t = text.value.trim();
    if (!t) return;
    senden.disabled = true;
    try {
      await dapi("POST", "/api/dock/senden", { text: t });
      text.value = "";
      text.style.height = "auto";
      setStatus("gesendet — Operator denkt nach …", "ok");
    } catch (e) {
      setStatus(e.message || "Senden fehlgeschlagen", "warnung");
    } finally {
      senden.disabled = false;
      text.focus();
    }
  }

  function auf() {
    offen = true;
    el.classList.remove("zu");
    setzeSeite(seite);
    if (toggle) toggle.classList.add("versteckt");
    localStorage.setItem("dock.offen", "1");
    laden();
    text.focus();
  }
  function zu() {
    if (STANDALONE) { window.close(); return; }   // Satellit: Fenster schließen
    offen = false;
    el.classList.add("zu");
    if (toggle) toggle.classList.remove("versteckt");
    localStorage.setItem("dock.offen", "0");
  }

  if (toggle) toggle.addEventListener("click", auf);
  document.getElementById("dock-zu").addEventListener("click", zu);
  const btnSeite = document.getElementById("dock-seite");
  if (btnSeite) {
    if (STANDALONE) btnSeite.classList.add("hidden");   // im eigenen Fenster sinnlos
    else btnSeite.addEventListener("click", () => {
      setzeSeite(SEITEN[(SEITEN.indexOf(seite) + 1) % SEITEN.length]);
    });
  }
  // 🛰 Satellit starten (nur im Dashboard sichtbar): eigenes Fenster auf OS-Ebene.
  const btnSat = document.getElementById("dock-satellit");
  if (btnSat) {
    if (STANDALONE) btnSat.classList.add("hidden");
    else btnSat.addEventListener("click", async () => {
      try {
        await dapi("POST", "/api/dock/fenster", {});
        setStatus("Satellit gestartet — eigenes Fenster öffnet sich", "ok");
      } catch (e) { setStatus(e.message || "Fenster-Start fehlgeschlagen", "warnung"); }
    });
  }
  // Beim Anmelden automatisch öffnen (Autostart) — nur im Satellit-Fenster angeboten.
  const autoBox = document.getElementById("dock-autostart");
  if (autoBox) {
    if (!STANDALONE) autoBox.classList.add("hidden");
    else {
      const cb = document.getElementById("dock-autostart-an");
      dapi("GET", "/api/dock/autostart").then((d) => { cb.checked = !!d.an; }).catch(() => {});
      cb.addEventListener("change", async () => {
        try {
          await dapi("POST", "/api/dock/autostart", { an: cb.checked });
          setStatus(cb.checked ? "öffnet sich künftig beim Anmelden" : "Autostart aus", "ok");
        } catch (e) { cb.checked = !cb.checked; setStatus(e.message, "warnung"); }
      });
    }
  }
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && offen && !STANDALONE) zu(); });
  senden.addEventListener("click", abschicken);
  text.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); abschicken(); }
  });
  text.addEventListener("input", () => {
    text.style.height = "auto";
    text.style.height = Math.min(text.scrollHeight, 120) + "px";
  });

  if (STANDALONE) {
    el.classList.remove("zu");
    auf();
  } else {
    setzeSeite(seite);
    if (localStorage.getItem("dock.offen") === "1") auf();
  }
})();
