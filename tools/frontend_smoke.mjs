// Димова перевірка інтерфейсу Mini App в емульованому браузері.
//   1) запустіть сервер:  python tools/preview.py --time "2026-10-12 11:40" [--week home] [--as-member]
//   2) node tools/frontend_smoke.mjs http://localhost:8080 admin|member|home
import { JSDOM } from "jsdom";

const BASE = process.argv[2] || "http://localhost:8080";
const MODE = process.argv[3] || "admin";

let failed = 0;
function check(name, cond, extra = "") {
  console.log(`${cond ? "  ✓" : "  ✗ ПРОВАЛ:"} ${name}${!cond && extra ? "  -> " + extra : ""}`);
  if (!cond) failed++;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const html = await (await fetch(BASE + "/")).text();
const alerts = [];
const dom = new JSDOM(html, {
  url: BASE + "/",
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = (u, o) => fetch(new URL(u, BASE), o);
    window.Telegram = { WebApp: {
      initData: "", ready() {}, expand() {},
      showAlert(m) { alerts.push(m); }, showConfirm(m, cb) { cb(true); },
      HapticFeedback: { notificationOccurred() {} },
    } };
  },
});
const { document } = dom.window;
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const text = () => document.getElementById("app").textContent;
async function until(fn, ms = 4000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (fn()) return true; await sleep(50); } return false; }
const clickTab = (label) => $$(".tab").find((t) => t.textContent.includes(label)).click();

console.log(`\nРежим: ${MODE}`);
check("сторінка завантажила дані", await until(() => $(".page")));

console.log("\n[Сьогодні]");
check("банер тривоги (жовтий рівень)", /Жовтий рівень небезпеки/.test($(".banner")?.textContent || ""));
check("на сьогодні 8 уроків (з нульовим)", $$(".card .lesson").length === 8, String($$(".card .lesson").length));
check("урок «Математика» є в списку", text().includes("Математика"));
const now = $("#nowcard")?.textContent || "";
check("картка «Зараз» показує поточний урок і зворотний відлік", /Математика/.test(now) && /До кінця/.test(now), now);

if (MODE === "home") {
  check("бейдж «Тиждень вдома»", text().includes("Тиждень вдома"));
  check("замість «скасовано» - «Класрум»", text().includes("Класрум") && !text().includes("скасовано"));
  check("кабінети приховані", !text().includes("каб."));
  check("немає позначок «дистанційно» на 7-му уроці", !$$(".chip").some((c) => c.textContent === "дистанційно"));
} else {
  check("бейдж «Очний тиждень»", text().includes("Очний тиждень"));
  check("скасований урок має позначку «скасовано»", $$(".lesson.cancelled").length === 1 && text().includes("скасовано"));
  check("кабінет показано (каб. 303)", text().includes("каб. 303"));
  check("додатковий 0-й урок позначено", $$(".chip").some((c) => c.textContent === "додатковий"));
  check("7-й урок підтверджено очно", $$(".chip").some((c) => c.textContent === "очно ✓"));
}

console.log("\n[Вкладки]");
const tabs = $$(".tab").map((t) => t.textContent);
if (MODE === "member") check("звичайний учень НЕ бачить вкладку «Адмін»", !tabs.some((t) => t.includes("Адмін")), tabs.join("|"));
else check("адмін бачить вкладку «Адмін»", tabs.some((t) => t.includes("Адмін")), tabs.join("|"));

clickTab("Тиждень");
check("«Тиждень»: показано понеділок і п'ятницю", text().includes("Понеділок") && text().includes("П'ятниця"));
clickTab("Тривоги");
check("«Тривоги»: статистика завантажилась", await until(() => text().includes("Статистика")));
check("«Тривоги»: є історія тривог", $$(".sess").length >= 2, String($$(".sess").length));
clickTab("Сьогодні");

console.log("\n[Дії]");
if (MODE === "member") {
  check("рядки уроків не клікабельні для учня", $$(".lesson.tappable").length === 0);
} else if (MODE === "admin") {
  const row = $$(".card .lesson").find((l) => l.textContent.includes("Англ. мова"));
  row.click();
  check("клік по уроку відкриває шторку дій", !!$(".sheet") && $(".sheet").textContent.includes("Скасувати урок"));
  $$(".sheet .btn").find((b) => b.textContent.includes("Скасувати урок")).click();
  check("після «Скасувати» урок з'являється як скасований", await until(() => $$(".lesson.cancelled").length === 2));
  check("шторка закрилась", !$(".sheet"));
  check("помилок не було", alerts.length === 0, alerts.join("; "));

  // повертаємо урок назад
  $$(".card .lesson").find((l) => l.textContent.includes("Англ. мова")).click();
  $$(".sheet .btn").find((b) => b.textContent.includes("Повернути урок")).click();
  check("«Повернути урок» знімає скасування", await until(() => $$(".lesson.cancelled").length === 1));

  clickTab("Адмін");
  check("вкладка «Адмін»: є форма оголошення і додавання уроку", text().includes("Оголошення в чат") && text().includes("Позаплановий урок"));
}

dom.window.close();
console.log(failed ? `\n❌ Провалено перевірок: ${failed}\n` : "\n✅ Усі перевірки інтерфейсу пройдено\n");
process.exit(failed ? 1 : 0);
