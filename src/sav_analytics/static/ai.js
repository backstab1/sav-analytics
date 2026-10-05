/* ИИ проекта (PQ.16, решение 034): выключатель «без ИИ», согласие при
   первом вызове, фоновые задачи и разбор анкеты.

   Разбор анкеты идёт фоновой задачей: файл уходит на сервер, модель
   сопоставляет анкету с массивом, сервер проверяет предложения и отдаёт
   строки «было → станет». Человек отмечает нужные, и они применяются одной
   ревизией — одним шагом «Отменить». */

const aiTypeLabels = {
  single_choice: "Один ответ",
  scale: "Шкала",
  numeric: "Числовой",
  open_text: "Открытый текст",
};
const aiKindLabels = {
  question_label: "Подписи вопросов",
  question_type: "Типы вопросов",
  variable_label: "Подписи переменных и пунктов",
  value_label: "Подписи кодов",
  order: "Порядок вопросов",
};

let aiStatus = null;
let aiJobsTimer = null;
let aiJobsKnown = new Map();
let questionnaireJobId = null;
let questionnaireRows = [];

function aiProjectSettings() {
  const stored = currentProject?.ai || {};
  return { enabled: stored.enabled !== false, acknowledged: Boolean(stored.acknowledged) };
}

/* Вызывается при каждой отрисовке проекта: подпись выключателя и задачи. */
function renderAiChrome() {
  if (!currentProject) return;
  const { enabled } = aiProjectSettings();
  document.querySelector("#ai-toggle").textContent = enabled ? "ИИ в проекте: включён" : "ИИ в проекте: выключен";
  document.querySelector("#load-questionnaire").hidden = !enabled;
  if (aiJobsKnown.projectId !== currentProject.id) {
    aiJobsKnown = new Map();
    aiJobsKnown.projectId = currentProject.id;
    void pollAiJobs();
  }
}

async function loadAiStatus() {
  aiStatus = await api(`/api/projects/${currentProject.id}/ai`);
  return aiStatus;
}

/* Перед первым вызовом ИИ в проекте человек видит, что и кому уйдёт.
   Возвращает true, если можно продолжать. */
async function ensureAiAllowed() {
  const status = await loadAiStatus();
  if (!status.configured) {
    alert("ИИ не подключён: на сервере не задан провайдер модели. Обратитесь к администратору.");
    return false;
  }
  if (!status.enabled) {
    alert("В этом проекте ИИ выключен. Включите его в меню «Выгрузка» → «ИИ в проекте».");
    return false;
  }
  if (status.acknowledged) return true;
  document.querySelector("#ai-consent-body").innerHTML = `
    <p>Чтобы выполнить задачу, приложение отправит провайдеру модели
      <b>${escapeHtml(status.provider || "—")}</b> данные проекта:</p>
    <ul class="ai-consent-list">
      <li>подписи вопросов, переменных и кодов, текст анкеты;</li>
      <li>значения массива и тексты открытых ответов, когда задача их требует.</li>
    </ul>
    <p>Все числа отчёта по-прежнему считает приложение, модель их только описывает.
      Если анкета или ответы не должны покидать контур, выключите ИИ в этом проекте —
      ручная работа останется доступной целиком.</p>
    <p class="muted">Модель: ${escapeHtml(status.model || "—")}${status.fast_model && status.fast_model !== status.model ? `, для массовых задач — ${escapeHtml(status.fast_model)}` : ""}.</p>`;
  openSheet(document.querySelector("#ai-consent-sheet"));
  return new Promise(resolve => {
    const accept = document.querySelector("#ai-consent-accept");
    const disable = document.querySelector("#ai-consent-disable");
    const finish = async choice => {
      accept.removeEventListener("click", onAccept);
      disable.removeEventListener("click", onDisable);
      sheetVeil.removeEventListener("click", onVeil);
      try {
        const settings = choice === "accept" ? { acknowledged: true } : { enabled: false };
        if (choice !== "dismiss") await setAiSettings(settings);
      } catch (error) {
        alert(error.message);
        resolve(false);
        return;
      }
      if (choice !== "dismiss") closeSheet();
      resolve(choice === "accept");
    };
    const onAccept = () => void finish("accept");
    const onDisable = () => void finish("disable");
    const onVeil = event => {
      if (event.target === sheetVeil || event.target.closest("[data-close-sheet]")) void finish("dismiss");
    };
    accept.addEventListener("click", onAccept);
    disable.addEventListener("click", onDisable);
    sheetVeil.addEventListener("click", onVeil);
  });
}

