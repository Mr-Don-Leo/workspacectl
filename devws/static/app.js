/* Developer Workspace Manager — frontend. Vanilla JS, no dependencies. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);

const state = {
  app: null,          // /api/state payload
  projects: [],
  browsePath: null,   // current path in the setup folder browser
  autoStarted: new Set(),
  pollTimer: null,
  logTimer: null,
};

/* ---------------- API ---------------- */

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `${method} ${path} failed (${resp.status})`);
  return data;
}

/* ---------------- tiny UI kit: toast, modal, dropdown, switch ---------------- */

function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = message;
  $("#toast-host").appendChild(el);
  setTimeout(() => el.remove(), isError ? 5200 : 3000);
}

function openModal(render) {
  const backdrop = $("#modal-backdrop");
  const modal = $("#modal");
  modal.innerHTML = "";
  render(modal);
  backdrop.classList.remove("hidden");
}

function closeModal() {
  $("#modal-backdrop").classList.add("hidden");
  if (state.logTimer) { clearInterval(state.logTimer); state.logTimer = null; }
}

$("#modal-backdrop").addEventListener("click", (e) => {
  if (e.target === $("#modal-backdrop")) closeModal();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/** Custom dropdown. options: [{value, label}], returns root element. */
function makeDropdown({ options, value, onChange, width }) {
  const root = document.createElement("div");
  root.className = "dropdown";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "dropdown-toggle";
  if (width) toggle.style.width = width;
  const valueSpan = document.createElement("span");
  valueSpan.className = "dd-value";
  const chevron = document.createElement("span");
  chevron.className = "chevron";
  toggle.append(valueSpan, chevron);
  const menu = document.createElement("div");
  menu.className = "dropdown-menu";
  root.append(toggle, menu);

  let current = value;
  const labelFor = (v) => (options.find((o) => o.value === v) || {}).label ?? v;
  const sync = () => { valueSpan.textContent = labelFor(current); };

  const rebuild = () => {
    menu.innerHTML = "";
    for (const opt of options) {
      if (opt.separator) {
        const sep = document.createElement("div");
        sep.className = "dropdown-sep";
        menu.appendChild(sep);
        continue;
      }
      const item = document.createElement("button");
      item.type = "button";
      item.className = "dropdown-item";
      const check = document.createElement("span");
      check.className = "check";
      check.textContent = opt.value === current ? "✓" : "";
      const label = document.createElement("span");
      label.textContent = opt.label;
      item.append(check, label);
      item.addEventListener("click", () => {
        root.classList.remove("open");
        if (opt.action) { opt.action(); return; }
        if (opt.value !== current) {
          current = opt.value;
          sync(); rebuild();
          onChange && onChange(opt.value);
        }
      });
      menu.appendChild(item);
    }
  };

  toggle.addEventListener("click", (e) => {
    e.stopPropagation();
    document.querySelectorAll(".dropdown.open").forEach((d) => d !== root && d.classList.remove("open"));
    root.classList.toggle("open");
  });
  document.addEventListener("click", () => root.classList.remove("open"));

  sync(); rebuild();
  root.setValue = (v) => { current = v; sync(); rebuild(); };
  return root;
}

/** Custom iOS-style switch. */
function makeSwitch({ checked, onChange }) {
  const label = document.createElement("label");
  label.className = "switch";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = !!checked;
  const track = document.createElement("span");
  track.className = "track";
  const thumb = document.createElement("span");
  thumb.className = "thumb";
  label.append(input, track, thumb);
  input.addEventListener("change", () => onChange && onChange(input.checked));
  return label;
}

function makeField({ placeholder, value, mono }) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const input = document.createElement("input");
  input.type = "text";
  input.spellcheck = false;
  input.placeholder = placeholder || "";
  input.value = value || "";
  if (mono) input.style.fontFamily = '"SF Mono", ui-monospace, Menlo, monospace';
  wrap.appendChild(input);
  wrap.input = input;
  return wrap;
}

/* ---------------- boot & top-level rendering ---------------- */

async function boot() {
  try {
    state.app = await api("GET", "/api/state");
  } catch (err) {
    toast(`Cannot reach server: ${err.message}`, true);
    return;
  }
  applyTheme();
  renderWorkspaceSwitcher();
  if (state.app.active_workspace) {
    await showDashboard();
  } else {
    showSetup();
  }
}

function applyTheme() {
  const theme = (state.app.settings || {}).theme;
  if (theme === "light" || theme === "dark") {
    document.documentElement.dataset.theme = theme;
  } else {
    delete document.documentElement.dataset.theme;
  }
}

function renderWorkspaceSwitcher() {
  const host = $("#workspace-switcher");
  host.innerHTML = "";
  const workspaces = state.app.workspaces || [];
  if (!workspaces.length) return;
  const active = state.app.active_workspace;
  const options = workspaces.map((w) => ({ value: w.id, label: w.name }));
  const dd = makeDropdown({
    options,
    value: active ? active.id : null,
    onChange: async (id) => {
      try {
        await api("POST", `/api/workspaces/${id}/activate`);
        state.app = await api("GET", "/api/state");
        renderWorkspaceSwitcher();
        await showDashboard();
      } catch (err) { toast(err.message, true); }
    },
  });
  host.appendChild(dd);
}

/* ---------------- setup screen (directory picker) ---------------- */

function showSetup() {
  $("#dashboard").classList.add("hidden");
  $("#setup").classList.remove("hidden");
  stopPolling();
  browseTo(state.browsePath || state.app.home);
}

async function browseTo(path) {
  let listing;
  try {
    listing = await api("GET", `/api/fs?path=${encodeURIComponent(path)}`);
  } catch (err) { toast(err.message, true); return; }
  state.browsePath = listing.path;
  $("#btn-create-workspace").disabled = false;

  const pathEl = $("#browser-path");
  pathEl.innerHTML = "";
  const icon = document.createElement("span");
  icon.textContent = "📁";
  pathEl.append(icon, document.createTextNode(listing.path));

  const list = $("#browser-list");
  list.innerHTML = "";
  if (listing.parent) {
    const up = document.createElement("li");
    up.className = "up";
    up.innerHTML = `<span class="folder-ico">↩︎</span> ..`;
    up.addEventListener("click", () => browseTo(listing.parent));
    list.appendChild(up);
  }
  if (!listing.dirs.length) {
    const empty = document.createElement("li");
    empty.className = "browser-empty";
    empty.textContent = "No subfolders";
    list.appendChild(empty);
  }
  for (const dir of listing.dirs) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="folder-ico">📁</span>`;
    li.append(document.createTextNode(dir.name));
    li.addEventListener("click", () => browseTo(dir.path));
    list.appendChild(li);
  }
}

$("#btn-create-workspace").addEventListener("click", async () => {
  const name = $("#ws-name").value.trim() || state.browsePath.split("/").pop() || "Workspace";
  const errEl = $("#setup-error");
  errEl.classList.add("hidden");
  try {
    await api("POST", "/api/workspaces", { name, root: state.browsePath });
    state.app = await api("GET", "/api/state");
    renderWorkspaceSwitcher();
    await showDashboard();
    toast(`Workspace “${name}” created`);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

/* ---------------- dashboard ---------------- */

async function showDashboard() {
  $("#setup").classList.add("hidden");
  $("#dashboard").classList.remove("hidden");
  const ws = state.app.active_workspace;
  $("#dash-title").textContent = ws.name;
  $("#dash-root").textContent = ws.root;
  await refreshProjects(true);
  startPolling();
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => refreshProjects(false), 5000);
}
function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}

async function refreshProjects(showErrors) {
  try {
    state.projects = await api("GET", "/api/projects");
  } catch (err) {
    if (showErrors) toast(err.message, true);
    return;
  }
  renderProjects();
  autoStartMarked();
}

function autoStartMarked() {
  for (const p of state.projects) {
    const wantsAuto = p.settings && p.settings.auto_start;
    const hasDev = p.processes.some((pr) => pr.kind === "dev" && pr.running);
    const key = `${state.app.active_workspace.id}:${p.name}`;
    if (wantsAuto && !hasDev && !state.autoStarted.has(key)) {
      state.autoStarted.add(key);
      startDev(p.name).catch(() => {});
    }
  }
}

function renderProjects() {
  const grid = $("#project-grid");
  grid.innerHTML = "";
  $("#dash-empty").classList.toggle("hidden", state.projects.length > 0);
  for (const p of state.projects) grid.appendChild(renderCard(p));
}

function chip(text, cls) {
  const el = document.createElement("span");
  el.className = "chip" + (cls ? ` ${cls}` : "");
  el.textContent = text;
  return el;
}

function renderCard(p) {
  const card = document.createElement("div");
  card.className = "card";

  // head: title + settings gear
  const head = document.createElement("div");
  head.className = "card-head";
  const titleWrap = document.createElement("div");
  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = p.name;
  const badges = document.createElement("div");
  badges.className = "card-badges";
  for (const t of p.types) badges.appendChild(chip(t, "accent"));
  titleWrap.append(title, badges);
  const gear = document.createElement("button");
  gear.className = "icon-btn";
  gear.title = "Project settings";
  gear.textContent = "⚙︎";
  gear.addEventListener("click", () => openProjectSettings(p));
  head.append(titleWrap, gear);
  card.appendChild(head);

  // git row
  if (p.git) {
    const git = document.createElement("div");
    git.className = "git-row";
    git.appendChild(chip(`⎇ ${p.git.branch || "?"}`, p.git.clean ? "green" : "orange"));
    if (p.git.ahead) git.appendChild(chip(`↑${p.git.ahead}`));
    if (p.git.behind) git.appendChild(chip(`↓${p.git.behind}`));
    if (!p.git.clean) {
      const parts = [];
      if (p.git.staged) parts.push(`${p.git.staged} staged`);
      if (p.git.unstaged) parts.push(`${p.git.unstaged} modified`);
      if (p.git.untracked) parts.push(`${p.git.untracked} new`);
      if (p.git.conflicts) parts.push(`${p.git.conflicts} conflicts`);
      git.appendChild(chip(parts.join(" · "), "orange"));
    }
    card.appendChild(git);
    if (p.git.last_commit) {
      const lc = document.createElement("div");
      lc.className = "git-commit";
      lc.textContent = `${p.git.last_commit.hash} · ${p.git.last_commit.subject} · ${p.git.last_commit.when}`;
      lc.title = lc.textContent;
      card.appendChild(lc);
    }
  }

  // ports
  if (p.ports.length) {
    const portsRow = document.createElement("div");
    portsRow.className = "git-row";
    for (const port of p.ports) {
      const c = chip(`:${port}`, "green port");
      c.title = `Open http://localhost:${port}`;
      c.addEventListener("click", () => window.open(`http://localhost:${port}`, "_blank"));
      portsRow.appendChild(c);
    }
    card.appendChild(portsRow);
  }

  // processes
  for (const proc of p.processes) card.appendChild(renderProcRow(proc));

  // actions
  const actions = document.createElement("div");
  actions.className = "card-actions";
  const devRunning = p.processes.find((pr) => pr.kind === "dev" && pr.running);
  const devBtn = document.createElement("button");
  devBtn.className = devRunning ? "btn btn-small btn-danger" : "btn btn-small btn-primary";
  devBtn.textContent = devRunning ? "Stop Dev Server" : "Start Dev Server";
  devBtn.addEventListener("click", async () => {
    try {
      if (devRunning) { await api("POST", `/api/processes/${devRunning.id}/stop`); toast(`${p.name}: dev server stopped`); }
      else await startDev(p.name);
      refreshProjects(false);
    } catch (err) { toast(err.message, true); }
  });
  actions.appendChild(devBtn);

  if (p.compose_file) {
    const composeRunning = p.processes.find((pr) => pr.kind === "compose" && pr.running);
    const composeBtn = document.createElement("button");
    composeBtn.className = "btn btn-small";
    composeBtn.textContent = composeRunning ? "Compose Down" : "Compose Up";
    if (!state.app.docker_available) {
      composeBtn.disabled = true;
      composeBtn.title = "Docker is not available on this machine";
    }
    composeBtn.addEventListener("click", async () => {
      try {
        if (composeRunning) {
          await api("POST", `/api/projects/${encodeURIComponent(p.name)}/compose/down`);
          toast(`${p.name}: compose down`);
        } else {
          await api("POST", `/api/projects/${encodeURIComponent(p.name)}/start`, { kind: "compose" });
          toast(`${p.name}: compose up`);
        }
        refreshProjects(false);
      } catch (err) { toast(err.message, true); }
    });
    actions.appendChild(composeBtn);
  }

  const termBtn = document.createElement("button");
  termBtn.className = "btn btn-small";
  termBtn.textContent = "Terminal";
  termBtn.disabled = !state.app.terminals.length;
  termBtn.title = termBtn.disabled ? "No terminal emulator found" : "Open a terminal here";
  termBtn.addEventListener("click", () => openTool(p.name, "terminal"));
  const editBtn = document.createElement("button");
  editBtn.className = "btn btn-small";
  editBtn.textContent = "Editor";
  editBtn.disabled = !state.app.editors.length;
  editBtn.title = editBtn.disabled ? "No editor found (set $EDITOR)" : "Open in editor";
  editBtn.addEventListener("click", () => openTool(p.name, "editor"));
  actions.append(termBtn, editBtn);

  card.appendChild(actions);
  return card;
}

