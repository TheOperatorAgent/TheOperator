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
    <div class="tile ${STATUS.skill_proposals ? "warn" : ""}"><div class="k">${STATUS.skills_count}</div><div class="l">Skills${STATUS.skill_proposals ? ` · ${STATUS.skill_proposals} Vorschlag${STATUS.skill_proposals > 1 ? "e" : ""} 💡` : ""}</div></div>
    <div class="tile ${STATUS.vault.exists ? (STATUS.vault.locked ? "warn" : "ok") : ""}"><div class="k">${STATUS.vault.exists ? (STATUS.vault.locked ? "🔒" : "🔓") : "—"}</div><div class="l">Tresor${STATUS.vault.exists && !STATUS.vault.locked ? ` · ${STATUS.vault.entries} Einträge` : STATUS.vault.exists ? " · gesperrt" : ""}</div></div>
    <div class="tile ${STATUS.m365.connected ? "ok" : ""}"><div class="k">${STATUS.m365.connected ? "✓" : "—"}</div><div class="l">Microsoft 365</div></div>
    <div class="tile ${STATUS.google.connected ? "ok" : ""}"><div class="k">${STATUS.google.connected ? "✓" : "—"}</div><div class="l">Google Drive</div></div>
    <div class="tile ${STATUS.health.synapse_ok ? "ok" : "err"}"><div class="k">${STATUS.health.synapse_ok ? "ok" : "down"}</div><div class="l">Matrix-Server</div></div>
    <div class="tile ${STATUS.health.disk_free_gb < 10 ? "warn" : ""}"><div class="k">${STATUS.health.disk_free_gb} GB</div><div class="l">Disk frei</div></div>
    <div class="tile"><div class="k">${STATUS.health.usage_5h.runs}</div><div class="l">Claude-Läufe (5 h) · ${STATUS.health.cron_jobs} Automationen</div></div>`;
  const audit = await api("GET", "/api/audit?limit=12");
  $("#audit-list").innerHTML = audit.entries.reverse().map((e) =>
    `<div>${esc(e.ts)} · ${esc(e.actor)} · ${esc(e.action)} ${esc(e.target || "")} ${e.ok ? "" : "❌"}</div>`).join("") || "<div>Noch keine Einträge</div>";
}
async function restartListener() { try { await api("POST", "/api/listener/restart"); toast("Listener neu gestartet"); loadStatus(); } catch (e) { toast(e.message, 1); } }

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

/* ---------- Skills ---------- */
const SKILL_SOURCE = { dashboard: "von dir gepflegt", bot: "vom Operator gelernt", scout: "Scout-Vorschlag" };
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
            <div class="meta">${esc(p.description)}</div>
            ${p.reason ? `<div class="small">Warum: ${esc(p.reason)}</div>` : ""}
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
  } catch (e) { toast(e.message, 1); }
}

async function deleteSkill(name) {
  if (!confirm(`Skill "${name}" wirklich löschen?`)) return;
  try { await api("DELETE", "/api/skills/" + name); $("#skill-editor").classList.add("hidden"); toast("Gelöscht"); loadSkills(); }
  catch (e) { toast(e.message, 1); }
}

async function skillProposal(id, action) {
  try {
    await api("POST", `/api/skills/proposals/${id}/${action}`);
    toast(action === "accept" ? "Angenommen — der Skill ist ab sofort aktiv" : "Abgelehnt");
    loadSkills();
  } catch (e) { toast(e.message, 1); }
}

/* ---------- Tresor ---------- */
async function loadVault() {
  const s = await api("GET", "/api/vault/status");
  const c = $("#vault-content");
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
      <div style="display:flex;gap:10px;align-items:center">
        <button class="primary" onclick="vaultUnlock()">Entsperren</button>
        <a href="#" onclick="vaultRecoverForm();return false" class="small">Master-Passwort vergessen?</a>
      </div>
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
    </div>`;
}

