const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file");
const fileTitle = document.querySelector("#file-title");
const fileCaption = document.querySelector("#file-caption");
const errorBox = document.querySelector("#form-error");
const submit = document.querySelector("#submit");
const dropZone = document.querySelector("#drop-zone");
const editor = document.querySelector("#question-editor");
let currentProject = null;
let currentQuestionCode = null;
let currentView = "questions";

const typeLabels = {
  single_choice: "Один ответ",
  multiple_choice_dichotomy: "Множественный",
  multiple_choice_categorical: "Множественный — категории",
  scale: "Шкала",
  numeric: "Числовой",
  ranking: "Ранжирование",
  matrix: "Матрица",
  open_text: "Открытый текст",
  technical: "Технический",
};

fileInput.addEventListener("change", updateFileLabel);
["dragenter", "dragover"].forEach(name => dropZone.addEventListener(name, event => {
  event.preventDefault();
  dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach(name => dropZone.addEventListener(name, event => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
}));
dropZone.addEventListener("drop", event => {
  const file = event.dataTransfer.files[0];
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  updateFileLabel();
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  errorBox.hidden = true;
  setBusy(submit, true, "Читаем структуру…");
  try {
    const project = await api("/api/projects", { method: "POST", body: new FormData(form) });
    showProject(project);
  } catch (error) {
    showError(errorBox, error);
  } finally {
    setBusy(submit, false, "Создать проект");
  }
});

document.querySelector("#new-project").addEventListener("click", () => {
  currentProject = null;
  currentQuestionCode = null;
  editor.hidden = true;
  document.querySelector("#workspace").hidden = true;
  document.querySelector("#start").hidden = false;
  form.reset();
  fileTitle.textContent = "Перетащите SAV сюда";
  fileCaption.textContent = "или нажмите, чтобы выбрать файл";
  loadProjects();
});

document.querySelector("#refresh-projects").addEventListener("click", loadProjects);
document.querySelector("#close-editor").addEventListener("click", () => {
  editor.hidden = true;
  currentQuestionCode = null;
});
document.querySelector("#refresh-preview").addEventListener("click", loadPreview);

document.querySelectorAll(".tabs button[data-view]").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach(item => item.classList.remove("active"));
  button.classList.add("active");
  currentView = button.dataset.view;
  renderTable();
}));

document.querySelector("#table-body").addEventListener("click", event => {
  const moveButton = event.target.closest("button[data-move]");
  if (moveButton) {
    moveQuestion(moveButton.dataset.code, Number(moveButton.dataset.move));
    return;
  }
  const row = event.target.closest("tr[data-code]");
  if (row && currentView !== "variables") openQuestion(row.dataset.code);
});

