// Перевірка міні-застосунку: синтаксис, баланс CSS і прогін реальних екранів у jsdom з реальними відповідями API.
//   node tests/check_miniapp.js /tmp/fixture.json   (fixture робить tests/make_fixture.py)
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "..", "miniapp", "index.html"), "utf8");
const fixture = JSON.parse(fs.readFileSync(process.argv[2] || "fixture.json", "utf8"));
const failures = [];
const check = (name, ok, extra = "") => { console.log(`${ok ? "OK  " : "FAIL"} ${name}${extra ? " | " + extra : ""}`); if (!ok) failures.push(name); };

// 1) синтаксис усіх скриптів і баланс фігурних дужок у CSS
const scripts = html.split(/<script>/).slice(1).map(x => x.split("</script>")[0]);
let syntax = true;
scripts.forEach(js => { try { new Function(js); } catch (e) { syntax = false; console.log("  syntax error:", e.message); } });
check("JS syntax", syntax);
const css = html.match(/<style>([\s\S]*?)<\/style>/)[1];
check("CSS braces balanced", (css.match(/{/g) || []).length === (css.match(/}/g) || []).length);
const trackRule = (css.match(/\.ticker-track\{[^}]*\}/) || [""])[0];
check("ticker: GPU-accelerated (will-change, translate3d, backface-visibility)", /will-change:transform/.test(trackRule) && /translate3d\(0,0,0\)/.test(trackRule) && /backface-visibility:hidden/.test(trackRule) && /translate3d\(-50%,0,0\)/.test(css));

// 2) екрани
const posts = [];
let subscribeStatus = 200;
const respond = (status, body) => ({ ok: status < 400, status, json: async () => body });
const days = fixture.state.days;
const stateFor = (url) => {
  const q = new URL(url).searchParams, from = q.get("from"), n = +(q.get("days") || 7);
  const keys = Object.keys(days).sort().filter(k => !from || (k >= from && k < new Date(new Date(from + "T00:00:00Z").getTime() + n * 864e5).toISOString().slice(0, 10)));
  return { ...fixture.state, days: Object.fromEntries(keys.map(k => [k, days[k]])) };
};
const errors = [];
const dom = new JSDOM(html, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "https://example.github.io/app/?api=" + encodeURIComponent("http://bot.test"),
  beforeParse(w) {
    w.fetch = async (url, opts = {}) => {
      const p = new URL(url).pathname;
      if ((opts.method || "GET") === "POST") {
        const body = opts.body ? JSON.parse(opts.body) : {};
        posts.push({ path: p, body });
        if (p === "/api/subscribe") return subscribeStatus === 200 ? respond(200, { ok: true, topics: body.topics }) : respond(subscribeStatus, { error: "start_bot" });
        if (p === "/api/admin/backup/send") return respond(200, { ok: true, files: 3, bytes: 1234 });
        if (p === "/api/admin/broadcast") return respond(200, body.test ? { ok: true, test: true } : { ok: true, total: fixture.users_stats.audience[body.audience] });
        return respond(200, { ok: true });
      }
      if (p === "/api/state") return respond(200, stateFor(url));
      if (p === "/api/me") return respond(200, fixture.me);
      if (p === "/api/alert_stats") return respond(200, fixture.alert_stats);
      if (p === "/api/admin/overview") return respond(200, fixture.overview);
      if (p === "/api/admin/activity") return respond(200, fixture.activity);
      if (p === "/api/admin/users") return respond(200, fixture.users_stats);
      if (p === "/api/duties") return respond(200, fixture.duties);
      if (p === "/api/power") return respond(200, fixture.power);
      return respond(404, {});
    };
    w.Telegram = { WebApp: { ready() {}, expand() {}, initData: fixture.init_data, isVersionAtLeast: () => true,
      requestWriteAccess(cb) { cb && cb(true); }, openTelegramLink() {}, showConfirm(m, cb) { cb(true); }, HapticFeedback: { selectionChanged() {} } } };
    w.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
    w.scrollTo = () => {};
    w.addEventListener("error", e => errors.push(e.message));
  },
});
const doc = dom.window.document;
const Ev = dom.window.Event;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const text = id => (doc.getElementById(id) || {}).textContent || "";
const click = async (sel, wait = 250) => { const el = doc.querySelector(sel); if (!el) { check("element exists: " + sel, false); return null; } el.click(); await sleep(wait); return el; };

