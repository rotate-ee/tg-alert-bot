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
        posts.push({ path: p, body: opts.body ? JSON.parse(opts.body) : {} });
        if (p === "/api/subscribe") return subscribeStatus === 200 ? respond(200, { ok: true, topics: JSON.parse(opts.body).topics }) : respond(subscribeStatus, { error: "start_bot" });
        if (p === "/api/admin/backup/send") return respond(200, { ok: true, files: 3, bytes: 1234 });
        return respond(200, { ok: true });
      }
      if (p === "/api/state") return respond(200, stateFor(url));
      if (p === "/api/me") return respond(200, fixture.me);
      if (p === "/api/alert_stats") return respond(200, fixture.alert_stats);
      if (p === "/api/admin/overview") return respond(200, fixture.overview);
      if (p === "/api/admin/activity") return respond(200, fixture.activity);
      if (p === "/api/duties") return respond(200, fixture.duties);
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
const sleep = ms => new Promise(r => setTimeout(r, ms));
const text = id => (doc.getElementById(id) || {}).textContent || "";
const app = () => doc.getElementById("app").textContent.replace(/\s+/g, " ");

(async () => {
  await sleep(900);
  // розклад
  check("schedule: stale banner in schedule", /застарілі/.test(doc.querySelector(".ban")?.textContent || ""));
  check("schedule: personal notification toggles", doc.querySelectorAll("[data-sub]").length === 2);

  // вкладка тривог
  doc.querySelector('[data-t="al"]').click();
  await sleep(500);
  const staleBox = doc.getElementById("alert-stale");
  check("alerts: stale warning visible", staleBox && !staleBox.hidden && /не оновлюються/.test(staleBox.textContent) && /тестова помилка/.test(staleBox.textContent), "admin sees the reason");
  const hist = doc.getElementById("alert-history-list").textContent;
  check("alerts: lesson interrupted shown in history", /Перервала:/.test(hist));
  check("alerts: false alert marked", /хибна/.test(hist));
  const stats = doc.getElementById("alert-stats-block");
  check("alerts: statistics block rendered with data", stats && /за 30 днів/.test(stats.querySelector(".fold-meta").textContent) && /Вплив на навчання/.test(stats.textContent) && /перервали/.test(stats.textContent));
  check("alerts: statistics charts (30 days + 24 hours)", stats.querySelectorAll('div[title]').length >= 54);
  check("alerts: admin tools present", !!doc.getElementById("alert-add") && doc.querySelectorAll("[data-clear-month]").length >= 1);

  // підписка на особисті сповіщення
  const alertsBox = doc.querySelector('[data-sub="alerts"]');
  alertsBox.checked = true; alertsBox.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await sleep(300);
  check("subscribe: POST with chosen topics", posts.some(p => p.path === "/api/subscribe" && JSON.stringify(p.body.topics) === '["alerts"]'));
  check("subscribe: checkbox stays on after success", doc.querySelector('[data-sub="alerts"]').checked === true);
  subscribeStatus = 409;
  const changesBox = doc.querySelector('[data-sub="changes"]');
  changesBox.checked = true; changesBox.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await sleep(300);
  check("subscribe: 409 shows 'start the bot' sheet", doc.getElementById("sheet").classList.contains("on") && /Спочатку запустіть бота/.test(text("panel")));
  check("subscribe: checkbox reverted after 409", doc.querySelector('[data-sub="changes"]').checked === false);
  doc.getElementById("sheet").classList.remove("on");

  // адмінка: резервна копія
  doc.querySelector('[data-t="adm"]').click();
  await sleep(600);
  const backupBtn = doc.getElementById("backup-send");
  check("admin: backup button present", !!backupBtn);
  if (backupBtn) { backupBtn.click(); await sleep(400); }
  check("admin: backup request sent", posts.some(p => p.path === "/api/admin/backup/send"));
  check("admin: toast confirms backup", /Копію надіслано/.test(text("toast")));

  check("no uncaught JS errors", errors.length === 0, errors.join("; "));
  console.log(failures.length ? `\n${failures.length} FAILED: ${failures.join(", ")}` : "\nAll miniapp checks passed");
  process.exit(failures.length ? 1 : 0);
})();
