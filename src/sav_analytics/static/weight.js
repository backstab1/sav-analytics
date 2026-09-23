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
  document.querySelector("#weight-method").value = weight?.method || "raking";
  savedWeightCells = weight?.cells || [];
  document.querySelector("#weight-cell-list").innerHTML = "";
  const list = document.querySelector("#weight-dimension-list");
  list.innerHTML = "";
  if (weight) weight.dimensions.forEach(dimension => addWeightDimension(dimension));
  else addWeightDimension();
  renderWeightMethod();
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

/* Взвешивание по ячейкам: цель задаётся сочетанию категорий, а не каждой
   категории. Переменные те же, что у raking, но их цели не нужны — вместо них
   сетка сочетаний. Введённые значения переживают перестройку сетки: ключ
   ячейки — подписи её категорий. */
let savedWeightCells = [];

// Строка метода для сводки отчёта и выбора веса.
function calculatedWeightSummary(weight) {
  const count = weight.dimensions.length;
  return weight.method === "cells"
    ? `по ячейкам · ${plural(weight.cells.length, "ячейка", "ячейки", "ячеек")} из ${plural(count, "переменной", "переменных", "переменных")}`
    : `raking / IPF · ${plural(count, "распределение", "распределения", "распределений")}`;
}

function weightMethod() {
  return document.querySelector("#weight-method").value;
}

function renderWeightMethod() {
  const cells = weightMethod() === "cells";
  document.querySelector("#weight-editor-kicker").textContent = cells ? "Взвешивание по ячейкам" : "Raking / IPF";
  document.querySelector("#weight-method-note").textContent = cells
    ? "Точный вес ячейки — цель, делённая на её долю в выборке. До трёх переменных."
    : "Подгоняет маргинальные распределения по очереди, пока все не сойдутся.";
  document.querySelector("#weight-trimming-note").textContent = cells
    ? "Вес ячейки вне границ — ошибка, а не обрезка: ячейку придётся объединить"
    : "Обрезать экстремумы и подогнать цели заново";
  document.querySelector("#weight-dimensions-title").textContent = cells ? "Переменные ячеек" : "Целевые распределения";
  document.querySelector("#weight-dimensions-note").textContent = cells ? "категории образуют сочетания" : "сумма = 100%";
  document.querySelector("#add-weight-dimension").textContent = cells ? "+ Добавить переменную" : "+ Добавить распределение";
  document.querySelector("#weight-dimension-list").classList.toggle("cells-mode", cells);
  document.querySelector("#weight-cells").hidden = !cells;
  if (cells) renderWeightCells();
}

function weightDimensionCategories() {
  return [...document.querySelectorAll("#weight-dimension-list .weight-dimension")].map(element =>
    [...element.querySelectorAll(".weight-target .lbl")].map(label => label.textContent));
}

function renderWeightCells() {
  const container = document.querySelector("#weight-cell-list");
  const current = new Map([...container.querySelectorAll(".weight-cell")].map(row => [row.dataset.cell, row.querySelector("input").value]));
  savedWeightCells.forEach(cell => {
    const key = JSON.stringify(cell.categories);
    if (!current.has(key)) current.set(key, String(cell.percent));
  });
  const dimensions = weightDimensionCategories();
  if (dimensions.length > 3) {
    container.innerHTML = '<p class="error">Ячейки строятся не более чем по трём переменным.</p>';
    return;
  }
  let combinations = [[]];
  dimensions.forEach(categories => {
    combinations = combinations.flatMap(prefix => categories.map(category => [...prefix, category]));
  });
  const equal = 100 / Math.max(1, combinations.length);
  container.innerHTML = combinations.map(categories => {
    const key = JSON.stringify(categories);
    const value = current.get(key) ?? String(Number(equal.toFixed(4)));
    const label = categories.join(" × ");
    return `<label class="weight-cell t-row" data-cell="${escapeAttribute(key)}"><span class="lbl" title="${escapeAttribute(label)}">${escapeHtml(label)}</span><input type="number" min="0" max="100" step="0.0001" value="${escapeAttribute(value)}" aria-label="Цель ячейки ${escapeAttribute(label)}, процентов" required /></label>`;
  }).join("");
  updateWeightCellsStatus();
}

function updateWeightCellsStatus() {
  const total = [...document.querySelectorAll("#weight-cell-list .weight-cell input")]
    .reduce((sum, input) => sum + (Number(input.value) || 0), 0);
  const valid = Math.abs(total - 100) <= 0.1;
  const badge = document.querySelector("#weight-cells-sum");
  badge.className = `sum ${valid ? "ok" : "bad"}`;
  badge.textContent = `${formatWeightNumber(total)}%`;
}

