/* Автоотчёт (PQ.18, решение 034) в разделе «ИИ отчёт».

   Три шага: бриф → план → отчёт. План предлагает ИИ, человек правит его и
   отвечает на уточнения. Сборка настраивает проект (баннер «Автоотчёт»,
   вес), считает разделы ядром и просит модель написать текст по готовым
   числам. Отчёт правится здесь же: текст, порядок разделов, скрытие и вид
   графиков. Правки не стираются пересборкой — она спрашивает про каждый
   изменённый блок. Скачивание — DOCX и обычная книга Excel. */

let autoreportState = null;
let autoreportStep = null;
let autoreportProject = null;

/* Раздел «ИИ отчёт» — отдельный пункт рельса рядом с «Ручным отчётом». */
function renderAutoreportSection() {
  if (!currentProject) return;
  if (autoreportProject !== currentProject.id) {
    autoreportProject = currentProject.id;
    autoreportState = null;
    autoreportStep = null;
  }
  void renderAutoreport();
}

async function loadAutoreport() {
  if (!currentProject) return null;
  const projectId = currentProject.id;
  try {
    const state = await api(`/api/projects/${projectId}/autoreport`);
    if (!currentProject || currentProject.id !== projectId) return null;
    autoreportState = state;
    return state;
  } catch {
    return null;
  }
}

function furthestStep(state) {
  if (state.report) return "report";
  if (state.plan) return "plan";
  return "brief";
}

async function renderAutoreport() {
  if (!autoreportState) await loadAutoreport();
  const state = autoreportState;
  if (!state) return;
  if (!autoreportStep) autoreportStep = furthestStep(state);
  const available = { brief: true, plan: Boolean(state.plan), report: Boolean(state.report) };
  document.querySelectorAll("[data-ar-step]").forEach(button => {
    const step = button.dataset.arStep;
    button.disabled = !available[step];
    button.classList.toggle("active", step === autoreportStep);
    button.classList.toggle("done", available[step] && step !== autoreportStep);
    button.setAttribute("aria-selected", String(step === autoreportStep));
  });
  const body = document.querySelector("#ar-body");
  if (autoreportStep === "brief") body.innerHTML = briefHtml(state);
  else if (autoreportStep === "plan") body.innerHTML = planHtml(state);
  else body.innerHTML = reportHtml(state);
  updateAutoreportProgress();
}

document.querySelector(".ar-steps").addEventListener("click", event => {
  const button = event.target.closest("[data-ar-step]");
  if (!button || button.disabled) return;
  autoreportStep = button.dataset.arStep;
  void renderAutoreport();
});

function autoreportJob() {
  const jobs = [...(aiJobsKnown?.values?.() || [])].filter(job => job.subject === "autoreport");
  return jobs.find(job => job.status === "queued" || job.status === "running") || null;
}

function updateAutoreportProgress() {
  const box = document.querySelector("#ar-progress");
  if (!box) return;
  const job = currentProject ? autoreportJob() : null;
  box.hidden = !job;
  document.querySelectorAll("#ar-body [data-ar-run]").forEach(button => { button.disabled = Boolean(job); });
  if (!job) return;
  box.innerHTML = `<div class="ai-progress-bar"><span style="width:${Math.max(4, job.progress)}%"></span></div>
    <p>${escapeHtml(job.title)}: ${escapeHtml(job.stage)}…</p>`;
}

async function onAutoreportJobFinished(job) {
  if (!currentProject || job.project_id !== currentProject.id) return;
  if (job.status === "failed") {
    alert(job.error || "Задача автоотчёта не выполнилась.");
    updateAutoreportProgress();
    return;
  }
  await loadAutoreport();
  if (job.kind === "autoreport_plan") autoreportStep = "plan";
  if (job.kind === "autoreport_build") {
    autoreportStep = "report";
    currentProject = await api(`/api/projects/${currentProject.id}`);
    renderProject();
  }
  if (currentView === "aireport") {
    const full = await api(`/api/projects/${currentProject.id}/jobs/${job.job_id}`).catch(() => null);
    await renderAutoreport();
    const warnings = full?.result?.warnings || [];
    if (warnings.length) showArNotice(`Сервер поправил план: ${warnings.join(" ")}`);
  }
}

