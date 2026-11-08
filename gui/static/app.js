"use strict";

// Everything shown here that did not come from strings.json came from the
// router, GitHub or the OS, so it only ever reaches the page as text. This page
// holds a token that can drive a flash: nothing is assigned to innerHTML.

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const LANG_KEY = "xiaomi-openwrt-install-lang";
// Every language that has a catalogue in strings.json, in switch order. The
// button is labelled with the next language's own name, so the choice is
// legible before it is made, and no language needs translating into another.
const LANGS = ["en", "ru", "zh-CN", "pt-BR"];
const LANG_NAMES = { en: "English", ru: "Русский", "zh-CN": "中文", "pt-BR": "Português" };
const STAGES = ["preflight", "backup", "pivot", "flash", "verify"];
const SEQUENCE = ["mode", "check", "config", "review", "run"];

const STATE = {
  step: "mode", mode: null, opts: {}, runId: null, stage: null, dry: false,
  prereqs: null, interfaces: [], releases: null, launching: false, result: null,
  hint: false, message: null,
};

let STRINGS = { en: {} };
let LANG = "en";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function t(key, values) {
  // A catalogue that failed to load must not blank the page: fall back to
  // English, then to the key itself, exactly as i18n.t() does in the CLI.
  let text = (STRINGS[LANG] || {})[key] ?? STRINGS.en[key] ?? key;
  for (const [name, value] of Object.entries(values || {})) {
    text = text.replaceAll(`{${name}}`, value);
  }
  return text;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function api(path, opts = {}) {
  const sep = path.includes("?") ? "&" : "?";
  return fetch(`${path}${sep}t=${encodeURIComponent(TOKEN)}`, {
    headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    ...opts,
  });
}

function setNote(node, text) {
  node.hidden = !text;
  node.textContent = text || "";
}

// ---- language ----

function preferredLanguage() {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (LANGS.includes(saved)) return saved;
  } catch (_) { /* a private window may refuse storage */ }
  const tag = (navigator.language || "").toLowerCase().replace("_", "-");
  if (tag.startsWith("zh")) return "zh-CN";
  if (tag.startsWith("pt")) return "pt-BR";
  return tag.startsWith("ru") ? "ru" : "en";
}

function nextLanguage() {
  return LANGS[(LANGS.indexOf(LANG) + 1) % LANGS.length];
}