(async () => {
  await sleep(900);
  // розклад
  check("schedule: stale banner in schedule", /застарілі/.test(doc.querySelector(".ban")?.textContent || ""));
  check("schedule: personal notification toggles", doc.querySelectorAll("[data-sub]").length === 2);

  // тривоги: Зараз
  await click('[data-t="al"]', 500);
  check("alerts: three sections", doc.querySelectorAll("[data-aview]").length === 3 && doc.querySelector('[data-aview="now"]').classList.contains("on"));
  const hero = doc.getElementById("av-hero");
  check("alerts: hero shows state + stale warning with reason (admin)", hero && /не оновлюються/.test(hero.textContent) && /тестова помилка/.test(hero.textContent) && /Повітряна тривога/.test(hero.textContent));
  check("alerts: 24h timeline for today", !!doc.querySelector("#av-today-wrap .av-24-track .now") && doc.querySelectorAll("#av-today-wrap .av-24-track i").length >= 2);
  check("alerts: map embed from alerts.in.ua", /alerts\.in\.ua\/\?embed/.test(doc.getElementById("alert-map-frame")?.src || ""));
  check("alerts: notification toggles shown on 'now'", doc.querySelectorAll("[data-sub]").length === 2);

  // підписка на особисті сповіщення
  const alertsBox = doc.querySelector('[data-sub="alerts"]');
  alertsBox.checked = true; alertsBox.dispatchEvent(new Ev("change", { bubbles: true }));
  await sleep(300);
  check("subscribe: POST with chosen topics", posts.some(p => p.path === "/api/subscribe" && JSON.stringify(p.body.topics) === '["alerts"]'));
  check("subscribe: checkbox stays on after success", doc.querySelector('[data-sub="alerts"]').checked === true);
  subscribeStatus = 409;
  const changesBox = doc.querySelector('[data-sub="changes"]');
  changesBox.checked = true; changesBox.dispatchEvent(new Ev("change", { bubbles: true }));
  await sleep(300);
  check("subscribe: 409 shows 'start the bot' sheet", doc.getElementById("sheet").classList.contains("on") && /Спочатку запустіть бота/.test(text("panel")));
  check("subscribe: checkbox reverted after 409", doc.querySelector('[data-sub="changes"]').checked === false);
  doc.getElementById("sheet").classList.remove("on");

  // бігуча строка: один постійний елемент, що не перезапускається при кожному оновленні екрана
  const tickerNode = doc.querySelector(".ticker");
  check("ticker: rendered once in its own slot", !!tickerNode && doc.getElementById("ticker-slot").contains(tickerNode) && !doc.getElementById("app").querySelector(".ticker"));

  // тривоги: Історія
  await click('[data-aview="hist"]');
  const rows = [...doc.querySelectorAll("#av-history .av-row")];
  check("history: grouped rows rendered as buttons for admin", rows.length >= 3 && rows.every(r => r.tagName === "BUTTON" && r.dataset.alertId), `${rows.length} rows`);
  check("history: day groups with counts", doc.querySelectorAll("#av-history .av-day").length >= 2 && /тривог/.test(doc.querySelector("#av-history .av-day span").textContent));
  check("history: interrupted lessons + false alert marked", /Перервала:/.test(text("av-history")) && /хибна/.test(text("av-history")));
  check("history: admin tools (add, clear by month)", !!doc.getElementById("alert-add") && doc.querySelectorAll("[data-clear-month]").length >= 1);
  const before = doc.querySelectorAll("#av-history .av-row").length;
  await click('[data-afilter="lessons"]');
  const filtered = [...doc.querySelectorAll("#av-history .av-row")];
  check("history: filter 'on lessons' keeps only interrupting alerts", filtered.length >= 1 && filtered.length < before && filtered.every(r => /Перервала:/.test(r.textContent)));
  await click('[data-afilter="all"]');

  // тривоги: Статистика
  await click('[data-aview="stats"]', 400);
  check("stats: period chips + 4 KPI cards", doc.querySelectorAll("[data-period]").length === 3 && doc.querySelectorAll(".alerts-page > .av-kpis .av-kpi").length === 4);
  check("stats: stacked chart with 30 clickable bars", doc.querySelectorAll("svg.av-svg [data-bar]").length === 30);
  const readout0 = doc.querySelector(".av-readout").textContent;
  const bars = [...doc.querySelectorAll("svg.av-svg [data-bar]")];
  bars[3].dispatchEvent(new Ev("click", { bubbles: true })); await sleep(200);
  check("stats: tapping a bar updates the day readout", doc.querySelector(".av-readout").textContent !== readout0);
  await click('[data-period="7"]');
  check("stats: 7-day period shows 7 bars", doc.querySelectorAll("svg.av-svg [data-bar]").length === 7);
  await click('[data-period="30"]');
  check("stats: 24h ring with 24 segments and peak", doc.querySelectorAll("svg.av-ring path").length === 24 && /пік/.test(doc.querySelector("svg.av-ring").textContent));
  check("stats: week strip with 7 days", doc.querySelectorAll(".av-week-row").length === 7);
  check("stats: lessons impact, records, months", /Вплив на навчання/.test(text("app")) && /Найдовша тривога/.test(text("app")) && /По місяцях/.test(text("app")) && doc.querySelectorAll(".av-hbar").length >= 2);

  // старий бот (ще без нових полів статистики) не повинен ламати застосунок
  const full = JSON.parse(JSON.stringify(fixture.alert_stats));
  fixture.alert_stats = { generated: full.generated, totals: full.totals, by_hour: full.by_hour, by_month: full.by_month, lessons: full.lessons,
    by_day: full.by_day.slice(-30).map(({ date, count, minutes }) => ({ date, count, minutes })) };
  await click('[data-t="sch"]', 300); await click('[data-t="al"]', 500); await click('[data-aview="stats"]', 500);
  check("old bot payload: stats still render (no crash overlay)", !doc.getElementById("fatal-overlay") && doc.querySelectorAll(".alerts-page > .av-kpis .av-kpi").length === 4 && doc.querySelectorAll("svg.av-svg [data-bar]").length === 30);
  check("old bot payload: hint that the bot is updating", /ще оновлюється/.test(text("app")));
  await click('[data-period="90"]', 300);
  check("old bot payload: 90-day period does not crash", !doc.getElementById("fatal-overlay") && doc.querySelectorAll("svg.av-svg [data-bar]").length === 30);
  fixture.alert_stats = full;

  // світло
  const pdays = fixture.power.snapshot.days, pkeys = Object.keys(pdays).sort();
  Object.assign(pdays[pkeys[0]].queues, { "1-І": [[0, 1440]], "2-І": [] });       // детермінований стан "зараз"
  pdays[pkeys[0]].levels = [{ s: 0, e: 1440, n: 2, text: "2 черги" }];
  await click('[data-t="pw"]', 500);
  check("power: 5 tabs for admin, no truncated emoji labels", doc.querySelectorAll("#tabs [data-t]").length === 5 && !/🚨/.test(doc.getElementById("tabs").textContent));
  check("power: queue picker with 12 sub-queues", doc.querySelectorAll(".pw-grid .pw-q").length === 12);
  check("power: day chips today/tomorrow", [...doc.querySelectorAll("[data-pday]")].map(b => b.textContent).join() === "Сьогодні,Завтра");
  await click('.pw-q[data-pq="1-І"]');
  const pwHero = () => doc.querySelector(".av-hero");
  check("power: whole-day outage shows 'no electricity'", pwHero().classList.contains("rd") && /Світла немає/.test(pwHero().textContent));
  check("power: selected queue is remembered", dom.window.localStorage.getItem("power_queue") === "1-І");
  await click('.pw-q[data-pq="2-І"]');
  check("power: queue without outages shows 'electricity is on'", pwHero().classList.contains("ok") && /Світло є/.test(pwHero().textContent));
  check("power: 24h chart, mode strip and all-queues table", !!doc.querySelector("section.av-card .av-24-track") && /Режим обмежень/.test(text("app")) && doc.querySelectorAll(".pw-row").length === 12);
  await click('.pw-row[data-pq="1-І"]');
  check("power: tapping a row in 'all queues' selects that queue", doc.querySelector(".pw-q.on").dataset.pq === "1-І");
  check("power: interval list for selected queue", /1 доба|24:00|24 год/.test(doc.querySelector(".av-list").textContent) || doc.querySelectorAll(".av-list .av-row").length >= 1);
  await click('[data-pday="' + pkeys[1] + '"]');
  check("power: tomorrow summary", /Завтра/.test(pwHero().textContent) && !pwHero().classList.contains("rd"));
  check("power: DPEM notes and source link", /Примітки ДПЕМ/.test(text("app")) && /info\.dpem\.mk\.ua/.test(doc.getElementById("app").innerHTML));
  check("power: admin controls present", !!doc.getElementById("pw-notify") && !!doc.getElementById("pw-refresh"));
  doc.getElementById("pw-refresh").click(); await sleep(400);
  check("power: manual refresh request", posts.some(p => p.path === "/api/admin/power/refresh"));
  const sw = doc.getElementById("pw-notify"); sw.checked = false; sw.dispatchEvent(new Ev("change", { bubbles: true })); await sleep(400);
  check("power: chat-notification switch saved", posts.some(p => p.path === "/api/admin/settings" && p.body.key === "power_notify" && p.body.value === false));
  check("ticker: same DOM node survived all re-renders (animation not restarted)", doc.querySelector(".ticker") === tickerNode);

  // адмінка
  await click('[data-t="adm"]', 700);
  const folds = [...doc.querySelectorAll(".admin-fold")].map(f => f.querySelector("summary").textContent);
  check("admin: users/starts and broadcast folds present", folds.some(t => /Користувачі та запуски/.test(t)) && folds.some(t => /Розсилка в ЛС/.test(t)));
  const usersFold = [...doc.querySelectorAll(".admin-fold")].find(f => /Користувачі та запуски/.test(f.textContent));
  check("admin: starts KPIs, per-day chart and recent starters", usersFold.querySelectorAll(".av-kpi").length === 4 && !!usersFold.querySelector("svg.av-svg") && usersFold.querySelectorAll(".av-row").length >= 1);
  const backupBtn = doc.getElementById("backup-send");
  check("admin: backup button present", !!backupBtn);
  if (backupBtn) { backupBtn.click(); await sleep(400); }
  check("admin: backup request sent", posts.some(p => p.path === "/api/admin/backup/send"));
  check("admin: toast confirms backup", /Копію надіслано/.test(text("toast")));

  // розсилка
  doc.getElementById("bc-send").click(); await sleep(200);
  check("broadcast: empty text is rejected", !posts.some(p => p.path === "/api/admin/broadcast") && /Введіть текст/.test(text("toast")));
  const ta = doc.getElementById("bc-text"); ta.value = "Завтра уроки з 9:00"; ta.dispatchEvent(new Ev("input", { bubbles: true }));
  check("broadcast: character counter", /19 \/ 3500/.test(text("bc-count")));
  doc.getElementById("bc-test").click(); await sleep(300);
  check("broadcast: test goes with test=true", posts.some(p => p.path === "/api/admin/broadcast" && p.body.test === true && p.body.text === "Завтра уроки з 9:00"));
  doc.getElementById("bc-send").click(); await sleep(500);
  const sent = posts.filter(p => p.path === "/api/admin/broadcast" && !p.body.test)[0];
  check("broadcast: send posts audience + button flag", !!sent && sent.body.audience === "all" && sent.body.button === true);
  check("broadcast: toast reports recipients", /Розсилку запущено/.test(text("toast")));

  check("no uncaught JS errors", errors.length === 0, errors.join("; "));
  console.log(failures.length ? `\n${failures.length} FAILED: ${failures.join(", ")}` : "\nAll miniapp checks passed");
  process.exit(failures.length ? 1 : 0);
})();