document.querySelector("#question-form").addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-question");
  const editorError = document.querySelector("#editor-error");
  editorError.hidden = true;
  setBusy(saveButton, true, "Сохраняем…");
  try {
    currentProject = await api(
      `/api/projects/${currentProject.id}/questions/${encodeURIComponent(currentQuestionCode)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          label: document.querySelector("#question-label").value.trim(),
          question_type: document.querySelector("#question-type").value,
          role: document.querySelector("#question-role").value,
          included_in_report: document.querySelector("#question-included").checked,
        }),
      },
    );
    renderProject();
    fillEditor(findQuestion(currentQuestionCode));
    await loadPreview();
  } catch (error) {
    showError(editorError, error);
  } finally {
    setBusy(saveButton, false, "Сохранить изменения");
  }
});

async function loadProjects() {
  const library = document.querySelector("#project-library");
  const list = document.querySelector("#project-list");
  try {
    const projects = await api("/api/projects");
    library.hidden = projects.length === 0;
    list.innerHTML = projects.map(project => `
      <button class="project-card" type="button" data-project-id="${project.id}">
        <span><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.original_filename)}</small></span>
        <time>${formatDate(project.created_at)}</time>
      </button>`).join("");
    list.querySelectorAll("[data-project-id]").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        showProject(await api(`/api/projects/${button.dataset.projectId}`));
      } catch (error) {
        showError(errorBox, error);
      } finally {
        button.disabled = false;
      }
    }));
  } catch (error) {
    library.hidden = false;
    list.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function showProject(project) {
  currentProject = project;
  currentQuestionCode = null;
  currentView = "questions";
  editor.hidden = true;
  document.querySelectorAll(".tabs button").forEach(button => {
    button.classList.toggle("active", button.dataset.view === "questions");
  });
  renderProject();
  document.querySelector("#start").hidden = true;
  document.querySelector("#workspace").hidden = false;
}

function renderProject() {
  const inspection = currentProject.inspection;
  const questions = configuredQuestions();
  document.querySelector("#project-name").textContent = currentProject.name;
  document.querySelector("#download-source").href = `/api/projects/${currentProject.id}/source`;
  document.querySelector("#summary").innerHTML = [
    [inspection.row_count.toLocaleString("ru-RU"), "респондентов"],
    [inspection.variable_count.toLocaleString("ru-RU"), "переменных"],
    [questions.length.toLocaleString("ru-RU"), "вопросов"],
    [questions.filter(item => item.included_in_report).length, "включено в отчёт"],
  ].map(([value, label]) => `<article><strong>${value}</strong><span>${label}</span></article>`).join("");
  const warningBox = document.querySelector("#warnings");
  warningBox.hidden = inspection.warnings.length === 0;
  warningBox.innerHTML = inspection.warnings.map(text => `<p>⚑ ${escapeHtml(text)}</p>`).join("");
  renderTable();
}

function renderTable() {
  if (!currentProject) return;
  if (currentView === "variables") {
    document.querySelector("#table-head").innerHTML = "<th>Имя</th><th>Метка</th><th>Формат</th><th>Measurement</th><th>Уникальных</th><th>Валидная база</th><th>Пропуски</th>";
    document.querySelector("#table-body").innerHTML = currentProject.inspection.variables.map(variable => `
      <tr>
        <td><code>${escapeHtml(variable.name)}</code></td><td><strong>${escapeHtml(variable.label)}</strong></td>
        <td>${escapeHtml(variable.original_format || variable.storage_type)}</td><td>${escapeHtml(variable.measurement_level || "—")}</td>
        <td>${variable.unique_count.toLocaleString("ru-RU")}</td><td>${variable.valid_count.toLocaleString("ru-RU")}</td><td>${variable.missing_count.toLocaleString("ru-RU")}</td>
      </tr>`).join("");
    return;
  }
  const allQuestions = configuredQuestions();
  const questions = currentView === "excluded" ? allQuestions.filter(item => !item.included_in_report) : allQuestions;
  document.querySelector("#table-head").innerHTML = "<th>Порядок</th><th>Код</th><th>Вопрос</th><th>Тип</th><th>Переменные</th><th>База</th><th>Статус</th>";
  document.querySelector("#table-body").innerHTML = questions.map((question, visibleIndex) => {
    const absoluteIndex = allQuestions.findIndex(item => item.code === question.code);
    return `<tr class="question-row ${question.code === currentQuestionCode ? "selected" : ""}" data-code="${escapeHtml(question.code)}">
      <td class="order-buttons"><button type="button" data-code="${escapeHtml(question.code)}" data-move="-1" ${absoluteIndex === 0 ? "disabled" : ""}>↑</button><button type="button" data-code="${escapeHtml(question.code)}" data-move="1" ${absoluteIndex === allQuestions.length - 1 ? "disabled" : ""}>↓</button><span>${visibleIndex + 1}</span></td>
      <td><code>${escapeHtml(question.code)}</code></td>
      <td><strong>${escapeHtml(question.label)}</strong>${question.warnings.map(w => `<small>${escapeHtml(w)}</small>`).join("")}</td>
      <td><span class="type">${typeLabels[question.question_type] || escapeHtml(question.question_type)}</span></td>
      <td>${question.source_variables.length}</td><td>${question.valid_count.toLocaleString("ru-RU")}</td>
      <td><span class="status ${question.recognition === "auto_review" ? "review" : ""}">${question.included_in_report ? (question.recognition === "auto_review" ? "Проверить" : "В отчёте") : "Исключён"}</span></td>
    </tr>`;
  }).join("");
}

async function moveQuestion(code, direction) {
  const codes = configuredQuestions().map(item => item.code);
  const index = codes.indexOf(code);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= codes.length) return;
  [codes[index], codes[target]] = [codes[target], codes[index]];
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/questions/order`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codes }),
    });
    renderProject();
  } catch (error) {
    alert(error.message);
  }
}

