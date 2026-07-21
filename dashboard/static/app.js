/* Operator Dashboard — Frontend (build-frei, vanilla JS) */
const TOKEN = (location.hash.match(/t=([0-9a-f]+)/) || [])[1] || sessionStorage.getItem("op_token") || "";
if (TOKEN) { sessionStorage.setItem("op_token", TOKEN); history.replaceState(null, "", location.pathname); }

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
async function loadStatus() {
  STATUS = await api("GET", "/api/status");
  const badge = $("#listener-badge");
  badge.textContent = STATUS.listener_running ? "● Listener läuft" : "● Listener aus";
  badge.className = "badge " + (STATUS.listener_running ? "ok" : "err");
  $("#overview-tiles").innerHTML = `
    <div class="tile ${STATUS.listener_running ? "ok" : "err"}"><div class="k">${STATUS.listener_running ? "aktiv" : "aus"}</div><div class="l">Listener · <a href="#" onclick="restartListener();return false">neu starten</a></div></div>
    <div class="tile"><div class="k">${STATUS.agents.length}</div><div class="l">Agenten (${Object.keys(STATUS.published).length} veröffentlicht)</div></div>
    <div class="tile"><div class="k">${STATUS.memory_count}</div><div class="l">Fakten im Gedächtnis</div></div>
    <div class="tile ${STATUS.m365.connected ? "ok" : ""}"><div class="k">${STATUS.m365.connected ? "✓" : "—"}</div><div class="l">Microsoft 365</div></div>
    <div class="tile ${STATUS.google.connected ? "ok" : ""}"><div class="k">${STATUS.google.connected ? "✓" : "—"}</div><div class="l">Google Drive</div></div>`;
  const audit = await api("GET", "/api/audit?limit=12");
  $("#audit-list").innerHTML = audit.entries.reverse().map((e) =>
    `<div>${esc(e.ts)} · ${esc(e.actor)} · ${esc(e.action)} ${esc(e.target || "")} ${e.ok ? "" : "❌"}</div>`).join("") || "<div>Noch keine Einträge</div>";
}
async function restartListener() { try { await api("POST", "/api/listener/restart"); toast("Listener neu gestartet"); loadStatus(); } catch (e) { toast(e.message, 1); } }

