/* ================================================================
   АДРЕСА

   `#/projects/<id>/<раздел>` — открытый проект и раздел, `#/home` —
   лендинг, `#/` — старт. Адрес пишется при каждой смене проекта, раздела
   и экрана, а при загрузке и кнопках «Назад» / «Вперёд» читается обратно:
   перезагрузка страницы возвращает туда же (GAP-007).
   ================================================================ */
let applyingRoute = false;

function currentRoute() {
  if (!document.querySelector("#screen-home").hidden) return "#/home";
  if (currentProject && !document.querySelector("#workspace").hidden) {
    return `#/projects/${currentProject.id}/${currentView}`;
  }
  return "#/";
}

// Последний открытый проект — для настройки кабинета «Открывать после входа».
const LAST_ROUTE_KEY = "sav-analytics:last-route";
const START_SCREEN_KEY = "sav-analytics:start-screen";

function writeRoute() {
  if (applyingRoute) return;
  const hash = currentRoute();
  if (location.hash !== hash) history.pushState(null, "", hash);
  if (hash.startsWith("#/projects/")) {
    try { localStorage.setItem(LAST_ROUTE_KEY, hash); } catch { /* без хранилища не запоминаем */ }
  }
}

/* Пустой адрес при загрузке ведёт туда, что выбрано в личном кабинете:
   к последнему проекту или к списку проектов (по умолчанию). */
function restoreStartRoute() {
  if (location.hash && location.hash !== "#/") return;
  try {
    const last = localStorage.getItem(LAST_ROUTE_KEY);
    if (localStorage.getItem(START_SCREEN_KEY) === "last-project" && last) {
      history.replaceState(null, "", last);
    }
  } catch { /* без хранилища — стартовый экран */ }
}

async function applyRoute() {
  const match = location.hash.match(/^#\/projects\/([0-9a-f-]{36})(?:\/([a-z]+))?$/);
  applyingRoute = true;
  try {
    if (location.hash === "#/home") {
      window.Shell.showScreen("home");
      return;
    }
    window.Shell.showScreen("manual");
    if (!match) {
      if (currentProject) document.querySelector("#new-project").click();
      return;
    }
    const view = SECTION_VIEWS.includes(match[2]) ? match[2] : "data";
    if (currentProject?.id === match[1]) {
      if (currentView !== view) setView(view);
      return;
    }
    try {
      showProject(await api(`/api/projects/${match[1]}`), view);
    } catch (error) {
      history.replaceState(null, "", "#/");
      try { localStorage.removeItem(LAST_ROUTE_KEY); } catch { /* нечего чистить */ }
      showError(errorBox, error);
    }
  } finally {
    applyingRoute = false;
  }
  writeRoute();
}

document.addEventListener("shell:screen", writeRoute);
window.addEventListener("popstate", () => { void applyRoute(); });
