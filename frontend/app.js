// SPDX-License-Identifier: AGPL-3.0-only
// The Takumion tab inside OrcaSlicer. Talks to the plugin over window.orca:
// sends {type: "tab", nonce, action}, receives {type: "tab-state", state}.
// The nonce is put in this page by the plugin; a page without it cannot
// drive the tab. Before navigating the tab away, the page says "leave" so the
// plugin stops pushing its state (which holds the pairing code).
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const nonceTag = document.querySelector('meta[name="takumion-tab"]');
  const nonce = nonceTag ? nonceTag.content : "";
  // The tab's first page goes on to Takumion; /tab (the way back) never does.
  let openTakumion = !!document.querySelector('meta[name="takumion-open"]');
  // In the plugin's own window Takumion stays in its tab: no "In this tab" here.
  const inPanel = !!document.querySelector('meta[name="takumion-view"][content="panel"]');
  if (inPanel) $("open-here").hidden = true;
  let offlineNote = "";
  let codeTimer = null;
  let latest = null;

  function post(action, extra) {
    if (window.orca && window.orca.postMessage) window.orca.postMessage(Object.assign({type: "tab", nonce, action}, extra || {}));
  }

  function leaveTo(url) {
    post("leave");
    location.assign(url);
  }

  // Back from Takumion (its Settings › Slicer) this page is /tab?return=<where it
  // was>: In this tab goes back there — the open project — instead of the
  // dashboard. Only on the configured Takumion's own origin: the bridge key in
  // the fragment must never reach another site.
  function inThisTab(here) {
    const back = new URLSearchParams(location.search).get("return");
    if (!back) return here;
    try {
      const want = new URL(back);
      const base = new URL(here);
      if (want.origin !== base.origin) return here;
      want.searchParams.set("host", "orca");
      want.hash = base.hash;
      return want.toString();
    } catch (e) {
      return here;
    }
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function hostOf(url) {
    try { return new URL(url).host; } catch (e) { return url || ""; }
  }

  function when(iso) {
    const date = new Date(iso);
    return isNaN(date) ? iso : date.toLocaleString();
  }

  function renderStatus(state) {
    const status = $("status");
    const port = state.server && state.server.port;
    status.textContent = port ? `Listening on 127.0.0.1:${port}` : (state.server && state.server.error) || "Not listening";
    status.className = "pill " + (port ? "ok" : "bad");
    $("versions").textContent = `Plugin ${state.plugin} · OrcaSlicer ${state.orca}`;
  }

  function renderCode(code) {
    clearInterval(codeTimer);
    $("pair-idle").hidden = !!code;
    $("pair-live").hidden = !code;
    if (!code) return;
    $("code").textContent = code.value;
    let left = code.secondsLeft;
    const tick = () => {
      $("code-left").textContent = left > 0 ? `Valid for ${left} s` : "Expired";
      if (left <= 0) { clearInterval(codeTimer); renderCode(null); }
      left -= 1;
    };
    tick();
    codeTimer = setInterval(tick, 1000);
  }

  function renderPairings(pairings) {
    const list = $("pairings");
    list.replaceChildren();
    $("pairings-empty").hidden = pairings.length > 0;
    for (const p of pairings) {
      const row = el("li");
      const info = el("div");
      info.append(el("div", "name", p.clientName), el("div", "muted small", `${p.origin} · last used ${when(p.lastUsed)}`));
      const revoke = el("button", "secondary", "Revoke");
      revoke.addEventListener("click", () => post("revoke", {id: p.id}));
      row.append(info, revoke);
      list.append(row);
    }
  }

  function bodyRow(body) {
    const row = el("li");
    const info = el("div");
    info.append(el("div", "name", body.name), el("div", "muted small", `${body.format.toUpperCase()} · sent ${when(body.lastSent)}`));
    const badges = el("div");
    if (body.reloadNeeded) badges.append(el("span", "badge reload", "Reload from disk"));
    else badges.append(el("span", body.onPlate ? "badge on" : "badge", body.onPlate ? "On plate" : "Not on plate"));
    row.append(info, badges);
    return row;
  }

  function renderDocs(docs) {
    const box = $("docs");
    box.replaceChildren();
    $("docs-empty").hidden = docs.length > 0;
    // Two documents may share a name ("Untitled Project"): tell them apart by key.
    const named = docs.map((doc) => doc.doc.name || doc.key);
    const shared = (name) => named.filter((n) => n === name).length > 1;
    docs.forEach((doc, i) => {
      const section = el("div", "doc");
      section.append(el("h3", "", shared(named[i]) ? `${named[i]} · ${doc.key.slice(0, 8)}` : named[i]));
      const list = el("ul", "rows");
      doc.bodies.forEach((body) => list.append(bodyRow(body)));
      section.append(list);
      box.append(section);
    });
  }

  // Go on to Takumion once it answers; offline, stay here and say so (an error
  // page in the tab would leave no way back). no-cors: only "it answered" matters.
  function openTakumionWhenUp(url) {
    openTakumion = false;
    const stop = new AbortController();
    setTimeout(() => stop.abort(), 5000);
    fetch(url, {mode: "no-cors", cache: "no-store", signal: stop.signal}).then(
      () => leaveTo(url),
      () => {
        offlineNote = `Takumion did not answer at ${hostOf(url)}. Use Open Takumion when it is back.`;
        if (latest) render(latest);
      },
    );
  }

  // Where Takumion's files go. Sends wait until a folder is in use; a newly
  // saved one is used after OrcaSlicer restarts (Orca allows a folder at load).
  let locationEditing = false;
  function renderLocation(loc) {
    const card = $("location");
    if (!loc) { card.hidden = true; return; }
    const ready = !!loc.active && !loc.needsRestart;
    card.classList.toggle("needs", !loc.active || loc.needsRestart);
    $("location-now").textContent = loc.needsRestart
      ? `Restart OrcaSlicer to start using ${loc.saved}. OrcaSlicer asks once to allow it.`
      : loc.active ? `Files go to ${loc.active}` : "Choose a folder first: Takumion's sends wait until one is set.";
    const editing = !ready || locationEditing;
    $("location-edit").hidden = !editing;
    $("location-change").hidden = editing;
    const input = $("location-input");
    if (document.activeElement !== input && !input.value) input.value = loc.saved || loc.active || (loc.suggestions || [])[0] || "";
    const chips = $("location-suggestions");
    chips.replaceChildren();
    for (const folder of loc.suggestions || []) {
      const chip = el("button", "secondary", folder);
      chip.addEventListener("click", () => { input.value = folder; input.focus(); });
      chips.append(chip);
    }
  }

  function render(state) {
    latest = state;
    renderLocation(state.location);
    if (openTakumion && state.takumionHere) openTakumionWhenUp(state.takumionHere);
    $("open-here").textContent = `In this tab (${hostOf(state.takumionHere)})`;
    renderStatus(state);
    renderCode(state.code);
    renderPairings(state.pairings || []);
    renderDocs(state.docs || []);
    $("setup").hidden = !(state.setup && state.setup.showCard);
    $("notice").textContent = state.notice || offlineNote;
  }

  if (window.orca && window.orca.onMessage) {
    window.orca.onMessage((message) => {
      if (message && message.type === "tab-state") render(message.state);
    });
  }

  $("pair").addEventListener("click", () => post("pair"));
  $("location-save").addEventListener("click", () => { locationEditing = false; post("set-location", {folder: $("location-input").value}); });
  $("location-change").addEventListener("click", () => { locationEditing = true; if (latest) render(latest); });
  $("pair-cancel").addEventListener("click", () => post("cancel-pair"));
  $("setup-done").addEventListener("click", () => post("dismiss-setup"));
  $("open-here").addEventListener("click", () => { if (latest && latest.takumionHere) leaveTo(inThisTab(latest.takumionHere)); });
  $("open-desktop").addEventListener("click", () => post("open-takumion", {target: "desktop"}));
  $("open-web").addEventListener("click", () => post("open-takumion", {target: "web"}));
  // Orca's Reload from disk fires no plugin event; this lets the plugin
  // re-read the plate on Orca's UI thread. No visibility check: Orca's
  // webview reports the tab as hidden even while it is shown.
  setInterval(() => post("refresh"), 3000);
  post("ready");
})();