function openQuestion(code) {
  currentQuestionCode = code;
  fillEditor(findQuestion(code));
  editor.hidden = false;
  renderTable();
  loadPreview();
}

function fillEditor(question) {
  if (!question) return;
  document.querySelector("#editor-code").textContent = question.code;
  document.querySelector("#question-label").value = question.label;
  document.querySelector("#question-type").value = question.question_type;
  document.querySelector("#question-role").value = question.role;
  document.querySelector("#question-included").checked = question.included_in_report;
  document.querySelector("#editor-error").hidden = true;
}

async function loadPreview() {
  if (!currentProject || !currentQuestionCode) return;
  const container = document.querySelector("#preview-content");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const preview = await api(`/api/projects/${currentProject.id}/questions/${encodeURIComponent(currentQuestionCode)}/preview`);
    container.innerHTML = renderPreview(preview);
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

function renderPreview(preview) {
  const base = `<div class="base-line"><span>Total <strong>${preview.total_base.toLocaleString("ru-RU")}</strong></span><span>Валидная база <strong>${preview.valid_base.toLocaleString("ru-RU")}</strong></span></div>`;
  const rows = preview.rows?.length ? `<div class="preview-rows"><div class="preview-row-head"><span>Ответ</span><strong>N</strong><em>Total</em><em>Valid</em></div>${preview.rows.map(row => `
    <div><span>${escapeHtml(row.label)}</span><strong>${row.count}</strong><em>${formatPercent(row.percent_main)}</em><em>${formatPercent(row.percent_filter)}</em></div>`).join("")}</div>` : "";
  const statistics = preview.statistics ? `<dl class="stats">
    ${stat("Среднее", preview.statistics.mean)}${stat("Медиана", preview.statistics.median)}
    ${stat("Минимум", preview.statistics.minimum)}${stat("Максимум", preview.statistics.maximum)}
    ${stat("Ст. отклонение", preview.statistics.stddev)}${stat("Ст. ошибка", preview.statistics.stderr)}
  </dl>` : "";
  return base + rows + statistics;
}

function stat(label, value) {
  return `<div><dt>${label}</dt><dd>${value == null ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 })}</dd></div>`;
}

function configuredQuestions() {
  return currentProject.configuration?.questions || currentProject.inspection.questions;
}

function findQuestion(code) {
  return configuredQuestions().find(item => item.code === code);
}

function updateFileLabel() {
  const file = fileInput.files[0];
  if (!file) return;
  fileTitle.textContent = file.name;
  fileCaption.textContent = `${(file.size / 1024 / 1024).toFixed(1)} МБ`;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const responseText = await response.text();
  let payload;
  try {
    payload = responseText ? JSON.parse(responseText) : {};
  } catch {
    payload = { detail: responseText || `Ошибка сервера ${response.status}` };
  }
  if (!response.ok) throw new Error(payload.detail || "Запрос не выполнен.");
  return payload;
}

function setBusy(button, busy, text) {
  button.disabled = busy;
  button.textContent = text;
}

function showError(element, error) {
  element.textContent = error.message;
  element.hidden = false;
}

function formatDate(value) {
  return new Date(value).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

function formatPercent(value) {
  return value == null ? "—" : `${(value * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

loadProjects();
