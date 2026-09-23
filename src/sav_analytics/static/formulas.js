/* Редактор формул. Классический скрипт после app.js: пользуется его состоянием
   (currentProject, api, renderProject) и объявляет функции, которые app.js
   вызывает только по действию аналитика. */

/* Формулы: числовая переменная из переменных массива. Сохранённая формула —
   обычный числовой вопрос структуры; из его карточки редактор и открывается. */
function configuredFormulas() {
  return currentProject?.configuration?.formulas || [];
}

function renderQuestionFormula(question) {
  const section = document.querySelector("#question-formula");
  const formula = question.formula_id
    ? configuredFormulas().find(item => item.id === question.formula_id)
    : null;
  section.hidden = !formula;
  section.innerHTML = formula ? `
    <div class="grp-cap"><strong>Формула</strong><small>Пересчитывается из массива при каждом расчёте</small></div>
    <button type="button" class="recode-row" data-open-formula="${escapeAttribute(formula.id)}">
      <span class="recode-row-text"><strong><code>${escapeHtml(formula.expression)}</code></strong><small>Изменить формулу</small></span>
      <span class="recode-row-go" aria-hidden="true">→</span>
    </button>` : "";
}

document.querySelector("#question-formula").addEventListener("click", event => {
  const button = event.target.closest("[data-open-formula]");
  if (button) openFormula(button.dataset.openFormula);
});


function openFormula(formulaId, options = {}) {
  if (!currentProject || !confirmDiscard(openInspectorPanel())) return;
  const formula = formulaId ? configuredFormulas().find(item => item.id === formulaId) : null;
  currentFormulaId = formula?.id || null;
  currentQuestionCode = null;
  showInspector(formulaEditor);
  setHeadingText(document.querySelector("#formula-editor-title"), formula ? formula.name : "Новая переменная");
  const name = document.querySelector("#formula-name");
  name.value = formula?.name || suggestFormulaName();
  name.readOnly = Boolean(formula);
  document.querySelector("#formula-label").value = formula?.label || options.label || "";
  document.querySelector("#formula-expression").value = formula?.expression || "";
  document.querySelector("#delete-formula").hidden = !formula;
  document.querySelector("#formula-error").hidden = true;
  document.querySelector("#formula-preview").innerHTML = '<p class="muted">Покажет, у скольких респондентов формула посчиталась и из-за какого поля пусто.</p>';
  renderFormulaVariables();
  renderVariableKinds("formula", Boolean(formula));
}

function suggestFormulaName() {
  const taken = new Set([
    ...currentProject.inspection.variables.map(item => item.name),
    ...configuredQuestions().map(item => item.code),
  ]);
  let index = 1;
  while (taken.has(`F${index}`)) index += 1;
  return `F${index}`;
}

// Подсказка полей: числовые переменные массива, щелчок вставляет имя в курсор.
function renderFormulaVariables() {
  // Формулы тоже годятся источником, кроме самой редактируемой.
  const numeric = currentProject.inspection.variables
    .filter(item => item.storage_type === "numeric" && (!item.formula_id || item.formula_id !== currentFormulaId))
    .slice(0, 80);
  document.querySelector("#formula-variables").innerHTML = numeric.map(item => `
    <button type="button" data-insert-variable="${escapeAttribute(item.name)}" title="${escapeAttribute(item.label || item.name)}">${escapeHtml(item.name)}</button>`).join("");
}

document.querySelector("#formula-variables").addEventListener("click", event => {
  const button = event.target.closest("[data-insert-variable]");
  if (!button) return;
  const field = document.querySelector("#formula-expression");
  const name = button.dataset.insertVariable;
  const token = /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : `[${name}]`;
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? field.value.length;
  field.value = field.value.slice(0, start) + token + field.value.slice(end);
  field.focus();
  field.selectionStart = field.selectionEnd = start + token.length;
  markInspectorDirty(formulaEditor);
});

function formulaPayload() {
  return {
    name: document.querySelector("#formula-name").value.trim(),
    label: document.querySelector("#formula-label").value.trim(),
    expression: document.querySelector("#formula-expression").value.trim(),
  };
}

document.querySelector("#check-formula").addEventListener("click", async () => {
  const container = document.querySelector("#formula-preview");
  const payload = formulaPayload();
  if (!payload.expression) {
    container.innerHTML = '<p class="muted">Сначала запишите выражение.</p>';
    return;
  }
  container.innerHTML = '<p class="muted">Считаем…</p>';
  const query = currentFormulaId ? `?formula_id=${encodeURIComponent(currentFormulaId)}` : "";
  try {
    const preview = await api(`/api/projects/${currentProject.id}/formulas/preview${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, label: payload.label || payload.name }),
    });
    container.innerHTML = renderFormulaPreview(preview);
  } catch (error) {
    container.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
});

function renderFormulaPreview(preview) {
  const number = value => value == null ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  const base = `<div class="base-line"><span>Посчитано <strong>${preview.valid.toLocaleString("ru-RU")}</strong> из ${preview.total.toLocaleString("ru-RU")}</span><span>Пусто <strong>${preview.missing.toLocaleString("ru-RU")}</strong></span></div>`;
  const stats = `<div class="base-line"><span>Среднее <strong>${number(preview.mean)}</strong></span><span>Мин. <strong>${number(preview.min)}</strong></span><span>Макс. <strong>${number(preview.max)}</strong></span></div>`;
  const reasons = preview.missing
    ? `<div class="formula-missing">${preview.missing_by_variable.map(item => `
        <div><span><code>${escapeHtml(item.variable)}</code> ${escapeHtml(item.label)}</span><strong>${item.count ? `пусто у ${item.count.toLocaleString("ru-RU")}` : "не мешает"}</strong></div>`).join("")}</div>`
    : "";
  return base + stats + reasons;
}

document.querySelector("#formula-form").addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-formula");
  const formulaError = document.querySelector("#formula-error");
  formulaError.hidden = true;
  const payload = formulaPayload();
  setBusy(saveButton, true, "Сохраняем…");
  try {
    const url = currentFormulaId
      ? `/api/projects/${currentProject.id}/formulas/${currentFormulaId}`
      : `/api/projects/${currentProject.id}/formulas`;
    currentProject = await api(url, {
      method: currentFormulaId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const saved = configuredFormulas().find(item => item.name === payload.name);
    markInspectorClean(formulaEditor);
    renderProject();
    if (saved) openQuestion(saved.name);
    showToast(`Формула ${payload.name} сохранена`);
  } catch (error) {
    showError(formulaError, error);
  } finally {
    setBusy(saveButton, false, "Сохранить");
  }
});

document.querySelector("#delete-formula").addEventListener("click", async () => {
  if (!currentFormulaId || !confirm("Удалить формулу? Её вопрос исчезнет из структуры и отчёта.")) return;
  const formulaError = document.querySelector("#formula-error");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/formulas/${currentFormulaId}`, { method: "DELETE" });
    markInspectorClean(formulaEditor);
    formulaEditor.hidden = true;
    currentFormulaId = null;
    renderProject();
    showToast("Формула удалена");
  } catch (error) {
    showError(formulaError, error);
  }
});

document.querySelector("#close-formula-editor").addEventListener("click", () => {
  if (!confirmDiscard(formulaEditor)) return;
  formulaEditor.hidden = true;
  currentFormulaId = null;
  renderTable();
});
