"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const api = (path, opts = {}) => {
  const sep = path.includes("?") ? "&" : "?";
  return fetch(`${path}${sep}t=${encodeURIComponent(TOKEN)}`, {
    headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    ...opts,
  });
};

const STATE = { mode: null, opts: {}, runId: null, stage: null };
const STAGES = ["preflight", "backup", "pivot", "flash", "verify"];
const STAGE_LABELS = {
  preflight: "Проверка", backup: "Резерв", pivot: "RAM-установщик",
  flash: "Запись", verify: "Проверка результата",
};
const MODE_TITLE = {
  install: "Установка OpenWrt", update: "Обновление OpenWrt",
  revert: "Возврат на сток",
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function show(step) {
  $$(".panel").forEach((p) => (p.hidden = p.dataset.panel !== step));
  $$("#steps li").forEach((li) => {
    const order = STAGES; // not used; sequence below
    li.classList.toggle("active", li.dataset.step === step);
  });
  const seq = ["mode", "check", "config", "review", "run"];
  const i = seq.indexOf(step);
  $$("#steps li").forEach((li, k) => li.classList.toggle("done", k < i));
  window.scrollTo(0, 0);
}

// ---- 1. mode ----
$$(".card").forEach((c) =>
  c.addEventListener("click", () => {
    STATE.mode = c.dataset.mode;
    startCheck();
  }));

// ---- 2. check ----
async function startCheck() {
  show("check");
  const r = await api("/api/prereqs").then((x) => x.json());
  const note = $("#platform-note");
  note.textContent = r.platform_note;
  note.className = "note " + (r.platform_ok ? "ok" : "bad");
  const ICON_OK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  const ICON_BAD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
  $("#checklist").innerHTML = r.checks
    .map((c) => `<li class="${c.ok ? "ok" : "bad"}">
        <span class="tick">${c.ok ? ICON_OK : ICON_BAD}</span>
        <span class="ck-label">${c.label}</span>
        <span class="ck-detail">${c.detail}</span></li>`)
    .join("");
  const hint = $("#install-hint");
  if (!r.ready && r.hint_install) {
    hint.hidden = false;
    hint.innerHTML = `Установите недостающее:<br><code>${r.hint_install}</code>`;
  } else hint.hidden = true;
  $("#check-next").disabled = !r.ready;
}
$("#recheck").addEventListener("click", startCheck);
$("#check-next").addEventListener("click", startConfig);

// ---- 3. config ----
async function startConfig() {
  show("config");
  $("#config-lede").textContent = {
    install: "Проверьте интерфейс и релиз. Остальное — по желанию.",
    update: "Укажите интерфейс и целевой релиз. UART держите под рукой как страховку.",
    revert: "Укажите путь к одобренному стоковому образу и интерфейс.",
  }[STATE.mode];

  $$("[data-when]").forEach((el) => {
    el.hidden = !el.dataset.when.split(" ").includes(STATE.mode);
  });

  // interfaces
  const ifaces = await api("/api/interfaces").then((x) => x.json());
  const sel = $("#interface");
  sel.innerHTML = '<option value="">— авто —</option>' +
    ifaces.interfaces.map((i) =>
      `<option value="${i.name}">${i.name} · ${i.transport}${i.ipv4[0] ? " · " + i.ipv4[0] : ""} · ${i.state}</option>`).join("");

  // releases
  const rel = await api(`/api/releases?device=rd03v2`).then((x) => x.json());
  const rs = $("#release");
  const opts = ['<option value="latest">latest (последний стабильный)</option>'];
  (rel.releases || []).forEach((r) => {
    if (r.tag === "latest") return;
    opts.push(`<option value="${r.tag}">${r.tag}${r.prerelease ? " (pre)" : ""} · ${r.published}</option>`);
  });
  rs.innerHTML = opts.join("");
  $("#release-note").textContent = rel.error
    ? "Список с GitHub недоступен — можно выбрать latest."
    : "По умолчанию — последний стабильный.";
}

$("#config-next").addEventListener("click", () => {
  const o = {};
  o.interface = $("#interface").value;
  o.release = $("#release").value || "latest";
  if (STATE.mode === "revert") o.stock_image = $("#stock-image").value.trim();
  if (STATE.mode === "install" || STATE.mode === "update") {
    o.wifi_ssid = $("#wifi-ssid").value.trim();
    o.wifi_key = $("#wifi-key").value.trim();
    o.wifi_country = $("#wifi-country").value.trim();
    o.root_password = $("#root-password").value;
  }
  if (STATE.mode === "update") {
    const up = $("#update-root").value;
    if (up) o.root_password = up;
    o.keep_settings = $("#keep-settings").checked;
  }
  STATE.opts = o;
  startReview();
});

// ---- 4. review ----
function startReview() {
  show("review");
  const o = STATE.opts;
  const rows = [
    ["Действие", MODE_TITLE[STATE.mode]],
    ["Интерфейс", o.interface || "авто"],
    ["Релиз", o.release],
  ];
  if (STATE.mode === "revert") rows.push(["Стоковый образ", o.stock_image || "—"]);
  if (STATE.mode === "update")
    rows.push(["Настройки", o.keep_settings === false ? "чистая установка" : "сохранить (sysupgrade)"]);
  if (o.wifi_ssid) rows.push(["Wi-Fi SSID", o.wifi_ssid]);
  if (o.wifi_country) rows.push(["Страна", o.wifi_country]);
  rows.push(["Пароль root", o.root_password ? "задан" : "не задан"]);
  $("#review").innerHTML = rows
    .map(([k, v]) => `<div class="rrow"><span>${k}</span><b>${v}</b></div>`)
    .join("");
  const adv = $("#review-advisory");
  if (STATE.mode === "update") {
    adv.hidden = false;
    adv.textContent = o.keep_settings === false
      ? "Чистая установка: настройки будут сброшены к заводским. Бэкап всё " +
        "равно сохраняется на ваш компьютер."
      : "Настройки сохранятся (из бэкапа, как sysupgrade), но установленные " +
        "пакеты слетят — доустановите их после загрузки.";
  } else {
    adv.hidden = true;
  }
  $("#confirm-real").hidden = true;
  $("#confirm-word").value = "";
  $("#confirm-go").disabled = true;
}

$("#run-dry").addEventListener("click", () => launch(true));
$("#run-real").addEventListener("click", () => {
  $("#confirm-real").hidden = false;
  $("#confirm-word").focus();
});
$("#confirm-word").addEventListener("input", (e) => {
  $("#confirm-go").disabled = e.target.value.trim().toUpperCase() !== "ПРОШИТЬ";
});
$("#confirm-go").addEventListener("click", () => launch(false));

// ---- 5. run ----
async function launch(dry) {
  const opts = { ...STATE.opts, dry_run: dry };
  const res = await api("/api/run", {
    method: "POST",
    body: JSON.stringify({ mode: STATE.mode, options: opts }),
  });
  const data = await res.json();
  if (!res.ok) { alert("Ошибка: " + (data.error || res.status)); return; }
  STATE.runId = data.run_id;
  show("run");
  $("#run-title").textContent =
    (dry ? "Пробный прогон · " : "") + MODE_TITLE[STATE.mode];
  renderTracker(null, dry);
  $("#log").textContent = "";
  $("#term-head").classList.add("running");
  $("#term-label").textContent = (dry ? "пробный прогон · " : "") + MODE_TITLE[STATE.mode];
  $("#wifi-banner").hidden = true;
  const oldHint = document.getElementById("run-hint");
  if (oldHint) oldHint.remove();
  $("#resume-box").hidden = true;
  $("#run-done").hidden = true;
  $("#run-cancel").hidden = false;
  stream(data.run_id);
}

function renderTracker(active, dry) {
  if (dry) { $("#tracker").innerHTML =
    '<div class="trk-dry">Пробный прогон — запись не выполняется</div>'; return; }
  $("#tracker").innerHTML = STAGES.map((s) => {
    const ai = STAGES.indexOf(active);
    const si = STAGES.indexOf(s);
    const cls = active == null ? "" : si < ai ? "done" : si === ai ? "active" : "";
    return `<div class="trk ${cls}"><span class="dot"></span>${STAGE_LABELS[s]}</div>`;
  }).join('<span class="trk-sep"></span>');
}

function stream(runId) {
  const es = new EventSource(`/api/stream/${runId}?t=${encodeURIComponent(TOKEN)}`);
  const log = $("#log");
  const lineClass = (s) => {
    if (s.startsWith("[+]")) return "l-ok";
    if (s.startsWith("[-]")) return "l-err";
    if (s.startsWith("[!]")) return "l-warn";
    if (s.startsWith("===")) return "l-step";
    if (s.startsWith("[*]")) return "l-dim";
    return "";
  };
  const appendLine = (s) => {
    const span = document.createElement("span");
    const cls = lineClass(s);
    if (cls) span.className = cls;
    span.textContent = s + "\n";
    log.appendChild(span);
    log.scrollTop = log.scrollHeight;
  };
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === "log") {
      appendLine(ev.line);
    } else if (ev.type === "stage") {
      STATE.stage = ev.stage;
      renderTracker(ev.stage, false);
    } else if (ev.type === "wifi") {
      $("#wifi-ssid-out").textContent = ev.ssid;
      $("#wifi-key-out").textContent = ev.key;
      $("#wifi-banner").hidden = false;
    } else if (ev.type === "hint") {
      let h = document.getElementById("run-hint");
      if (!h) {
        h = document.createElement("div");
        h.id = "run-hint";
        h.className = "note bad";
        $("#wifi-banner").after(h);
      }
      h.hidden = false;
      h.textContent = ev.text;
    } else if (ev.type === "resume" || ev.type === "output_dir") {
      const box = $("#resume-box");
      box.hidden = false;
      box.innerHTML = (ev.type === "resume"
        ? "Манифест для восстановления: <code>" + ev.path + "</code>"
        : "Логи и бэкапы: <code>" + ev.path + "</code>");
    } else if (ev.type === "done") {
      es.close();
      $("#term-head").classList.remove("running");
      $("#term-label").textContent = ev.ok ? "установщик · завершено" : "установщик · ошибка";
      $("#run-cancel").hidden = true;
      $("#run-done").hidden = false;
      const banner = document.createElement("div");
      banner.className = "note " + (ev.ok ? "ok" : "bad");
      banner.textContent = ev.ok
        ? "Готово — операция завершилась успешно"
        : "Операция завершилась с ошибкой (код " + ev.code + ") — смотрите лог выше";
      const term = log.closest(".term");
      (term || log).after(banner);
    }
  };
  es.onerror = () => { /* keep open; server closes when done */ };
}

$("#run-cancel").addEventListener("click", async () => {
  if (!STATE.runId) return;
  const r = await api(`/api/cancel/${STATE.runId}`, { method: "POST", body: "{}" })
    .then((x) => x.json());
  if (!r.ok) alert(r.message || "Нельзя отменить");
});

// nav buttons
$$("[data-nav]").forEach((b) =>
  b.addEventListener("click", () => show(b.dataset.nav)));

show("mode");
