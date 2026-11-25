// Promítárna pro Windows – okno aplikace.
//
// Obrazovky vykresluje stejně jako aplikace pro Android TV: logika (app_api) vrací, co na nich je
// (řady karet, tlačítka, cíle karet), okno to jen ukáže. Cíle karet (`target.type`) mají stejný
// význam jako v Navigator.kt na televizi. Ovládání: myš, nebo klávesnice – šipky, Enter, Esc
// (zpět), podržený Esc (hlavní nabídka), klávesa Menu nebo pravé tlačítko (nabídka karty), / (hledat).
"use strict";

const $ = (selector, root) => (root || document).querySelector(selector);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

// --- spojení s logikou ---------------------------------------------------------------------------

async function post(path, params) {
  try {
    const response = await fetch(path, {
      method: "POST", headers: {"Content-Type": "application/json", "X-Token": window.TOKEN},
      body: JSON.stringify(params || {}),
    });
    return await response.json();
  } catch (error) {
    return {ok: false, error: "Aplikace neodpovídá (" + error + ")."};
  }
}
const api = (action, params) => post("/api/" + action, params);
const local = (action, params) => post("/local/" + action, params);

let assets = {icons: [], art: []};
fetch("ui/assets/manifest.json").then((r) => r.json()).then((data) => { assets = data; }).catch(() => {});
const hasArt = (name) => assets.art.includes(name + ".webp");
const hasIcon = (name) => assets.icons.includes(name);
const image = (value) => (value || "").startsWith("res:") ? "ui/assets/" + value.slice(4) + ".webp" : value || "";

// --- drobnosti: hláška, okna ---------------------------------------------------------------------

let toastTimer = 0;
function toast(text) {
  if (!text) return;
  const node = $("#toast");
  node.textContent = text;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 4500);
}

const dialogs = [];
// Okno: {title, message, items: [{label, note, current, action}], buttons: [{label, action, primary}], input, pre}
function dialog(spec) {
  const overlay = $("#overlay");
  const box = el("div", "dialog");
  box.setAttribute("role", "dialog");
  if (spec.title) box.append(el("h3", "", spec.title));
  if (spec.message) box.append(el("div", "message", spec.message));
  if (spec.pre) box.append(el("pre", "", spec.pre));
  let input = null;
  if (spec.input) {
    input = el("input", "f");
    input.type = spec.input.password ? "password" : "text";
    input.value = spec.input.value || "";
    input.placeholder = spec.input.hint || "";
    box.append(input);
  }
  const previous = document.activeElement;
  const close = () => {
    const index = dialogs.indexOf(box);
    if (index >= 0) dialogs.splice(index, 1);
    box.remove();
    overlay.hidden = !dialogs.length;
    if (!dialogs.length && previous && document.contains(previous)) previous.focus();
  };
  box.close = close;
  if (spec.items && spec.items.length) {
    const list = el("div", "items");
    for (const item of spec.items) {
      const button = el("button", "f" + (item.current ? " current" : ""), item.label);
      if (item.note) button.append(el("span", "note", item.note));
      button.onclick = () => { close(); item.action && item.action(); };
      list.append(button);
    }
    box.append(list);
  }
  const buttons = el("div", "buttons");
  for (const spec2 of spec.buttons || [{label: "OK"}]) {
    const button = el("button", "f btn" + (spec2.primary ? " primary" : ""), spec2.label);
    button.onclick = () => { close(); spec2.action && spec2.action(input ? input.value : undefined); };
    buttons.append(button);
  }
  box.append(buttons);
  if (input) input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); buttons.querySelector(".primary, button").click(); }
  });
  overlay.innerHTML = "";
  overlay.append(box);
  overlay.hidden = false;
  dialogs.push(box);
  const first = input || box.querySelector(".items .current") || box.querySelector(".items button, .buttons button");
  if (first) first.focus();
  return box;
}
const info = (title, text) => dialog({title, message: text});

// --- karty a řady ------------------------------------------------------------------------------

