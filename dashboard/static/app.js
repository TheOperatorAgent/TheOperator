/* Operator Dashboard — Frontend (build-frei, vanilla JS) */
// Token DAUERHAFT im Browser merken (localStorage) — einmal per open.py/»operator« rein,
// danach öffnet 127.0.0.1:8737 direkt, ohne Token-Getue. Nur localhost, browser-isoliert.
const _store = window.localStorage;
let TOKEN = (location.hash.match(/(?:^|[#&])t=([0-9a-f]+)/) || [])[1]
  || _store.getItem("op_token") || sessionStorage.getItem("op_token") || "";
const OTT = (location.hash.match(/(?:^|[#&])ott=([0-9a-f]+)/) || [])[1] || "";
if (TOKEN) { _store.setItem("op_token", TOKEN); history.replaceState(null, "", location.pathname); }
else if (OTT) {
  // Einmal-Link aus dem Chat: Ticket gegen Sitzungs-Token tauschen (Ticket wird verbraucht)
  fetch("/api/auth/ott", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ott: OTT }) })
    .then((r) => r.json()).then((d) => {
      if (d.token) {
        _store.setItem("op_token", d.token);
        history.replaceState(null, "", location.pathname);
        location.reload();
      } else {
        document.body.innerHTML = "<div style='max-width:520px;margin:15vh auto;font-family:monospace;padding:24px;border:1px solid #3a5;border-radius:12px'>" +
          "<h2>Einmal-Link nicht mehr gültig</h2><p>" + (d?.error?.message_de || "Der Link wurde schon benutzt oder ist abgelaufen.") +
          "</p><p>Neuen Link im Chat anfordern — oder am Rechner ausführen:<br><code>python3 ~/.claude/matrix-bot/dashboard/open.py</code></p></div>";
      }
    }).catch(() => {});
}

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: { "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data?.error?.message_de || ("HTTP " + r.status));
  return data;
}

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isErr ? "err" : "";
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 3500);
}

// Technik-Fehler → einfache Sprache mit nächstem Schritt (EINFACHHEIT.md). Rohe Codes wie
// »HTTP 502« oder »M_FORBIDDEN« sieht der Nutzer nie; die Details bleiben in der Konsole/im Log.
// Auf welchem Gerät sitzt der Betrachter? Die Entsperr-Karte hat lange »am Mac« gesagt —
// auch auf einem Windows-Rechner (Michi, 30.07.). Sie erscheint, WEIL der Zugang fehlt,
// kann also nichts vom Server erfragen; deshalb aus dem Browser selbst ableiten.
function geraeteName() {
  const p = (navigator.userAgent || "") + " " + (navigator.platform || "");
  if (/Windows|Win32|Win64/i.test(p)) return "Windows-Rechner";
  if (/Macintosh|Mac OS X/i.test(p)) return "Mac";
  if (/Linux|X11/i.test(p)) return "Linux-Rechner";
  return "Rechner";
}
function terminalName() {
  return geraeteName() === "Windows-Rechner"
    ? "in PowerShell" : "im Terminal";
}

function friendlyError(e) {
  const raw = (e && e.message ? String(e.message) : String(e || "")).trim();
  const low = raw.toLowerCase();
  try { if (raw) console.warn("Fehler-Detail:", raw); } catch (_) {}
  if (low.includes("failed to fetch") || low.includes("networkerror") || low.includes("load failed"))
    return "Keine Verbindung zum Operator. 👉 Läuft der Dienst noch? Lade die Seite neu (Cmd+R).";
  if (/\b401\b/.test(low) || low.includes("unauth") || low.includes("dashboard-token") || low.includes("token"))
    return "Deine Anmeldung ist abgelaufen. 👉 Schreib deinem Operator im Chat »dashboard« für einen neuen Zugangs-Link.";
  if (/\b403\b/.test(low) || low.includes("forbidden"))
    return "Zugriff abgelehnt: Name oder Passwort stimmen nicht. 👉 Es sind dieselben Daten wie in Element — bitte prüfen und nochmal versuchen.";
  if (/\b429\b/.test(low) || low.includes("viele"))
    return "Zu viele Versuche kurz hintereinander. 👉 Warte einen Moment und versuch es erneut.";
  if (/\b5\d\d\b/.test(low) || low.includes("bad gateway") || low.includes("timeout") || low.includes("timed out") || low.includes("zu lange"))
    return "Der Server hat nicht rechtzeitig geantwortet. 👉 Bitte in einer Minute nochmal versuchen.";
  // Schon ein verständlicher Satz vom Backend? (hat Leerzeichen, ist kein roher Code)
  if (raw && /\s/.test(raw) && !/^HTTP \d+$/i.test(raw) && !/\b[A-Z]_[A-Z]{3,}\b/.test(raw) && !/errno/i.test(raw))
    return raw;
  return "Da ist etwas schiefgegangen. 👉 Bitte nochmal versuchen — bleibt es so, frag im Tab »🧭 Assistent« nach »Fehler prüfen«.";
}

/* ---------- Tabs ---------- */
document.querySelectorAll("nav button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll("nav button, .tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("#tab-" + b.dataset.tab).classList.add("active");
    refresh();
  }));

/* ---------- Übersicht ---------- */
let STATUS = null;

// #59: Kachel für den Claude-Zugang — sagt in einfacher Sprache, was zu tun ist.
function claudeLoginTile() {
  const s = (STATUS && STATUS.claude_login && STATUS.claude_login.state) || "unknown";
  if (s === "ok") return { cls: "ok", icon: "✓", hint: " · alles gut" };
  if (s === "expired") return { cls: "err", icon: "🔑",
    hint: " · abgelaufen — bitte am Rechner <code>claude /login</code> eingeben" };
  if (s === "limit") return { cls: "warn", icon: "⏳",
    hint: " · Abo am Limit — API-Key als Reserve hilft" };
  return { cls: "", icon: "—", hint: " · noch nicht geprüft" };
}

async function loadStatus() {
  STATUS = await api("GET", "/api/status");
  const ver = $("#app-version");
  if (ver && STATUS.version) ver.textContent = "v" + STATUS.version;
  const badge = $("#listener-badge");
  badge.textContent = STATUS.listener_running ? "● Listener läuft" : "● Listener aus";
  badge.className = "badge " + (STATUS.listener_running ? "ok" : "err");
  renderSandbox();
  $("#overview-tiles").innerHTML = `
    <div class="tile ${STATUS.listener_running ? "ok" : "err"}"><div class="k">${STATUS.listener_running ? "aktiv" : "aus"}</div><div class="l">Listener · <a href="#" onclick="restartListener();return false">neu starten</a></div></div>
    <div class="tile"><div class="k">${STATUS.agents.length}</div><div class="l">Agenten (${Object.keys(STATUS.published).length} veröffentlicht)</div></div>
    <div class="tile"><div class="k">${STATUS.memory_count}</div><div class="l">Fakten im Gedächtnis</div></div>
    <div class="tile ${STATUS.skill_proposals ? "warn" : ""}"><div class="k">${STATUS.skills_count}</div><div class="l">Skills${STATUS.skill_proposals ? ` · ${STATUS.skill_proposals} Vorschlag${STATUS.skill_proposals > 1 ? "e" : ""} 💡` : ""}</div></div>
    <div class="tile ${STATUS.vault.exists ? (STATUS.vault.locked ? "warn" : "ok") : ""}"><div class="k">${STATUS.vault.exists ? (STATUS.vault.locked ? "🔒" : "🔓") : "—"}</div><div class="l">Tresor${STATUS.vault.exists && !STATUS.vault.locked ? ` · ${STATUS.vault.entries} Einträge` : STATUS.vault.exists ? " · gesperrt" : ""}${STATUS.vault.fido_keys ? ` · 🔑${STATUS.vault.fido_keys}` : ""}</div></div>
    <div class="tile ${STATUS.m365.connected ? "ok" : ""}"><div class="k">${STATUS.m365.connected ? "✓" : "—"}</div><div class="l">Microsoft 365</div></div>
    <div class="tile ${STATUS.google.connected ? "ok" : ""}"><div class="k">${STATUS.google.connected ? "✓" : "—"}</div><div class="l">Google Drive</div></div>
    <div class="tile ${claudeLoginTile().cls}"><div class="k">${claudeLoginTile().icon}</div><div class="l">Claude-Zugang${claudeLoginTile().hint}</div></div>
    <div class="tile ${STATUS.health.synapse_ok ? "ok" : "err"}"><div class="k">${STATUS.health.synapse_ok ? "ok" : "down"}</div><div class="l">Matrix-Server</div></div>
    <div class="tile ${STATUS.health.disk_free_gb < 10 ? "warn" : ""}"><div class="k">${STATUS.health.disk_free_gb} GB</div><div class="l">Disk frei</div></div>
    <div class="tile"><div class="k">${STATUS.health.usage_5h.runs}</div><div class="l">Claude-Läufe (5 h) · ${STATUS.health.cron_jobs} Automationen</div></div>`;
  const audit = await api("GET", "/api/audit?limit=12");
  $("#audit-list").innerHTML = audit.entries.reverse().map((e) =>
    `<div>${esc(e.ts)} · ${esc(e.actor)} · ${esc(e.action)} ${esc(e.target || "")} ${e.ok ? "" : "❌"}</div>`).join("") || "<div>Noch keine Einträge</div>";
  loadUpdate().catch(() => {});
  api("GET", "/api/audit/integrity").then((i) => {
    const el = $("#audit-integrity"); if (!el) return;
    el.textContent = i.ok ? "🔒 Audit-Log unverändert" : "⚠️ " + (i.reason || "Audit-Log verändert!");
    el.style.color = i.ok ? "var(--muted)" : "var(--red,#f85149)";
  }).catch(() => {});
}
async function restartListener() { try { await api("POST", "/api/listener/restart"); toast("Listener neu gestartet"); loadStatus(); } catch (e) { toast(friendlyError(e), 1); } }

/* Update-Benachrichtigung (#64) */
async function loadUpdate() {
  const b = $("#update-banner");
  if (!b) return;
  let u;
  try { u = await api("GET", "/api/update/status"); } catch (e) { b.innerHTML = ""; return; }
  if (!u.update_available) { b.innerHTML = ""; return; }
  b.innerHTML = `
    <div class="card" style="border-color:var(--green,#2ea043);margin-bottom:14px">
      <div class="row-between">
        <h2 style="margin:0">🎉 Neue Version verfügbar — ${esc(u.latest)}</h2>
        <span class="pill">aktuell: ${esc(u.current)}</span>
      </div>
      <ul style="margin:8px 0 12px">${(u.highlights || []).map((h) => `<li>${esc(h)}</li>`).join("")}</ul>
      <button class="primary" onclick="applyUpdate(this)">Jetzt aktualisieren</button>
      <span class="small" style="margin-left:10px">Dauert ~15 s, danach Seite neu laden.</span>
    </div>`;
}

async function applyUpdate(btn) {
  if (!confirm("Operator jetzt auf die neue Version aktualisieren? Listener und Dashboard "
    + "starten dabei kurz neu (deine Daten & Einstellungen bleiben unverändert).")) return;
  if (btn) { btn.disabled = true; btn.textContent = "Aktualisiere …"; }
  try {
    await api("POST", "/api/update/apply");
    $("#update-banner").innerHTML = `<div class="card" style="border-color:var(--green,#2ea043);margin-bottom:14px">
      <h2 style="margin:0">⏳ Update läuft …</h2>
      <p class="small">Listener und Dashboard starten in ~15 Sekunden neu. Danach bitte diese Seite neu laden.</p></div>`;
    setTimeout(() => location.reload(), 18000);
  } catch (e) { toast(friendlyError(e), 1); if (btn) { btn.disabled = false; btn.textContent = "Jetzt aktualisieren"; } }
}

/* ---------- Agenten ---------- */
const ALL_TOOLS = ["Bash", "Read", "Write", "WebFetch", "WebSearch", "Agent", "Skill"];
async function loadAgents() {
  const d = await api("GET", "/api/agents");
  $("#agent-list").innerHTML = d.agents.map((a) => `
    <div class="agent-row">
      <div>
        <strong>${esc(a.name)}</strong>
        <span class="pill model">${esc(a.model)}</span>
        ${a.published ? `<span class="pill published">veröffentlicht als ${esc(a.bot_user_id)}</span>` : ""}
        <div class="meta">${esc(a.description)}</div>
        <div>${a.tools.map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="ghost" onclick="editAgent('${a.name}')">Bearbeiten</button>
        ${a.published
          ? `<button class="danger" onclick="unpublishAgent('${a.name}')">Bot entfernen</button>`
          : `<button class="ghost" onclick="publishAgent('${a.name}')">Als Bot veröffentlichen</button>`}
      </div>
    </div>`).join("") || "<p class='hint'>Noch keine Agenten.</p>";
}

async function editAgent(name) {
  let a = { name: "", description: "", tools: ["Read"], model: "haiku", body: "" };
  if (name) a = await api("GET", "/api/agents/" + name);
  // Modell-Liste dynamisch: Claude-Modelle + konfigurierte Fremd-Provider
  let models = [{ value: "inherit", label: "Claude · Standard" }, { value: "haiku", label: "Claude · haiku" },
                { value: "sonnet", label: "Claude · sonnet" }, { value: "opus", label: "Claude · opus" }];
  let foreign = false;
  try {
    const md = await api("GET", "/api/models");
    if (md.models && md.models.length) models = md.models;
    foreign = models.some((m) => m.kind === "foreign");
  } catch (e) { /* Fallback: nur Claude */ }
  if (a.model && !models.some((m) => m.value === a.model)) models.push({ value: a.model, label: a.model });
  $("#agent-editor").classList.remove("hidden");
  $("#agent-editor").innerHTML = `
    <h2>${name ? "Agent bearbeiten: " + esc(name) : "Neuer Agent"}</h2>
    <label>Name (klein, a-z 0-9 -)</label>
    <input type="text" id="ag-name" value="${esc(a.name)}" ${name ? "disabled" : ""}>
    <label>Beschreibung (wann soll der Operator an diesen Agenten delegieren?)</label>
    <input type="text" id="ag-desc" value="${esc(a.description)}">
    <label>Sprachmodell</label>
    <select id="ag-model">${models.map((m) =>
      `<option value="${esc(m.value)}" ${m.value === a.model ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select>
    ${foreign ? `<p class="small" style="margin:4px 0">Fremd-Modelle (Ollama/OpenAI/Azure) antworten nur mit <strong>Text</strong> — ohne Werkzeuge (Bash/Dateien). Konfigurieren im Tab System → Modelle & Provider.</p>` : ""}
    <label>Werkzeuge</label>
    <div style="margin:6px 0 12px">${ALL_TOOLS.map((t) =>
      `<label class="switch" style="display:inline-flex;margin-right:14px"><input type="checkbox" data-tool="${t}" ${a.tools.includes(t) ? "checked" : ""}>${t}</label>`).join("")}</div>
    <label>Verhaltens-Prompt</label>
    <textarea id="ag-body" rows="10" class="mono">${esc(a.body)}</textarea>
    <div style="display:flex;gap:10px">
      <button class="primary" onclick="saveAgent(${name ? `'${name}'` : "null"})">Speichern</button>
      <button class="ghost" onclick="$('#agent-editor').classList.add('hidden')">Abbrechen</button>
      ${name ? `<button class="danger" style="margin-left:auto" onclick="deleteAgent('${name}')">Löschen</button>` : ""}
    </div>`;
}

async function saveAgent(existing) {
  const payload = {
    name: existing || $("#ag-name").value.trim(),
    description: $("#ag-desc").value.trim(),
    model: $("#ag-model").value,
    tools: [...document.querySelectorAll("#agent-editor input[data-tool]:checked")].map((x) => x.dataset.tool),
    body: $("#ag-body").value,
  };
  if (payload.tools.includes("Bash") && !confirm("⚠️ Bash erlaubt diesem Agenten, Kommandos auf deinem Mac auszuführen. Sicher?")) return;
  try {
    await api(existing ? "PUT" : "POST", "/api/agents" + (existing ? "/" + existing : ""), payload);
    $("#agent-editor").classList.add("hidden");
    toast("Gespeichert — ab der nächsten Nachricht aktiv");
    loadAgents();
  } catch (e) { toast(friendlyError(e), 1); }
}

async function deleteAgent(name) {
  if (!confirm(`Agent "${name}" wirklich löschen?`)) return;
  try { await api("DELETE", "/api/agents/" + name); $("#agent-editor").classList.add("hidden"); toast("Gelöscht"); loadAgents(); }
  catch (e) { toast(friendlyError(e), 1); }
}

// Kleines Overlay-Modal (kein Klartext-prompt() mehr — Passwörter gehören maskiert).
function opModal(title, innerHtml) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.65);display:flex;"
    + "align-items:center;justify-content:center;z-index:9999";
  ov.innerHTML = `<div class="card" style="max-width:460px;width:92%">
    <h2 style="margin-top:0">${title}</h2>${innerHtml}
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
      <button class="ghost" data-op="cancel">Abbrechen</button>
      <button class="primary" data-op="ok">Veröffentlichen</button>
    </div></div>`;
  document.body.appendChild(ov);
  const close = () => ov.remove();
  ov.querySelector('[data-op="cancel"]').onclick = close;
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); } });
  return { ov, close, ok: ov.querySelector('[data-op="ok"]') };
}

// Nutzer-Eingabe → Matrix-Name: nimmt auch »@michael:server« oder »michael@server.de« an
// und macht daraus den reinen Namen (»michael«). Büro-Regel: jede plausible Schreibweise zählt.
function mxLocalpart(s) {
  s = (s || "").trim().replace(/^@/, "");
  if (s.includes(":")) s = s.split(":")[0];
  if (s.includes("@")) s = s.split("@")[0];
  return s.toLowerCase();
}

// Ein Klick, kein Konto, kein Passwort: der Operator legt mit seinem vorhandenen Zugang
// einen eigenen Chat-Raum für den Agenten an (EINFACHHEIT.md — massentauglicher Standard).
async function publishAgent(name) {
  try {
    await api("POST", `/api/agents/${name}/publish`, {});
    toast(`Fertig! »${name}« hat jetzt einen eigenen Chat. Öffne Element — dort wartet die Einladung »${name} (Operator-Agent)«.`);
    loadAgents();
  } catch (e) {
    let msg = e.message || "";
    if (msg.includes("bereits veröffentlicht")) {
      msg = `»${name}« hat schon einen eigenen Chat — schau in Element nach »${name} (Operator-Agent)«.`;
    }
    toast(msg, 1);
  }
}

async function unpublishAgent(name) {
  if (!confirm(`Bot von "${name}" entfernen? Der Matrix-Zugang wird invalidiert.`)) return;
  try { await api("DELETE", `/api/agents/${name}/publish`); toast("Bot entfernt"); loadAgents(); }
  catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- Skills ---------- */
const SKILL_SOURCE = { dashboard: "von dir gepflegt", bot: "vom Operator gelernt", scout: "Scout-Vorschlag" };
/* SkillGuard (#48): Ampel-Pill + Befundliste */
function scanPill(scan) {
  if (!scan) return "";
  const map = { ok: ["🟢 Scan: sauber", "var(--green, #2ea043)"],
    warnung: ["🟡 Scan: Warnung", "var(--amber)"],
    gefahr: ["🔴 Scan: GEFAHR", "var(--red, #f85149)"] };
  const [label, color] = map[scan.level] || map.ok;
  return `<span class="pill" style="color:${color}">${label}</span>`;
}
function scanFindings(scan) {
  if (!scan || !scan.findings || !scan.findings.length) return "";
  return `<ul class="small" style="margin:6px 0">` + scan.findings.map((f) =>
    `<li>${f.level === "gefahr" ? "🔴" : "🟡"} ${esc(f.msg)} — <span class="mono">${esc(f.snippet)}</span></li>`).join("") + "</ul>";
}

async function loadSkills() {
  const d = await api("GET", "/api/skills");
  $("#skill-proposals").innerHTML = d.proposals.length ? `
    <div class="card" style="border-color:var(--amber)">
      <h2>💡 Vorschläge deines Operators (${d.proposals.length})</h2>
      <p class="hint">Diese Muster hat dein Operator in deinen Aufgaben erkannt. Annehmen = wird
      sofort ein Skill. Ablehnen = Vorschlag verschwindet.</p>
      ${d.proposals.map((p) => `
        <div class="agent-row">
          <div><strong>${esc(p.name)}</strong> <span class="pill">${esc(p.created)}</span>
            ${scanPill(p.scan)}
            <div class="meta">${esc(p.description)}</div>
            ${p.reason ? `<div class="small">Warum: ${esc(p.reason)}</div>` : ""}
            ${scanFindings(p.scan)}
            <details class="small"><summary>Anleitung ansehen</summary><pre class="mono small">${esc(p.content)}</pre></details></div>
          <div style="display:flex;gap:8px">
            <button class="primary" onclick="skillProposal('${p.id}','accept')">Annehmen</button>
            <button class="danger" onclick="skillProposal('${p.id}','reject')">Ablehnen</button>
          </div></div>`).join("")}
    </div>` : "";
  $("#skill-list").innerHTML = d.skills.map((s) => `
    <div class="agent-row">
      <div><strong>${esc(s.name)}</strong>
        <span class="pill ${s.source === "bot" ? "model" : ""}">${SKILL_SOURCE[s.source] || esc(s.source)}</span>
        <div class="meta">${esc(s.description)}</div>
        <div class="small">Zuletzt geändert: ${esc(s.modified)}</div></div>
      <div style="display:flex;gap:8px">
        <button class="ghost" onclick="editSkill('${s.name}')">Bearbeiten</button>
        <button class="danger" onclick="deleteSkill('${s.name}')">Löschen</button>
      </div></div>`).join("") || "<p class='hint'>Noch keine Skills. Leg einen an — oder warte, bis dein Operator ein Muster erkennt.</p>";
}

async function editSkill(name) {
  let s = { name: "", description: "", body: "" };
  if (name) s = await api("GET", "/api/skills/" + name);
  $("#skill-editor").classList.remove("hidden");
  $("#skill-editor").innerHTML = `
    <h2>${name ? "Skill bearbeiten: " + esc(name) : "Neuer Skill"}</h2>
    <label>Name (klein, a-z 0-9 -, z. B. pi-status)</label>
    <input type="text" id="sk-name" value="${esc(s.name)}" ${name ? "disabled" : ""}>
    <label>Beschreibung — WANN soll der Operator diesen Skill nutzen? (das liest er zuerst)</label>
    <input type="text" id="sk-desc" value="${esc(s.description)}">
    <label>Anleitung — WIE geht die Aufgabe, Schritt für Schritt? (Markdown, Befehle in \`\`\`bash-Blöcken)</label>
    <textarea id="sk-body" rows="12" class="mono">${esc(s.body)}</textarea>
    <div style="display:flex;gap:10px">
      <button class="primary" onclick="saveSkill(${name ? `'${name}'` : "null"})">Speichern</button>
      <button class="ghost" onclick="$('#skill-editor').classList.add('hidden')">Abbrechen</button>
    </div>
    <p class="hint" style="margin-top:8px">Nach dem Speichern ist der Skill ab der nächsten
    Nachricht aktiv — du kannst ihn im Chat auch direkt ansprechen („Nutze den Skill ${name ? esc(name) : "…"}").
    Bearbeitest du hier einen gelernten Skill, gehört er ab jetzt dir — der Operator ändert ihn dann nie mehr selbst.</p>`;
}

async function saveSkill(existing) {
  const payload = {
    name: existing || $("#sk-name").value.trim(),
    description: $("#sk-desc").value.trim(),
    body: $("#sk-body").value,
  };
  try {
    await api(existing ? "PUT" : "POST", "/api/skills" + (existing ? "/" + existing : ""), payload);
    $("#skill-editor").classList.add("hidden");
    toast("Gespeichert — ab der nächsten Nachricht einsatzbereit");
    loadSkills();
  } catch (e) { toast(friendlyError(e), 1); }
}

async function deleteSkill(name) {
  if (!confirm(`Skill "${name}" wirklich löschen?`)) return;
  try { await api("DELETE", "/api/skills/" + name); $("#skill-editor").classList.add("hidden"); toast("Gelöscht"); loadSkills(); }
  catch (e) { toast(friendlyError(e), 1); }
}

async function skillProposal(id, action) {
  try {
    await api("POST", `/api/skills/proposals/${id}/${action}`);
    toast(action === "accept" ? "Angenommen — der Skill ist ab sofort aktiv" : "Abgelehnt");
    loadSkills();
  } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- SkillGuard-Import (#48) ---------- */
function showSkillImport() {
  $("#skill-import").classList.remove("hidden");
  $("#skill-import").innerHTML = `
    <h2>⬇ Skill importieren</h2>
    <p class="hint">Der Skill wird VOR dem Speichern von SkillGuard auf gefährliche Muster
    geprüft (Secret-Zugriff, Daten-Exfiltration, versteckte Anweisungen). Du entscheidest danach.</p>
    <label>Adresse der SKILL.md (http/https)</label>
    <input type="text" id="si-url" placeholder="https://…/SKILL.md">
    <div style="display:flex;gap:10px;margin-top:8px">
      <button class="primary" onclick="previewSkillImport()">Laden &amp; prüfen</button>
      <button class="ghost" onclick="$('#skill-import').classList.add('hidden')">Abbrechen</button></div>
    <div id="si-preview"></div>`;
}

let _siData = null;
async function previewSkillImport() {
  try {
    const d = await api("POST", "/api/skills/import", { url: $("#si-url").value.trim() });
    _siData = d;
    const danger = d.scan.level === "gefahr";
    $("#si-preview").innerHTML = `
      <div class="agent-row" style="display:block;margin-top:10px">
        <strong>${esc(d.name || "(ohne Namen)")}</strong> ${scanPill(d.scan)}
        <div class="meta">${esc(d.description || "")}</div>
        ${scanFindings(d.scan)}
        <details class="small"><summary>Kompletten Inhalt ansehen</summary>
          <pre class="mono small">${esc(d.body)}</pre></details>
        <div style="display:flex;gap:10px;margin-top:8px">
          <button class="${danger ? "danger" : "primary"}" onclick="confirmSkillImport()">
            ${danger ? "TROTZDEM übernehmen (nicht empfohlen)" : "Übernehmen"}</button>
        </div></div>`;
  } catch (e) { toast(friendlyError(e), 1); }
}

async function confirmSkillImport() {
  if (!_siData) return;
  const name = (_siData.name || "").trim() || prompt("Name für den Skill (klein, mit Bindestrichen):") || "";
  if (!name) return;
  if (_siData.scan.level === "gefahr"
      && !confirm("⚠️ SkillGuard hat GEFÄHRLICHE Muster gefunden (siehe Befunde). Wirklich übernehmen?")) return;
  try {
    await api("POST", "/api/skills", { name, description: _siData.description, body: _siData.body });
    $("#skill-import").classList.add("hidden");
    toast("Skill importiert"); loadSkills();
  } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- Tresor ---------- */
async function loadVault() {
  const c = $("#vault-content");
  let bk;
  try { bk = await api("GET", "/api/vault/backend"); } catch (e) { bk = { backend: "local" }; }
  const backend = bk.backend || "local";
  c.innerHTML = `
    <div class="card">
      <h2>Wo liegen die Passwörter?</h2>
      <p class="hint">Standard: <strong>lokal auf deinem Mac</strong> — empfohlen, keine weitere
      Software nötig. Alternativ nutzt du deine eigene <strong>Vaultwarden-Instanz</strong> als
      Quelle. Für deinen Operator ändert sich nichts: Er benutzt in beiden Fällen nur Platzhalter
      wie <span class="mono">{{tresor:name}}</span> und sieht das Passwort nie.</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="${backend === "local" ? "primary" : "ghost"}" onclick="setVaultBackend('local')">🖥️ Lokal (Standard)</button>
        <button class="${backend === "vaultwarden" ? "primary" : "ghost"}" onclick="setVaultBackend('vaultwarden')">🗄️ Vaultwarden</button>
      </div>
    </div>
    <div id="vault-mode"></div>`;
  if (backend === "vaultwarden") return renderVaultwarden(bk.vaultwarden || {});
  return renderLocalVault();
}

async function setVaultBackend(backend) {
  try { await api("PUT", "/api/vault/backend", { backend }); } catch (e) { return toast(friendlyError(e), 1); }
  loadVault(); loadStatus().catch(() => {});
}

async function renderVaultwarden(vw) {
  const c = $("#vault-mode");
  if (!vw.bw_installed) {
    c.innerHTML = `<div class="card warn">
      <h2>Vaultwarden braucht die <span class="mono">bw</span>-App</h2>
      <p class="hint">Damit dein Operator Passwörter aus Vaultwarden holen kann, muss einmalig die
      offizielle Bitwarden-Kommandozeile installiert werden. Im Terminal:</p>
      <pre class="mono" style="user-select:all">brew install bitwarden-cli</pre>
      <p class="small">Danach diese Seite neu laden. (Alternativ <span class="mono">npm install -g @bitwarden/cli</span>.)</p>
    </div>`;
    return;
  }
  if (!vw.configured || !vw.url) {
    c.innerHTML = `<div class="card">
      <h2>Mit deiner Vaultwarden-Instanz verbinden</h2>
      <p class="hint">Trag die Adresse deiner Vaultwarden-Instanz ein (die, unter der du dich im
      Browser anmeldest).</p>
      <label>Server-Adresse</label>
      <input type="text" id="vw-url" placeholder="https://vault.deine-domain.de" value="${esc(vw.url || "")}">
      <button class="primary" onclick="vwSaveServer()">Server speichern</button>
    </div>`;
    return;
  }
  if (!vw.unlocked) {
    c.innerHTML = `<div class="card">
      <h2>🔒 Vaultwarden entsperren</h2>
      <p class="hint">Verbunden mit <span class="mono">${esc(vw.url)}</span>. Zum Öffnen dein
      Vaultwarden-Master-Passwort eingeben (nach jedem Mac-Neustart einmal). Beim allerersten Mal
      auch deine E-Mail — danach genügt das Passwort.</p>
      <label>E-Mail (nur beim ersten Anmelden nötig)</label>
      <input type="text" id="vw-email" autocomplete="username" placeholder="du@deine-domain.de">
      <label>Vaultwarden Master-Passwort</label>
      <input type="password" id="vw-pw" autocomplete="current-password"
        onkeydown="if(event.key==='Enter')vwUnlock()">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:6px">
        <button class="primary" onclick="vwUnlock()">Entsperren</button>
        <button class="ghost" onclick="vwDisconnect()">Verbindung trennen</button>
      </div>
      <div id="vw-status" class="small" style="margin-top:8px"></div>
    </div>`;
    return;
  }
  let items = { items: [] };
  try { items = await api("GET", "/api/vault/vaultwarden/items"); } catch (e) { /* gesperrt/Fehler */ }
  c.innerHTML = `<div class="card">
    <div class="row-between"><h2>🔓 Vaultwarden entsperrt${vw.items != null ? ` · ${vw.items} ${vw.items === 1 ? "Eintrag" : "Einträge"}` : ""}</h2>
      <div style="display:flex;gap:8px">
        <button class="ghost" onclick="vwLock()">Jetzt sperren</button>
        <button class="ghost" onclick="vwDisconnect()">Trennen</button>
      </div></div>
    <p class="hint">Diese Einträge kommen aus deiner Vaultwarden-Instanz und werden <strong>dort</strong>
    gepflegt (hier nur zur Ansicht, ohne Passwörter). Im Chat nutzt du sie über den Namen:
    <span class="mono">{{tresor:Name}}</span>.</p>
    ${(items.items || []).map((e) => `
      <div class="agent-row">
        <div><strong>${esc(e.name)}</strong>
          <span class="pill mono">{{tresor:${esc(e.name)}}}</span>
          ${e.username ? `<span class="pill">${esc(e.username)}</span>` : ""}
          ${e.url ? `<div class="meta">${esc(e.url)}</div>` : ""}</div>
      </div>`).join("") || "<p class='hint'>Keine Login-Einträge gefunden.</p>"}
  </div>`;
}

async function vwSaveServer() {
  try { await api("PUT", "/api/vault/vaultwarden/config", { url: $("#vw-url").value.trim() }); }
  catch (e) { return toast(friendlyError(e), 1); }
  toast("Server gespeichert"); loadVault();
}

async function vwUnlock() {
  const st = $("#vw-status"); if (st) st.textContent = "Melde bei Vaultwarden an…";
  try {
    await api("POST", "/api/vault/vaultwarden/unlock",
      { master_pw: $("#vw-pw").value, email: ($("#vw-email").value || "").trim() });
  } catch (e) { if (st) st.textContent = ""; return toast(friendlyError(e), 1); }
  toast("Vaultwarden entsperrt"); loadVault(); loadStatus().catch(() => {});
}

async function vwLock() {
  try { await api("POST", "/api/vault/vaultwarden/lock"); } catch (e) { return toast(friendlyError(e), 1); }
  toast("Vaultwarden gesperrt"); loadVault(); loadStatus().catch(() => {});
}

async function vwDisconnect() {
  if (!confirm("Vaultwarden-Verbindung trennen? Der Operator nutzt dann wieder den lokalen Tresor, sobald du oben umschaltest.")) return;
  try { await api("DELETE", "/api/vault/vaultwarden"); } catch (e) { return toast(friendlyError(e), 1); }
  toast("Getrennt"); loadVault();
}

async function renderLocalVault() {
  const s = await api("GET", "/api/vault/status");
  const c = $("#vault-mode");
  if (!s.exists) {
    c.innerHTML = `<div class="card">
      <h2>Tresor anlegen — dauert 1 Minute</h2>
      <div class="stepbox">
        <div class="stepline"><span class="num">1</span><span>Denk dir ein
          <strong>Master-Passwort</strong> aus (mindestens 10 Zeichen). Damit schließt du
          den Tresor auf — nach jedem Neustart deines Macs einmal.</span></div>
        <div class="stepline"><span class="num">2</span><span>Du bekommst danach GENAU EINMAL
          einen <strong>Wiederherstellungsschlüssel</strong> angezeigt. Druck ihn aus oder
          speichere die Notfall-Datei an einem sicheren Ort — er ist deine einzige Rettung,
          falls du das Master-Passwort vergisst.</span></div>
        <div class="stepline"><span class="num">3</span><span>Dann Passwörter eintragen —
          und im Chat einfach schreiben: <em>„Nutze {{tresor:name}} dafür"</em>.</span></div>
      </div>
      <label>Master-Passwort (mind. 10 Zeichen)</label>
      <input type="password" id="v-pw1" autocomplete="new-password">
      <label>Master-Passwort wiederholen</label>
      <input type="password" id="v-pw2" autocomplete="new-password">
      <button class="primary" onclick="vaultInit()">Tresor anlegen</button>
    </div>`;
    return;
  }
  if (s.locked) {
    c.innerHTML = `<div class="card">
      <h2>🔒 Tresor ist gesperrt</h2>
      <p class="hint">Master-Passwort eingeben, um ihn für diese Sitzung zu öffnen.
      Dein Operator kann Zugangsdaten erst wieder nutzen, wenn du entsperrt hast.</p>
      <label>Master-Passwort</label>
      <input type="password" id="v-unlock-pw" autocomplete="current-password"
        onkeydown="if(event.key==='Enter')vaultUnlock()">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="primary" onclick="vaultUnlock()">Entsperren</button>
        ${s.fido_keys && s.fido_supported !== false ? `<button class="ghost" onclick="vaultFidoUnlock()">🔑 Mit Sicherheitsschlüssel entsperren</button>` : ""}
        <a href="#" onclick="vaultRecoverForm();return false" class="small">Master-Passwort vergessen?</a>
      </div>
      <div id="v-fido-status" class="small" style="margin-top:8px"></div>
      <div id="v-recover-form"></div>
    </div>`;
    return;
  }
  const d = await api("GET", "/api/vault/entries");
  c.innerHTML = `
    <div class="card">
      <div class="row-between"><h2>🔓 Entsperrt · ${s.entries} ${s.entries === 1 ? "Eintrag" : "Einträge"}</h2>
        <div style="display:flex;gap:8px">
          <button class="ghost" onclick="vaultLock()">Jetzt sperren</button>
          <button class="ghost" onclick="vaultRotateForm()">Master-Passwort ändern</button>
        </div></div>
      <div id="v-rotate-form"></div>
      ${d.entries.map((e) => `
        <div class="agent-row">
          <div><strong>${esc(e.name)}</strong>
            <span class="pill mono">{{tresor:${esc(e.name)}}}</span>
            ${e.username ? `<span class="pill">${esc(e.username)}</span>` : ""}
            <div class="meta">${esc(e.description)}</div>
            <div class="small">Wert gespeichert · zuletzt geändert ${esc(e.updated)}
              — der Wert wird nie wieder angezeigt</div></div>
          <button class="danger" onclick="deleteVaultEntry('${e.name}')">Löschen</button>
        </div>`).join("") || "<p class='hint'>Noch keine Einträge.</p>"}
    </div>
    <div class="card"><h2>Eintrag speichern / ersetzen</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div><label>Name (klein, z. B. gitea-admin)</label><input type="text" id="v-name"></div>
        <div><label>Benutzername (optional)</label><input type="text" id="v-user"></div>
      </div>
      <label>Wofür ist das? (hilft deinem Operator, den richtigen Eintrag zu wählen)</label>
      <input type="text" id="v-desc" placeholder="z. B. Admin-Login für Gitea auf dem Pi">
      <label>Passwort / Wert (wird nach dem Speichern nie wieder angezeigt)</label>
      <input type="password" id="v-value" autocomplete="new-password">
      <button class="primary" onclick="saveVaultEntry()">Verschlüsselt speichern</button>
      <p class="hint" style="margin-top:8px">Danach im Chat: <em>„Logge dich mit
      {{tresor:${"name"}}} ein"</em> — dein Operator setzt das Passwort ein, ohne es zu sehen.</p>
    </div>
    ${s.fido_supported === false ? "" : `
    <div class="card"><h2>🔑 Sicherheitsschlüssel (FIDO)</h2>
      <p class="hint">Statt dein Master-Passwort zu tippen, kannst du den Tresor auch mit einem
      Hardware-Schlüssel (z. B. YubiKey) öffnen — einfach einstecken und antippen. Du kannst
      mehrere registrieren (z. B. einen Haupt- und einen Backup-Schlüssel). Verlierst du einen,
      entfernst du ihn hier; dein Master-Passwort und der Wiederherstellungsschlüssel funktionieren
      immer weiter.</p>
      <div id="v-fido-list"></div>
      <div style="display:flex;gap:10px;align-items:flex-end;margin-top:10px">
        <div style="flex:1"><label>Name für den neuen Schlüssel</label>
          <input type="text" id="v-fido-label" placeholder="z. B. YubiKey blau"></div>
        <button class="primary" onclick="vaultFidoEnroll()">Schlüssel hinzufügen</button>
      </div>
      <div id="v-fido-enroll-status" class="small" style="margin-top:8px"></div>
    </div>`}`;
  if (s.fido_supported !== false) loadFidoList();
}

async function loadFidoList() {
  const d = await api("GET", "/api/vault/fido");
  const el = $("#v-fido-list");
  if (!el) return;
  el.innerHTML = d.keys.map((k) => `
    <div class="agent-row" style="padding:8px 14px">
      <div><strong>${esc(k.label)}</strong> <span class="small">hinzugefügt ${esc(k.added)}</span></div>
      <button class="danger" onclick="vaultFidoRemove('${k.label.replace(/'/g, "\\'")}')">Entfernen</button>
    </div>`).join("") || "<p class='hint'>Noch kein Sicherheitsschlüssel registriert.</p>";
}

async function vaultFidoEnroll() {
  const label = $("#v-fido-label").value.trim();
  if (!label) return toast("Bitte einen Namen für den Schlüssel angeben", 1);
  const st = $("#v-fido-enroll-status");
  st.textContent = "🔑 Steck deinen Schlüssel ein und tippe ihn an, wenn er blinkt (zweimal beim Registrieren)…";
  try {
    await api("POST", "/api/vault/fido/enroll", { label });
    st.textContent = "";
    $("#v-fido-label").value = "";
    toast(`Schlüssel „${label}" registriert`);
    loadFidoList();
  } catch (e) { st.textContent = ""; toast(friendlyError(e), 1); }
}

async function vaultFidoRemove(label) {
  if (!confirm(`Sicherheitsschlüssel "${label}" entfernen?`)) return;
  try { await api("DELETE", "/api/vault/fido/" + encodeURIComponent(label)); toast("Entfernt"); loadFidoList(); }
  catch (e) { toast(friendlyError(e), 1); }
}

async function vaultFidoUnlock() {
  const st = $("#v-fido-status");
  if (st) st.textContent = "🔑 Tippe jetzt deinen Sicherheitsschlüssel an…";
  try {
    await api("POST", "/api/vault/fido/unlock");
    toast("Tresor entsperrt"); loadVault(); loadStatus().catch(() => {});
  } catch (e) { if (st) st.textContent = ""; toast(friendlyError(e), 1); }
}

async function vaultInit() {
  const p1 = $("#v-pw1").value, p2 = $("#v-pw2").value;
  if (p1 !== p2) return toast("Die Passwörter stimmen nicht überein", 1);
  try {
    const r = await api("POST", "/api/vault/init", { master_pw: p1 });
    showEmergencyKit(r.recovery_key, "Tresor angelegt!");
  } catch (e) { toast(friendlyError(e), 1); }
}

function kitText(key) {
  const d = new Date().toLocaleDateString("de-DE");
  return `OPERATOR NOTFALL-KIT  (erstellt am ${d})
=========================================

Wiederherstellungsschlüssel für deinen Operator-Tresor:

    ${key}

Master-Passwort (von Hand eintragen): ______________________

Wenn du dein Master-Passwort vergisst:
Dashboard (http://127.0.0.1:8737) → Tab „Tresor" → „Master-Passwort vergessen?"
→ diesen Schlüssel eingeben → neues Master-Passwort setzen.
Danach bekommst du einen NEUEN Schlüssel — dieses Blatt dann vernichten.

Bewahre dieses Blatt sicher auf (nicht auf dem Mac speichern!).`;
}

function showEmergencyKit(key, title) {
  $("#vault-content").innerHTML = `<div class="card" style="border-color:var(--accent)">
    <h2>${esc(title)} Dein Wiederherstellungsschlüssel:</h2>
    <div class="kit-key mono">${esc(key)}</div>
    <p class="hint"><strong>Dieser Schlüssel wird NIE wieder angezeigt.</strong> Er ist deine
    einzige Rettung, wenn du das Master-Passwort vergisst. Speichere die Notfall-Datei an
    einem sicheren Ort (USB-Stick, Passwort-Manager, Ausdruck im Ordner) — nicht einfach
    auf diesem Mac liegen lassen.</p>
    <div style="display:flex;gap:10px;margin:10px 0">
      <button class="ghost" onclick="downloadKit('txt')">Notfall-Kit (.txt)</button>
      <button class="ghost" onclick="downloadKit('html')">Notfall-Kit zum Drucken (.html)</button>
    </div>
    <label class="switch"><input type="checkbox" id="v-kit-ok">Ich habe den
      Wiederherstellungsschlüssel gesichert</label>
    <button class="primary" style="margin-top:10px"
      onclick="if(!$('#v-kit-ok').checked)return toast('Bitte erst den Schlüssel sichern und das Häkchen setzen',1);KIT_KEY=null;loadVault()">
      Weiter zum Tresor</button>
  </div>`;
  KIT_KEY = key;
}
let KIT_KEY = null;
function downloadKit(fmt) {
  if (!KIT_KEY) return;
  const txt = kitText(KIT_KEY);
  const content = fmt === "html"
    ? `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Operator Notfall-Kit</title></head>
       <body style="font-family:monospace;padding:40px;max-width:640px"><pre style="white-space:pre-wrap;font-size:14px">${txt.replace(/</g, "&lt;")}</pre></body></html>`
    : txt;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([content], { type: fmt === "html" ? "text/html" : "text/plain" }));
  a.download = "Operator-Notfall-Kit." + fmt;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function vaultUnlock() {
  try {
    await api("POST", "/api/vault/unlock", { master_pw: $("#v-unlock-pw").value });
    toast("Tresor entsperrt"); loadVault(); loadStatus().catch(() => {});
  } catch (e) { toast(friendlyError(e), 1); }
}
async function vaultLock() {
  try { await api("POST", "/api/vault/lock"); toast("Tresor gesperrt"); loadVault(); loadStatus().catch(() => {}); }
  catch (e) { toast(friendlyError(e), 1); }
}

function vaultRecoverForm() {
  $("#v-recover-form").innerHTML = `
    <label style="margin-top:12px">Wiederherstellungsschlüssel (aus deinem Notfall-Kit)</label>
    <input type="text" id="v-rk" class="mono" placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX">
    <label>Neues Master-Passwort (mind. 10 Zeichen)</label>
    <input type="password" id="v-new-pw" autocomplete="new-password">
    <button class="primary" onclick="vaultRecover()">Neues Master-Passwort setzen</button>`;
}
async function vaultRecover() {
  try {
    const r = await api("POST", "/api/vault/recover",
      { recovery_key: $("#v-rk").value, new_master_pw: $("#v-new-pw").value });
    showEmergencyKit(r.recovery_key, "Neues Master-Passwort gesetzt! Alter Schlüssel ist ungültig.");
  } catch (e) { toast(friendlyError(e), 1); }
}

function vaultRotateForm() {
  $("#v-rotate-form").innerHTML = `<div class="stepbox" style="margin-bottom:12px">
    <label>Aktuelles Master-Passwort</label><input type="password" id="v-old-pw">
    <label>Neues Master-Passwort (mind. 10 Zeichen)</label><input type="password" id="v-rot-pw">
    <button class="primary" onclick="vaultRotate()">Ändern</button></div>`;
}
async function vaultRotate() {
  try {
    await api("POST", "/api/vault/rotate-master",
      { old_pw: $("#v-old-pw").value, new_pw: $("#v-rot-pw").value });
    toast("Master-Passwort geändert — Wiederherstellungsschlüssel bleibt gültig"); loadVault();
  } catch (e) { toast(friendlyError(e), 1); }
}

async function saveVaultEntry() {
  const name = $("#v-name").value.trim();
  try {
    await api("PUT", "/api/vault/entries/" + encodeURIComponent(name), {
      value: $("#v-value").value, description: $("#v-desc").value.trim(),
      username: $("#v-user").value.trim(),
    });
    $("#v-value").value = "";
    toast(`Gespeichert — im Chat nutzbar als {{tresor:${name}}}`);
    loadVault();
  } catch (e) { toast(friendlyError(e), 1); }
}
async function deleteVaultEntry(name) {
  if (!confirm(`Eintrag "${name}" endgültig löschen?`)) return;
  try { await api("DELETE", "/api/vault/entries/" + encodeURIComponent(name)); loadVault(); }
  catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- M365 ---------- */
const M365_SERVICES = [
  ["mail", "Mail", ""], ["calendar", "Kalender", ""], ["onedrive", "OneDrive", ""],
  ["sharepoint", "SharePoint", ""], ["planner", "Planner", ""],
  ["teams", "Teams", "Nur Basisdaten (Teams/Kanäle). Nachrichten lesen = von Microsoft geschützte API, Senden app-seitig nicht möglich."],
  ["status", "Status & Berichte", "Läuft Microsoft? Störungen, Meldungen aus dem Message Center, Lizenzen, Nutzung. Reines Nachschauen — hier gibt es nichts zu verändern."],
];
// Dienste ohne sinnvollen Schreib-Regler (spiegelt m365_setup.NUR_LESEN)
const M365_NUR_LESEN = ["teams", "status"];
async function loadM365() {
  const s = await api("GET", "/api/m365/status");
  const c = $("#m365-content");
  if (!s.configured) {
    c.innerHTML = `<div class="card">
      <h2>Einmalige Hersteller-Einrichtung nötig</h2>
      <p class="hint">Danach ist die M365-Anbindung für alle Nutzer ein reiner Berechtigungs-Dialog
      („Als Admin anmelden" → Microsoft-Consent → Regler setzen — fertig).<br><br>
      Voraussetzung ist die Multi-Tenant-App <strong>„The Operator Setup"</strong> im
      Hersteller-Tenant — sie ist der Name, den Admins auf dem Microsoft-Consent-Screen
      sehen. Bewusst keine geliehene Fremd-Client-ID: Wer zustimmt, muss sehen, wem.
      Anleitung: README im Repo (15 Minuten, einmalig).</p>
      <label>Client-ID der Setup-App (GUID)</label><input type="text" id="m365-cid" placeholder="xxxxxxxx-xxxx-…">
      <button class="primary" onclick="saveM365Cid()">Speichern</button></div>`;
    return;
  }
  const perms = s.permissions || {};
  c.innerHTML = `
    <div class="card">${s.connected
      ? `<p>✅ <strong>Verbunden.</strong> Dein Operator kann jetzt auf die unten
         eingeschalteten Dienste zugreifen. Frag ihn im Chat z. B.:
         <em>„Was steht in meinen letzten 3 Mails?"</em><br>
         <span class="small">Firma: <span class="mono">${esc(s.tenant_id)}</span> ·
         Zugang gültig bis ${esc((s.secret_expires || "?").slice(0, 10))}</span></p>
         <div class="stepbox" style="margin-top:12px">
           <strong>Wessen Daten?</strong>
           <p class="hint" style="margin:6px 0 8px">Es ist keine weitere Anmeldung nötig.
           Trage hier einfach die M365-E-Mail-Adresse des Benutzers ein, dessen Postfach,
           Kalender und OneDrive dein Operator verwenden soll (z. B. deine eigene):</p>
           <input type="text" id="m365-upn" value="${esc(s.primary_user || "")}" placeholder="name@deinefirma.de">
           <button class="ghost" onclick="saveM365Upn()">Benutzer speichern</button>
         </div>`
      : `<p class="hint"><strong>So verbindest du dein Microsoft 365 — in 3 Schritten:</strong></p>
         <div class="stepbox">
           <div class="stepline"><span class="num">1</span><span>Klicke unten auf
             <strong>„Als Admin anmelden"</strong>. Dein Browser öffnet die normale
             Microsoft-Anmeldung — melde dich mit deinem <strong>Admin-Konto</strong> an
             und klicke auf <strong>Accept</strong>.</span></div>
           <div class="stepline"><span class="num">2</span><span>Komm hierher zurück und
             schalte unten ein, was dein Operator darf — zum Beispiel
             <strong>Mail → Lesen</strong>. Alles ist zu Beginn AUS.</span></div>
           <div class="stepline"><span class="num">3</span><span>Klicke auf
             <strong>„Einrichtung starten"</strong> und schau zu — den Rest erledigt
             Operator von allein.</span></div>
         </div>
         <button class="primary" onclick="m365Login()">Als Admin anmelden</button>`}
    </div>
    <div class="card"><h2>Microsoft-Status</h2>
      <div id="m365-zustand"><p class="hint">wird geladen …</p></div>
    </div>
    <div class="card"><h2>Berechtigungen je Dienst</h2>
      ${M365_SERVICES.map(([k, label, note]) => {
        const p = perms[k] || { read: false, write: false };
        const noWrite = M365_NUR_LESEN.includes(k);
        return `<div class="svc"><div><div class="name">${label}</div>${note ? `<div class="note">${note}</div>` : ""}</div>
          <div class="toggles">
            <label class="switch"><input type="checkbox" data-svc="${k}" data-mode="read" ${p.read ? "checked" : ""}>Lesen</label>
            <label class="switch"><input type="checkbox" data-svc="${k}" data-mode="write" ${p.write ? "checked" : ""} ${noWrite ? "disabled" : ""}>Schreiben</label>
          </div></div>`;
      }).join("")}
      <div style="margin-top:14px;display:flex;gap:10px">
        <button class="primary" onclick="m365Apply(${s.connected})">${s.connected ? "Rechte aktualisieren" : "Einrichtung starten"}</button>
        ${s.connected ? `<button class="danger" onclick="m365Delete()">Verbindung + Entra-App löschen</button>` : ""}
      </div>
      <p class="hint" style="margin-top:8px">Zur Sicherheit startet alles AUS.
      „Schreiben" erlaubt automatisch auch „Lesen". Schaltest du einen Regler wieder AUS,
      wird das Recht bei Microsoft <strong>wirklich entzogen</strong> — nicht nur versteckt.</p>
    </div>`;
  loadM365Zustand();
}
// #117: Ampel je Microsoft-Dienst. Läuft absichtlich NACH dem Rendern und getrennt —
// eine langsame oder fehlende Microsoft-Antwort darf den ganzen Tab nicht blockieren.
async function loadM365Zustand() {
  const box = $("#m365-zustand");
  if (!box) return;
  try {
    const z = await api("GET", "/api/m365/dienstzustand");
    if (!z.verfuegbar) {
      box.innerHTML = `<p class="hint">Noch kein Einblick in den Microsoft-Status.
        👉 Schalte oben <strong>„Status &amp; Berichte → Lesen"</strong> ein und klicke
        auf <strong>„Rechte aktualisieren"</strong>.</p>
        <p class="small mono">${esc(z.hinweis || "")}</p>`;
      return;
    }
    const kopf = z.alles_gut === null ? "Microsoft meldet keine Dienste."
      : z.alles_gut ? "🟢 Alles läuft normal."
        : "Nicht alles läuft normal — die Zeilen unten zeigen, wo.";
    box.innerHTML = `<p class="small" style="margin:2px 0 8px"><strong>${esc(kopf)}</strong></p>`
      + z.dienste.map((d) => `<div class="agent-row" style="display:block;padding:6px 14px">
           <div style="display:flex;justify-content:space-between;gap:10px">
             <div>${esc(d.ampel)} <strong>${esc(d.name)}</strong></div>
             <span class="small">${esc(d.text)}</span>
           </div>
           ${(d.probleme || []).map((p) => `<div class="hint" style="margin:4px 0 0 24px">
             ↳ ${esc(p.titel)} <span class="mono small">(${esc(p.id)}, seit ${esc(p.seit)})</span>
           </div>`).join("")}
         </div>`).join("");
  } catch (e) {
    box.innerHTML = `<p class="hint">Status gerade nicht abrufbar: ${esc(friendlyError(e))}</p>`;
  }
}
async function saveM365Cid() {
  try { await api("PUT", "/api/m365/setup-client", { client_id: $("#m365-cid").value.trim() }); toast("Gespeichert"); loadM365(); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function saveM365Upn() {
  try {
    await api("PUT", "/api/m365/primary-user", { upn: $("#m365-upn").value });
    toast("Gespeichert — dein Operator nutzt jetzt die Daten dieses Benutzers");
  } catch (e) { toast(friendlyError(e), 1); }
}
async function m365Login() {
  try { const r = await api("POST", "/api/m365/auth/start"); window.open(r.auth_url, "_blank"); toast("Anmeldefenster geöffnet — danach hier fortfahren"); }
  catch (e) { toast(friendlyError(e), 1); }
}
function collectM365Matrix() {
  const m = {};
  document.querySelectorAll("#m365-content input[data-svc]").forEach((i) => {
    m[i.dataset.svc] = m[i.dataset.svc] || { read: false, write: false };
    m[i.dataset.svc][i.dataset.mode] = i.checked;
  });
  Object.values(m).forEach((p) => { if (p.write) p.read = true; });
  return m;
}
async function m365Apply(connected) {
  const permissions = collectM365Matrix();
  const any = Object.values(permissions).some((p) => p.read || p.write);
  if (!connected && !any) {
    return toast("Bitte zuerst mindestens einen Regler aktivieren (z. B. Mail › Lesen)", 1);
  }
  if (connected && !any && !confirm("Alle Regler sind AUS — das entzieht der Connector-App sämtliche Rechte. Fortfahren?")) return;
  if (!connected) {
    const log = $("#m365-log");
    log.classList.remove("hidden");
    log.textContent = "Einrichtung läuft…\n";
    document.querySelectorAll(".ascii-banner").forEach((b) => b.remove());
    const r = await fetch("/api/m365/setup/run", {
      method: "POST",
      headers: { "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify({ permissions }),
    });
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "", failed = false, finished = false;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      for (const line of buf.split("\n\n")) {
        if (!line.startsWith("data: ")) continue;
        try {
          const e = JSON.parse(line.slice(6));
          log.textContent += `[${e.status}] ${e.step}: ${e.detail}\n`;
          if (e.status === "error") failed = true;
          if (e.step === "finish" && e.status === "done") finished = true;
        } catch {}
      }
      buf = buf.slice(buf.lastIndexOf("\n\n") + 2);
    }
    log.insertAdjacentHTML("afterend", asciiBanner(finished && !failed));
    if (finished && !failed) { toast("Microsoft 365 ist verbunden ✓"); }
    loadM365();
  } else {
    try { const r = await api("PUT", "/api/m365/permissions", { permissions }); toast(`Rechte aktualisiert (+${r.added} / −${r.removed})`); loadM365(); }
    catch (e) { toast(friendlyError(e), 1); }
  }
}
async function m365Delete() {
  if (!confirm("Connector-App im Entra löschen und Verbindung trennen?")) return;
  try { await api("DELETE", "/api/m365"); toast("Getrennt"); loadM365(); } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- Google ---------- */
async function loadGoogle() {
  const s = await api("GET", "/api/google/status");
  const c = $("#google-content");
  if (!s.configured) {
    c.innerHTML = `<div class="card"><h2>Google Drive verbinden — einmalige Vorbereitung</h2>
      <p class="hint">Deine Google-Daten sollen <strong>nur dir</strong> gehören. Darum legst du
      einmalig deinen eigenen, kostenlosen „Zugangs-Schlüssel" bei Google an (~5 Minuten) —
      so läuft nichts über fremde Firmen. Einfach die 4 Links von oben nach unten abarbeiten,
      auf jeder Seite ist es nur ein Klick oder zwei:</p>
      <div class="stepbox">
        <div class="stepline"><span class="num">1</span><span>
          <a href="https://console.cloud.google.com/projectcreate" target="_blank">Neues Google-Projekt anlegen</a>
          — Name ist egal, z. B. „operator". Auf „Erstellen" klicken.</span></div>
        <div class="stepline"><span class="num">2</span><span>
          <a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank">Drive-Schnittstelle einschalten</a>
          — blauen Knopf „Aktivieren" klicken.</span></div>
        <div class="stepline"><span class="num">3</span><span>
          <a href="https://console.cloud.google.com/auth/branding" target="_blank">Zustimmungs-Seite einrichten</a>
          — „Extern" wählen und <strong>deine eigene Gmail-Adresse als Testnutzer</strong> eintragen.</span></div>
        <div class="stepline"><span class="num">4</span><span>
          <a href="https://console.cloud.google.com/auth/clients" target="_blank">Schlüssel erstellen</a>
          — Typ <strong>„Desktopanwendung"</strong> wählen. Google zeigt dir dann zwei lange
          Zeichenketten: <strong>Client-ID</strong> und <strong>Client-Secret</strong>. Beide unten einfügen.</span></div>
      </div>
      <label>Client-ID (endet auf .apps.googleusercontent.com)</label><input type="text" id="g-cid" placeholder="….apps.googleusercontent.com">
      <label>Client-Secret</label><input type="password" id="g-secret">
      <button class="primary" onclick="saveGoogleCfg()">Speichern</button></div>`;
    return;
  }
  c.innerHTML = `<div class="card">
    ${s.connected
      ? `<p>✅ Verbunden als <strong>${esc(s.connected_email)}</strong> — Modus: <strong>${s.write_enabled ? "Lesen + Schreiben" : "nur Lesen"}</strong></p>`
      : `<p class="hint">Client konfiguriert — jetzt mit deinem Google-Konto verbinden.</p>`}
    <div class="svc"><div><div class="name">Google Drive</div><div class="note">Schreiben = Dateien anlegen/ändern; Wechsel erfordert erneute Google-Zustimmung</div></div>
      <div class="toggles"><label class="switch"><input type="checkbox" id="g-write" ${s.write_enabled ? "checked" : ""}>Schreiben erlauben</label></div></div>
    <div style="margin-top:14px;display:flex;gap:10px">
      <button class="primary" onclick="googleConnect()">${s.connected ? "Neu verbinden (Regler übernehmen)" : "Mit Google verbinden"}</button>
      ${s.connected ? `<button class="danger" onclick="googleDisconnect()">Trennen (Token widerrufen)</button>` : ""}
    </div></div>`;
}
async function saveGoogleCfg() {
  try { await api("PUT", "/api/google/config", { client_id: $("#g-cid").value, client_secret: $("#g-secret").value }); toast("Gespeichert"); loadGoogle(); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function googleConnect() {
  try { const r = await api("POST", "/api/google/auth/start", { write: $("#g-write").checked }); window.open(r.auth_url, "_blank"); toast("Google-Anmeldung geöffnet"); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function googleDisconnect() {
  if (!confirm("Google-Zugriff widerrufen und Tokens löschen?")) return;
  try { await api("DELETE", "/api/google"); toast("Getrennt"); loadGoogle(); } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- Verlauf (A1) ---------- */
async function loadSessions() {
  const q = $("#sess-q").value.trim();
  const d = await api("GET", "/api/sessions?limit=30" + (q ? "&q=" + encodeURIComponent(q) : ""));
  $("#sess-list").innerHTML = d.sessions.map((s) => `
    <div class="card" style="padding:12px 16px;margin-bottom:8px">
      <div class="row-between" style="margin-bottom:4px">
        <div><strong>${esc(s.bot)}</strong> <span class="pill">${esc(s.kind)}</span>
          ${s.rc !== 0 ? '<span class="pill" style="color:var(--red)">Fehler</span>' : ""}</div>
        <span class="small">${esc(s.ts)} · ${(s.duration_ms / 1000).toFixed(1)}s · ${s.tokens_out} tok</span>
      </div>
      <div class="small">➤ ${esc(s.messages.slice(0, 200))}</div>
      <div class="small" style="color:var(--text);margin-top:4px">✦ ${esc((s.result || "").slice(0, 300))}</div>
    </div>`).join("") || "<p class='hint'>Noch keine Läufe aufgezeichnet.</p>";
}

/* ---------- Automationen (A3) ---------- */
async function loadCron() {
  const d = await api("GET", "/api/cron");
  $("#cron-list").innerHTML = d.jobs.map((j) => `
    <div class="agent-row">
      <div><strong>${esc(j.name)}</strong>
        <span class="pill model">${esc(j.schedule || "manuell")}</span>
        <span class="pill">${esc(j.target)}</span>
        ${j.enabled ? "" : '<span class="pill" style="color:var(--amber)">pausiert</span>'}
        <div class="meta">${esc(j.prompt.slice(0, 120))}</div>
        <div class="small">${j.last_run ? "Zuletzt: " + esc(j.last_run) : "Noch nie gelaufen"}</div></div>
      <div style="display:flex;gap:8px">
        <button class="ghost" onclick="runCron('${j.id}')">▶ Jetzt</button>
        <button class="ghost" onclick="editCron('${j.id}')">Bearbeiten</button>
        <button class="danger" onclick="deleteCron('${j.id}')">Löschen</button>
      </div></div>`).join("") || "<p class='hint'>Noch keine Automationen.</p>";
}

async function editCron(id) {
  let j = { name: "", schedule: "0 7 * * *", prompt: "", target: "owner", enabled: true };
  if (id) j = (await api("GET", "/api/cron")).jobs.find((x) => x.id === id) || j;
  const agents = (await api("GET", "/api/agents")).agents.filter((a) => a.published);
  $("#cron-editor").classList.remove("hidden");
  $("#cron-editor").innerHTML = `
    <h2>${id ? "Automation bearbeiten" : "Neue Automation"}</h2>
    <label>Name</label><input type="text" id="cr-name" value="${esc(j.name)}">
    <label>Zeitplan (Min Std Tag Monat Wochentag — leer = nur manuell)</label>
    <input type="text" id="cr-schedule" value="${esc(j.schedule)}" placeholder="0 7 * * 1-5">
    <label>Auftrag an den Operator</label>
    <textarea id="cr-prompt" rows="4">${esc(j.prompt)}</textarea>
    <label>Ausführen als</label>
    <select id="cr-target"><option value="owner" ${j.target === "owner" ? "selected" : ""}>Operator (Haupt-Bot)</option>
      ${agents.map((a) => `<option value="${a.name}" ${j.target === a.name ? "selected" : ""}>Agent: ${a.name}</option>`).join("")}</select>
    <label class="switch" style="margin:8px 0"><input type="checkbox" id="cr-enabled" ${j.enabled ? "checked" : ""}>aktiv</label>
    <div style="display:flex;gap:10px">
      <button class="primary" onclick="saveCron(${id ? `'${id}'` : "null"})">Speichern</button>
      <button class="ghost" onclick="$('#cron-editor').classList.add('hidden')">Abbrechen</button></div>`;
}

async function saveCron(id) {
  const payload = { name: $("#cr-name").value.trim(), schedule: $("#cr-schedule").value.trim(),
    prompt: $("#cr-prompt").value.trim(), target: $("#cr-target").value,
    enabled: $("#cr-enabled").checked };
  try {
    await api(id ? "PUT" : "POST", "/api/cron" + (id ? "/" + id : ""), payload);
    $("#cron-editor").classList.add("hidden");
    toast("Gespeichert"); loadCron();
  } catch (e) { toast(friendlyError(e), 1); }
}
async function runCron(id) {
  try { await api("POST", `/api/cron/${id}/run`); toast("Gestartet — Ergebnis kommt in den Matrix-Raum und in den Verlauf"); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function deleteCron(id) {
  if (!confirm("Automation löschen?")) return;
  try { await api("DELETE", "/api/cron/" + id); loadCron(); } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- Ereignis-Regeln (#47) ---------- */
async function loadTriggers() {
  const d = await api("GET", "/api/triggers");
  $("#trigger-list").innerHTML = (d.pending ? `<p class="small">⏳ ${d.pending} Ereignis(se) in der Warteschlange</p>` : "")
    + d.rules.map((r) => `
    <div class="agent-row">
      <div><strong>⚡ ${esc(r.name)}</strong>
        <span class="pill model">Quelle: ${esc(r.source)}</span>
        ${r.keyword ? `<span class="pill">Stichwort: ${esc(r.keyword)}</span>` : ""}
        <span class="pill">${esc(r.target)}</span>
        ${r.enabled ? "" : '<span class="pill" style="color:var(--amber)">pausiert</span>'}
        ${r.prompt ? `<div class="meta">${esc(r.prompt.slice(0, 120))}</div>` : ""}</div>
      <div style="display:flex;gap:8px">
        <button class="ghost" onclick="editTrigger('${r.id}')">Bearbeiten</button>
        <button class="danger" onclick="deleteTrigger('${r.id}')">Löschen</button>
      </div></div>`).join("") || "<p class='hint'>Noch keine Ereignis-Regeln.</p>";
}

async function editTrigger(id) {
  let r = { name: "", source: "", keyword: "", prompt: "", target: "owner", enabled: true };
  if (id) r = (await api("GET", "/api/triggers")).rules.find((x) => x.id === id) || r;
  const agents = (await api("GET", "/api/agents")).agents.filter((a) => a.published);
  $("#trigger-editor").classList.remove("hidden");
  $("#trigger-editor").innerHTML = `
    <h2>${id ? "Regel bearbeiten" : "Neue Ereignis-Regel"}</h2>
    <label>Name (z. B. »Wichtige Mail«)</label><input type="text" id="tr-name" value="${esc(r.name)}">
    <label>Quelle — muss exakt zum <span class="mono">source</span>-Feld des Absenders passen (z. B. »n8n-mail«)</label>
    <input type="text" id="tr-source" value="${esc(r.source)}" placeholder="n8n-mail">
    <label>Stichwort-Filter (optional — nur Ereignisse, deren Text das enthält)</label>
    <input type="text" id="tr-keyword" value="${esc(r.keyword || "")}" placeholder="z. B. Rechnung">
    <label>Anweisung an den Operator (optional)</label>
    <textarea id="tr-prompt" rows="3" placeholder="z. B. Fasse die Mail kurz zusammen und schlage eine Antwort vor.">${esc(r.prompt || "")}</textarea>
    <label>Ausführen als</label>
    <select id="tr-target"><option value="owner" ${r.target === "owner" ? "selected" : ""}>Operator (Haupt-Bot)</option>
      ${agents.map((a) => `<option value="${a.name}" ${r.target === a.name ? "selected" : ""}>Agent: ${a.name}</option>`).join("")}</select>
    <label class="switch" style="margin:8px 0"><input type="checkbox" id="tr-enabled" ${r.enabled ? "checked" : ""}>aktiv</label>
    <div style="display:flex;gap:10px">
      <button class="primary" onclick="saveTrigger(${id ? `'${id}'` : "null"})">Speichern</button>
      <button class="ghost" onclick="$('#trigger-editor').classList.add('hidden')">Abbrechen</button></div>`;
}

async function saveTrigger(id) {
  const payload = { name: $("#tr-name").value.trim(), source: $("#tr-source").value.trim(),
    keyword: $("#tr-keyword").value.trim(), prompt: $("#tr-prompt").value.trim(),
    target: $("#tr-target").value, enabled: $("#tr-enabled").checked };
  try {
    await api(id ? "PUT" : "POST", "/api/triggers" + (id ? "/" + id : ""), payload);
    $("#trigger-editor").classList.add("hidden");
    toast("Gespeichert"); loadTriggers();
  } catch (e) { toast(friendlyError(e), 1); }
}
async function deleteTrigger(id) {
  if (!confirm("Ereignis-Regel löschen?")) return;
  try { await api("DELETE", "/api/triggers/" + id); loadTriggers(); } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- Retro-Statusbanner (Pixel-Art) ---------- */
const SKULL = String.raw`
      ▄▄▄▄▄▄▄▄▄▄▄
     █████████████
    ██▀▀▀█████▀▀▀██
    ██   █████   ██
    ███▄▄█████▄▄███
     █████▀▀▀█████
      ███ █▄█ ███
       ▀█▀▀▀▀▀█▀
       ▄█▄▄▄▄▄█▄

  A C C E S S   D E N I E D`;
const GRANTED = String.raw`
                  ▄██
                 ▄██▀
      ██▄       ▄██▀
       ▀██▄    ▄██▀
        ▀██▄  ▄██▀
         ▀██▄▄██▀
          ▀████▀
           ▀██▀

  A C C E S S   G R A N T E D`;
function asciiBanner(ok) {
  return `<div class="ascii-banner ${ok ? "ok" : "err"}"><pre>${ok ? GRANTED : SKULL}</pre><span class="cursor">█</span></div>`;
}

/* ---------- Nutzung (A4) ---------- */
function bars(buckets, labelFn) {
  const max = Math.max(1, ...buckets.map((b) => b.runs));
  const w = 100 / buckets.length;
  const rects = buckets.map((b, i) => {
    const h = (b.runs / max) * 80;
    const x = 100 - (i + 1) * w;
    return `<rect x="${x}%" y="${90 - h}" width="${w * 0.7}%" height="${h}" rx="2"
      fill="var(--accent)" opacity="${b.runs ? 0.9 : 0.15}">
      <title>${labelFn(b)}: ${b.runs} Läufe, ${b.tokens_out} Tokens</title></rect>`;
  }).join("");
  return `<svg viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:120px">${rects}</svg>`;
}
async function loadUsage() {
  const d = await api("GET", "/api/usage");
  const w = d.window_5h;
  $("#usage-tiles").innerHTML = `
    <div class="tile"><div class="k">${w.runs}</div><div class="l">Läufe (letzte 5 h)</div></div>
    <div class="tile"><div class="k">${(w.tokens_out / 1000).toFixed(1)}k</div><div class="l">Antwort-Tokens (5 h)</div></div>
    <div class="tile"><div class="k">${(w.tokens_in / 1000).toFixed(0)}k</div><div class="l">Kontext-Tokens (5 h)</div></div>
    <div class="tile"><div class="k">${(w.duration_ms / 60000).toFixed(1)} min</div><div class="l">Rechenzeit (5 h)</div></div>`;
  $("#usage-24h").innerHTML = bars(d.buckets_24h, (b) => `vor ${b.offset} h`);
  $("#usage-7d").innerHTML = bars(d.buckets_7d, (b) => `vor ${b.offset} Tagen`);
}

/* ---------- Gedächtnis (A2) ---------- */
async function loadMemory() {
  const q = $("#mem-q").value.trim();
  const d = await api("GET", "/api/memory?limit=50" + (q ? "&q=" + encodeURIComponent(q) : ""));
  $("#mem-list").innerHTML = d.memories.map((m) => `
    <div class="agent-row" style="padding:10px 16px">
      <div>${esc(m.text)}<div class="small">#${m.id} · ${esc(m.created)} · ${m.uses}× abgerufen</div></div>
      <button class="danger" onclick="forgetMemory(${m.id})">Vergessen</button>
    </div>`).join("") || "<p class='hint'>Leeres Gedächtnis.</p>";
  renderSemantik(d.semantik);
}

/* #109: Zustand der semantischen Suche ehrlich zeigen — ein stiller Rückfall auf
   reine Wortsuche bleibt sonst unbemerkt (»es funktioniert ja, nur schlechter«). */
function renderSemantik(s) {
  const el = document.getElementById("mem-semantik");
  if (!el) return;
  el.textContent = "";
  if (!s) return;
  const p = document.createElement("p");
  p.className = "small";
  p.textContent = (s.aktiv ? "🔎 " : "⚠️ ") + s.grund;
  el.appendChild(p);
  el.style.borderLeft = "3px solid var(--" + (s.aktiv ? "green" : "amber") + ")";
  if (s.aktiv && s.ohne_vektor > 0) {
    const w = document.createElement("p");
    w.className = "small";
    w.textContent = `${s.ohne_vektor} von ${s.fakten} Fakten sind noch nicht für die `
      + "semantische Suche vorbereitet.";
    const b = document.createElement("button");
    b.className = "ghost";
    b.textContent = "Jetzt nachtragen";
    b.onclick = async () => {
      b.disabled = true; b.textContent = "läuft …";
      try { await api("POST", "/api/memory/reindex"); toast("Nachgetragen"); loadMemory(); }
      catch (e) { toast(friendlyError(e), 1); b.disabled = false; b.textContent = "Jetzt nachtragen"; }
    };
    el.appendChild(w); el.appendChild(b);
  }
}
async function addMemory() {
  const text = $("#mem-new").value.trim();
  if (!text) return;
  try { await api("POST", "/api/memory", { text }); $("#mem-new").value = ""; toast("Gespeichert"); loadMemory(); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function forgetMemory(id) {
  try { await api("DELETE", "/api/memory/" + id); loadMemory(); } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- Logs (A5) ---------- */
async function loadLogs() {
  const d = await api("GET", `/api/logs?file=${$("#log-file").value}&lines=300&errors_only=${$("#log-errors").checked}`);
  const v = $("#log-view");
  v.textContent = d.lines.join("\n") || "(leer)";
  v.scrollTop = v.scrollHeight;
}

/* ---------- Modelle & Provider ---------- */
// Geführte Schritt-für-Schritt-Hilfe je Provider (immer sichtbar, damit niemand raten muss).
function providerSteps(id) {
  const steps = {
    ollama: [
      "<strong>Ollama starten:</strong> öffne die Ollama-App (Symbol in der Menüleiste) — oder im Terminal <code>ollama serve</code>.",
      "<strong>Für Cloud-Modelle</strong> (z. B. <code>kimi-k2.7-code:cloud</code>): einmalig im Terminal <code>ollama signin</code> — meldet dich bei deinem Ollama-Konto an. Cloud-Modelle rechnen auf Ollamas Servern, nicht auf deinem Mac.",
      "<strong>Modell eintragen:</strong> oben in »Modelle« den Namen, mehrere mit Komma trennen.",
      "<strong>Speichern &amp; testen</strong> — die Ampel unten zeigt sofort, ob alles passt.",
    ],
    openai: [
      "<strong>API-Key holen:</strong> auf platform.openai.com/api-keys einen Key erzeugen.",
      "Key unten eintragen, Modelle (z. B. <code>gpt-4o</code>) mit Komma, dann <strong>Speichern &amp; testen</strong>.",
    ],
    azure: [
      "<strong>Endpoint + Key</strong> aus deinem Azure-AI-Foundry-Projekt eintragen (Server-Adresse endet meist auf <code>/openai/v1/</code>).",
      "Deployment-Namen als Modell eintragen, dann <strong>Speichern &amp; testen</strong>.",
    ],
  }[id] || [];
  if (!steps.length) return "";
  return `<details class="mp-help" style="margin-top:6px"><summary class="small" style="cursor:pointer;color:var(--accent)">So verbindest du das — Schritt für Schritt</summary>
    <ol class="small" style="margin:6px 0 0;padding-left:18px;line-height:1.6">${steps.map(s => `<li>${s}</li>`).join("")}</ol></details>`;
}

// hint → farbige Ampel + konkreter nächster Schritt (aus providers.test()).
function renderProviderStatus(id, r) {
  const el = $("#mp-" + id + "-status"); if (!el) return;
  const ok = r.test_ok, hint = r.test_hint || (ok ? "ok" : "down");
  const next = {
    down: "👉 Nächster Schritt: Ollama-App starten (bzw. Server-Adresse prüfen), dann erneut testen.",
    nourl: "👉 Trag oben eine Server-Adresse ein.",
    nokey: "👉 Trag oben deinen API-Key ein und speichere.",
    auth: "👉 Der Key stimmt nicht — kopiere ihn frisch aus dem Anbieter-Konto.",
    cloud: "👉 Falls Kimi & Co. nicht antworten: einmal <code>ollama signin</code> im Terminal.",
    ok: "",
  }[hint];
  const color = ok ? "var(--accent)" : "#e66";
  const dot = ok && hint !== "cloud" ? "🟢" : (hint === "cloud" ? "🟡" : "🔴");
  el.innerHTML = `<div style="color:${color}">${dot} ${esc(r.test_msg || "")}</div>`
    + (next ? `<div class="small" style="margin-top:3px;opacity:.85">${next}</div>` : "");
}

async function testProvider(id) {
  const el = $("#mp-" + id + "-status");
  if (el) el.innerHTML = '<span class="small">teste Verbindung…</span>';
  try { renderProviderStatus(id, await api("GET", "/api/models/" + id + "/test")); }
  catch (e) { if (el) el.innerHTML = `<div class="small" style="color:#e66">Test fehlgeschlagen: ${esc(e.message)}</div>`; }
}

async function loadModels() {
  const d = await api("GET", "/api/models");
  const p = d.providers;
  const ov = await api("GET", "/api/owner-verify");
  const ovCard = `<div class="agent-row" style="display:block;padding:12px 14px">
    <strong>🔎 Antwort-Prüfung (2. Modell)</strong>
    ${ov.enabled ? '<span class="pill">an</span>' : '<span class="pill">aus</span>'}
    <p class="small">Ein zweites Modell prüft jede Antwort im Haupt-Chat auf Fehler (Verwechslungen, Halluzinationen), bevor sie an dich geht — und korrigiert sie bei Bedarf. Kostet pro Antwort einen zusätzlichen Modell-Lauf → etwas langsamer und mehr Abo-Verbrauch.</p>
    <div style="max-width:320px;margin-top:4px">
      <label>Prüfer-Modell</label>
      <select id="ov-model">
        <option value="">Claude (Standard)</option>
        ${(d.models || []).filter(m => m.value !== "inherit").map(m => `<option value="${esc(m.value)}" ${ov.model === m.value ? 'selected' : ''}>${esc(m.label)}</option>`).join('')}
      </select>
    </div>
    <div style="display:flex;gap:10px;align-items:center;margin-top:8px">
      <label class="switch"><input type="checkbox" id="ov-en" ${ov.enabled ? "checked" : ""}> Prüfung aktiv</label>
      <button class="primary" onclick="saveOwnerVerify()">Speichern</button>
    </div></div>`;
  const card = (id, label, needsKey) => `
    <div class="agent-row" style="display:block;padding:12px 14px">
      <strong>${label}</strong>
      ${p[id].enabled ? '<span class="pill">aktiv</span>' : '<span class="pill">aus</span>'}
      ${needsKey ? (p[id].has_key ? '<span class="pill">Key ✓</span>' : '<span class="pill">kein Key</span>') : '<span class="pill">ohne Key</span>'}
      ${providerSteps(id)}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">
        <div><label>Server-Adresse</label><input id="mp-${id}-url" value="${esc(p[id].base_url)}"></div>
        <div><label>Modelle (mit Komma trennen)</label><input id="mp-${id}-models" value="${esc((p[id].models || []).join(', '))}" placeholder="${id === 'ollama' ? 'kimi-k2.7-code:cloud' : 'z. B. gpt-4o'}"></div>
      </div>
      ${needsKey ? `<label>API-Key ${p[id].has_key ? '(leer lassen = unverändert)' : ''}</label><input type="password" id="mp-${id}-key" autocomplete="new-password">` : ""}
      <div style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap">
        <label class="switch"><input type="checkbox" id="mp-${id}-en" ${p[id].enabled ? "checked" : ""}> aktiv</label>
        <button class="primary" onclick="saveProvider('${id}')">Speichern &amp; testen</button>
        <button class="ghost" onclick="testProvider('${id}')">Nur Verbindung testen</button>
        <button class="ghost" onclick="delProvider('${id}')">Entfernen</button>
      </div>
      <div id="mp-${id}-status" class="mp-status" style="margin-top:8px"></div>
    </div>`;
  const fb = p.anthropic_fallback;
  $("#models-content").innerHTML =
    ovCard
    + card("ollama", "🖥️ Ollama (lokal, privat)", false)
    + card("openai", "OpenAI / ChatGPT", true)
    + card("azure", "Azure AI Foundry", true)
    + `<div class="agent-row" style="display:block;padding:12px 14px">
        <strong>🔑 Claude-API-Key als Reserve</strong>
        ${fb.enabled ? '<span class="pill">aktiv</span>' : '<span class="pill">aus</span>'} ${fb.has_key ? '<span class="pill">Key ✓</span>' : ''}
        <p class="small">Springt automatisch ein, wenn dein Claude-Abo gerade am Limit ist — mit Hinweis im Chat. Kostet dann echtes Geld pro Nachricht (Anthropic-API).</p>
        <label>Anthropic-API-Key (leer lassen = unverändert)</label>
        <input type="password" id="mp-fb-key" autocomplete="new-password">
        <div style="display:flex;gap:10px;align-items:center;margin-top:8px">
          <label class="switch"><input type="checkbox" id="mp-fb-en" ${fb.enabled ? "checked" : ""}> Reserve aktiv</label>
          <button class="primary" onclick="saveFallback()">Speichern</button>
        </div></div>`;
  // Aktive Provider gleich live prüfen → Ampel zeigt Status ohne Klick.
  ["ollama", "openai", "azure"].filter(id => p[id].enabled).forEach(testProvider);
}

async function saveProvider(id) {
  const body = { base_url: $("#mp-" + id + "-url").value.trim(), enabled: $("#mp-" + id + "-en").checked,
    models: $("#mp-" + id + "-models").value.split(",").map((x) => x.trim()).filter(Boolean) };
  const k = $("#mp-" + id + "-key"); if (k && k.value) body.key = k.value;
  const st = $("#mp-" + id + "-status"); if (st) st.innerHTML = '<span class="small">speichere & teste…</span>';
  try {
    const r = await api("PUT", "/api/models/" + id, body);
    renderProviderStatus(id, r);
    toast("Gespeichert");
  } catch (e) { if (st) st.innerHTML = ""; toast(friendlyError(e), 1); }
}
async function delProvider(id) {
  if (!confirm("Provider „" + id + "\" entfernen (inkl. Key)?")) return;
  try { await api("DELETE", "/api/models/" + id); toast("Entfernt"); loadModels(); } catch (e) { toast(friendlyError(e), 1); }
}
async function saveFallback() {
  const body = { enabled: $("#mp-fb-en").checked };
  const k = $("#mp-fb-key"); if (k && k.value) body.key = k.value;
  try { await api("PUT", "/api/models/anthropic-fallback", body); toast("Gespeichert"); loadModels(); } catch (e) { toast(friendlyError(e), 1); }
}

async function saveOwnerVerify() {
  const body = { enabled: $("#ov-en").checked, model: $("#ov-model").value || null };
  try { await api("PUT", "/api/owner-verify", body); toast(body.enabled ? "Prüfung aktiv" : "Prüfung aus"); loadModels(); } catch (e) { toast(friendlyError(e), 1); }
}

async function loadN8n() {
  const s = await api("GET", "/api/n8n/status");
  $("#n8n-content").innerHTML = s.configured
    ? `<p>✅ Verbunden mit <strong>${esc(s.url)}</strong> — frag deinen Operator z. B.:
       <em>„Welche n8n-Workflows sind aktiv?" oder „Warum ist der letzte Lauf fehlgeschlagen?"</em></p>
       <button class="danger" onclick="n8nDisconnect()">Trennen (Key löschen)</button>`
    : `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div><label>Server-Adresse</label><input type="text" id="n8n-url" placeholder="https://n8n.meinserver.de"></div>
        <div><label>API-Key</label><input type="password" id="n8n-key"></div></div>
      <button class="primary" onclick="n8nSave()">Verbinden & testen</button>`;
}
async function n8nSave() {
  try {
    await api("PUT", "/api/n8n/config", { url: $("#n8n-url").value, api_key: $("#n8n-key").value });
    toast("n8n verbunden ✓ — dein Operator kann es ab der nächsten Nachricht nutzen");
    loadN8n();
  } catch (e) { toast(friendlyError(e), 1); }
}
async function n8nDisconnect() {
  if (!confirm("n8n-Verbindung trennen und API-Key löschen?")) return;
  try { await api("DELETE", "/api/n8n"); toast("Getrennt"); loadN8n(); } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- System: Backup + MCP (B1/B2) ---------- */
async function loadSystem() {
  await loadN8n();
  await loadModels();
  const b = await api("GET", "/api/backups");
  $("#backup-list").innerHTML = b.backups.map((x) => `
    <div class="agent-row" style="padding:8px 14px">
      <div class="mono small">${esc(x.name)} · ${(x.size / 1e6).toFixed(1)} MB · ${esc(x.ts)}</div>
      <button class="ghost" onclick="restoreBackup('${x.name}')">Wiederherstellen</button>
    </div>`).join("") || "<p class='hint'>Noch keine Backups.</p>";
  const m = await api("GET", "/api/mcp");
  const cat = await api("GET", "/api/mcp/catalog");
  const inst = new Set(cat.installed);
  const catHtml = cat.catalog.map((c) => inst.has(c.id) ? `
    <div class="agent-row" style="display:block;padding:10px 14px">
      <strong>${c.emoji} ${esc(c.label)}</strong> <span class="pill">eingerichtet</span>
    </div>` : `
    <div class="agent-row" style="display:block;padding:10px 14px">
      <strong>${c.emoji} ${esc(c.label)}</strong>
      <a href="${esc(c.homepage)}" target="_blank" rel="noopener" class="small">Doku ↗</a>
      <p class="small" style="margin:4px 0">${esc(c.desc)}</p>
      <p class="hint" style="margin:4px 0">${esc(c.setup)}</p>
      <div style="display:grid;gap:6px;margin:8px 0">
        ${c.fields.map((fl) => `<div><label class="small">${esc(fl.label)}</label>
          <input id="cat-${c.id}-${fl.key}" type="${fl.secret ? 'password' : 'text'}"
                 ${fl.secret ? 'autocomplete="new-password"' : ''} value="${esc(fl.default || '')}"></div>`).join("")}
      </div>
      <button class="primary" onclick="addCatalogMcp('${c.id}')">Einrichten</button>
      <span id="cat-${c.id}-status" class="small"></span>
    </div>`).join("");
  const listHtml = m.servers.map((s) => `
    <div class="agent-row" style="padding:8px 14px">
      <div><strong>${esc(s.name)}</strong> <span class="pill">${esc(s.transport)}</span>
        <span class="mono small">${esc(s.command || s.url)}</span></div>
      <button class="danger" onclick="deleteMcp('${s.name}')">Entfernen</button>
    </div>`).join("") || "<p class='hint'>Keine MCP-Server konfiguriert.</p>";
  $("#mcp-list").innerHTML =
    `<p class="small" style="margin:2px 0 6px"><strong>Empfohlene Integrationen</strong> — geprüfte Server. Sie laufen mit deinen Rechten; Zugangsdaten landen in der lokalen <span class="mono">.mcp.json</span>.</p>`
    + catHtml
    + `<p class="small" style="margin:14px 0 6px"><strong>Eingerichtete MCP-Server</strong></p>`
    + listHtml;
}
async function addCatalogMcp(id) {
  const st = $("#cat-" + id + "-status");
  const inputs = document.querySelectorAll(`[id^="cat-${id}-"]`);
  const fields = {};
  inputs.forEach((el) => { if (el.tagName === "INPUT") fields[el.id.replace("cat-" + id + "-", "")] = el.value; });
  if (st) st.textContent = "…";
  try { await api("POST", "/api/mcp/catalog/" + id, { fields }); toast("Eingerichtet"); loadSystem(); }
  catch (e) { if (st) st.textContent = ""; toast(friendlyError(e), 1); }
}
async function createBackup() {
  try { const r = await api("POST", "/api/backup"); toast(`Backup erstellt: ${r.name} (${(r.size / 1e6).toFixed(1)} MB)`); loadSystem(); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function restoreBackup(name) {
  if (!confirm(`Backup "${name}" zur Prüfung entpacken? (Überschreibt nichts automatisch)`)) return;
  try { const r = await api("POST", "/api/backup/restore", { name }); toast("Entpackt nach: " + r.dest); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function addMcp() {
  const name = $("#mcp-name").value.trim(), target = $("#mcp-target").value.trim();
  if (!name || !target) return toast("Name und Kommando/URL angeben", 1);
  if (!confirm(`⚠️ MCP-Server "${name}" wird mit deinen Rechten ausgeführt und steht dem Operator als Werkzeug zur Verfügung. Vertraust du der Quelle?`)) return;
  const payload = target.startsWith("http")
    ? { name, url: target }
    : { name, command: target.split(" ")[0], args: target.split(" ").slice(1) };
  try { await api("POST", "/api/mcp", payload); toast("Hinzugefügt"); $("#mcp-name").value = ""; $("#mcp-target").value = ""; loadSystem(); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function deleteMcp(name) {
  try { await api("DELETE", "/api/mcp/" + name); loadSystem(); } catch (e) { toast(friendlyError(e), 1); }
}

/* ---------- Verhalten & Datenschutz ---------- */
async function loadVerhalten() { $("#verhalten-text").value = (await api("GET", "/api/verhalten")).content; }

/* ---------- Persona & Profil ---------- */
async function loadPersona() {
  const d = await api("GET", "/api/persona");
  const p = d.persona, pr = d.profile, o = d.options;
  const opt = (id, arr, val) => { $(id).innerHTML = arr.map((x) => `<option ${x === val ? "selected" : ""}>${x}</option>`).join(""); };
  $("#pn-name").value = p.name || "";
  opt("#pn-gender", o.gender_presentation, p.gender_presentation);
  opt("#pn-tone", o.tone, p.tone);
  opt("#pn-formality", o.formality, p.formality);
  opt("#pn-humor", o.humor, p.humor);
  opt("#pn-verbosity", o.verbosity, p.verbosity);
  $("#pn-emoji").checked = !!p.emoji;
  $("#pn-soul").value = p.soul || "";
  $("#pf-name").value = pr.preferred_name || ""; $("#pf-pronouns").value = pr.pronouns || "";
  $("#pf-role").value = pr.role || ""; $("#pf-language").value = pr.language || "";
  $("#pf-work").value = pr.work_context || ""; $("#pf-interests").value = (pr.interests || []).join(", ");
  $("#pf-comm").value = pr.comm_prefs || ""; $("#pf-boundaries").value = (pr.boundaries || []).join(", ");
  $("#pn-preview").textContent = d.preview || "(noch nichts gesetzt)";
}
async function savePersona() {
  const body = { name: $("#pn-name").value, gender_presentation: $("#pn-gender").value,
    tone: $("#pn-tone").value, formality: $("#pn-formality").value, humor: $("#pn-humor").value,
    verbosity: $("#pn-verbosity").value, emoji: $("#pn-emoji").checked, soul: $("#pn-soul").value };
  try { const r = await api("PUT", "/api/persona", body); $("#pn-preview").textContent = r.preview || ""; toast("Persona gespeichert — wirkt ab der nächsten Nachricht"); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function saveProfil() {
  const body = { preferred_name: $("#pf-name").value, pronouns: $("#pf-pronouns").value,
    role: $("#pf-role").value, language: $("#pf-language").value, work_context: $("#pf-work").value,
    interests: $("#pf-interests").value, comm_prefs: $("#pf-comm").value, boundaries: $("#pf-boundaries").value };
  try { const r = await api("PUT", "/api/profil", body); $("#pn-preview").textContent = r.preview || "(noch nichts gesetzt)"; toast("Profil gespeichert"); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function deleteProfil() {
  if (!confirm("Dein Profil wirklich löschen? Der Operator vergisst dann diese Angaben über dich.")) return;
  try { const r = await api("DELETE", "/api/profil"); $("#pn-preview").textContent = r.preview || "(noch nichts gesetzt)"; loadPersona(); toast("Profil gelöscht"); }
  catch (e) { toast(friendlyError(e), 1); }
}
async function saveVerhalten() {
  try { await api("PUT", "/api/verhalten", { content: $("#verhalten-text").value }); toast("Gespeichert — wirkt ab der nächsten Nachricht"); }
  catch (e) { toast(friendlyError(e), 1); }
}
const PII_MODES = [
  ["standard", "Sicher & genau (empfohlen)", "Namen, Orte, Firmen + E-Mail/Telefon/IBAN werden durch realistische Platzhalter ersetzt. Bester Kompromiss."],
  ["strict", "Streng", "Zusätzlich Datumsangaben. Maximaler Schutz, kann Antworten minimal ungenauer machen."],
  ["structured", "Nur Kontaktdaten", "Nur E-Mail, Telefon, IBAN, Kreditkarte, IP — Namen bleiben. Geringster Schutz."],
];
async function loadPseudonymize() {
  const p = await api("GET", "/api/pseudonymize");
  const st = p.last?.stats || {};
  const total = Object.values(st).reduce((a, b) => a + b, 0);
  $("#pii-box").innerHTML = `
    <div class="card">
      <div class="row-between"><h2>🕵 Pseudonymisierung ${p.enabled ? '<span class="pill" style="color:var(--accent)">AN</span>' : '<span class="pill" style="color:var(--amber)">AUS</span>'}</h2>
        <label class="switch"><input type="checkbox" id="pii-enabled" ${p.enabled ? "checked" : ""} onchange="savePii()">aktiv</label></div>
      <p class="hint">Bevor eine Nachricht an Claude geht, ersetzt dein Operator alle echten
      Namen, E-Mail-Adressen, Telefonnummern usw. durch <strong>realistische Platzhalter</strong>.
      Anthropic sieht so <strong>nie deine echten Personendaten</strong>. In der Antwort werden
      die echten Werte automatisch wieder eingesetzt — du merkst also nichts davon.
      ${p.presidio_ready ? "" : '<br><span style="color:var(--amber)">⚠ Dienst nicht installiert — läuft erst nach der Einrichtung.</span>'}</p>
      <label>Schutzstufe</label>
      <select id="pii-mode" onchange="savePii()">
        ${PII_MODES.map(([v, t]) => `<option value="${v}" ${p.mode === v ? "selected" : ""}>${t}</option>`).join("")}</select>
      <p class="small">${PII_MODES.find((m) => m[0] === p.mode)?.[2] || ""}</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
        <div><label>Das bin ich (bleibt im Klartext — je Zeile ein Name/eine Mail)</label>
          <textarea id="pii-allow" rows="3" class="mono">${esc((p.allow || []).join("\n"))}</textarea></div>
        <div><label>Immer ersetzen (Namen, die der Automatik durchrutschen)</label>
          <textarea id="pii-deny" rows="3" class="mono">${esc((p.deny || []).join("\n"))}</textarea></div>
      </div>
      <button class="primary" onclick="savePii(true)">Speichern</button>
      ${total ? `<p class="hint" style="margin-top:10px">Letzte Nachricht: ${Object.entries(st).map(([k, v]) => `${v}× ${PII_LABEL[k] || k}`).join(", ")} pseudonymisiert (${esc(p.last.ts || "")}).</p>` : ""}
    </div>`;
}
const PII_LABEL = {PERSON: "Name", EMAIL_ADDRESS: "E-Mail", PHONE_NUMBER: "Telefon",
  IBAN_CODE: "IBAN", LOCATION: "Ort", ORGANIZATION: "Firma", DATE_TIME: "Datum",
  CREDIT_CARD: "Kreditkarte", IP_ADDRESS: "IP"};
async function savePii(withLists) {
  const body = {enabled: $("#pii-enabled").checked, mode: $("#pii-mode").value};
  if (withLists) {
    body.allow = $("#pii-allow").value.split("\n").map((x) => x.trim()).filter(Boolean);
    body.deny = $("#pii-deny").value.split("\n").map((x) => x.trim()).filter(Boolean);
  }
  try { await api("PUT", "/api/pseudonymize", body); toast("Gespeichert — wirkt ab der nächsten Nachricht"); if (withLists) loadPseudonymize(); }
  catch (e) { toast(friendlyError(e), 1); }
}

// #18: Aufbewahrung — was liegt hier, wie lange noch, und wie werde ich es los?
function renderRetention() {
  const a = (STATUS && STATUS.aufbewahrung) || {};
  const box = $("#retention-list");
  if (!box) return;
  if (!a.daten) { box.innerHTML = "<p class='small'>Noch keine Angaben.</p>"; return; }
  box.innerHTML = `<table class="kv">${a.daten.map(d => `<tr>
      <td>${d.name}<div class="small muted">${d.datei}</div></td>
      <td>${d.kb} KB${d.eintraege !== undefined ? ` · ${d.eintraege} Einträge` : ""}</td>
      <td class="small">wird nach ${d.frist_tage} Tagen gelöscht${
        d.aeltester_tage !== null && d.aeltester_tage !== undefined
          ? ` · ältester Eintrag: ${d.aeltester_tage} Tage` : ""}</td>
    </tr>`).join("")}</table>`;
  const l = $("#retention-last");
  if (l) l.textContent = a.letzter_lauf
    ? "Zuletzt aufgeräumt: " + new Date(a.letzter_lauf * 1000).toLocaleString("de-DE")
    : "Noch nie aufgeräumt — läuft automatisch einmal täglich.";
}

async function retentionNow() {
  try {
    const r = await api("POST", "/api/aufbewahrung/aufraeumen");
    const e = r.ergebnis || {};
    toast(`Aufgeräumt: ${e.sessions || 0} Gesprächsrunden, ${
      (e.log_zeilen || 0) + (e.audit_zeilen || 0)} Protokollzeilen entfernt.`);
    await loadStatus(); renderRetention();
  } catch (e) { toast(friendlyError(e)); }
}

async function retentionExport() {
  try {
    const d = await api("POST", "/api/aufbewahrung/export");
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "operator-meine-daten.json";
    a.click(); URL.revokeObjectURL(a.href);
    toast("Deine Daten wurden als Datei heruntergeladen.");
  } catch (e) { toast(friendlyError(e)); }
}

async function retentionWipe() {
  if (!confirm("Wirklich den kompletten Gesprächsverlauf löschen?\n\n" +
               "Der Operator vergisst damit alle bisherigen Unterhaltungen. " +
               "Das lässt sich nicht rückgängig machen.")) return;
  try {
    const r = await api("POST", "/api/aufbewahrung/loeschen");
    toast(`${r.geloescht} Gesprächsrunden gelöscht.`);
    await loadStatus(); renderRetention();
  } catch (e) { toast(friendlyError(e)); }
}

async function loadPrivacy() {
  renderRetention();
  const s = STATUS || await api("GET", "/api/status");
  await loadPseudonymize();
  $("#privacy-tables").innerHTML = `<div class="card"><table class="kv">
    <tr><td>Chat-Verarbeitung</td><td>Nachrichten werden zur Beantwortung an die Claude-API (Anthropic) übertragen — über dein persönliches Abo</td></tr>
    <tr><td>Bilder &amp; Dateien</td><td>Was du im Chat schickst, landet in deinem Arbeitsordner unter <span class="mono">eingang/</span> — nur für dich lesbar, und es wird nach derselben Frist gelöscht wie dein Gesprächsverlauf. <strong>Der Inhalt einer Datei wird nicht pseudonymisiert</strong>: Ein Foto geht so zum Modell, wie es ist.</td></tr>
    <tr><td>Gedächtnis</td><td>${s.memory_count} Fakten, lokal in <span class="mono">~/.claude/matrix-bot/memory.db</span> — verlässt deinen Mac nie</td></tr>
    <tr><td>Persona</td><td>wie dein Operator auftritt, lokal in <span class="mono">persona.json</span> — kein Personenbezug; im Tab »🎭 Persona« änderbar</td></tr>
    <tr><td>Dein Profil</td><td>deine Angaben (Ansprache, Rolle, Interessen), lokal in <span class="mono">profile.json</span> — verlässt deinen Mac nie, im Tab »🎭 Persona« jederzeit <strong>löschbar</strong></td></tr>
    <tr><td>Matrix-Zugangsdaten</td><td>im macOS-Schlüsselbund (nicht mehr als Klartext-Datei)</td></tr>
    <tr><td>OAuth-Tokens (Google/M365)</td><td>AES-256-verschlüsselt in <span class="mono">secrets/</span>, Schlüssel im macOS-Schlüsselbund</td></tr>
    <tr><td>Microsoft 365</td><td>${s.m365.connected ? "Aktive Rechte: <span class='mono'>" + esc(s.m365.active_values.join(", ")) + "</span>" : "nicht verbunden — keine Daten"}</td></tr>
    <tr><td>Google Drive</td><td>${s.google.connected ? "Scope: <span class='mono'>" + esc(s.google.scopes.join(", ")) + "</span> (" + esc(s.google.connected_email) + ")" : "nicht verbunden — keine Daten"}</td></tr>
    <tr><td>Telemetrie</td><td>keine. Es gibt keinen Hersteller-Server.</td></tr>
    <tr><td>Löschung (Art. 17)</td><td><span class="mono">bash install.sh --uninstall</span> entfernt Dienste, widerruft Tokens und löscht auf Wunsch alle Daten</td></tr>
  </table></div>`;
}

/* ---------- Init ---------- */
async function refresh() {
  const active = document.querySelector("nav button.active").dataset.tab;
  try {
    if (active === "overview") await loadStatus();
    if (active === "agents") await loadAgents();
    if (active === "assistant") asstGreet();
    if (active === "skills") await loadSkills();
    if (active === "vault") await loadVault();
    if (active === "sessions") await loadSessions();
    if (active === "cron") { await loadCron(); await loadTriggers(); }
    if (active === "usage") await loadUsage();
    if (active === "memory") await loadMemory();
    if (active === "m365") await loadM365();
    if (active === "google") await loadGoogle();
    if (active === "logs") await loadLogs();
    if (active === "system") await loadSystem();
    if (active === "verhalten") await loadVerhalten();
    if (active === "persona") await loadPersona();
    if (active === "privacy") await loadPrivacy();
  } catch (e) {
    if (String(e.message).includes("Dashboard-Token")) {
      // Gespeicherter Token ungültig (z. B. nach Neuinstallation) → verwerfen + freundlich anleiten
      try { _store.removeItem("op_token"); sessionStorage.removeItem("op_token"); } catch (x) {}
      document.body.innerHTML = "<main><div class='card' style='max-width:600px;margin:10vh auto'>" +
        "<h2>🔒 Dashboard entsperren</h2>" +
        "<p class='hint'>Aus Sicherheitsgründen musst du dieses Dashboard einmal freischalten — danach merkt sich dein Browser den Zugang dauerhaft und du kommst direkt rein.</p>" +
        "<div style='border:1px solid var(--accent);border-radius:10px;padding:14px 16px;margin:14px 0'>" +
          "<div style='color:var(--accent);font-weight:600;margin-bottom:6px'>✅ Am einfachsten — über den Chat</div>" +
          "<ol class='small' style='margin:0;padding-left:18px;line-height:1.7'>" +
            "<li>Öffne den Chat mit deinem Operator im Matrix (Element-App).</li>" +
            "<li>Schreib ihm einfach <strong>»dashboard«</strong>.</li>" +
            "<li>Er antwortet mit einem <strong>Ein-Klick-Link</strong> — antippen, fertig. Kein Terminal, kein Tippen.</li>" +
          "</ol>" +
          "<p class='small' style='margin:8px 0 0;opacity:.8'>Der Link gilt 10 Minuten und wird beim ersten Klick verbraucht — sicher, auch wenn jemand später den Chat liest.</p>" +
        "</div>" +
        "<details><summary class='small' style='cursor:pointer;opacity:.85'>Lieber am Rechner? Terminal-Weg anzeigen</summary>" +
          `<p class='small' style='margin:8px 0 4px'>Gib auf dem ${geraeteName()}, auf dem der Operator läuft, ${terminalName()} ein:</p>` +
          "<pre class='mono' style='user-select:all;padding:12px'>operator</pre>" +
          "<p class='small' style='opacity:.8'>Öffnet dieses Dashboard automatisch mit Zugang. Falls »command not found«: der Operator ist auf diesem Rechner nicht installiert — nutz den Chat-Weg oben.</p>" +
        "</details></div></main>";
    } else toast(friendlyError(e), 1);
  }
}
loadStatus().catch(() => refresh());
refresh();
setInterval(() => { if (document.querySelector("nav button.active").dataset.tab === "overview") loadStatus().catch(() => {}); }, 15000);

/* ---------- Einrichtungs-Assistent ---------- */
let ASST = [];              // Gesprächsverlauf {role, content}
let ASST_AUTO = 0;          // Zähler gegen Aktions-Endlosschleifen pro Nutzer-Nachricht
const ASST_READ_ACTIONS = ["test_provider"];   // »Lesen frei« → ohne Bestätigung

async function asstGreet() {
  if (ASST.length || ASST_PENDING) { asstRender(); return; }
  // Erst-Einrichtung: nur wenn Persona noch nicht gesetzt UND Willkommen noch nicht gezeigt.
  let onboarded = true;
  try { onboarded = !!(await api("GET", "/api/persona")).persona.onboarded; } catch (e) {}
  let welcomed = false;
  try { welcomed = localStorage.getItem("op_welcomed") === "1"; } catch (e) {}
  if (!onboarded && !welcomed) { asstOnboardStart(); return; }
  ASST.push({ role: "assistant", content: "Hi 👋 Ich bin dein Einrichtungs-Assistent. "
    + "Sag z. B. »prüf ob Kimi läuft«, »veröffentliche coder« oder »richte meine Persona ein«." });
  asstRender();
}
function asstReset() { ASST = []; ASST_PENDING = null; asstGreet(); }

/* ---------- Onboarding-Interview (Erst-Einrichtung, überspringbar) ---------- */
const ONB_STEPS = [
  { key: "pf_name", q: "Schön, dass du da bist! 👋 Wie soll ich dich ansprechen?", type: "text", ph: "z. B. Michi" },
  { key: "formality", q: "Sollen wir per Du oder per Sie?", type: "pick", opts: ["du", "Sie"] },
  { key: "gender", q: "Wie soll ich auftreten?", type: "pick", opts: ["neutral", "androgyn", "weiblich", "männlich"] },
  { key: "tone", q: "Welcher Ton passt zu dir?", type: "pick", opts: ["freundlich", "professionell", "locker", "humorvoll", "direkt"] },
  { key: "role", q: "Woran arbeitest du gerade? (hilft mir, dich besser zu unterstützen — optional)", type: "text", ph: "z. B. Gründer", optional: true },
];
let ONB = {};
function asstOnboardStart() {
  ONB = {};
  const l = $("#asst-log");
  l.innerHTML = `<div style="align-self:flex-start;max-width:88%">
    <div class="card" style="border-color:var(--accent);padding:12px 14px">
      <strong>Willkommen bei deinem Operator 🎭</strong>
      <p class="small" style="margin:6px 0">Magst du mich in 5 kurzen Fragen einrichten? Dauert ~1 Minute,
        du kannst jederzeit überspringen und alles später im Tab »🎭 Persona« ändern.</p>
      <div style="display:flex;gap:8px"><button class="primary" data-x="go">Los geht's</button>
        <button class="ghost" data-x="skip">Überspringen</button></div></div></div>`;
  l.querySelector('[data-x="go"]').onclick = () => asstOnboardStep(0);
  l.querySelector('[data-x="skip"]').onclick = () => asstOnboardSkip();
}
function asstOnboardSkip() {
  try { localStorage.setItem("op_welcomed", "1"); } catch (e) {}
  ASST = [{ role: "assistant", content: "Alles gut 🙂 Du kannst mich jederzeit im Tab »🎭 Persona« einrichten. "
    + "Womit kann ich helfen?" }];
  asstRender();
}
function asstOnboardStep(i) {
  if (i >= ONB_STEPS.length) return asstOnboardFinish();
  const s = ONB_STEPS[i], l = $("#asst-log");
  const ctrl = s.type === "pick"
    ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${s.opts.map((o) =>
        `<button class="ghost" data-opt="${esc(o)}">${esc(o)}</button>`).join("")}</div>`
    : `<div style="display:flex;gap:8px;margin-top:8px"><input id="onb-in" placeholder="${esc(s.ph || "")}" style="flex:1"
        onkeydown="if(event.key==='Enter')document.querySelector('[data-x=next]').click()">
        <button class="primary" data-x="next">Weiter</button></div>`;
  l.innerHTML = `<div style="align-self:flex-start;max-width:88%">
    <div class="card" style="border-color:var(--accent);padding:12px 14px">
      <div class="small" style="opacity:.6">Schritt ${i + 1} von ${ONB_STEPS.length}</div>
      <div style="margin:4px 0"><strong>${esc(s.q)}</strong></div>${ctrl}
      ${s.optional ? '<button class="ghost small" data-x="skipstep" style="margin-top:6px">Überspringen</button>' : ""}
    </div></div>`;
  if (s.type === "pick") l.querySelectorAll("[data-opt]").forEach((b) => { b.onclick = () => { ONB[s.key] = b.dataset.opt; asstOnboardStep(i + 1); }; });
  else { l.querySelector('[data-x="next"]').onclick = () => { ONB[s.key] = $("#onb-in").value.trim(); asstOnboardStep(i + 1); }; setTimeout(() => { const el = $("#onb-in"); if (el) el.focus(); }, 30); }
  const sk = l.querySelector('[data-x="skipstep"]'); if (sk) sk.onclick = () => asstOnboardStep(i + 1);
}
async function asstOnboardFinish() {
  const persona = { formality: ONB.formality === "Sie" ? "sie" : "du",
    gender_presentation: ONB.gender || "neutral", tone: ONB.tone || "freundlich" };
  const profile = { preferred_name: ONB.pf_name || "", role: ONB.role || "" };
  try { localStorage.setItem("op_welcomed", "1"); } catch (e) {}
  try {
    await api("PUT", "/api/persona", persona);
    if (profile.preferred_name || profile.role) await api("PUT", "/api/profil", profile);
  } catch (e) { /* fail-open: Onboarding darf nie hängen */ }
  const anrede = ONB.pf_name ? ONB.pf_name : "";
  ASST = [{ role: "assistant", content: `Perfekt${anrede ? ", " + anrede : ""} — eingerichtet! 🎉 `
    + "Ich richte mich ab jetzt danach. Alles jederzeit änderbar im Tab »🎭 Persona«. Womit legen wir los?" }];
  asstRender();
  toast("Persona eingerichtet");
}
function asstPush(role, content) { ASST.push({ role, content }); asstRender(); }

function asstFmt(t) {
  return esc(t).replace(/```(\w*)\n?([\s\S]*?)```/g,
    '<pre class="mono" style="background:#0009;padding:8px;border-radius:6px;overflow-x:auto;margin:4px 0">$2</pre>');
}
let ASST_PENDING = null;    // ausstehende Bestätigungskarte {name, args} — Teil des Renders,
                            // damit kein Re-Render sie wegwischt
function asstRender() {
  const l = $("#asst-log"); if (!l) return;
  l.innerHTML = ASST.map((m) => {
    const mine = m.role === "user", sys = m.role === "tool" || m.role === "system";
    const align = mine ? "flex-end" : "flex-start";
    const label = mine ? "Du" : sys ? "System" : "Assistent";
    const bg = mine ? "rgba(0,201,46,.16)" : sys ? "rgba(255,255,255,.03)" : "rgba(255,255,255,.05)";
    return `<div style="align-self:${align};max-width:84%">
      <div class="small" style="opacity:.5;margin-bottom:2px">${label}</div>
      <div style="background:${bg};border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:8px 11px">${asstFmt(m.content)}</div></div>`;
  }).join("");
  if (ASST_PENDING) {
    const { name, args } = ASST_PENDING;
    const div = document.createElement("div");
    div.style.cssText = "align-self:flex-start;max-width:84%";
    div.innerHTML = `<div class="card" style="border-color:var(--accent);padding:10px 12px">
      <div class="small" style="opacity:.7">Der Assistent möchte ausführen:</div>
      <div style="margin:5px 0"><strong>${esc(asstActionLabel(name, args))}</strong></div>
      <div style="display:flex;gap:8px"><button class="primary" data-x="ok">Ausführen</button>
        <button class="ghost" data-x="no">Ablehnen</button></div></div>`;
    div.querySelector('[data-x="ok"]').onclick = async () => { ASST_PENDING = null; await asstExec(name, args); };
    div.querySelector('[data-x="no"]').onclick = () => { ASST_PENDING = null; asstPush("system", "Aktion abgelehnt."); asstTurn(); };
    l.appendChild(div);
  }
  l.scrollTop = l.scrollHeight;
}

async function asstSend() {
  const inp = $("#asst-input"); const text = (inp.value || "").trim(); if (!text) return;
  inp.value = ""; ASST_AUTO = 0; asstPush("user", text); await asstTurn();
}
async function asstTurn() {
  const btn = $("#asst-send"); if (btn) btn.disabled = true;
  asstPush("assistant", "…denkt nach…");
  const msgs = ASST.filter((m) => m.content !== "…denkt nach…");
  try {
    const r = await api("POST", "/api/assistant", { messages: msgs });
    ASST = ASST.filter((m) => m.content !== "…denkt nach…");
    asstPush("assistant", r.reply || "(keine Antwort)");
    if (r.action && r.action.action) await asstHandleAction(r.action);
  } catch (e) {
    ASST = ASST.filter((m) => m.content !== "…denkt nach…");
    asstPush("system", "Fehler: " + e.message);
  } finally { if (btn) btn.disabled = false; asstRender(); }
}

function asstActionLabel(name, a) {
  return ({
    test_provider: `Verbindung testen: ${a.provider}`,
    set_provider: `Provider konfigurieren: ${a.provider}`,
    publish_agent: `Als Bot veröffentlichen: ${a.name}`,
    unpublish_agent: `Bot entfernen: ${a.name}`,
    restart_listener: "Listener-Dienst neu starten",
  })[name] || name;
}
async function asstHandleAction(action) {
  if (ASST_AUTO++ > 6) { asstPush("system", "(Aktions-Limit erreicht — bitte weiter per Nachricht.)"); return; }
  const { action: name, args = {} } = action;
  if (ASST_READ_ACTIONS.includes(name)) { await asstExec(name, args); return; }  // Lesen frei
  ASST_PENDING = { name, args };   // Schreibend → Bestätigungskarte (übersteht Re-Render)
  asstRender();
}
async function asstExec(name, args) {
  try {
    let result;
    if (name === "test_provider") {
      const d = await api("GET", "/api/models/" + encodeURIComponent(args.provider) + "/test");
      result = `Test ${args.provider}: ${d.test_ok ? "OK" : "FEHLER"} — ${d.test_msg}`;
    } else if (name === "set_provider") {
      const body = {}; ["base_url", "models", "enabled", "default"].forEach((k) => { if (args[k] !== undefined) body[k] = args[k]; });
      const d = await api("PUT", "/api/models/" + encodeURIComponent(args.provider), body);
      result = `Provider ${args.provider} gespeichert. Test: ${d.test_ok ? "OK" : "FEHLER"} — ${d.test_msg}`;
    } else if (name === "unpublish_agent") {
      await api("DELETE", "/api/agents/" + encodeURIComponent(args.name) + "/publish");
      result = `Bot ${args.name} entfernt.`;
    } else if (name === "restart_listener") {
      await api("POST", "/api/listener/restart");
      result = "Listener-Dienst neu gestartet.";
    } else if (name === "publish_agent") {
      const d = await api("POST", "/api/agents/" + encodeURIComponent(args.name) + "/publish", {});
      result = `»${args.name}« hat jetzt einen eigenen Chat-Raum (${d.room_id}). `
        + "In Element wartet die Einladung — annehmen, losschreiben. Kein Passwort nötig.";
      loadAgents();
    } else { result = "Unbekannte Aktion: " + name; }
    asstPush("tool", result);
    await asstTurn();
  } catch (e) { asstPush("system", "Aktion fehlgeschlagen: " + e.message); await asstTurn(); }
}

/* ---------- Matrix-Code-Regen (dezent, im Hintergrund) ---------- */
(function rain() {
  const cv = document.getElementById("rain");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  // Bewusst nur Zeichen, die jeder lesen kann. Vorher standen hier japanische Katakana
  // (Matrix-Film-Zitat) — die wirkten wie fremde Schriftzeichen unklarer Herkunft und
  // warfen im Dashboard eines Datenschutz-Produkts genau die falsche Frage auf.
  const CHARS = "01ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<>/\\{}[]()#$%&*+=|OPERATOR";
  let cols, drops;
  function size() {
    cv.width = innerWidth; cv.height = innerHeight;
    cols = Math.floor(cv.width / 18);
    drops = Array.from({ length: cols }, () => Math.random() * -50);
  }
  size();
  addEventListener("resize", size);
  setInterval(() => {
    ctx.fillStyle = "rgba(3, 9, 5, 0.12)";
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.fillStyle = "#00ff41";
    ctx.font = "15px monospace";
    for (let i = 0; i < cols; i++) {
      ctx.fillText(CHARS[Math.floor(Math.random() * CHARS.length)], i * 18, drops[i] * 18);
      if (drops[i] * 18 > cv.height && Math.random() > 0.975) drops[i] = 0;
      drops[i]++;
    }
  }, 66);
})();

/* ---------- Schutzraum-Karte (#104-A): ehrlich zeigen, ob die OS-Sandbox greift ---------- */
function renderSandbox() {
  const el = document.getElementById("sandbox-card");
  if (!el || !STATUS) return;
  const s = STATUS.sandbox || { an: false, grund: "unbekannt" };
  // Bewusst als Text zusammengebaut, damit der Grund-Text (kommt aus dem System)
  // nie als Markup interpretiert werden kann.
  el.textContent = "";
  const h = document.createElement("h2");
  h.textContent = s.an ? "🧱 Schutzraum aktiv" : "🧱 Schutzraum nicht verfügbar";
  const p = document.createElement("p");
  p.className = "small";
  p.textContent = s.an
    ? ("Dein Operator arbeitet in einem abgeschirmten Bereich: Er darf nur in seinem "
       + "Arbeitsordner Dateien anlegen und ändern. Alles andere auf deinem Rechner — "
       + "auch seine eigenen Sicherheitseinstellungen — ist für ihn schreibgeschützt, "
       + "und zwar vom Betriebssystem erzwungen, nicht nur per Regel. Lesen und "
       + "Internet bleiben normal möglich.")
    : ("Auf diesem Rechner steht kein Schutzraum zur Verfügung. Der Operator fragt "
       + "weiterhin bei riskanten Befehlen nach — aber ohne die zusätzliche Absicherung "
       + "durch das Betriebssystem.");
  const g = document.createElement("p");
  g.className = "small mono";
  g.textContent = s.grund || "";
  el.appendChild(h); el.appendChild(p); el.appendChild(g);
  if (!s.an) el.style.borderLeft = "3px solid var(--amber)";
  else el.style.borderLeft = "3px solid var(--green)";
}
