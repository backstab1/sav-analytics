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

function writeRoute() {
  if (applyingRoute) return;
  const hash = currentRoute();
  if (location.hash !== hash) history.pushState(null, "", hash);
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
      showError(errorBox, error);
    }
  } finally {
    applyingRoute = false;
  }
  writeRoute();
}

document.addEventListener("shell:screen", writeRoute);
window.addEventListener("popstate", () => { void applyRoute(); });
