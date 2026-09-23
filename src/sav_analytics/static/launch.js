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

/* ---------------- Кнопки выгрузки ---------------- */

// Одна и та же процедура на два входа: меню выгрузки и полоса запуска
// раздела «Отчёт». Вид артефакта берётся из data-report-kind.
document.querySelectorAll("#download-report, #download-statistics, #launch-report, #launch-statistics")
  .forEach(link => {
    link.dataset.defaultLabel = link.textContent;
    link.addEventListener("click", downloadPreparedReport);
  });

/* ---------------- Задание сборки: запомнить, продолжить после перезагрузки, скачать ---------------- */

const REPORT_JOB_KEY = "sav-analytics:report-job";

function rememberReportJob(result) {
  if (!result?.job_id || !["queued", "running"].includes(result.status)) return;
  try {
    sessionStorage.setItem(REPORT_JOB_KEY, JSON.stringify({ project: currentProject.id, job: result.job_id }));
  } catch {
    // Без хранилища сборка просто не восстановится после перезагрузки.
  }
}

function forgetReportJob() {
  try {
    sessionStorage.removeItem(REPORT_JOB_KEY);
  } catch {
    // см. rememberReportJob
  }
}

async function resumeReportJob() {
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(REPORT_JOB_KEY) || "null");
  } catch {
    saved = null;
  }
  if (!saved || !currentProject || saved.project !== currentProject.id) return;
  const feedback = document.querySelector("#report-feedback");
  let status = document.querySelector("#report-status");
  if (!status) {
    status = document.createElement("span");
    status.id = "report-status";
    status.className = "report-status";
    status.setAttribute("role", "status");
    feedback.append(status);
  }
  feedback.hidden = false;
  status.classList.remove("error");
  const projectId = currentProject.id;
  let result;
  try {
    do {
      const response = await fetch(`/api/projects/${projectId}/reports/jobs/${saved.job}`);
      if (!response.ok) {
        // Задание знает только процесс сервера: после его перезапуска номера нет.
        forgetReportJob();
        status.textContent = "Сборка, начатая до перезагрузки, не найдена — запустите её заново.";
        return;
      }
      result = await response.json();
      if (currentProject?.id !== projectId) return;
      if (result.status === "queued" || result.status === "running") {
        status.textContent = `Сборка продолжается: ${result.stage} · ${result.progress || 0}%`;
        await new Promise(resolve => window.setTimeout(resolve, 700));
      }
    } while (result.status === "queued" || result.status === "running");
  } catch {
    return;
  }
  forgetReportJob();
  if (result.status === "failed") {
    status.textContent = result.error || "Сборка, начатая до перезагрузки, не удалась.";
    status.classList.add("error");
    return;
  }
  const links = Object.entries(result.downloads || {})
    .map(([kind, url]) => `<a href="${escapeAttribute(url)}" class="report-ready-link">${kind === "statistics" ? "statistics.txt" : "Excel"}</a>`)
    .join(" · ");
  status.innerHTML = `Отчёт, собиравшийся до перезагрузки, готов: ${links}`;
  if (currentView === "reports") void loadRunHistory(true);
}