function cardNode(card, onOpen) {
  const node = el("div", "card f" + (card.wide ? " wide" : ""));
  node.tabIndex = 0;
  const art = el("div", "art");
  const picture = image(card.image);
  if (picture) {
    art.style.backgroundImage = "url(\"" + picture.replace(/"/g, "%22") + "\")";
  } else {
    // dlaždice bez obrázku: malba tuší (tile_<ikona>), jinak obrysová ikona – jako na televizi
    const name = (card.icon || "folder").split(":")[0] || "folder";
    const tile = el("div", "tile", card.title);
    if (hasArt("tile_" + name)) tile.style.backgroundImage = "url(\"ui/assets/tile_" + name + ".webp\")";
    else {
      const glyph = el("img", "glyph");
      glyph.src = "ui/assets/" + (hasIcon("ic_tile_" + name) ? "ic_tile_" + name : "ic_tile_folder") + ".svg";
      tile.append(glyph);
    }
    art.append(tile);
  }
  if (card.badges && card.badges.filter(Boolean).length) {
    const badges = el("div", "badges");
    for (const badge of card.badges) if (badge) badges.append(el("span", "", badge));
    art.append(badges);
  }
  if (card.watched) art.append(el("div", "watched", "✓"));
  if (card.progress) {
    const bar = el("div", "progress");
    const fill = el("i");
    fill.style.width = Math.max(2, Math.min(100, card.progress)) + "%";
    bar.append(fill);
    art.append(bar);
  }
  node.append(art);
  if (picture || card.subtitle) {
    const infoBox = el("div", "info");
    if (picture) infoBox.append(el("div", "title", card.title));
    if (card.subtitle) infoBox.append(el("div", "subtitle", card.subtitle));
    node.append(infoBox);
  }
  node.card = card;
  node.onclick = () => onOpen(card);
  node.oncontextmenu = (event) => { event.preventDefault(); cardMenu(card, onOpen.changed); };
  return node;
}

function rowsNode(rows, onOpen) {
  const box = el("div", "rows");
  for (const row of rows || []) {
    if (!row.items || !row.items.length) continue;
    box.append(el("h2", "row", row.title || ""));
    const items = el("div", "row-items");
    for (const card of row.items) items.append(cardNode(card, onOpen));
    box.append(items);
  }
  return box;
}

function cardMenu(card, changed) {
  if (!card.menu || !card.menu.length) return;
  dialog({title: card.title, items: card.menu.map((item) => ({label: item.title,
    action: () => openTarget(item.target, card.title, changed)})), buttons: [{label: "Zpět"}]});
}

// --- obrazovky ---------------------------------------------------------------------------------

const stack = [];          // obrazovky nad hlavní: {kind, call, params, title}
let home = null;           // data hlavní obrazovky
let section = 0;

function setCrumbs() {
  $("#crumbs").textContent = stack.length ? stack[stack.length - 1].title || "" : "";
  document.body.classList.toggle("wide", stack.length > 0);
}

function show(screen) {
  stack.push(screen);
  setCrumbs();
  render();
}

function back() {
  if (dialogs.length) { dialogs[dialogs.length - 1].close(); return; }
  if (!stack.length) { $("#side .current") && $("#side .current").focus(); return; }
  stack.pop();
  setCrumbs();
  render(true);
}

function toMenu() {
  while (dialogs.length) dialogs[dialogs.length - 1].close();
  stack.length = 0;
  setCrumbs();
  render(true);
  const current = $("#side .current");
  if (current) current.focus();
}

function busy() {
  const screen = $("#screen");
  screen.innerHTML = "";
  screen.append(el("div", "spinner"));
}

async function render(keepFocus) {
  const screen = stack[stack.length - 1];
  if (!screen) return renderHome(keepFocus);
  if (screen.kind === "list") return renderList(screen);
  if (screen.kind === "page") return renderPage(screen);
  if (screen.kind === "title") return renderTitle(screen);
  if (screen.kind === "search") return renderSearch(screen);
  if (screen.kind === "settings") return renderSettings(screen);
  if (screen.kind === "diagnostics") return renderDiagnostics(screen);
  if (screen.kind === "playing") return renderPlaying(screen);
}

function opener(changed) {
  const open = (card) => openTarget(card.target, card.title, changed);
  open.changed = changed;
  return open;
}

async function loadHome() {
  const answer = await api("home");
  if (!answer.ok) { toast(answer.error); return; }
  const first = home === null;
  home = answer.data;
  if (first) section = home.start || 0;
  const profile = home.profile || {};
  $("#who").textContent = profile.name || "";
  $("#avatar").src = "ui/assets/avatar_" + (profile.avatar || "clapper") + ".webp";
  if (!stack.length) renderHome(!first);
}

function renderSide() {
  // Postranní nabídka se staví jednou: při pohybu šipkami se mění jen obsah sekce (překreslení
  // nabídky by výběr ztratilo).
  const side = $("#side");
  const titles = home.sections.map((item) => item.title).join("|");
  if (side.dataset.titles !== titles) {
    side.innerHTML = "";
    side.dataset.titles = titles;
    home.sections.forEach((item, index) => {
      const button = el("button", "f");
      const icon = el("img");
      icon.src = "ui/assets/ic_" + item.icon + ".svg";
      button.append(icon, el("span", "", item.title));
      button.onclick = () => { section = index; renderHome(); const first = $("#screen .f"); if (first) first.focus(); };
      button.onfocus = () => { if (section !== index) { section = index; renderHome(true); } };
      side.append(button);
    });
  }
  [...side.children].forEach((button, index) => button.classList.toggle("current", index === section));
}

function renderHome(keepFocus) {
  const screen = $("#screen");
  if (!home) { busy(); return; }
  renderSide();
  const current = home.sections[section];
  const focused = document.activeElement && document.activeElement.card && document.activeElement.card.title;
  screen.innerHTML = "";
  const changed = () => loadHome();
  if (current.hero) {
    const hero = el("div", "hero f");
    hero.tabIndex = 0;
    hero.style.backgroundImage = "url(\"" + (image(current.hero.image) || "ui/assets/hero_default.webp") + "\")";
    hero.append(el("div", "label", "Pokračovat ve sledování"), el("div", "name", current.hero.title),
      el("div", "sub", current.hero.subtitle || ""));
    const bar = el("div", "bar");
    const fill = el("i");
    fill.style.width = (current.hero.progress || 0) + "%";
    bar.append(fill);
    hero.append(bar, el("div", "btn primary", "▶  " + (current.hero.label || "Pokračovat")));
    hero.onclick = () => openTarget(current.hero.target, current.hero.title, changed);
    screen.append(hero);
  }
  screen.append(rowsNode(current.rows, opener(changed)));
  if (keepFocus && focused) {
    const again = [...screen.querySelectorAll(".card")].find((node) => node.card.title === focused);
    if (again) again.focus();
  }
}

async function renderList(screen) {
  busy();
  const answer = await api(screen.call, screen.params);
  const box = $("#screen");
  box.innerHTML = "";
  if (!answer.ok) { box.append(el("div", "empty", answer.error)); return; }
  const data = answer.data;
  if (data.title && !screen.title) { screen.title = data.title; setCrumbs(); }
  const changed = () => render(true);
  const open = (card) => {
    if (card.target.type === "filter") return showFilters(card.target, screen);
    openTarget(card.target, card.title, changed);
  };
  open.changed = changed;
  const items = data.items || [];
  if (data.status) box.append(el("div", "status", data.status));
  if (!items.length) { box.append(el("div", "empty", data.empty || "Nic tu není.")); }
  const grid = el("div", "grid" + (items.some((card) => card.wide) && items.every((card) => card.wide || !card.image) ? " wide" : ""));
  for (const card of items) grid.append(cardNode(card, open));
  box.append(grid);
  let next = data.next;
  const more = async () => {
    if (!next || stack[stack.length - 1] !== screen) return;
    const params = next.params || next;
    next = null;
    const page = await api(screen.call, params);
    if (page.ok) {
      for (const card of page.data.items || []) grid.append(cardNode(card, open));
      next = page.data.next;
    }
  };
  box.onscroll = () => { if (box.scrollTop + box.clientHeight > box.scrollHeight - 600) more(); };
  const first = grid.querySelector(".card:not(:first-child)") || grid.querySelector(".card");
  if (first && !box.contains(document.activeElement)) first.focus();
  if (data.refresh) {
    // výpis se plní (ověřování na pozadí): za chvíli znovu
    clearTimeout(screen.timer);
    screen.timer = setTimeout(() => { if (stack[stack.length - 1] === screen && !dialogs.length) renderList(screen); },
      Math.max(2, data.refresh) * 1000);
  }
}

async function showFilters(target, screen) {
  const answer = await api(target.call, target.params);
  if (!answer.ok) { toast(answer.error || "Filtr se nepodařilo načíst."); return; }
  const data = answer.data;
  const apply = (params) => { screen.params = params; render(); };
  const items = (data.filters || []).map((group) => ({label: group.title + ": " + group.value, action: () => dialog({
    title: group.title, buttons: [{label: "Zpět"}],
    items: (group.options || []).map((option) => ({label: option.title, current: option.current, action: () => apply(option.params)})),
  })}));
  if (data.reset) items.push({label: "Zrušit filtry", action: () => apply(data.reset)});
  dialog({title: data.title || "Filtr", items, buttons: [{label: "Zpět"}]});
}

async function renderPage(screen) {
  busy();
  const answer = await api(screen.call, screen.params);
  const box = $("#screen");
  box.innerHTML = "";
  if (!answer.ok) { box.append(el("div", "empty", answer.error)); return; }
  box.append(rowsNode(answer.data.rows, opener(() => render(true))));
  const first = box.querySelector(".card");
  if (first) first.focus();
}

async function renderTitle(screen) {
  if (!screen.loaded) busy();
  const answer = await api(screen.call, screen.params);
  const box = $("#screen");
  box.innerHTML = "";
  if (!answer.ok) { box.append(el("div", "empty", answer.error)); return; }
  screen.loaded = true;
  const data = answer.data;
  const detail = data.detail || {};
  const page = el("div", "title-page");
  const backdrop = el("div", "backdrop");
  if (detail.backdrop) backdrop.style.backgroundImage = "url(\"" + detail.backdrop + "\")";
  const head = el("div", "title-head");
  const poster = el("div", "poster");
  if (detail.poster) poster.style.backgroundImage = "url(\"" + detail.poster + "\")";
  const text = el("div");
  text.append(el("h1", "", detail.title || screen.title || ""), el("div", "meta", detail.meta || ""),
    el("div", "plot", detail.plot || ""));
  if (detail.file) text.append(el("div", "file", detail.file));
  const changed = () => renderTitle(screen);
  const actions = el("div", "actions");
  (data.actions || []).forEach((action, index) => {
    const label = action.subtitle ? action.title + "  ·  " + action.subtitle : action.title;
    const button = el("button", "f btn" + (index === 0 ? " primary" : ""), label);
    button.onclick = () => {
      if (action.menu && action.menu.length) {
        dialog({title: action.title, buttons: [{label: "Zpět"}], items: action.menu.map((item) => ({
          label: item.title, action: () => openTarget(item.target, item.title, changed)}))});
      } else openTarget(action.target, action.title, changed);
    };
    actions.append(button);
  });
  text.append(actions);
  head.append(poster, text);
  page.append(backdrop, head, rowsNode(data.rows, opener(changed)));
  box.append(page);
  const first = actions.querySelector("button");
  if (first) first.focus();
  if (data.retry && !screen.retried) {
    screen.retried = true;
    setTimeout(() => { if (stack[stack.length - 1] === screen) renderTitle(screen); }, 8000);
  }
}

async function renderSearch(screen) {
  const box = $("#screen");
  box.innerHTML = "";
  const form = el("div", "search-box");
  const input = el("input", "f");
  input.placeholder = "Film, seriál, herec, režisér…";
  input.value = screen.query || "";
  const go = el("button", "f btn primary", "Hledat");
  form.append(input, go);
  const results = el("div");
  box.append(form, results);
  const run = async () => {
    const query = input.value.trim();
    if (!query) return;
    screen.query = query;
    results.innerHTML = "";
    results.append(el("div", "spinner"));
    const answer = await api("search", {q: query});
    results.innerHTML = "";
    if (!answer.ok) { results.append(el("div", "empty", answer.error)); return; }
    const rows = answer.data.rows || [];
    if (!rows.length) results.append(el("div", "empty", "Nic jsem nenašel."));
    results.append(rowsNode(rows, opener(run)));
  };
  go.onclick = run;
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); run(); } });
  if (screen.query) run();
  else {
    const history = await api("search_history");
    const rows = (history.ok && history.data.rows) || [];
    if (rows.length) results.append(rowsNode(rows, (card) => {
      input.value = (card.target.params && card.target.params.q) || card.title;
      run();
    }));
  }
  input.focus();
}