function collectWeightCells() {
  const cells = [...document.querySelectorAll("#weight-cell-list .weight-cell")].map(row => ({
    categories: JSON.parse(row.dataset.cell),
    percent: Number(row.querySelector("input").value) || 0,
  }));
  if (!cells.length) throw new Error("Добавьте переменные ячеек.");
  const total = cells.reduce((sum, cell) => sum + cell.percent, 0);
  if (Math.abs(total - 100) > 0.1) {
    throw new Error(`Сумма целей ячеек должна составлять 100%. Сейчас ${formatWeightNumber(total)}%.`);
  }
  return cells;
}

/* Шаблон целей (§10): Excel по переменным редактора — заполнить и загрузить
   обратно. Загрузка только расставляет проценты по строкам редактора: ключ
   строки — источник измерения и код категории (или подпись, если кода нет),
   у ячеек — подписи их категорий. */
function weightTargetDraft() {
  const dimensions = [...document.querySelectorAll("#weight-dimension-list .weight-dimension")].map(element => {
    const source = weightSourceCategories(element.querySelector(".weight-dimension-source").value);
    return {
      variable: source.code,
      label: source.label,
      recoding_id: source.recodingId,
      targets: [...element.querySelectorAll(".weight-target")].map(row => ({
        label: row.querySelector(".lbl").textContent,
        values: [JSON.parse(row.dataset.value)],
        percent: Number(row.querySelector("input").value) || null,
      })),
    };
  });
  const cells = [...document.querySelectorAll("#weight-cell-list .weight-cell")].map(row => ({
    categories: JSON.parse(row.dataset.cell),
    percent: Number(row.querySelector("input").value) || 0,
  }));
  return { method: weightMethod(), dimensions, cells };
}

async function downloadWeightTemplate() {
  const status = document.querySelector("#weight-targets-status");
  status.textContent = "";
  try {
    const response = await fetch(`/api/projects/${currentProject.id}/weights/targets-template`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(weightTargetDraft()),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(typeof payload.detail === "string" ? payload.detail : "Шаблон собрать не удалось.");
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = "weight_targets.xlsx";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 60_000);
  } catch (error) {
    status.textContent = error.message;
  }
}

function sameTargetCode(code, value) {
  if (code === "") return false;
  const number = Number(code);
  return Number.isFinite(number) && typeof value === "number" ? number === value : code === String(value);
}

async function importWeightTargets(file) {
  const status = document.querySelector("#weight-targets-status");
  status.textContent = "Читаем…";
  const body = new FormData();
  body.append("file", file);
  try {
    const { rows } = await api(`/api/projects/${currentProject.id}/weights/targets-import`, { method: "POST", body });
    let applied = 0;
    rows.forEach(row => {
      if (row.key === "ячейка") {
        const cell = [...document.querySelectorAll("#weight-cell-list .weight-cell")]
          .find(element => JSON.parse(element.dataset.cell).join(" × ") === row.category);
        if (cell) {
          cell.querySelector("input").value = row.percent;
          applied += 1;
        }
        return;
      }
      const dimension = [...document.querySelectorAll("#weight-dimension-list .weight-dimension")]
        .find(element => element.querySelector(".weight-dimension-source").value === row.key);
      const target = dimension && [...dimension.querySelectorAll(".weight-target")].find(element =>
        sameTargetCode(row.code, JSON.parse(element.dataset.value))
        || element.querySelector(".lbl").textContent === row.category);
      if (target) {
        target.querySelector("input").value = row.percent;
        applied += 1;
      }
    });
    document.querySelectorAll("#weight-dimension-list .weight-dimension").forEach(updateWeightDimensionStatus);
    if (weightMethod() === "cells") updateWeightCellsStatus();
    markInspectorDirty(weightEditor);
    const missed = rows.length - applied;
    status.textContent = `Цели загружены: ${applied} из ${rows.length}`
      + (missed ? ` · не нашлось в редакторе: ${missed}` : "");
  } catch (error) {
    status.textContent = error.message;
  }
}

function renderWeightTrimming() {
  const enabled = document.querySelector("#weight-trimming").checked;
  document.querySelector("#weight-lower").disabled = !enabled;
  document.querySelector("#weight-upper").disabled = !enabled;
  document.querySelector("#weight-bound-fields").hidden = !enabled;
}