async function vaultInit() {
  const p1 = $("#v-pw1").value, p2 = $("#v-pw2").value;
  if (p1 !== p2) return toast("Die Passwörter stimmen nicht überein", 1);
  try {
    const r = await api("POST", "/api/vault/init", { master_pw: p1 });
    showEmergencyKit(r.recovery_key, "Tresor angelegt!");
  } catch (e) { toast(e.message, 1); }
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
  } catch (e) { toast(e.message, 1); }
}
async function vaultLock() {
  try { await api("POST", "/api/vault/lock"); toast("Tresor gesperrt"); loadVault(); loadStatus().catch(() => {}); }
  catch (e) { toast(e.message, 1); }
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
  } catch (e) { toast(e.message, 1); }
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
  } catch (e) { toast(e.message, 1); }
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
  } catch (e) { toast(e.message, 1); }
}
async function deleteVaultEntry(name) {
  if (!confirm(`Eintrag "${name}" endgültig löschen?`)) return;
  try { await api("DELETE", "/api/vault/entries/" + encodeURIComponent(name)); loadVault(); }
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
      <p class="hint" style="margin-top:8px">Zur Sicherheit startet alles AUS.
      „Schreiben" erlaubt automatisch auch „Lesen". Schaltest du einen Regler wieder AUS,
      wird das Recht bei Microsoft <strong>wirklich entzogen</strong> — nicht nur versteckt.</p>
    </div>`;
}
async function saveM365Cid() {
  try { await api("PUT", "/api/m365/setup-client", { client_id: $("#m365-cid").value.trim() }); toast("Gespeichert"); loadM365(); }
  catch (e) { toast(e.message, 1); }
}
async function saveM365Upn() {
  try {
    await api("PUT", "/api/m365/primary-user", { upn: $("#m365-upn").value });
    toast("Gespeichert — dein Operator nutzt jetzt die Daten dieses Benutzers");
  } catch (e) { toast(e.message, 1); }
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
  } catch (e) { toast(e.message, 1); }
}
async function runCron(id) {
  try { await api("POST", `/api/cron/${id}/run`); toast("Gestartet — Ergebnis kommt in den Matrix-Raum und in den Verlauf"); }
  catch (e) { toast(e.message, 1); }
}
async function deleteCron(id) {
  if (!confirm("Automation löschen?")) return;
  try { await api("DELETE", "/api/cron/" + id); loadCron(); } catch (e) { toast(e.message, 1); }
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
}
async function addMemory() {
  const text = $("#mem-new").value.trim();
  if (!text) return;
  try { await api("POST", "/api/memory", { text }); $("#mem-new").value = ""; toast("Gespeichert"); loadMemory(); }
  catch (e) { toast(e.message, 1); }
}
async function forgetMemory(id) {
  try { await api("DELETE", "/api/memory/" + id); loadMemory(); } catch (e) { toast(e.message, 1); }
}

/* ---------- Logs (A5) ---------- */
async function loadLogs() {
  const d = await api("GET", `/api/logs?file=${$("#log-file").value}&lines=300&errors_only=${$("#log-errors").checked}`);
  const v = $("#log-view");
  v.textContent = d.lines.join("\n") || "(leer)";
  v.scrollTop = v.scrollHeight;
}

/* ---------- System: Backup + MCP (B1/B2) ---------- */
async function loadSystem() {
  const b = await api("GET", "/api/backups");
  $("#backup-list").innerHTML = b.backups.map((x) => `
    <div class="agent-row" style="padding:8px 14px">
      <div class="mono small">${esc(x.name)} · ${(x.size / 1e6).toFixed(1)} MB · ${esc(x.ts)}</div>
      <button class="ghost" onclick="restoreBackup('${x.name}')">Wiederherstellen</button>
    </div>`).join("") || "<p class='hint'>Noch keine Backups.</p>";
  const m = await api("GET", "/api/mcp");
  $("#mcp-list").innerHTML = m.servers.map((s) => `
    <div class="agent-row" style="padding:8px 14px">
      <div><strong>${esc(s.name)}</strong> <span class="pill">${esc(s.transport)}</span>
        <span class="mono small">${esc(s.command || s.url)}</span></div>
      <button class="danger" onclick="deleteMcp('${s.name}')">Entfernen</button>
    </div>`).join("") || "<p class='hint'>Keine MCP-Server konfiguriert.</p>";
}
async function createBackup() {
  try { const r = await api("POST", "/api/backup"); toast(`Backup erstellt: ${r.name} (${(r.size / 1e6).toFixed(1)} MB)`); loadSystem(); }
  catch (e) { toast(e.message, 1); }
}
async function restoreBackup(name) {
  if (!confirm(`Backup "${name}" zur Prüfung entpacken? (Überschreibt nichts automatisch)`)) return;
  try { const r = await api("POST", "/api/backup/restore", { name }); toast("Entpackt nach: " + r.dest); }
  catch (e) { toast(e.message, 1); }
}
async function addMcp() {
  const name = $("#mcp-name").value.trim(), target = $("#mcp-target").value.trim();
  if (!name || !target) return toast("Name und Kommando/URL angeben", 1);
  if (!confirm(`⚠️ MCP-Server "${name}" wird mit deinen Rechten ausgeführt und steht dem Operator als Werkzeug zur Verfügung. Vertraust du der Quelle?`)) return;
  const payload = target.startsWith("http")
    ? { name, url: target }
    : { name, command: target.split(" ")[0], args: target.split(" ").slice(1) };
  try { await api("POST", "/api/mcp", payload); toast("Hinzugefügt"); $("#mcp-name").value = ""; $("#mcp-target").value = ""; loadSystem(); }
  catch (e) { toast(e.message, 1); }
}
async function deleteMcp(name) {
  try { await api("DELETE", "/api/mcp/" + name); loadSystem(); } catch (e) { toast(e.message, 1); }
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
    if (active === "skills") await loadSkills();
    if (active === "vault") await loadVault();
    if (active === "sessions") await loadSessions();
    if (active === "cron") await loadCron();
    if (active === "usage") await loadUsage();
    if (active === "memory") await loadMemory();
    if (active === "m365") await loadM365();
    if (active === "google") await loadGoogle();
    if (active === "logs") await loadLogs();
    if (active === "system") await loadSystem();
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

/* ---------- Matrix-Code-Regen (dezent, im Hintergrund) ---------- */
(function rain() {
  const cv = document.getElementById("rain");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const CHARS = "アィウェオカキクケコサシスセソタチツテトナニヌネノ01オペレータOPERATOR";
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