async function renderSettings(screen) {
  busy();
  const answer = await api("settings_get");
  const box = $("#screen");
  box.innerHTML = "";
  if (!answer.ok) { box.append(el("div", "empty", answer.error)); return; }
  const data = answer.data;
  const folder = await local("folder");
  const form = el("div", "form");
  const field = (label, name, value, password) => {
    const wrap = el("label", "", label);
    const input = el("input", "f");
    input.name = name;
    input.type = password ? "password" : "text";
    input.value = value || "";
    if (password && data.has_password) input.placeholder = "uložené – pro změnu napište nové";
    wrap.append(input);
    form.append(wrap);
    return input;
  };
  const user = field("E-mail k Přehraj.to", "username", data.username);
  const password = field("Heslo k Přehraj.to", "password", "", true);
  const tmdb = field("Klíč TMDB (zdarma na themoviedb.org)", "tmdb_key", data.tmdb_key);
  const dir = field("Kam stahovat (složka)", "download_dir", data.download_dir || (folder.ok ? folder.data.download_dir : ""));
  const cleanup = el("button", "f btn", "Mazat stažené po dokoukání: " + (data.delete_watched ? "zapnuto" : "vypnuto"));
  cleanup.onclick = async () => { await api("settings_set", {delete_watched: !data.delete_watched}); renderSettings(screen); };
  const save = el("button", "f btn primary", "Uložit");
  save.onclick = async () => {
    const params = {username: user.value, tmdb_key: tmdb.value, download_dir: dir.value};
    if (password.value) params.password = password.value;
    const saved = await api("settings_set", params);
    toast(saved.ok ? "Uloženo." : saved.error);
    if (saved.ok) loadHome();
  };
  const buttons = el("div", "actions");
  const check = el("button", "f btn", "Ověřit přihlášení");
  check.onclick = () => openTarget({type: "command", call: "account_status", params: {}}, "Ověření přihlášení");
  const sync = el("button", "f btn", "Synchronizovat s účtem");
  sync.onclick = () => openTarget({type: "command", call: "sync", params: {}}, "Synchronizace");
  const updates = el("button", "f btn", "Nové verze na GitHubu");
  updates.onclick = () => local("open", {url: "https://github.com/petermikula-glitch/streamujtv/releases"});
  buttons.append(save, cleanup, check, sync, updates);
  form.append(buttons);
  box.append(form);
  user.focus();
}

