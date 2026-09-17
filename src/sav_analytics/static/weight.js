/* Редактор рассчитанного веса: измерения, цели и предпросмотр. Классический скрипт после app.js (см. решение 016): пользуется
   его состоянием, а app.js вызывает эти функции только после запуска. */

function openWeight(weightId = null) {
  if (!confirmDiscard(openInspectorPanel())) return;
  currentWeightId = weightId;
  currentQuestionCode = null;
  currentRecodingId = null;
  currentBannerId = null;
  currentFilterId = null;
  showInspector(weightEditor);
  const weight = weightId ? configuredWeights().find(item => item.id === weightId) : null;
  setHeadingText(document.querySelector("#weight-editor-title"), weight ? weight.name : "Новый вес");
  document.querySelector("#weight-name").value = weight?.name || "Вес по целевым распределениям";
  const trimming = weight ? weight.lower_bound != null || weight.upper_bound != null : true;
  document.querySelector("#weight-trimming").checked = trimming;
  document.querySelector("#weight-lower").value = weight?.lower_bound ?? 0.3;
  document.querySelector("#weight-upper").value = weight?.upper_bound ?? 3;
  renderWeightTrimming();
  const list = document.querySelector("#weight-dimension-list");
  list.innerHTML = "";
  if (weight) weight.dimensions.forEach(dimension => addWeightDimension(dimension));
  else addWeightDimension();
  document.querySelector("#delete-weight").hidden = !weight;
  const downloadWeight = document.querySelector("#download-weight");
  downloadWeight.hidden = !weight;
  downloadWeight.href = weight
    ? `/api/projects/${currentProject.id}/weights/${weight.id}/export.xlsx`
    : "#";
  document.querySelector("#weight-error").hidden = true;
  document.querySelector("#weight-preview-section").hidden = !weight;
  document.querySelector("#weight-preview").innerHTML = weight
    ? '<p class="muted">Считаем…</p>'
    : "";
  renderTable();
  if (weight) loadWeightPreview();
}

function closeWeight() {
  weightEditor.hidden = true;
  currentWeightId = null;
  renderTable();
}

function renderWeightTrimming() {
  const enabled = document.querySelector("#weight-trimming").checked;
  document.querySelector("#weight-lower").disabled = !enabled;
  document.querySelector("#weight-upper").disabled = !enabled;
  document.querySelector("#weight-bound-fields").hidden = !enabled;
}

function weightSourceOptions(selectedVariable = "") {
  return eligibleWeightQuestions().map(question => {
    const variable = question.source_variables[0];
    return `<option value="${escapeAttribute(variable)}" ${variable === selectedVariable ? "selected" : ""}>${escapeHtml(question.code)} — ${escapeHtml(question.label)}</option>`;
  }).join("");
}

function eligibleWeightQuestions() {
  return configuredQuestions().filter(question => {
    if (question.question_type !== "single_choice" || question.source_variables.length !== 1) return false;
    const variable = currentProject.inspection.variables.find(item => item.name === question.source_variables[0]);
    return variable?.value_labels?.length >= 2;
  });
}

function addWeightDimension(dimension = {}) {
  if (!eligibleWeightQuestions().length) {
    throw new Error("Для raking нужна хотя бы одна категориальная переменная с метками значений.");
  }
  const element = document.createElement("div");
  element.className = "weight-dimension dim";
  element.innerHTML = `<div class="dim-head"><div class="weight-dimension-source-field"><span>Переменная</span><select class="weight-dimension-source" aria-label="Переменная целевого распределения">${weightSourceOptions(dimension.variable)}</select></div><span class="sum" aria-live="polite"></span><button class="del" type="button" data-remove-weight-dimension title="Удалить распределение" aria-label="Удалить распределение">×</button></div><div class="weight-targets"></div>`;
  document.querySelector("#weight-dimension-list").append(element);
  renderWeightTargets(element, dimension.targets || []);
}

