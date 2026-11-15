// SPDX-License-Identifier: AGPL-3.0-only
// The Takumion tab inside OrcaSlicer. Talks to the plugin over window.orca:
// sends {type: "tab", action}, receives {type: "tab-state", state}.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let codeTimer = null;

  function post(action, extra) {
    if (window.orca && window.orca.postMessage) window.orca.postMessage(Object.assign({type: "tab", action}, extra || {}));
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
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
    for (const doc of docs) {
      const section = el("div", "doc");
      section.append(el("h3", "", doc.doc.name || doc.key));
      const list = el("ul", "rows");
      doc.bodies.forEach((body) => list.append(bodyRow(body)));
      section.append(list);
      box.append(section);
    }
  }

  function render(state) {
    renderStatus(state);
    renderCode(state.code);
    renderPairings(state.pairings || []);
    renderDocs(state.docs || []);
    $("setup").hidden = !(state.setup && state.setup.showCard);
    $("notice").textContent = state.notice || "";
  }

  if (window.orca && window.orca.onMessage) {
    window.orca.onMessage((message) => {
      if (message && message.type === "tab-state") render(message.state);
    });
  }

  $("pair").addEventListener("click", () => post("pair"));
  $("pair-cancel").addEventListener("click", () => post("cancel-pair"));
  $("setup-done").addEventListener("click", () => post("dismiss-setup"));
  $("open-desktop").addEventListener("click", () => post("open-takumion", {target: "desktop"}));
  $("open-web").addEventListener("click", () => post("open-takumion", {target: "web"}));
  post("ready");
})();