async function renderDiagnostics(screen) {
  busy();
  const answer = await api("diagnostics");
  const box = $("#screen");
  box.innerHTML = "";
  if (!answer.ok) { box.append(el("div", "empty", answer.error)); return; }
  const lines = el("div", "lines");
  for (const line of answer.data.lines || []) {
    const row = el("div", line.ok === false ? "bad" : "");
    row.append(el("span", "", line.ok === true ? "✓" : line.ok === false ? "!" : "·"), el("b", "", line.label), el("span", "", line.value));
    lines.append(row);
  }
  const buttons = el("div", "actions");
  const again = el("button", "f btn primary", "Zkontrolovat znovu");
  again.onclick = () => renderDiagnostics(screen);
  const test = el("button", "f btn", "Zkouška Přehraj.to");
  test.onclick = () => openTarget({type: "command", call: "prehrajto_test", params: {}}, "Zkouška Přehraj.to", () => renderDiagnostics(screen));
  const report = el("button", "f btn", "Hlášení (zkopírovat)");
  report.onclick = async () => {
    const device = [{label: "Aplikace", value: "Windows (přenosná verze)", ok: null}];
    const made = await api("diagnostics_report", {device, dirs: []});
    if (!made.ok) { toast(made.error); return; }
    try { await navigator.clipboard.writeText(made.data.text); toast("Hlášení je ve schránce – vložte ho, kam potřebujete."); }
    catch (error) { dialog({title: "Hlášení", pre: made.data.text}); }
  };
  buttons.append(again, test, report);
  box.append(buttons, lines);
  if ((answer.data.errors || []).length) {
    box.append(el("h2", "row", "Poslední chyby"));
    const errors = el("div", "lines");
    for (const error of answer.data.errors) errors.append(el("div", "", error));
    box.append(errors);
  }
  again.focus();
}