async function setAiSettings(settings) {
  aiStatus = await api(`/api/projects/${currentProject.id}/ai`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  currentProject.ai = { enabled: aiStatus.enabled, acknowledged: aiStatus.acknowledged };
  // Запись проекта подняла ревизию — без неё следующая правка упрётся в конфликт.
  currentProject = await api(`/api/projects/${currentProject.id}`);
  renderAiChrome();
}

document.querySelector("#ai-toggle").addEventListener("click", async () => {
  if (!currentProject) return;
  const { enabled } = aiProjectSettings();
  if (enabled && !confirm("Выключить ИИ в этом проекте? Загрузка анкеты, кодирование открытых ответов и автоотчёт станут недоступны, ручная работа — нет.")) return;
  try {
    await setAiSettings({ enabled: !enabled });
    showToast(enabled ? "ИИ в проекте выключен" : "ИИ в проекте включён");
  } catch (error) {
    alert(error.message);
  }
});

/* ---------------- Фоновые задачи ---------------- */

async function pollAiJobs() {
  if (!currentProject) return;
  const projectId = currentProject.id;
  window.clearTimeout(aiJobsTimer);
  let jobs = [];
  try {
    ({ jobs } = await api(`/api/projects/${projectId}/jobs`));
  } catch {
    jobs = [];
  }
  if (!currentProject || currentProject.id !== projectId) return;
  for (const job of jobs) {
    const before = aiJobsKnown.get(job.job_id);
    if (before && before.status !== job.status && (job.status === "complete" || job.status === "failed")) {
      announceAiJob(job);
    }
    aiJobsKnown.set(job.job_id, job);
  }
  renderAiJobs(jobs);
  if (jobs.some(job => job.status === "queued" || job.status === "running")) {
    aiJobsTimer = window.setTimeout(pollAiJobs, 1500);
  }
}

function announceAiJob(job) {
  if (job.status === "failed") {
    showToast(`${job.title}: ошибка — откройте «Задачи ИИ»`);
    return;
  }
  showToast(`${job.title}: готово`);
  if (job.kind === "questionnaire" && job.job_id === questionnaireJobId) void showQuestionnaireJob(job.job_id);
}

function renderAiJobs(jobs) {
  const button = document.querySelector("#ai-jobs");
  const active = jobs.filter(job => job.status === "queued" || job.status === "running");
  const failed = jobs.filter(job => job.status === "failed");
  button.hidden = !jobs.length;
  button.classList.toggle("is-busy", active.length > 0);
  button.classList.toggle("is-failed", !active.length && failed.length > 0);
  button.textContent = active.length
    ? `ИИ: ${active.length === 1 ? active[0].stage : `${active.length} задачи`}`
    : failed.length ? `ИИ: ошибка` : "ИИ: готово";
  document.querySelector("#ai-jobs-list").innerHTML = jobs.map(job => `
    <li class="ai-job is-${job.status}">
      <div><b>${escapeHtml(job.title)}</b>
        <small>${escapeHtml(job.status === "failed" ? job.error || "Ошибка" : job.stage)}${job.status === "running" ? ` · ${job.progress}%` : ""}</small></div>
      ${job.status === "failed" ? `<button type="button" class="secondary" data-ai-retry="${job.job_id}">Повторить</button>` : ""}
      ${job.status === "complete" && job.kind === "questionnaire" ? `<button type="button" class="secondary" data-ai-open="${job.job_id}">Открыть</button>` : ""}
    </li>`).join("") || '<li class="muted">Задач нет</li>';
}

document.querySelector("#ai-jobs").addEventListener("click", () => {
  openSheet(document.querySelector("#ai-jobs-sheet"));
});

document.querySelector("#ai-jobs-list").addEventListener("click", async event => {
  const retry = event.target.closest("[data-ai-retry]");
  const open = event.target.closest("[data-ai-open]");
  if (retry) {
    retry.disabled = true;
    try {
      await api(`/api/projects/${currentProject.id}/jobs/${retry.dataset.aiRetry}/retry`, { method: "POST" });
      await pollAiJobs();
    } catch (error) {
      alert(error.message);
    }
  } else if (open) {
    questionnaireJobId = open.dataset.aiOpen;
    await showQuestionnaireJob(questionnaireJobId);
  }
});

/* ---------------- Анкета ---------------- */

document.querySelector("#questionnaire-file").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || !currentProject) return;
  try {
    if (!(await ensureAiAllowed())) return;
  } catch (error) {
    alert(error.message);
    return;
  }
  const body = document.querySelector("#questionnaire-body");
  document.querySelector("#questionnaire-subtitle").textContent = file.name;
  document.querySelector("#questionnaire-apply").disabled = true;
  body.innerHTML = '<p class="muted">Отправляем анкету…</p>';
  openSheet(document.querySelector("#questionnaire-sheet"));
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const job = await api(`/api/projects/${currentProject.id}/questionnaire`, { method: "POST", body: form });
    questionnaireJobId = job.job_id;
    aiJobsKnown.set(job.job_id, job);
    renderQuestionnaireProgress(job);
    await followQuestionnaire(job.job_id);
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
});