function showArNotice(text) {
  const box = document.querySelector("#ar-body .ar-notice-slot");
  if (box) box.innerHTML = `<p class="cq-notice">${escapeHtml(text)}</p>`;
}

/* ---------------- Шаг 1. Бриф ---------------- */

function briefHtml(state) {
  const brief = state.brief;
  return `<div class="ar-card">
    <h2>Бриф</h2>
    <p class="muted">По брифу ИИ составит план: какие вопросы в какие разделы, какой разрез и вес. Чем конкретнее задачи — тем точнее план и выводы.</p>
    <div class="ar-notice-slot"></div>
    <label class="f">Задачи исследования<textarea rows="4" data-brief="tasks" placeholder="Например: оценить знание и лояльность к бренду, понять барьеры покупки, сравнить с конкурентами">${escapeHtml(brief.tasks)}</textarea></label>
    <label class="f">Описание исследования<textarea rows="3" data-brief="description" placeholder="Кого и как опрашивали, когда, сколько респондентов">${escapeHtml(brief.description)}</textarea></label>
    <label class="f">Объект интереса<input data-brief="object" value="${escapeAttribute(brief.object)}" placeholder="Бренд или продукт заказчика — вокруг него строятся выводы" /></label>
    <div class="ar-actions">
      <button type="button" class="secondary" data-ar-questionnaire title="Анкета подпишет вопросы без названий, а разделы плана пойдут по её блокам">${state.questionnaire ? "Заменить анкету…" : "Загрузить анкету…"}</button>
      <span class="ar-source muted">${state.questionnaire
        ? `Анкета «${escapeHtml(state.questionnaire.filename)}» учитывается в плане`
        : "Без анкеты план строится по подписям массива"}${state.wave_label ? ` · отчёт по волне «${escapeHtml(state.wave_label)}»` : ""}</span>
      <span class="toolbar-grow"></span>
      <button type="button" data-ar-run="plan">${state.plan ? "Пересоставить план с ИИ" : "Составить план с ИИ"}</button>
    </div>
  </div>`;
}

function collectBrief() {
  const read = key => document.querySelector(`#ar-body [data-brief="${key}"]`)?.value ?? autoreportState.brief[key];
  return { tasks: read("tasks"), description: read("description"), object: read("object") };
}

async function saveBrief() {
  autoreportState = await api(`/api/projects/${currentProject.id}/autoreport/brief`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collectBrief()),
  });
}

async function startPlan() {
  if (!(await ensureAiAllowed())) return;
  if (autoreportStep === "brief") await saveBrief();
  if (autoreportStep === "plan") await savePlan();
  const job = await api(`/api/projects/${currentProject.id}/autoreport/plan`, { method: "POST" });
  aiJobsKnown.set(job.job_id, job);
  updateAutoreportProgress();
  void pollAiJobs();
}

/* ---------------- Шаг 2. План ---------------- */

function questionLabelFor(state, code) {
  return state.labels[code] || code;
}