// --- přehrávání (mpv) ----------------------------------------------------------------------------

const ASK_RESUME_AFTER = 60;

async function play(target, title) {
  const answer = await api(target.call, target.params);
  if (!answer.ok) { info(title || "Přehrávání", answer.error); return; }
  const stream = answer.data;
  if (stream.official || stream.drm) {
    info(title || "Chráněný pořad", "Tento pořad ČT chrání. Na počítači ho pusťte na webu iVysílání (ivysilani.cz).");
    return;
  }
  const params = target.params || {};
  const position = stream.position || 0;
  const start = (from) => startPlayer(stream, from, title);
  if (position >= ASK_RESUME_AFTER && !params.resume && !params.fresh && !stream.live) {
    const clock = (seconds) => new Date(seconds * 1000).toISOString().substr(seconds >= 3600 ? 11 : 14, seconds >= 3600 ? 8 : 5);
    dialog({title: stream.title, buttons: [{label: "Zpět"}], items: [
      {label: "Pokračovat od " + clock(position), action: () => start(position)},
      {label: "Přehrát od začátku", action: () => start(0)}]});
  } else start(position);
}

async function startPlayer(stream, position, title) {
  const started = await local("play", {stream, position});
  if (!started.ok) { info("Přehrávač", started.error); return; }
  show({kind: "playing", title: stream.title || title, stream, since: Date.now()});
}