function renderQuestionnaireProgress(job) {
  document.querySelector("#questionnaire-body").innerHTML = `
    <div class="ai-progress" role="status">
      <div class="ai-progress-bar"><span style="width:${Math.max(4, job.progress)}%"></span></div>
      <p>${escapeHtml(job.stage)}…</p>
      <p class="muted">Большая анкета разбирается минуту-две. Лист можно закрыть: когда разбор закончится, придёт уведомление, а результат откроется из «Задач ИИ».</p>
    </div>`;
}

/* Пока лист открыт, следим за своей задачей чаще общего опроса. */
async function followQuestionnaire(jobId) {
  void pollAiJobs();
  const sheet = document.querySelector("#questionnaire-sheet");
  while (questionnaireJobId === jobId && !sheet.hidden) {
    const job = await api(`/api/projects/${currentProject.id}/jobs/${jobId}`);
    if (job.status === "complete") {
      renderQuestionnaireResult(job);
      return;
    }
    if (job.status === "failed") {
      document.querySelector("#questionnaire-body").innerHTML = `
        <p class="error">${escapeHtml(job.error || "Разбор не удался.")}</p>
        <button type="button" class="secondary" data-ai-retry-inline="${job.job_id}">Повторить</button>`;
      return;
    }
    renderQuestionnaireProgress(job);
    await new Promise(resolve => window.setTimeout(resolve, 1000));
  }
}

document.querySelector("#questionnaire-body").addEventListener("click", async event => {
  const retry = event.target.closest("[data-ai-retry-inline]");
  if (!retry) return;
  try {
    const job = await api(`/api/projects/${currentProject.id}/jobs/${retry.dataset.aiRetryInline}/retry`, { method: "POST" });
    renderQuestionnaireProgress(job);
    await followQuestionnaire(job.job_id);
  } catch (error) {
    alert(error.message);
  }
});

async function showQuestionnaireJob(jobId) {
  const job = await api(`/api/projects/${currentProject.id}/jobs/${jobId}`);
  questionnaireJobId = jobId;
  document.querySelector("#questionnaire-subtitle").textContent = job.result?.filename || job.title;
  openSheet(document.querySelector("#questionnaire-sheet"));
  if (job.status === "complete") renderQuestionnaireResult(job);
  else await followQuestionnaire(jobId);
}