function planHtml(state) {
  const plan = state.plan;
  const catalog = state.catalog;
  const bannerOptions = catalog.filter(item => ["single_choice", "multiple_choice_dichotomy", "multiple_choice_categorical"].includes(item.type) && !plan.banner.includes(item.code));
  if (state.wave_variable && !plan.banner.includes(state.wave_variable)) {
    bannerOptions.unshift({ code: state.wave_variable, label: "Волна — сравнение волн" });
  }
  const clarifications = state.clarifications.map(item => `
    <div class="ar-question" data-clarification="${escapeAttribute(item.id)}">
      <p><b>${escapeHtml(item.question)}</b></p>
      <div class="ar-options">${item.options.map(option => `<button type="button" class="pill${state.answers[item.id] === option ? " active" : ""}" data-answer-option="${escapeAttribute(option)}">${escapeHtml(option)}</button>`).join("")}</div>
      <input data-answer value="${escapeAttribute(state.answers[item.id] || "")}" placeholder="Свой ответ" aria-label="Ответ на уточнение" />
    </div>`).join("");
  const sections = plan.sections.map((section, index) => `
    <div class="ar-section" data-section-id="${escapeAttribute(section.id)}">
      <div class="ar-section-head">
        <span class="ar-section-number">${index + 1}</span>
        <input data-section-title value="${escapeAttribute(section.title)}" aria-label="Название раздела" />
        <button type="button" class="icon-button" data-section-remove aria-label="Убрать раздел" title="Убрать раздел">×</button>
      </div>
      <input class="ar-goal" data-section-goal value="${escapeAttribute(section.goal)}" placeholder="Цель раздела" aria-label="Цель раздела" />
      <div class="ar-chips">${section.questions.map(code => `<span class="answer-chip" title="${escapeAttribute(questionLabelFor(state, code))}"><code>${escapeHtml(code)}</code> ${escapeHtml(questionLabelFor(state, code).slice(0, 60))}<button type="button" class="chip-x" data-question-remove="${escapeAttribute(code)}" aria-label="Убрать вопрос">×</button></span>`).join("")}
        <select data-question-add aria-label="Добавить вопрос в раздел"><option value="">+ вопрос</option>${catalog.filter(item => !section.questions.includes(item.code)).map(item => `<option value="${escapeAttribute(item.code)}">${escapeHtml(item.code)} — ${escapeHtml(item.label.slice(0, 70))}</option>`).join("")}</select>
      </div>
    </div>`).join("");
  return `<div class="ar-card">
    <h2>План отчёта</h2>
    <div class="ar-notice-slot">${plan.notes ? `<p class="muted">Комментарий ИИ: ${escapeHtml(plan.notes)}</p>` : ""}</div>
    ${clarifications ? `<section class="ar-block"><h3>Уточнения от ИИ</h3><p class="muted">Ответы учтутся при пересоставлении плана и в тексте отчёта.</p>${clarifications}</section>` : ""}
    <section class="ar-block">
      <h3>Разрез и вес</h3>
      <div class="ar-chips">${plan.banner.map(code => `<span class="answer-chip"><code>${escapeHtml(code)}</code> ${escapeHtml(questionLabelFor(state, code).slice(0, 50))}<button type="button" class="chip-x" data-banner-remove="${escapeAttribute(code)}" aria-label="Убрать из разреза">×</button></span>`).join("") || '<span class="muted">Без разреза — только итог</span>'}
        <select data-banner-add aria-label="Добавить в разрез"><option value="">+ разрез</option>${bannerOptions.map(item => `<option value="${escapeAttribute(item.code)}">${escapeHtml(item.code)} — ${escapeHtml(item.label.slice(0, 60))}</option>`).join("")}</select>
      </div>
      <label class="f ar-weight">Вес<select data-plan-weight><option value="">Без веса</option>${state.weights.map(name => `<option value="${escapeAttribute(name)}" ${plan.weight === name ? "selected" : ""}>${escapeHtml(name)}</option>`).join("")}</select></label>
    </section>
    <section class="ar-block">
      <h3>Разделы · ${plan.sections.length}</h3>
      <div class="ar-sections">${sections}</div>
      <button type="button" class="text-button" data-section-add>+ Раздел</button>
    </section>
    <div class="ar-actions">
      <button type="button" class="secondary" data-ar-run="plan">Пересоставить план с ИИ</button>
      <span class="toolbar-grow"></span>
      <button type="button" data-ar-run="build">${state.report ? "Пересобрать отчёт" : "Собрать отчёт"}</button>
    </div>
  </div>`;
}