/* ---------- Agenten ---------- */
const ALL_TOOLS = ["Bash", "Read", "Write", "WebFetch", "WebSearch", "Agent"];
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
  $("#agent-editor").classList.remove("hidden");
  $("#agent-editor").innerHTML = `
    <h2>${name ? "Agent bearbeiten: " + esc(name) : "Neuer Agent"}</h2>
    <label>Name (klein, a-z 0-9 -)</label>
    <input type="text" id="ag-name" value="${esc(a.name)}" ${name ? "disabled" : ""}>
    <label>Beschreibung (wann soll der Operator an diesen Agenten delegieren?)</label>
    <input type="text" id="ag-desc" value="${esc(a.description)}">
    <label>Sprachmodell</label>
    <select id="ag-model">${["haiku", "sonnet", "opus", "inherit"].map((m) =>
      `<option ${m === a.model ? "selected" : ""}>${m}</option>`).join("")}</select>
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
  } catch (e) { toast(e.message, 1); }
}

async function deleteAgent(name) {
  if (!confirm(`Agent "${name}" wirklich löschen?`)) return;
  try { await api("DELETE", "/api/agents/" + name); $("#agent-editor").classList.add("hidden"); toast("Gelöscht"); loadAgents(); }
  catch (e) { toast(e.message, 1); }
}

async function publishAgent(name) {
  const localpart = prompt("Matrix-Benutzername für den Bot:", name);
  if (!localpart) return;
  const admin_user = prompt("Admin-Benutzer deines Homeservers (für die Account-Anlage; leer lassen, wenn der Account schon existiert):", "");
  let admin_password = "", password = "";
  if (admin_user) admin_password = prompt("Admin-Passwort:") || "";
  else password = prompt("Passwort des bestehenden Bot-Accounts:") || "";
  try {
    const r = await api("POST", `/api/agents/${name}/publish`, { localpart, admin_user, admin_password, password });
    toast(`Veröffentlicht: ${r.user_id} — Einladung in deiner Matrix-App annehmen!`);
    loadAgents();
  } catch (e) { toast(e.message, 1); }
}

async function unpublishAgent(name) {
  if (!confirm(`Bot von "${name}" entfernen? Der Matrix-Zugang wird invalidiert.`)) return;
  try { await api("DELETE", `/api/agents/${name}/publish`); toast("Bot entfernt"); loadAgents(); }
  catch (e) { toast(e.message, 1); }
}

/* ---------- M365 ---------- */
const M365_SERVICES = [
  ["mail", "Mail", ""], ["calendar", "Kalender", ""], ["onedrive", "OneDrive", ""],
  ["sharepoint", "SharePoint", ""], ["planner", "Planner", ""],
  ["teams", "Teams", "Nur Basisdaten (Teams/Kanäle). Nachrichten lesen = von Microsoft geschützte API, Senden app-seitig nicht möglich."],
];
async function loadM365() {
  const s = await api("GET", "/api/m365/status");
  const c = $("#m365-content");
  if (!s.configured) {
    c.innerHTML = `<div class="card">
      <p class="hint">Einmalige Voraussetzung: Die Multi-Tenant-App „The Operator Setup" (Anleitung im Repo, Sprint 0). Danach hier die Client-ID eintragen:</p>
      <label>Setup-Client-ID (GUID)</label><input type="text" id="m365-cid" placeholder="xxxxxxxx-xxxx-…">
      <button class="primary" onclick="saveM365Cid()">Speichern</button></div>`;
    return;
  }
  const perms = s.permissions || {};
  c.innerHTML = `
    <div class="card">${s.connected
      ? `<p>✅ Verbunden mit Tenant <span class="mono">${esc(s.tenant_id)}</span> — Connector-App <span class="mono">${esc(s.app_client_id)}</span><br>
         <span class="small">Secret läuft ab: ${esc((s.secret_expires || "?").slice(0, 10))}</span></p>`
      : `<p class="hint">Noch nicht verbunden. Melde dich als M365-Admin an — die Connector-App wird danach automatisch in deinem Entra registriert.</p>
         <button class="primary" onclick="m365Login()">Als Admin anmelden</button>`}
    </div>
    <div class="card"><h2>Berechtigungen je Dienst</h2>
      ${M365_SERVICES.map(([k, label, note]) => {
        const p = perms[k] || { read: false, write: false };
        const noWrite = k === "teams";
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
      <p class="hint" style="margin-top:8px">Privacy by Default: Alles startet AUS. Schreiben schaltet Lesen mit ein. Regler AUS entzieht die Rechte auch im Entra wieder.</p>
    </div>`;
}
async function saveM365Cid() {
  try { await api("PUT", "/api/m365/setup-client", { client_id: $("#m365-cid").value.trim() }); toast("Gespeichert"); loadM365(); }
  catch (e) { toast(e.message, 1); }
}
async function m365Login() {
  try { const r = await api("POST", "/api/m365/auth/start"); window.open(r.auth_url, "_blank"); toast("Anmeldefenster geöffnet — danach hier fortfahren"); }
  catch (e) { toast(e.message, 1); }
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
  if (!connected) {
    const log = $("#m365-log");
    log.classList.remove("hidden");
    log.textContent = "Einrichtung läuft…\n";
    const r = await fetch("/api/m365/setup/run", {
      method: "POST",
      headers: { "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify({ permissions }),
    });
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      for (const line of buf.split("\n\n")) {
        if (!line.startsWith("data: ")) continue;
        try { const e = JSON.parse(line.slice(6)); log.textContent += `[${e.status}] ${e.step}: ${e.detail}\n`; } catch {}
      }
      buf = buf.slice(buf.lastIndexOf("\n\n") + 2);
    }
    loadM365();
  } else {
    try { const r = await api("PUT", "/api/m365/permissions", { permissions }); toast(`Rechte aktualisiert (+${r.added} / −${r.removed})`); loadM365(); }
    catch (e) { toast(e.message, 1); }
  }
}
async function m365Delete() {
  if (!confirm("Connector-App im Entra löschen und Verbindung trennen?")) return;
  try { await api("DELETE", "/api/m365"); toast("Getrennt"); loadM365(); } catch (e) { toast(e.message, 1); }
}

/* ---------- Google ---------- */
async function loadGoogle() {
  const s = await api("GET", "/api/google/status");
  const c = $("#google-content");
  if (!s.configured) {
    c.innerHTML = `<div class="card"><h2>Einmalige Einrichtung (dein eigener Google-Client)</h2>
      <p class="hint">Operator nutzt bewusst KEINEN zentralen Hersteller-Client — deine Drive-Daten laufen nie über Dritte. Dafür brauchst du einmalig (~5 min) einen kostenlosen OAuth-Client:</p>
      <ol class="steps">
        <li><a href="https://console.cloud.google.com/projectcreate" target="_blank">Google-Cloud-Projekt anlegen</a> (Name egal, z. B. „operator")</li>
        <li><a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank">Google Drive API aktivieren</a></li>
        <li><a href="https://console.cloud.google.com/auth/branding" target="_blank">OAuth-Zustimmungsbildschirm</a>: extern, dich selbst als <strong>Testnutzer</strong> eintragen</li>
        <li><a href="https://console.cloud.google.com/auth/clients" target="_blank">Client erstellen</a>: Typ <strong>Desktopanwendung</strong> → Client-ID + Secret unten eintragen</li>
      </ol>
      <label>Client-ID</label><input type="text" id="g-cid" placeholder="….apps.googleusercontent.com">
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
  catch (e) { toast(e.message, 1); }
}
async function googleConnect() {
  try { const r = await api("POST", "/api/google/auth/start", { write: $("#g-write").checked }); window.open(r.auth_url, "_blank"); toast("Google-Anmeldung geöffnet"); }
  catch (e) { toast(e.message, 1); }
}
async function googleDisconnect() {
  if (!confirm("Google-Zugriff widerrufen und Tokens löschen?")) return;
  try { await api("DELETE", "/api/google"); toast("Getrennt"); loadGoogle(); } catch (e) { toast(e.message, 1); }
}

/* ---------- Verhalten & Datenschutz ---------- */
async function loadVerhalten() { $("#verhalten-text").value = (await api("GET", "/api/verhalten")).content; }
async function saveVerhalten() {
  try { await api("PUT", "/api/verhalten", { content: $("#verhalten-text").value }); toast("Gespeichert — wirkt ab der nächsten Nachricht"); }
  catch (e) { toast(e.message, 1); }
}
async function loadPrivacy() {
  const s = STATUS || await api("GET", "/api/status");
  $("#privacy-content").innerHTML = `<div class="card"><table class="kv">
    <tr><td>Chat-Verarbeitung</td><td>Nachrichten werden zur Beantwortung an die Claude-API (Anthropic) übertragen — über dein persönliches Abo</td></tr>
    <tr><td>Gedächtnis</td><td>${s.memory_count} Fakten, lokal in <span class="mono">~/.claude/matrix-bot/memory.db</span> — verlässt deinen Mac nie</td></tr>
    <tr><td>Matrix-Zugangsdaten</td><td>lokal, Dateirechte 0600</td></tr>
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
    if (active === "m365") await loadM365();
    if (active === "google") await loadGoogle();
    if (active === "verhalten") await loadVerhalten();
    if (active === "privacy") await loadPrivacy();
  } catch (e) {
    if (String(e.message).includes("Dashboard-Token"))
      document.body.innerHTML = "<main><div class='card'><h2>Zugang verweigert</h2><p class='hint'>Bitte über <span class='mono'>python3 ~/.claude/matrix-bot/dashboard/open.py</span> öffnen — der Link enthält dein Zugangs-Token.</p></div></main>";
    else toast(e.message, 1);
  }
}
loadStatus().catch(() => refresh());
refresh();
setInterval(() => { if (document.querySelector("nav button.active").dataset.tab === "overview") loadStatus().catch(() => {}); }, 15000);