function renderPlaying(screen) {
  const box = $("#screen");
  box.innerHTML = "";
  const panel = el("div", "playing");
  panel.append(el("div", "big", "▶  " + (screen.stream.title || "")),
    el("div", "keys", "Přehrává se v okně přehrávače.\nMezerník – pauza · šipky – posun · # – zvuková stopa · J – titulky · F – celá obrazovka · Q – konec"));
  const stop = el("button", "f btn", "Zpět do aplikace");
  stop.onclick = back;
  panel.append(stop);
  box.append(panel);
  stop.focus();
  const poll = async () => {
    if (stack[stack.length - 1] !== screen) return;
    const status = await local("player");
    const state = (status.ok && status.data) || {};
    if (state.playing !== false) { setTimeout(poll, 2000); return; }
    afterPlayback(screen, state);
  };
  setTimeout(poll, 2000);
}

async function afterPlayback(screen, state) {
  const quick = Date.now() - screen.since < 20000 && !state.ended && (state.position || 0) < 5;
  if (quick && state.choice && state.track) {
    // video se hned zavřelo: verze nejspíš nejde přehrát – zkusit jinou (jako na televizi)
    const other = await api("play_other", {track: state.track, choice: state.choice, tried: []});
    if (other.ok) {
      toast("Verzi se nepodařilo přehrát – pouštím jinou: " + (other.data.switched || ""));
      stack.pop();
      return startPlayer(other.data, other.data.position || 0, screen.title);
    }
  }
  stack.pop();
  setCrumbs();
  render(true);
  if (!state.ended) { loadHome(); return; }
  const next = await api("play_next", {track: state.track, choice: state.choice});
  loadHome();
  const following = next.ok && next.data.next;
  if (!following) return;
  let seconds = following.auto ? 10 : 0;
  const box = dialog({title: following.title, message: following.subtitle + (seconds ? "\n\nSpustí se za " + seconds + " s." : ""),
    buttons: [{label: "Přehrát", primary: true, action: () => { clearInterval(timer); openTarget(following.target, following.subtitle); }},
              {label: "Zavřít", action: () => clearInterval(timer)}]});
  const timer = setInterval(() => {
    if (!document.contains(box)) { clearInterval(timer); return; }
    seconds -= 1;
    const message = box.querySelector(".message");
    if (message && seconds > 0) message.textContent = following.subtitle + "\n\nSpustí se za " + seconds + " s.";
    if (seconds === 0 && following.auto) { clearInterval(timer); box.close(); openTarget(following.target, following.subtitle); }
  }, 1000);
}

// --- cíle karet (stejné typy jako Navigator.kt na televizi) ------------------------------------------