function collectPlan() {
  const plan = autoreportState.plan;
  const sections = [...document.querySelectorAll("#ar-body .ar-section")].map(element => {
    const stored = plan.sections.find(item => item.id === element.dataset.sectionId) || { questions: [] };
    return {
      id: element.dataset.sectionId,
      title: element.querySelector("[data-section-title]").value.trim() || stored.title || "Раздел",
      goal: element.querySelector("[data-section-goal]").value.trim(),
      questions: stored.questions,
    };
  }).filter(section => section.questions.length);
  const answers = {};
  document.querySelectorAll("#ar-body [data-clarification]").forEach(element => {
    const value = element.querySelector("[data-answer]").value.trim();
    if (value) answers[element.dataset.clarification] = value;
  });
  return {
    banner: plan.banner,
    weight: document.querySelector("#ar-body [data-plan-weight]")?.value || null,
    sections,
    answers,
  };
}

async function savePlan() {
  const result = await api(`/api/projects/${currentProject.id}/autoreport/plan`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collectPlan()),
  });
  autoreportState = result;
  return result;
}

/* Изменения плана живут в состоянии до сохранения: перерисовка берёт их оттуда. */
function syncPlanFromForm() {
  const draft = collectPlan();
  const plan = autoreportState.plan;
  plan.weight = draft.weight;
  plan.sections = plan.sections.map(section => {
    const edited = draft.sections.find(item => item.id === section.id);
    return edited ? { ...section, title: edited.title, goal: edited.goal } : section;
  });
  autoreportState.answers = { ...autoreportState.answers, ...draft.answers };
}

async function startBuild() {
  if (!(await ensureAiAllowed())) return;
  await savePlan();
  let overwrite = [];
  const edited = autoreportState.edited_blocks || [];
  if (edited.length) {
    const names = edited.map(block => `• ${block.title}`).join("\n");
    const replace = confirm(`Эти блоки вы правили вручную:\n${names}\n\nOK — заменить их новым текстом ИИ, Отмена — оставить ваши правки.`);
    overwrite = replace ? edited.map(block => block.id) : [];
  }
  const job = await api(`/api/projects/${currentProject.id}/autoreport/build`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ overwrite }),
  });
  aiJobsKnown.set(job.job_id, job);
  updateAutoreportProgress();
  void pollAiJobs();
}

/* ---------------- Шаг 3. Отчёт ---------------- */

function reportHtml(state) {
  const report = state.report;
  const warn = list => list && list.length ? `<p class="ar-warn">Проверьте числа, которых нет в таблицах: ${escapeHtml(list.join(", "))}</p>` : "";
  const sections = report.sections.map((section, index) => `
    <section class="ar-report-section" data-report-section="${escapeAttribute(section.id)}">
      <div class="ar-section-head">
        <h3>${escapeHtml(section.title)}</h3>
        ${section.edited ? '<span class="answer-source is-manual">правлено</span>' : ""}
        <span class="toolbar-grow"></span>
        <button type="button" class="icon-button" data-move="-1" ${index === 0 ? "disabled" : ""} aria-label="Выше">↑</button>
        <button type="button" class="icon-button" data-move="1" ${index === report.sections.length - 1 ? "disabled" : ""} aria-label="Ниже">↓</button>
      </div>
      <textarea class="ar-text" rows="4" data-section-text aria-label="Текст раздела">${escapeHtml(section.text)}</textarea>
      ${warn(section.warnings)}
      <div class="ar-cards">${section.cards.map(card => cardHtml(card)).join("")}</div>
    </section>`).join("");
  return `<div class="ar-card ar-report">
    <div class="ar-actions ar-report-bar">
      <div>
        <h2>Отчёт</h2>
        <small class="muted">Собран ${new Date(report.built_at).toLocaleString("ru-RU")}${state.stale ? " · проект менялся после сборки — пересоберите, чтобы обновить числа" : ""}</small>
      </div>
      <span class="toolbar-grow"></span>
      <button type="button" class="secondary" data-ar-run="build">Пересобрать</button>
      <button type="button" class="secondary" data-ar-excel title="Книга Excel с теми же разрезом и весом — в «Ручном отчёте»">Книга Excel</button>
      <a class="link-button" href="/api/projects/${currentProject.id}/autoreport/report.docx" download>Скачать DOCX</a>
    </div>
    <section class="ar-report-section" data-report-block="summary">
      <div class="ar-section-head"><h3>Ключевые выводы</h3>${report.summary.edited ? '<span class="answer-source is-manual">правлено</span>' : ""}</div>
      <textarea class="ar-text" rows="5" data-block-text="summary" aria-label="Ключевые выводы">${escapeHtml(report.summary.text)}</textarea>
      ${warn(report.summary.warnings)}
    </section>
    <section class="ar-report-section">
      <div class="ar-section-head"><h3>Методология</h3><span class="muted">считается приложением</span></div>
      <p class="ar-method">${escapeHtml(report.method)}</p>
    </section>
    ${sections}
    <section class="ar-report-section" data-report-block="conclusion">
      <div class="ar-section-head"><h3>Общий вывод</h3>${report.conclusion.edited ? '<span class="answer-source is-manual">правлено</span>' : ""}</div>
      <textarea class="ar-text" rows="4" data-block-text="conclusion" aria-label="Общий вывод">${escapeHtml(report.conclusion.text)}</textarea>
      ${warn(report.conclusion.warnings)}
    </section>
  </div>`;
}