// Источник измерения — переменная SAV или сохранённая перекодировка. У
// перекодировки значение опции `recoding:<id>`, цели — её категории по номеру.
function weightSourceOptions(dimension = {}) {
  const selected = dimension.recoding_id ? `recoding:${dimension.recoding_id}` : dimension.variable || "";
  const questions = eligibleWeightQuestions().map(question => {
    const variable = question.source_variables[0];
    return `<option value="${escapeAttribute(variable)}" ${variable === selected ? "selected" : ""}>${escapeHtml(question.code)} — ${escapeHtml(question.label)}</option>`;
  });
  const recodings = configuredRecodings().map(recoding => {
    const value = `recoding:${recoding.id}`;
    return `<option value="${escapeAttribute(value)}" ${value === selected ? "selected" : ""}>↳ ${escapeHtml(recoding.code)} — ${escapeHtml(recoding.name)}</option>`;
  });
  return questions.join("") + recodings.join("");
}

// Категории выбранного источника: подпись и значение цели.
function weightSourceCategories(value) {
  if (value.startsWith("recoding:")) {
    const recoding = configuredRecodings().find(item => item.id === value.slice(9));
    return {
      label: recoding.name,
      code: recoding.code,
      recodingId: recoding.id,
      categories: recoding.categories.map((category, index) => ({ value: index + 1, label: category.label })),
    };
  }
  const variable = currentProject.inspection.variables.find(item => item.name === value);
  return { label: variable.label, code: value, recodingId: null, categories: variable.value_labels };
}

function eligibleWeightQuestions() {
  return configuredQuestions().filter(question => {
    if (question.question_type !== "single_choice" || question.source_variables.length !== 1) return false;
    const variable = currentProject.inspection.variables.find(item => item.name === question.source_variables[0]);
    return variable?.value_labels?.length >= 2;
  });
}

function addWeightDimension(dimension = {}) {
  if (!eligibleWeightQuestions().length && !configuredRecodings().length) {
    throw new Error("Для веса нужна категориальная переменная с метками значений или перекодировка.");
  }
  const element = document.createElement("div");
  element.className = "weight-dimension dim";
  element.innerHTML = `<div class="dim-head"><div class="weight-dimension-source-field"><span>Переменная</span><select class="weight-dimension-source" aria-label="Переменная целевого распределения">${weightSourceOptions(dimension)}</select></div><span class="sum" aria-live="polite"></span><button class="del" type="button" data-remove-weight-dimension title="Удалить распределение" aria-label="Удалить распределение">×</button></div><div class="weight-targets"></div>`;
  document.querySelector("#weight-dimension-list").append(element);
  renderWeightTargets(element, dimension.targets || []);
}