function renderQuestionnaireResult(job) {
  const result = job.result;
  questionnaireRows = result.rows;
  document.querySelector("#questionnaire-subtitle").textContent = result.filename;
  const groups = Object.keys(aiKindLabels)
    .map(kind => [kind, result.rows.filter(row => row.kind === kind)])
    .filter(([, rows]) => rows.length);
  const stale = currentProject.configuration.revision !== result.revision;
  document.querySelector("#questionnaire-body").innerHTML = `
    ${result.rows.length
      ? `<p class="wave-rows">Модель предложила ${plural(result.rows.length, "изменение", "изменения", "изменений")}. Отметьте, что применить.${result.truncated ? " Анкета длинная, в разбор ушло её начало." : ""}</p>`
      : '<p class="wave-rows">Модель не нашла, что поменять: подписи массива уже совпадают с анкетой.</p>'}
    ${stale ? '<p class="qn-stale">Проект менялся после загрузки анкеты. Применяется к текущему состоянию; изменения, которым больше не к чему относиться, сервер отклонит.</p>' : ""}
    ${result.notes ? `<p class="muted">Комментарий модели: ${escapeHtml(result.notes)}</p>` : ""}
    ${groups.length ? '<div class="qn-tools"><button type="button" class="text-button" data-qn-all="1">Отметить всё</button><button type="button" class="text-button" data-qn-all="0">Снять всё</button></div>' : ""}
    ${groups.map(([kind, rows]) => `
      <section class="qn-group">
        <h4><label class="checkbox"><input type="checkbox" data-qn-kind="${kind}" checked /> ${aiKindLabels[kind]} · ${rows.length}</label></h4>
        <table class="qn-table">
          <tbody>${rows.map(renderQuestionnaireRow).join("")}</tbody>
        </table>
      </section>`).join("")}
    ${result.skipped.length ? `<details class="qn-skipped"><summary>Отброшено при проверке · ${result.skipped.length}</summary><ul>${result.skipped.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></details>` : ""}`;
  updateQuestionnaireApply();
}

function renderQuestionnaireRow(row) {
  let target;
  let before = row.before;
  let after = row.after;
  if (row.kind === "question_label" || row.kind === "question_type") target = row.question;
  else if (row.kind === "variable_label") target = row.variable;
  else if (row.kind === "value_label") target = `${row.variable} = ${formatQuestionnaireValue(row.value)}`;
  if (row.kind === "question_type") {
    before = aiTypeLabels[before] || typeLabels[before] || before;
    after = aiTypeLabels[after] || after;
  }
  if (row.kind === "order") {
    return `<tr><td class="qn-check"><input type="checkbox" data-qn-row="${row.id}" checked aria-label="Применить порядок" /></td>
      <td colspan="3">По анкете: ${escapeHtml(row.after.join(" → "))}</td></tr>`;
  }
  return `<tr>
    <td class="qn-check"><input type="checkbox" data-qn-row="${row.id}" checked aria-label="Применить для ${escapeHtml(target)}" /></td>
    <td class="qn-target"><code>${escapeHtml(target)}</code></td>
    <td class="qn-before">${before ? escapeHtml(before) : '<span class="muted">нет подписи</span>'}</td>
    <td class="qn-after">${escapeHtml(after)}</td>
  </tr>`;
}

function formatQuestionnaireValue(value) {
  return typeof value === "number" && Number.isInteger(value) ? String(value) : String(value).replace(/\.0$/, "");
}

function chosenQuestionnaireRows() {
  return [...document.querySelectorAll("#questionnaire-body [data-qn-row]:checked")].map(input => input.dataset.qnRow);
}

function updateQuestionnaireApply() {
  const count = chosenQuestionnaireRows().length;
  const button = document.querySelector("#questionnaire-apply");
  button.disabled = count === 0;
  button.textContent = count ? `Применить ${count}` : "Применить";
  document.querySelectorAll("#questionnaire-body [data-qn-kind]").forEach(box => {
    const rows = [...box.closest(".qn-group").querySelectorAll("[data-qn-row]")];
    const checked = rows.filter(input => input.checked).length;
    box.checked = checked === rows.length;
    box.indeterminate = checked > 0 && checked < rows.length;
  });
}

document.querySelector("#questionnaire-body").addEventListener("change", event => {
  const kind = event.target.closest("[data-qn-kind]");
  if (kind) {
    kind.closest(".qn-group").querySelectorAll("[data-qn-row]").forEach(input => { input.checked = kind.checked; });
  }
  updateQuestionnaireApply();
});

document.querySelector("#questionnaire-body").addEventListener("click", event => {
  const all = event.target.closest("[data-qn-all]");
  if (!all) return;
  document.querySelectorAll("#questionnaire-body [data-qn-row]").forEach(input => { input.checked = all.dataset.qnAll === "1"; });
  updateQuestionnaireApply();
});

document.querySelector("#questionnaire-apply").addEventListener("click", async () => {
  const rowIds = chosenQuestionnaireRows();
  if (!rowIds.length || !questionnaireJobId) return;
  const button = document.querySelector("#questionnaire-apply");
  setBusy(button, true, "Применяем…");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/questionnaire/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: questionnaireJobId, row_ids: rowIds }),
    });
    closeSheet();
    renderProject();
    void loadReportPreflight();
    showToast(`Анкета применена: ${plural(rowIds.length, "изменение", "изменения", "изменений")}`);
  } catch (error) {
    document.querySelector("#questionnaire-body").insertAdjacentHTML("afterbegin", `<p class="error">${escapeHtml(error.message)}</p>`);
    setBusy(button, false, "Применить");
    updateQuestionnaireApply();
  }
});