/* Карточка вопроса: график по итогу (горизонтальный или вертикальный) и
   таблица по группам разреза — те же числа, что в DOCX и книге. */
function cardHtml(card) {
  const table = card.table;
  const total = table.rows.map(row => ({ label: row.label, value: row.cells[0]?.value }))
    .filter(item => item.value !== null && item.value !== undefined);
  const max = Math.max(100, ...total.map(item => item.value));
  const horizontal = card.chart !== "column";
  const chart = total.length ? `<div class="ar-chart ${horizontal ? "is-bar" : "is-column"}">${total.map(item => `
      <div class="ar-bar"><span class="ar-bar-label" title="${escapeAttribute(item.label)}">${escapeHtml(item.label)}</span>
      <span class="ar-bar-track"><span class="ar-bar-fill" style="${horizontal ? "width" : "height"}:${(item.value / max) * 100}%"></span></span>
      <b>${item.value}%</b></div>`).join("")}</div>` : "";
  const columns = table.columns;
  const rows = table.rows.map(row => `<tr><th>${escapeHtml(row.label)}</th>${row.cells.map(cell => `<td class="${cell.direction ? `is-${cell.direction}` : ""}${cell.small ? " is-small" : ""}">${cell.value === null ? "—" : `${cell.value}%${cell.direction === "higher" ? " ▲" : cell.direction === "lower" ? " ▼" : ""}`}</td>`).join("")}</tr>`).join("");
  return `<article class="ar-question-card${card.hidden ? " is-hidden" : ""}" data-card="${escapeAttribute(card.code)}">
    <header>
      <b><code>${escapeHtml(card.code)}</code> ${escapeHtml(card.label)}</b>
      <span class="toolbar-grow"></span>
      <div class="segmented compact">
        <button type="button" class="${horizontal ? "active" : ""}" data-chart="bar" title="Горизонтальные столбцы">▤</button>
        <button type="button" class="${horizontal ? "" : "active"}" data-chart="column" title="Вертикальные столбцы">▥</button>
      </div>
      <button type="button" class="text-button" data-card-toggle>${card.hidden ? "Вернуть в отчёт" : "Убрать из отчёта"}</button>
    </header>
    ${card.hidden ? '<p class="muted">Не войдёт в DOCX.</p>' : `${chart}
    <div class="ar-table-wrap"><table class="ar-table"><thead><tr><th></th>${columns.map(column => `<th class="${column.small ? "is-small" : ""}">${escapeHtml(column.label)}<small>n=${column.base}</small></th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`}
  </article>`;
}

async function patchReport(patch) {
  autoreportState = await api(`/api/projects/${currentProject.id}/autoreport/report`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

/* ---------------- События ---------------- */

const arBody = document.querySelector("#ar-body");

arBody.addEventListener("click", async event => {
  const target = event.target;
  try {
    if (target.closest("[data-ar-run='plan']")) {
      await startPlan();
    } else if (target.closest("[data-ar-run='build']")) {
      await startBuild();
    } else if (target.closest("[data-ar-questionnaire]")) {
      document.querySelector("#questionnaire-file").click();
    } else if (target.closest("[data-ar-excel]")) {
      setView("reports");
    } else if (target.closest("[data-answer-option]")) {
      const element = target.closest("[data-clarification]");
      element.querySelector("[data-answer]").value = target.closest("[data-answer-option]").dataset.answerOption;
      element.querySelectorAll("[data-answer-option]").forEach(button => button.classList.toggle("active", button === target.closest("[data-answer-option]")));
    } else if (target.closest("[data-question-remove]")) {
      syncPlanFromForm();
      const section = autoreportState.plan.sections.find(item => item.id === target.closest(".ar-section").dataset.sectionId);
      section.questions = section.questions.filter(code => code !== target.closest("[data-question-remove]").dataset.questionRemove);
      void renderAutoreport();
    } else if (target.closest("[data-section-remove]")) {
      syncPlanFromForm();
      autoreportState.plan.sections = autoreportState.plan.sections.filter(item => item.id !== target.closest(".ar-section").dataset.sectionId);
      void renderAutoreport();
    } else if (target.closest("[data-section-add]")) {
      syncPlanFromForm();
      autoreportState.plan.sections.push({ id: `new-${Date.now()}`, title: "Новый раздел", goal: "", questions: [] });
      void renderAutoreport();
    } else if (target.closest("[data-banner-remove]")) {
      syncPlanFromForm();
      autoreportState.plan.banner = autoreportState.plan.banner.filter(code => code !== target.closest("[data-banner-remove]").dataset.bannerRemove);
      void renderAutoreport();
    } else if (target.closest("[data-move]")) {
      const ids = autoreportState.report.sections.map(section => section.id);
      const id = target.closest("[data-report-section]").dataset.reportSection;
      const index = ids.indexOf(id);
      const next = index + Number(target.closest("[data-move]").dataset.move);
      [ids[index], ids[next]] = [ids[next], ids[index]];
      await patchReport({ order: ids });
      void renderAutoreport();
    } else if (target.closest("[data-chart]")) {
      const sectionId = target.closest("[data-report-section]").dataset.reportSection;
      const code = target.closest("[data-card]").dataset.card;
      await patchReport({ sections: [{ id: sectionId, charts: { [code]: target.closest("[data-chart]").dataset.chart } }] });
      void renderAutoreport();
    } else if (target.closest("[data-card-toggle]")) {
      const sectionElement = target.closest("[data-report-section]");
      const section = autoreportState.report.sections.find(item => item.id === sectionElement.dataset.reportSection);
      const code = target.closest("[data-card]").dataset.card;
      const hidden = section.cards.filter(card => card.hidden).map(card => card.code);
      const next = hidden.includes(code) ? hidden.filter(item => item !== code) : [...hidden, code];
      await patchReport({ sections: [{ id: section.id, hidden: next }] });
      void renderAutoreport();
    }
  } catch (error) {
    alert(error.message);
  }
});

arBody.addEventListener("change", event => {
  const target = event.target;
  if (target.matches("[data-question-add]") && target.value) {
    syncPlanFromForm();
    const section = autoreportState.plan.sections.find(item => item.id === target.closest(".ar-section").dataset.sectionId);
    section.questions.push(target.value);
    void renderAutoreport();
  } else if (target.matches("[data-banner-add]") && target.value) {
    syncPlanFromForm();
    autoreportState.plan.banner.push(target.value);
    void renderAutoreport();
  } else if (target.matches("[data-section-text]") || target.matches("[data-block-text]")) {
    // Текст сохраняется по уходу из поля: правка помечается и переживает пересборку.
    const patch = target.matches("[data-section-text]")
      ? { sections: [{ id: target.closest("[data-report-section]").dataset.reportSection, text: target.value }] }
      : { [target.dataset.blockText]: target.value };
    patchReport(patch).then(() => showToast("Текст сохранён")).catch(error => alert(error.message));
  } else if (target.matches("[data-brief]")) {
    saveBrief().catch(() => {});
  }
});