function renderWeightTargets(element, savedTargets = []) {
  const variableName = element.querySelector(".weight-dimension-source").value;
  const variable = currentProject.inspection.variables.find(item => item.name === variableName);
  const equalTarget = 100 / variable.value_labels.length;
  element.querySelector(".weight-targets").innerHTML = variable.value_labels.map(item => {
    const saved = savedTargets.find(target => target.values.some(value => String(value) === String(item.value)));
    const encoded = escapeAttribute(JSON.stringify(item.value));
    const percent = saved?.percent ?? equalTarget;
    return `<label class="weight-target t-row" data-value="${encoded}"><span class="lbl" title="${escapeAttribute(item.label)}">${escapeHtml(item.label)}</span><span class="t-track" aria-hidden="true"><span class="t-fill" style="width:${Math.min(100, Math.max(0, Number(percent)))}%"></span></span><input type="number" min="0.0001" max="100" step="0.0001" value="${percent}" aria-label="Цель для ${escapeAttribute(item.label)}, процентов" required /></label>`;
  }).join("");
  updateWeightDimensionStatus(element);
}

function updateWeightDimensionStatus(element) {
  const inputs = [...element.querySelectorAll(".weight-target input")];
  const total = inputs.reduce((sum, input) => sum + (Number(input.value) || 0), 0);
  const valid = Math.abs(total - 100) <= 0.1;
  const badge = element.querySelector(".sum");
  badge.className = `sum ${valid ? "ok" : "bad"}`;
  badge.textContent = `${formatWeightNumber(total)}%`;
  badge.title = valid ? "Сумма целей корректна" : "Сумма целей должна составлять 100%";
  inputs.forEach(input => {
    const percent = Math.min(100, Math.max(0, Number(input.value) || 0));
    input.closest(".weight-target").querySelector(".t-fill").style.width = `${percent}%`;
  });
}

function collectWeightDimensions() {
  const elements = [...document.querySelectorAll("#weight-dimension-list .weight-dimension")];
  if (!elements.length) throw new Error("Добавьте хотя бы одно целевое распределение.");
  return elements.map(element => {
    const variableName = element.querySelector(".weight-dimension-source").value;
    const variable = currentProject.inspection.variables.find(item => item.name === variableName);
    const targets = [...element.querySelectorAll(".weight-target")].map(row => ({
      label: row.querySelector(".lbl").textContent,
      values: [JSON.parse(row.dataset.value)],
      percent: Number(row.querySelector("input").value),
    }));
    const total = targets.reduce((sum, target) => sum + target.percent, 0);
    if (Math.abs(total - 100) > 0.1) {
      throw new Error(`Сумма целей для «${variable.label}» должна составлять 100%. Сейчас ${formatWeightNumber(total)}%.`);
    }
    return { variable: variableName, label: variable.label, targets };
  });
}

async function loadWeightPreview() {
  if (!currentProject || !currentWeightId) return;
  const container = document.querySelector("#weight-preview");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const preview = await api(`/api/projects/${currentProject.id}/weights/${currentWeightId}/preview`);
    container.innerHTML = renderWeightPreview(preview);
  } catch (error) {
    container.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function renderWeightPreview(preview) {
  const metrics = [
    [preview.minimum, "Минимум"], [preview.maximum, "Максимум"],
    [preview.mean, "Среднее"], [preview.stddev, "Стандартное отклонение"],
    [preview.effective_base, "Эффективная база"], [preview.design_effect, "Design effect"],
    [preview.efficiency_percent, "Эффективность, %"], [preview.iterations, "Итераций"],
  ];
  const metricGrid = `<dl class="diag-grid">${metrics.map(([value, label]) => `<div><dt>${escapeHtml(label)}</dt><dd class="${label === "Среднее" || label === "Эффективность, %" ? "ok" : ""}">${formatWeightNumber(value)}</dd></div>`).join("")}</dl>`;
  const distributions = preview.distributions.map(dimension => `<section class="weight-distribution"><div class="weight-distribution-head"><strong>${escapeHtml(dimension.label)}</strong><span>До → после · цель</span></div>${dimension.categories.map(category => `<div class="weight-result-row"><span title="${escapeAttribute(category.label)}">${escapeHtml(category.label)}</span><em>${category.before_percent.toFixed(1)} → <b>${category.after_percent.toFixed(1)}</b> · ${category.target_percent.toFixed(1)}%</em></div>`).join("")}</section>`).join("");
  return metricGrid + distributions;
}

function formatWeightNumber(value) {
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}

async function deleteWeight() {
  if (!currentProject || !currentWeightId) return;
  const errorBox = document.querySelector("#weight-error");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/weights/${currentWeightId}`, { method: "DELETE" });
    closeWeight();
    renderProject();
    showToast("Вес удалён");
  } catch (error) {
    showError(errorBox, error);
  }
}