function applyLanguage() {
  document.documentElement.lang = LANG;
  document.title = t("app.title");
  const button = $("#lang-switch");
  if (button) button.textContent = LANG_NAMES[nextLanguage()];
  $$("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
  $$("[data-i18n-placeholder]").forEach((n) => {
    n.placeholder = t(n.dataset.i18nPlaceholder);
  });
  renderCheck();
  renderConfig();
  renderReview();
  renderRun();
}

$("#lang-switch").addEventListener("click", () => {
  LANG = nextLanguage();
  try { localStorage.setItem(LANG_KEY, LANG); } catch (_) { /* see above */ }
  applyLanguage();
});

// ---- navigation ----

function show(step) {
  STATE.step = step;
  $$(".panel").forEach((p) => { p.hidden = p.dataset.panel !== step; });
  const at = SEQUENCE.indexOf(step);
  $$("#steps li").forEach((li, index) => {
    li.classList.toggle("active", li.dataset.step === step);
    li.classList.toggle("done", index < at);
  });
  window.scrollTo(0, 0);
}

$$("[data-nav]").forEach((b) => b.addEventListener("click", () => show(b.dataset.nav)));

// ---- 1. mode ----

$$(".card").forEach((c) => c.addEventListener("click", () => {
  STATE.mode = c.dataset.mode;
  startCheck();
}));

// ---- 2. check ----

async function startCheck() {
  show("check");
  STATE.prereqs = await api("/api/prereqs").then((r) => r.json());
  renderCheck();
}

function renderCheck() {
  const r = STATE.prereqs;
  if (!r) return;
  const note = $("#platform-note");
  note.textContent = t(`prereq.platform.${r.platform}`);
  note.className = "note " + (r.platform_ok && r.platform !== "wsl" ? "ok" : "bad");
  const list = $("#checklist");
  list.replaceChildren(...r.checks.map((c) => {
    const li = el("li", c.ok ? "ok" : "bad");
    const tick = el("span", "tick");
    tick.append($(c.ok ? "#icon-ok" : "#icon-bad").content.cloneNode(true));
    const detail = t(`prereq.${c.id}`) + (c.version ? ` (${c.version})` : "");
    li.append(tick, el("span", "ck-label", c.label), el("span", "ck-detail", detail));
    return li;
  }));
  $("#install-hint").hidden = r.ready || !r.hint_install;
  $("#install-hint-command").textContent = r.hint_install;
  $("#check-next").disabled = !r.ready;
}

$("#recheck").addEventListener("click", startCheck);
$("#check-next").addEventListener("click", startConfig);

// ---- 3. config ----

async function startConfig() {
  show("config");
  setNote($("#config-error"), null);
  $$("[data-when]").forEach((node) => {
    node.hidden = !node.dataset.when.split(" ").includes(STATE.mode);
  });
  renderConfig();
  const [ifaces, releases] = await Promise.all([
    api("/api/interfaces").then((r) => r.json()),
    api("/api/releases?device=rd03v2").then((r) => r.json()),
  ]);
  STATE.interfaces = ifaces.interfaces || [];
  STATE.releases = releases;
  renderConfig();
}

function option(value, label) {
  const node = el("option", "", label);
  node.value = value;
  return node;
}

function renderConfig() {
  if (STATE.mode) $("#config-lede").textContent = t(`config.lede.${STATE.mode}`);

  const iface = $("#interface");
  const chosen = iface.value;
  iface.replaceChildren(
    option("", t("iface.auto")),
    ...STATE.interfaces.map((i) => option(
      i.name,
      [i.name, i.transport, i.ipv4[0], i.state].filter(Boolean).join(" · "))));
  iface.value = chosen;

  const rel = STATE.releases;
  const release = $("#release");
  const chosenRelease = release.value;
  const tags = ((rel && rel.releases) || []).filter((r) => r.tag && r.tag !== "latest");
  release.replaceChildren(
    option("latest", t("release.latest")),
    ...tags.map((r) => option(r.tag, [r.tag, r.prerelease ? t("release.pre") : "", r.published]
      .filter(Boolean).join(" · "))));
  if (chosenRelease) release.value = chosenRelease;
  if (!release.value) release.value = "latest";
  $("#release-note").textContent = t(rel && rel.error ? "release.unavailable" : "release.default");
}

function validate(o) {
  const chosen = STATE.interfaces.find((i) => i.name === o.interface);
  const onWifi = chosen && chosen.transport === "wifi";
  if (STATE.mode === "revert") {
    if (!o.stock_image) return "err.stock_image_required";
    if (!o.interface) return "err.interface_required";
  }
  if (STATE.mode === "install") {
    if (onWifi && !(o.wifi_ssid && o.wifi_key && o.wifi_country)) return "err.wifi_required";
    if (!o.wifi_ssid && (o.wifi_key || o.wifi_country)) return "err.wifi_ssid_required";
    if (o.wifi_ssid) {
      if (!/^[\x20-\x7e]{8,63}$/.test(o.wifi_key)) return "err.wifi_key";
      if (!/^[A-Z]{2}$/.test(o.wifi_country)) return "err.wifi_country";
    }
  }
  if (STATE.mode === "update" && o.keep_settings === false && onWifi) {
    return "err.clean_over_wifi";
  }
  return null;
}

$("#config-next").addEventListener("click", () => {
  const o = { interface: $("#interface").value, release: $("#release").value || "latest" };
  if (STATE.mode === "revert") o.stock_image = $("#stock-image").value.trim();
  if (STATE.mode === "install") {
    o.wifi_ssid = $("#wifi-ssid").value.trim();
    o.wifi_key = $("#wifi-key").value;
    o.wifi_country = $("#wifi-country").value.trim().toUpperCase();
    o.root_password = $("#root-password").value;
  }
  if (STATE.mode === "update") {
    o.root_password = $("#current-password").value;
    o.keep_settings = $("#keep-settings").checked;
    o.config_backup = $("#config-backup").value.trim();
  }
  const problem = validate(o);
  setNote($("#config-error"), problem ? t(problem) : null);
  if (problem) return;
  STATE.opts = o;
  startReview();
});

// ---- 4. review ----

function startReview() {
  setNote($("#review-error"), null);
  $("#confirm-real").hidden = true;
  $("#confirm-word").value = "";
  $("#confirm-go").disabled = true;
  show("review");
  renderReview();
}

function renderReview() {
  if (!STATE.mode) return;
  const o = STATE.opts;
  const rows = [
    ["review.action", t(`review.action.${STATE.mode}`)],
    ["review.interface", o.interface || t("iface.auto")],
    ["review.release", o.release || "latest"],
  ];
  if (STATE.mode === "revert") rows.push(["review.stock_image", o.stock_image || ""]);
  if (STATE.mode === "update") {
    rows.push(["review.settings",
      t(o.keep_settings === false ? "review.settings.clean" : "review.settings.keep")]);
    rows.push(["review.current_password", t(o.root_password ? "review.set" : "review.unset")]);
    if (o.config_backup) rows.push(["review.config_backup", o.config_backup]);
  }
  if (STATE.mode === "install") {
    if (o.wifi_ssid) rows.push(["review.wifi_ssid", o.wifi_ssid]);
    if (o.wifi_country) rows.push(["review.wifi_country", o.wifi_country]);
    rows.push(["review.root_password", t(o.root_password ? "review.set" : "review.unset")]);
  }
  $("#review").replaceChildren(...rows.map(([key, value]) => {
    const row = el("div", "rrow");
    row.append(el("span", "", t(key)), el("b", "", value));
    return row;
  }));
  $("#safety-route").textContent = t(STATE.mode === "install" ? "safety.install" : "safety.nofallback");
  const advisory = STATE.mode === "update"
    ? `${t(o.keep_settings === false ? "advisory.clean" : "advisory.keep")} ${t("advisory.untested")}`
    : null;
  setNote($("#review-advisory"), advisory);
  $("#confirm-label").textContent = t("confirm.label", { word: t("confirm.word") });
  checkWord();
}

function checkWord() {
  const typed = $("#confirm-word").value.trim().toUpperCase();
  $("#confirm-go").disabled = STATE.launching || typed !== t("confirm.word");
}

$("#run-dry").addEventListener("click", () => launch(true));
$("#run-real").addEventListener("click", () => {
  $("#confirm-real").hidden = false;
  $("#confirm-word").focus();
});
$("#confirm-word").addEventListener("input", checkWord);
$("#confirm-go").addEventListener("click", () => launch(false));

// ---- 5. run ----

async function launch(dry) {
  if (STATE.launching) return;       // the server refuses a second run as well
  STATE.launching = true;
  $("#run-dry").disabled = true;
  $("#run-real").disabled = true;
  checkWord();
  setNote($("#review-error"), null);
  try {
    const res = await api("/api/run", {
      method: "POST",
      body: JSON.stringify({ mode: STATE.mode, options: { ...STATE.opts, dry_run: dry } }),
    });
    const data = await res.json();
    if (!res.ok) {
      setNote($("#review-error"),
        data.code === "busy" ? t("err.busy") : t("err.request", { detail: data.error || res.status }));
      return;
    }
    Object.assign(STATE, {
      runId: data.run_id, dry, stage: null, result: null, hint: false, message: null,
    });
    $("#log").replaceChildren();
    $("#term-head").classList.add("running");
    ["#wifi-banner", "#output-box", "#resume-box", "#run-done"].forEach((s) => { $(s).hidden = true; });
    $("#run-cancel").hidden = false;
    show("run");
    renderRun();
    stream(data.run_id);
  } catch (error) {
    setNote($("#review-error"), t("err.request", { detail: String(error) }));
  } finally {
    STATE.launching = false;
    $("#run-dry").disabled = false;
    $("#run-real").disabled = false;
    checkWord();
  }
}

function renderRun() {
  if (!STATE.mode) return;
  const action = t(`review.action.${STATE.mode}`);
  const title = STATE.dry ? `${t("run.dry_prefix")} · ${action}` : action;
  $("#run-title").textContent = STATE.runId ? title : t("run.h1");
  $("#term-label").textContent = STATE.result === null
    ? (STATE.runId ? title : t("term.waiting"))
    : t(STATE.result.ok ? "term.done" : "term.failed");

  const tracker = $("#tracker");
  if (STATE.dry) {
    tracker.replaceChildren(el("div", "trk-dry", t("run.dry_tracker")));
  } else {
    const at = STAGES.indexOf(STATE.stage);
    tracker.replaceChildren(...STAGES.map((stage, index) => {
      const state = at < 0 ? "" : index < at ? "done" : index === at ? "active" : "";
      const node = el("div", `trk ${state}`.trim());
      node.append(el("span", "dot"), document.createTextNode(t(`stage.${stage}`)));
      return node;
    }));
  }

  setNote($("#run-hint"), STATE.hint ? t("hint.ssl") : null);
  setNote($("#run-message"), STATE.message ? t(STATE.message) : null);
  const result = $("#run-result");
  if (STATE.result === null) {
    setNote(result, null);
  } else {
    result.className = "note " + (STATE.result.ok ? "ok" : "bad");
    setNote(result, STATE.result.ok ? t("run.ok") : t("run.failed", { code: STATE.result.code }));
  }
}

function lineClass(line) {
  if (line.startsWith("[+]")) return "l-ok";
  if (line.startsWith("[-]")) return "l-err";
  if (line.startsWith("[!]")) return "l-warn";
  if (line.startsWith("===")) return "l-step";
  if (line.startsWith("[*]")) return "l-dim";
  return "";
}

function stream(runId) {
  // The server honours Last-Event-ID, so a reconnect continues rather than
  // replaying the log.
  const es = new EventSource(`/api/stream/${runId}?t=${encodeURIComponent(TOKEN)}`);
  const log = $("#log");
  es.onmessage = (message) => {
    const ev = JSON.parse(message.data);
    if (ev.type === "log") {
      log.append(el("span", lineClass(ev.line), `${ev.line}\n`));
      log.scrollTop = log.scrollHeight;
    } else if (ev.type === "stage") {
      STATE.stage = ev.stage;
      renderRun();
    } else if (ev.type === "wifi") {
      $("#wifi-ssid-out").textContent = ev.ssid;
      $("#wifi-key-out").textContent = ev.key;
      $("#wifi-banner").hidden = false;
    } else if (ev.type === "hint") {
      STATE.hint = true;
      renderRun();
    } else if (ev.type === "resume") {
      $("#resume-path").textContent = ev.path;
      $("#resume-box").hidden = false;
    } else if (ev.type === "output_dir") {
      $("#output-path").textContent = ev.path;
      $("#output-box").hidden = false;
    } else if (ev.type === "done") {
      es.close();
      STATE.result = { ok: ev.ok, code: ev.code };
      $("#term-head").classList.remove("running");
      $("#run-cancel").hidden = true;
      $("#run-done").hidden = false;
      renderRun();
    }
  };
}

$("#run-cancel").addEventListener("click", async () => {
  if (!STATE.runId) return;
  const r = await api(`/api/cancel/${STATE.runId}`, { method: "POST", body: "{}" }).then((x) => x.json());
  STATE.message = r.ok ? null : `cancel.${r.code}`;
  renderRun();
});

// ---- start ----

(async function init() {
  // A catalogue that cannot be fetched must not leave the page blank: t()
  // falls back to English and then to the key, but only if this resolves.
  STRINGS = await fetch("strings.json")
    .then((r) => r.json())
    .catch(() => ({ en: {} }));
  LANG = preferredLanguage();
  applyLanguage();
  show("mode");
})();