function renderWeightTargets(element, savedTargets = []) {
  const source = weightSourceCategories(element.querySelector(".weight-dimension-source").value);
  const equalTarget = 100 / source.categories.length;
  element.querySelector(".weight-targets").innerHTML = source.categories.map(item => {
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
  if (weightMethod() === "cells") {
    return elements.map(element => {
      const source = weightSourceCategories(element.querySelector(".weight-dimension-source").value);
      const targets = [...element.querySelectorAll(".weight-target")].map(row => ({
        label: row.querySelector(".lbl").textContent,
        values: [JSON.parse(row.dataset.value)],
      }));
      return { variable: source.code, label: source.label, recoding_id: source.recodingId, targets };
    });
  }
  return elements.map(element => {
    const source = weightSourceCategories(element.querySelector(".weight-dimension-source").value);
    const targets = [...element.querySelectorAll(".weight-target")].map(row => ({
      label: row.querySelector(".lbl").textContent,
      values: [JSON.parse(row.dataset.value)],
      percent: Number(row.querySelector("input").value),
    }));
    const total = targets.reduce((sum, target) => sum + target.percent, 0);
    if (Math.abs(total - 100) > 0.1) {
      throw new Error(`Сумма целей для «${source.label}» должна составлять 100%. Сейчас ${formatWeightNumber(total)}%.`);
    }
    return { variable: source.code, label: source.label, recoding_id: source.recodingId, targets };
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
  if (preview.method === "cells") metrics.pop();
  const metricGrid = `<dl class="diag-grid">${metrics.map(([value, label]) => `<div><dt>${escapeHtml(label)}</dt><dd class="${label === "Среднее" || label === "Эффективность, %" ? "ok" : ""}">${formatWeightNumber(value)}</dd></div>`).join("")}</dl>`;
  const distributions = preview.distributions.map(dimension => `<section class="weight-distribution"><div class="weight-distribution-head"><strong>${escapeHtml(dimension.label)}</strong><span>До → после · цель</span></div>${dimension.categories.map(category => `<div class="weight-result-row"><span title="${escapeAttribute(category.label)}">${escapeHtml(category.label)}</span><em>${category.before_percent.toFixed(1)} → <b>${category.after_percent.toFixed(1)}</b> · ${category.target_percent.toFixed(1)}%</em></div>`).join("")}</section>`).join("");
  const cells = preview.cells
    ? `<section class="weight-distribution"><div class="weight-distribution-head"><strong>Ячейки</strong><span>Доля в выборке → цель · вес</span></div>${preview.cells.map(cell => `<div class="weight-result-row"><span title="${escapeAttribute(cell.label)}">${escapeHtml(cell.label)}</span><em>${cell.before_percent.toFixed(1)} → <b>${cell.target_percent.toFixed(1)}%</b> · ${formatWeightNumber(cell.weight)}</em></div>`).join("")}</section>`
    : "";
  // Вес считается внутри каждой волны: у каждой свои база и design effect.
  const waves = preview.waves
    ? `<section class="weight-distribution"><div class="weight-distribution-head"><strong>Внутри волн</strong><span>База · эфф. база · DEFF</span></div>${preview.waves.map(wave => `<div class="weight-result-row"><span title="${escapeAttribute(wave.label)}">${escapeHtml(wave.label)}</span><em>${wave.base.toLocaleString("ru-RU")} · ${formatWeightNumber(wave.effective_base)} · <b>${formatWeightNumber(wave.design_effect)}</b></em></div>`).join("")}</section>`
    : "";
  return metricGrid + waves + cells + distributions;
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

/* ---------------- Кнопки и поля редактора ---------------- */

document.querySelector("#close-weight-editor").addEventListener("click", () => {
  if (confirmDiscard(weightEditor)) closeWeight();
});
document.querySelector("#add-weight-dimension").addEventListener("click", () => {
  addWeightDimension();
  if (weightMethod() === "cells") renderWeightCells();
});
document.querySelector("#delete-weight").addEventListener("click", (...args) => deleteWeight(...args));
document.querySelector("#refresh-weight-preview").addEventListener("click", (...args) => loadWeightPreview(...args));
document.querySelector("#weight-trimming").addEventListener("change", (...args) => renderWeightTrimming(...args));

/* ---------------- Измерения и цели ---------------- */

document.querySelector("#weight-dimension-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-weight-dimension]");
  if (!button) return;
  button.closest(".weight-dimension").remove();
  if (weightMethod() === "cells") renderWeightCells();
});
document.querySelector("#weight-dimension-list").addEventListener("change", event => {
  if (event.target.matches(".weight-dimension-source")) {
    renderWeightTargets(event.target.closest(".weight-dimension"));
    if (weightMethod() === "cells") renderWeightCells();
  }
});
document.querySelector("#weight-method").addEventListener("change", () => renderWeightMethod());
document.querySelector("#weight-template").addEventListener("click", () => { void downloadWeightTemplate(); });
document.querySelector("#weight-targets-file").addEventListener("change", event => {
  const [file] = event.target.files;
  event.target.value = "";
  if (file) void importWeightTargets(file);
});
document.querySelector("#weight-cell-list").addEventListener("input", () => updateWeightCellsStatus());
document.querySelector("#weight-dimension-list").addEventListener("input", event => {
  if (event.target.matches(".weight-target input")) {
    updateWeightDimensionStatus(event.target.closest(".weight-dimension"));
  }
});

/* ---------------- Сохранение ---------------- */

document.querySelector("#weight-form").addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-weight");
  const weightError = document.querySelector("#weight-error");
  weightError.hidden = true;
  let dimensions;
  try {
    dimensions = collectWeightDimensions();
  } catch (error) {
    showError(weightError, error);
    return;
  }
  const method = weightMethod();
  let cells = [];
  try {
    if (method === "cells") cells = collectWeightCells();
  } catch (error) {
    showError(weightError, error);
    return;
  }
  const trimming = document.querySelector("#weight-trimming").checked;
  const payload = {
    name: document.querySelector("#weight-name").value.trim(),
    method,
    dimensions,
    cells,
    lower_bound: trimming ? Number(document.querySelector("#weight-lower").value) : null,
    upper_bound: trimming ? Number(document.querySelector("#weight-upper").value) : null,
    tolerance: 0.001,
    maximum_iterations: 500,
  };
  setBusy(saveButton, true, "Рассчитываем…");
  try {
    const url = currentWeightId
      ? `/api/projects/${currentProject.id}/weights/${currentWeightId}`
      : `/api/projects/${currentProject.id}/weights`;
    currentProject = await api(url, {
      method: currentWeightId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!currentWeightId) {
      currentWeightId = configuredWeights().find(item => item.name === payload.name)?.id;
    }
    markInspectorClean(weightEditor);
    renderProject();
    openWeight(currentWeightId);
    await loadWeightPreview();
    showToast("Вес рассчитан и сохранён");
  } catch (error) {
    showError(weightError, error);
  } finally {
    setBusy(saveButton, false, "Рассчитать и сохранить");
  }
});