async function downloadPreparedReport(event) {
  event.preventDefault();
  const link = event.currentTarget;
  if (!currentProject || link.getAttribute("aria-disabled") === "true") return;
  const downloads = [...document.querySelectorAll("#download-report, #download-statistics, #launch-report, #launch-statistics")];
  const feedback = document.querySelector("#report-feedback");
  feedback.hidden = false;
  let status = document.querySelector("#report-status");
  if (!status) {
    status = document.createElement("span");
    status.id = "report-status";
    status.className = "report-status";
    status.setAttribute("role", "status");
    feedback.append(status);
  }
  let progress = document.querySelector("#report-progress");
  if (!progress) {
    progress = document.createElement("progress");
    progress.id = "report-progress";
    progress.className = "report-progress";
    progress.max = 100;
    feedback.append(progress);
  }
  let findings = document.querySelector("#report-findings");
  if (!findings) {
    findings = document.createElement("ul");
    findings.id = "report-findings";
    findings.className = "report-findings";
    feedback.append(findings);
  }
  findings.innerHTML = "";
  findings.hidden = true;
  progress.hidden = false;
  progress.value = 0;
  downloads.forEach(item => item.setAttribute("aria-disabled", "true"));
  link.textContent = "Формируется…";
  status.textContent = "Проверяем настройки отчёта…";
  status.classList.remove("error");
  let keepOpen = false;
  try {
    // Проверка идёт до запуска: ошибки конфигурации должны быть видны сразу,
    // а не через минуту ожидания сборки.
    const preflight = await api(`/api/projects/${currentProject.id}/reports/preflight`);
    keepOpen = renderPreflightFindings(findings, preflight);
    if (!preflight.can_prepare) {
      progress.hidden = true;
      throw new Error("Отчёт не сформирован: сначала исправьте ошибки настройки.");
    }
    status.textContent = "Готовим Excel и статистику. Для большого отчёта это может занять около минуты.";
    let result = await api(`/api/projects/${currentProject.id}/reports/prepare`, {
      method: "POST",
    });
    rememberReportJob(result);
    while (result.status === "queued" || result.status === "running") {
      progress.value = result.progress || 0;
      status.textContent = `${result.stage} · ${result.progress || 0}%`;
      await new Promise(resolve => window.setTimeout(resolve, 500));
      result = await api(
        `/api/projects/${currentProject.id}/reports/jobs/${result.job_id}`
      );
    }
    forgetReportJob();
    if (result.status === "failed") {
      throw new Error(result.error || "Не удалось сформировать отчёт.");
    }
    progress.value = 100;
    const downloadKind = link.dataset.reportKind
      || (link.id === "download-statistics" ? "statistics" : "topline");
    const preparedUrl = result.downloads?.[downloadKind];
    if (!preparedUrl) {
      throw new Error("Сервер не вернул ссылку на подготовленный отчёт.");
    }
    const preparedLink = document.createElement("a");
    preparedLink.href = preparedUrl;
    document.body.append(preparedLink);
    preparedLink.click();
    preparedLink.remove();
    status.textContent = result.cached
      ? "Готовый файл взят из кэша."
      : "Отчёт сформирован. Повторные скачивания будут мгновенными.";
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
    keepOpen = true;
  } finally {
    downloads.forEach(item => item.setAttribute("aria-disabled", "false"));
    link.textContent = link.dataset.defaultLabel;
    // Полоса запуска перерисовывает свой вердикт: сборка могла изменить
    // ревизию конфигурации, а с ней и результат проверки.
    if (currentView === "reports") void loadReportPreflight();
    // Сборка — единственное, что меняет историю запусков.
    if (currentProject) runHistoryCache.delete(currentProject.id);
    if (currentView === "reports") void loadRunHistory(true);
    // Найденные проблемы и ошибки остаются на экране: их нужно прочитать,
    // а не поймать взглядом за три секунды.
    if (!keepOpen) {
      window.setTimeout(() => {
        progress.hidden = true;
        feedback.hidden = true;
      }, 3500);
    } else {
      progress.hidden = true;
    }
  }
}

// Возвращает true, если есть что читать и панель не надо закрывать по таймеру.
function renderPreflightFindings(container, preflight) {
  const rows = [
    ...(preflight.errors || []).map(item => ({ item, kind: "error" })),
    ...(preflight.warnings || []).map(item => ({ item, kind: "warning" })),
  ];
  container.hidden = rows.length === 0;
  container.innerHTML = rows.map(({ item, kind }) => {
    const label = kind === "error" ? "Ошибка" : "Предупреждение";
    return `<li class="finding finding-${kind}"><b>${label}</b>${escapeHtml(item.message)}</li>`;
  }).join("");
  return rows.length > 0;
}