function renderProcRow(proc) {
  const row = document.createElement("div");
  row.className = "proc-row";
  const label = document.createElement("div");
  label.className = "proc-label";
  const dot = document.createElement("span");
  dot.className = "dot " + (proc.running ? "live" : proc.returncode === 0 ? "dead" : "failed");
  const name = document.createElement("span");
  name.className = "name";
  const uptime = proc.running ? ` · ${formatUptime(proc.uptime)}` : ` · exited (${proc.returncode})`;
  name.textContent = proc.label + uptime;
  name.title = proc.command;
  label.append(dot, name);

  const btns = document.createElement("div");
  btns.className = "proc-actions";
  const logBtn = document.createElement("button");
  logBtn.className = "icon-btn";
  logBtn.textContent = "Logs";
  logBtn.addEventListener("click", () => openLogs(proc));
  btns.appendChild(logBtn);
  if (proc.running) {
    const stopBtn = document.createElement("button");
    stopBtn.className = "icon-btn danger";
    stopBtn.textContent = "Stop";
    stopBtn.addEventListener("click", async () => {
      try { await api("POST", `/api/processes/${proc.id}/stop`); refreshProjects(false); }
      catch (err) { toast(err.message, true); }
    });
    btns.appendChild(stopBtn);
  } else {
    const clearBtn = document.createElement("button");
    clearBtn.className = "icon-btn";
    clearBtn.textContent = "Clear";
    clearBtn.addEventListener("click", async () => {
      try { await api("DELETE", `/api/processes/${proc.id}`); refreshProjects(false); }
      catch (err) { toast(err.message, true); }
    });
    btns.appendChild(clearBtn);
  }
  row.append(label, btns);
  return row;
}