async function runCommand(target, title, changed) {
  if (target.confirm) {
    const ask = target.confirm;
    dialog({title: ask.title || title, message: ask.text || ask.message || "", buttons: [
      {label: "Zpět"}, {label: ask.ok || "Pokračovat", primary: true,
        action: () => runCommand(Object.assign({}, target, {confirm: null}), title, changed)}]});
    return;
  }
  const waiting = dialog({title, message: "Pracuji…", buttons: []});
  const progress = setInterval(async () => {
    const state = await api("progress");
    const message = waiting.querySelector(".message");
    if (state.ok && state.data.text && message) message.textContent = state.data.text;
  }, 1000);
  const answer = await api(target.call, target.params);
  clearInterval(progress);
  waiting.close();
  const data = answer.data || {};
  if (answer.ok && data.start) local("downloads");
  if (data.toast) toast(data.toast);
  if (answer.ok && data.open) { openTarget(data.open, title); return; }
  const finished = () => {
    if (answer.ok && data.close && stack.length && stack[stack.length - 1].kind === "list") { back(); loadHome(); }
    else if (changed) changed();
  };
  const message = answer.ok ? data.message : answer.error;
  if (message) dialog({title, message, buttons: [{label: "OK", action: finished}]});
  else finished();
}

async function showChoices(target, changed) {
  const answer = await api(target.call, target.params);
  if (!answer.ok) { toast(answer.error || "Nabídku se nepodařilo načíst."); return; }
  const data = answer.data;
  dialog({title: data.title, buttons: [{label: "Zpět"}], items: (data.filters || []).map((group) => ({
    label: group.title + ": " + group.value, action: () => dialog({title: group.title, buttons: [{label: "Zpět"}],
      items: (group.options || []).map((option) => ({label: option.title, current: option.current, action: async () => {
        const applied = await api(data.apply, option.params);
        if (!applied.ok) toast(applied.error);
        if (changed) changed();
        loadHome();
      }}))})}))});
}

function backupMenu(changed) {
  dialog({title: "Záloha a obnova", buttons: [{label: "Zpět"}], items: [
    {label: "Zálohovat", note: "Do složky data\\Zálohy vedle programu", action: async () => {
      const folder = await local("folder");
      const made = await api("backup_export", {dir: folder.ok ? folder.data.backup_dir : "", version: "windows"});
      info("Záloha", made.ok ? (made.data.message || "Záloha je uložená.") : made.error);
    }},
    {label: "Obnovit ze zálohy", action: async () => {
      const folder = await local("folder");
      const found = await api("backup_list", {dirs: folder.ok ? [{dir: folder.data.backup_dir, label: "data\\Zálohy"}] : []});
      const items = (found.ok && found.data.backups) || [];
      if (!items.length) { info("Obnova", found.ok ? "Žádná záloha se nenašla." : found.error); return; }
      dialog({title: "Kterou zálohu obnovit?", buttons: [{label: "Zpět"}], items: items.map((backup) => ({
        label: backup.title, note: backup.path, action: () => dialog({
          title: "Obnovit zálohu?", message: "Současná data aplikace se nahradí zálohou.",
          buttons: [{label: "Zpět"}, {label: "Obnovit", primary: true, action: async () => {
            const restored = await api("backup_import", {path: backup.path});
            info("Obnova", restored.ok ? "Obnoveno. Aplikaci zavřete a spusťte znovu." : restored.error);
          }}]})}))});
    }}]});
}

async function openTarget(target, title, changed) {
  if (!target) return;
  title = target.title || title || "";
  switch (target.type) {
    case "list": return show({kind: "list", call: target.call, params: target.params || {}, title});
    case "page": return show({kind: "page", call: target.call, params: target.params || {}, title});
    case "title": return show({kind: "title", call: target.call, params: target.params || {}, title});
    case "search": return show({kind: "search", title: "Hledat", query: (target.params || {}).q || ""});
    case "settings": return show({kind: "settings", title: "Nastavení a přihlášení"});
    case "diagnostics": return show({kind: "diagnostics", title: "Diagnostika"});
    case "play": return play(target, title);
    case "refresh": return changed && changed();
    case "info": return info(title, (target.params || {}).text || "");
    case "open": {
      const params = target.params || {};
      const url = params.youtube ? "https://www.youtube.com/watch?v=" + params.youtube : params.url;
      if (url) local("open", {url});
      return;
    }
    case "command": return runCommand(target, title, changed);
    case "download": {
      const answer = await api(target.call, target.params);
      toast(answer.ok ? (answer.data || {}).message : answer.error);
      if (answer.ok) { local("downloads"); if (changed) changed(); }
      return;
    }
    case "choices": return showChoices(target, changed);
    case "input": {
      const params = target.params || {};
      return dialog({title: params.hint || title, input: {value: params.value || "", hint: params.hint},
        buttons: [{label: "Zrušit"}, {label: "Uložit", primary: true, action: (text) => runCommand(
          {type: "command", call: target.call, params: Object.assign({}, params, {text: text || params.value || ""})}, title, changed)}]});
    }
    case "backup": return backupMenu(changed);
    case "storage": return show({kind: "settings", title: "Nastavení a přihlášení"});
    case "move": return info(title, "Na počítači stažené soubory přesuňte v Průzkumníku a v Nastavení změňte, kam stahovat.");
    case "menu": return;
    default: toast("Tohle zatím ve Windows verzi nejde: " + target.type);
  }
}

