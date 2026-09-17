/* Полоса запуска отчёта: проверка перед сборкой и история запусков. Классический скрипт после app.js (см. решение 016): пользуется
   его состоянием, а app.js вызывает эти функции только после запуска. */

/* ---------------- Полоса запуска ---------------- */

async function loadReportPreflight() {
  if (!currentProject) return;
  const container = document.querySelector("#report-preflight");
  const revision = currentProject.configuration?.revision ?? null;
  if (preflightCache.report && preflightCache.revision === revision) {
    renderLaunchStatus(preflightCache.report);
    return;
  }
  container.innerHTML = '<span class="pf">Проверяем настройки отчёта…</span>';
  const projectId = currentProject.id;
  try {
    const report = await api(`/api/projects/${projectId}/reports/preflight`);
    preflightCache = { revision, report };
    if (currentProject?.id === projectId && currentView === "reports") renderLaunchStatus(report);
  } catch (error) {
    container.innerHTML = `<span class="pf error"><i></i>${escapeHtml(error.message)}</span>`;
  }
}

// История запусков — индекс неизменных сборок проекта. Кэшируется на проект
// и сбрасывается после каждой сборки: только тогда в ней что-то меняется.
const runHistoryCache = new Map();

async function loadRunHistory(force = false) {
  if (!currentProject) return;
  const projectId = currentProject.id;
  if (!force && runHistoryCache.has(projectId)) {
    renderRunHistory(runHistoryCache.get(projectId));
    return;
  }
  try {
    const history = await api(`/api/projects/${projectId}/reports/history`);
    runHistoryCache.set(projectId, history.runs);
    if (currentProject?.id === projectId && currentView === "reports") renderRunHistory(history.runs);
  } catch (error) {
    const box = document.querySelector("#report-runs");
    if (box) box.innerHTML = `<p class="runs-empty">${escapeHtml(error.message)}</p>`;
  }
}

function renderRunHistory(runs) {
  const box = document.querySelector("#report-runs");
  if (!box) return;
  if (!runs.length) {
    box.innerHTML = '<p class="runs-empty">Отчёт ещё не собирался. Каждая сборка появится здесь и останется доступной.</p>';
    return;
  }
  box.innerHTML = runs.map(run => {
    const when = new Date(run.created_at).toLocaleString("ru-RU", {
      day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
    });
    const table = run.kind === "table";
    const parts = [`ревизия <b>${escapeHtml(run.configuration_revision)}</b>`];
    const summary = run.summary;
    if (summary) {
      if (table) parts.unshift(summary.scope === "table" ? "выгрузка таблицы" : "отчёт по разрезу «Таблиц»");
      parts.push(escapeHtml(plural(summary.questions, "вопрос", "вопроса", "вопросов")));
      parts.push(summary.banner ? `разрез «${escapeHtml(summary.banner)}»` : "только Total");
      if (summary.filter) parts.push(`фильтр «${escapeHtml(summary.filter)}»`);
      if (summary.weight) parts.push(`вес ${escapeHtml(summary.weight)}`);
    }
    const current = run.current ? '<span class="run-current">текущие настройки</span>' : "";
    const links = table
      ? `<a href="${escapeAttribute(run.downloads.table)}">Excel</a>`
      : `<a href="${escapeAttribute(run.downloads.topline)}">Excel</a><a href="${escapeAttribute(run.downloads.statistics)}">statistics.txt</a>`;
    return `<div class="run">
      <time datetime="${escapeAttribute(run.created_at)}">${escapeHtml(when)}${current}</time>
      <span class="run-meta">${parts.join(" · ")}</span>
      <span class="run-links">${links}</span>
    </div>`;
  }).join("");
}

function renderLaunchStatus(report) {
  const container = document.querySelector("#report-preflight");
  const rows = [
    ...(report.errors || []).map(item => ({ item, kind: "error" })),
    ...(report.warnings || []).map(item => ({ item, kind: "warn" })),
  ];
  container.innerHTML = rows.length
    ? rows.map(({ item, kind }) => `<span class="pf ${kind}"><i></i>${escapeHtml(item.message)}</span>`).join("")
    : '<span class="pf ok"><i></i>Проверка пройдена: отчёт можно собирать</span>';
  document.querySelector("#launch-report").disabled = !report.can_prepare;
}