function formatUptime(seconds) {
  seconds = Math.floor(seconds);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

async function startDev(projectName) {
  const proc = await api("POST", `/api/projects/${encodeURIComponent(projectName)}/start`, { kind: "dev" });
  toast(`${projectName}: dev server started (pid ${proc.pid})`);
}

async function openTool(projectName, tool) {
  try {
    const result = await api("POST", `/api/projects/${encodeURIComponent(projectName)}/open/${tool}`);
    toast(`Opened ${result.tool}`);
  } catch (err) { toast(err.message, true); }
}

/* ---------------- log viewer ---------------- */

function openLogs(proc) {
  openModal((modal) => {
    const h = document.createElement("h3");
    h.textContent = proc.label;
    const sub = document.createElement("p");
    sub.className = "modal-sub";
    sub.textContent = `${proc.command} — ${proc.cwd}`;
    const view = document.createElement("div");
    view.className = "log-view";
    const foot = document.createElement("div");
    foot.className = "modal-foot";
    const closeBtn = document.createElement("button");
    closeBtn.className = "btn";
    closeBtn.textContent = "Close";
    closeBtn.addEventListener("click", closeModal);
    foot.appendChild(closeBtn);
    modal.append(h, sub, view, foot);

    let cursor = 0;
    const pull = async () => {
      try {
        const log = await api("GET", `/api/processes/${proc.id}/logs?since=${cursor}`);
        if (log.lines.length) {
          const stick = view.scrollTop + view.clientHeight >= view.scrollHeight - 8;
          view.textContent += (view.textContent ? "\n" : "") + log.lines.join("\n");
          cursor = log.cursor;
          if (stick) view.scrollTop = view.scrollHeight;
        }
        if (!log.running && state.logTimer) { clearInterval(state.logTimer); state.logTimer = null; }
      } catch { /* proc may be gone */ }
    };
    pull();
    state.logTimer = setInterval(pull, 1000);
  });
}

/* ---------------- project settings modal ---------------- */

function openProjectSettings(p) {
  openModal((modal) => {
    const h = document.createElement("h3");
    h.textContent = p.name;
    const sub = document.createElement("p");
    sub.className = "modal-sub";
    sub.textContent = p.path;
    modal.append(h, sub);

    const cmdLabel = document.createElement("label");
    cmdLabel.className = "field-label";
    cmdLabel.textContent = "Dev server command";
    const cmdField = makeField({
      placeholder: p.suggested_command || "npm run dev",
      value: p.settings.dev_command || "",
      mono: true,
    });
    modal.append(cmdLabel, cmdField);
    if (p.suggested_command) {
      const hint = document.createElement("p");
      hint.className = "modal-sub";
      hint.style.marginTop = "6px";
      hint.textContent = `Detected: ${p.suggested_command} (used when left empty)`;
      modal.append(hint);
    }

    const edLabel = document.createElement("label");
    edLabel.className = "field-label";
    edLabel.textContent = "Editor";
    const editorOptions = [{ value: "", label: "Default" }].concat(
      state.app.editors.map((e) => ({ value: e, label: e }))
    );
    let editorValue = p.settings.editor || "";
    const edDD = makeDropdown({
      options: editorOptions,
      value: editorValue,
      onChange: (v) => { editorValue = v; },
      width: "100%",
    });
    modal.append(edLabel, edDD);

    const row = document.createElement("div");
    row.className = "field-row";
    const rowText = document.createElement("div");
    rowText.innerHTML = `<div class="row-label">Auto-start dev server</div><div class="row-sub">Start automatically when this workspace opens</div>`;
    let autoValue = !!p.settings.auto_start;
    const sw = makeSwitch({ checked: autoValue, onChange: (v) => { autoValue = v; } });
    row.append(rowText, sw);
    modal.append(row);

    const foot = document.createElement("div");
    foot.className = "modal-foot";
    const cancel = document.createElement("button");
    cancel.className = "btn";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", closeModal);
    const save = document.createElement("button");
    save.className = "btn btn-primary";
    save.textContent = "Save";
    save.addEventListener("click", async () => {
      try {
        await api("POST", `/api/projects/${encodeURIComponent(p.name)}/settings`, {
          dev_command: cmdField.input.value.trim(),
          editor: editorValue,
          auto_start: autoValue || "",
        });
        closeModal();
        toast(`${p.name}: settings saved`);
        refreshProjects(false);
      } catch (err) { toast(err.message, true); }
    });
    foot.append(cancel, save);
    modal.append(foot);
  });
}

/* ---------------- global settings & ports modals ---------------- */

$("#btn-settings").addEventListener("click", () => {
  openModal((modal) => {
    const h = document.createElement("h3");
    h.textContent = "Settings";
    modal.append(h);

    const addDD = (labelText, options, value, key) => {
      const label = document.createElement("label");
      label.className = "field-label";
      label.textContent = labelText;
      const dd = makeDropdown({
        options,
        value,
        width: "100%",
        onChange: async (v) => {
          try {
            state.app.settings = await api("POST", "/api/settings", { [key]: v });
            applyTheme();
          } catch (err) { toast(err.message, true); }
        },
      });
      modal.append(label, dd);
    };

    const settings = state.app.settings || {};
    addDD("Appearance", [
      { value: "", label: "Match System" },
      { value: "light", label: "Light" },
      { value: "dark", label: "Dark" },
    ], settings.theme || "", "theme");
    addDD("Preferred terminal",
      [{ value: "", label: "Automatic" }].concat(state.app.terminals.map((t) => ({ value: t, label: t }))),
      settings.terminal || "", "terminal");
    addDD("Preferred editor",
      [{ value: "", label: "Automatic" }].concat(state.app.editors.map((e) => ({ value: e, label: e }))),
      settings.editor || "", "editor");

    // workspace management
    const wsLabel = document.createElement("label");
    wsLabel.className = "field-label";
    wsLabel.textContent = "Workspaces";
    modal.append(wsLabel);
    for (const ws of state.app.workspaces) {
      const row = document.createElement("div");
      row.className = "field-row";
      const text = document.createElement("div");
      text.innerHTML = `<div class="row-label">${ws.name}</div><div class="row-sub">${ws.root}</div>`;
      const del = document.createElement("button");
      del.className = "btn btn-small btn-danger";
      del.textContent = "Remove";
      del.addEventListener("click", async () => {
        try {
          await api("DELETE", `/api/workspaces/${ws.id}`);
          state.app = await api("GET", "/api/state");
          renderWorkspaceSwitcher();
          closeModal();
          state.app.active_workspace ? showDashboard() : showSetup();
        } catch (err) { toast(err.message, true); }
      });
      row.append(text, del);
      modal.append(row);
    }

    const foot = document.createElement("div");
    foot.className = "modal-foot";
    const closeBtn = document.createElement("button");
    closeBtn.className = "btn";
    closeBtn.textContent = "Done";
    closeBtn.addEventListener("click", closeModal);
    foot.appendChild(closeBtn);
    modal.append(foot);
  });
});

$("#btn-ports").addEventListener("click", async () => {
  let entries;
  try { entries = await api("GET", "/api/ports"); }
  catch (err) { toast(err.message, true); return; }
  openModal((modal) => {
    const h = document.createElement("h3");
    h.textContent = "Listening Ports";
    const sub = document.createElement("p");
    sub.className = "modal-sub";
    sub.textContent = "All TCP ports currently in LISTEN state on this machine.";
    const table = document.createElement("table");
    table.className = "port-table";
    table.innerHTML = "<thead><tr><th>Port</th><th>Address</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const e of entries) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${e.port}</td><td>${e.addr}</td>`;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    const foot = document.createElement("div");
    foot.className = "modal-foot";
    const closeBtn = document.createElement("button");
    closeBtn.className = "btn";
    closeBtn.textContent = "Close";
    closeBtn.addEventListener("click", closeModal);
    foot.appendChild(closeBtn);
    modal.append(h, sub, table, foot);
  });
});

$("#btn-new-workspace").addEventListener("click", () => {
  $("#ws-name").value = "";
  showSetup();
});

$("#btn-refresh").addEventListener("click", () => refreshProjects(true));

boot();