// --- ovládání klávesnicí ----------------------------------------------------------------------------

function focusables() {
  const root = dialogs.length ? dialogs[dialogs.length - 1] : document;
  return [...root.querySelectorAll(".f")].filter((node) => node.offsetParent !== null && !node.disabled);
}

function moveFocus(direction) {
  const current = document.activeElement;
  const nodes = focusables();
  if (!nodes.includes(current)) { if (nodes[0]) nodes[0].focus(); return; }
  const from = current.getBoundingClientRect();
  const cx = from.left + from.width / 2, cy = from.top + from.height / 2;
  let best = null, bestScore = Infinity;
  for (const node of nodes) {
    if (node === current) continue;
    const rect = node.getBoundingClientRect();
    const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
    const dx = x - cx, dy = y - cy;
    const ok = direction === "left" ? dx < -5 : direction === "right" ? dx > 5 : direction === "up" ? dy < -5 : dy > 5;
    if (!ok) continue;
    const along = direction === "left" || direction === "right" ? Math.abs(dx) : Math.abs(dy);
    const across = direction === "left" || direction === "right" ? Math.abs(dy) : Math.abs(dx);
    const score = along + across * 2.5;
    if (score < bestScore) { bestScore = score; best = node; }
  }
  if (best) { best.focus(); best.scrollIntoView({block: "nearest", inline: "nearest"}); }
}

let escapeHeld = 0;
document.addEventListener("keydown", (event) => {
  const typing = event.target.tagName === "INPUT";
  const keys = {ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down"};
  if (keys[event.key] && !(typing && (event.key === "ArrowLeft" || event.key === "ArrowRight"))) {
    event.preventDefault();
    moveFocus(keys[event.key]);
  } else if (event.key === "Escape" || (event.key === "Backspace" && !typing)) {
    event.preventDefault();
    if (event.repeat) {
      escapeHeld += 1;
      if (escapeHeld === 8 && stack.length) toMenu();     // podržený Esc: rovnou hlavní nabídka
    } else { escapeHeld = 0; back(); }
  } else if ((event.key === "ContextMenu" || (event.key === "m" && !typing)) && document.activeElement.card) {
    event.preventDefault();
    cardMenu(document.activeElement.card, document.activeElement.onclick && (() => render(true)));
  } else if (event.key === "Enter" && !typing && document.activeElement.classList.contains("card")) {
    event.preventDefault();
    document.activeElement.click();
  } else if (event.key === "/" && !typing && !dialogs.length) {
    event.preventDefault();
    show({kind: "search", title: "Hledat"});
  }
});
document.addEventListener("mousedown", (event) => {
  if (event.button === 3) back();     // tlačítko Zpět na myši
});

$("#search").onclick = () => show({kind: "search", title: "Hledat"});
$("#profile").onclick = () => show({kind: "list", call: "profiles", params: {}, title: "Kdo se dívá?"});

// okno se hlásí, že je otevřené (v prohlížeči podle toho aplikace pozná, že ji uživatel zavřel)
setInterval(() => local("alive"), 10000);
local("alive");

// --- start -------------------------------------------------------------------------------------------

(async () => {
  busy();
  await loadHome();
  const current = $("#side .current");
  if (current) current.focus();
  api("quick_sync").then((answer) => {
    if (answer.ok && (answer.data.new > 0 || answer.data.refresh)) { toast(answer.data.toast); loadHome(); }
  });
  const downloads = await api("downloads_pending");
  if (downloads.ok && downloads.data.pending) local("downloads");
})();
