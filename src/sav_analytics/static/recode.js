/* Редактор перекодировки: диапазоны, группы категорий и логические переменные. Классический скрипт после app.js (см. решение 016): пользуется
   его состоянием, а app.js вызывает эти функции только после запуска. */

/* Перекодировка — свойство переменной, а не отчёта: её заводят из карточки
   вопроса, на котором она строится. Отсюда и возврат: закрыв редактор,
   аналитик попадает обратно в тот вопрос, из которого пришёл. */
let recodeReturnTo = null;

function openRecoding(recodingId = null, options = {}) {
  if (!confirmDiscard(openInspectorPanel())) return;
  if ("returnTo" in options) recodeReturnTo = options.returnTo || null;
  currentRecodingId = recodingId;
  currentQuestionCode = null;
  showInspector(recodeEditor);
  const recoding = recodingId ? configuredRecodings().find(item => item.id === recodingId) : null;
  setHeadingText(document.querySelector("#recode-editor-title"), recoding ? recoding.code : "Новая");
  document.querySelector("#recode-code").value = recoding?.code || suggestRecodeCode();
  document.querySelector("#recode-name").value = recoding?.name || options.suggestName || "";
  document.querySelector("#recode-mode").value = recoding?.mode || options.mode || "ranges";
  fillRecodeSources(recoding?.source_variable || options.sourceVariable);
  const rangeList = document.querySelector("#range-list");
  rangeList.innerHTML = "";
  const categoryList = document.querySelector("#category-group-list");
  categoryList.innerHTML = "";
  document.querySelector("#condition-category-list").innerHTML = "";
  renderSegmentEditor(recoding?.mode === "segments" ? recoding : null);
  if (document.querySelector("#recode-mode").value === "segments") {
    // Сегменты считает сервер при сохранении; здесь только их параметры.
  } else if (document.querySelector("#recode-mode").value === "categories") {
    void renderCategoryEditor(recoding?.categories || defaultCategoryGroups());
  } else if (document.querySelector("#recode-mode").value === "conditions") {
    renderConditionCategories(recoding?.categories || defaultConditionCategories());
  } else if (recoding) {
    recoding.categories.forEach(category => addRangeRow(category));
  } else if (document.querySelector("#recode-mode").value === "ranges") {
    addRangeRow({ label: "18–24", lower: 18, upper: 24 });
    addRangeRow({ label: "25–34", lower: 25, upper: 34 });
    addRangeRow({ label: "35 и старше", lower: 35, upper: null });
  }
  renderRecodeMode();
  document.querySelector("#delete-recoding").hidden = !recoding;
  document.querySelector("#recode-error").hidden = true;
  document.querySelector("#recode-preview").innerHTML = recoding
    ? '<p class="muted">Считаем…</p>'
    : '<p class="muted">Сохраните перекодировку для расчёта.</p>';
  renderTable();
  if (recoding) loadRecodePreview();
}

function closeRecoding() {
  recodeEditor.hidden = true;
  currentRecodingId = null;
  const back = recodeReturnTo;
  recodeReturnTo = null;
  if (back && findQuestion(back)) {
    openQuestion(back);
    return;
  }
  renderTable();
}

function fillRecodeSources(selected) {
  const mode = document.querySelector("#recode-mode").value;
  const sources = currentProject.inspection.variables.filter(item => (
    mode === "ranges" ? item.storage_type === "numeric" : item.value_labels.length > 0
  ));
  document.querySelector("#recode-source").innerHTML = sources.map(variable => `
    <option value="${escapeHtml(variable.name)}" ${variable.name === selected ? "selected" : ""}>${escapeHtml(variable.name)} — ${escapeHtml(variable.label)}</option>`).join("");
}

function renderRecodeMode() {
  const mode = document.querySelector("#recode-mode").value;
  document.querySelector("#range-editor").hidden = mode !== "ranges";
  document.querySelector("#category-editor").hidden = mode !== "categories";
  document.querySelector("#condition-editor").hidden = mode !== "conditions";
  document.querySelector("#segment-editor").hidden = mode !== "segments";
  // У логической переменной и сегментации нет одной исходной переменной.
  const sourceless = mode === "conditions" || mode === "segments";
  document.querySelector("#recode-source-field").hidden = sourceless;
  document.querySelector("#recode-source").disabled = sourceless;
}

/* Сегментация (PQ.11): переменные, число сегментов и подписи. Центры
   считает сервер при сохранении, подбор числа сегментов — тоже он. */
function renderSegmentEditor(recoding) {
  const chosen = new Set(recoding?.variables || []);
  const candidates = configuredQuestions().filter(question =>
    ["numeric", "scale"].includes(question.question_type) && question.source_variables.length === 1);
  document.querySelector("#segment-variable-list").innerHTML = candidates.length
    ? candidates.map(question => `<label class="checkbox"><input type="checkbox" value="${escapeAttribute(question.code)}" ${chosen.has(question.code) ? "checked" : ""} /> <code>${escapeHtml(question.code)}</code> ${escapeHtml(question.label)}</label>`).join("")
    : '<p class="muted">В проекте нет числовых вопросов и шкал.</p>';
  document.querySelector("#segment-k").value = recoding?.k || 3;
  document.querySelector("#segment-options").innerHTML = "";
  document.querySelector("#segment-labels").innerHTML = recoding
    ? `<div class="range-heading grp-cap"><strong>Подписи сегментов</strong><small>По убыванию размера</small></div>${recoding.categories.map((category, index) => `<label class="f">Сегмент ${index + 1}<input class="segment-label" value="${escapeAttribute(category.label)}" maxlength="250" /></label>`).join("")}`
    : "";
}

function chosenSegmentVariables() {
  return [...document.querySelectorAll("#segment-variable-list input:checked")].map(input => input.value);
}

function collectSegmentDefinition() {
  const variables = chosenSegmentVariables();
  if (!variables.length) throw new Error("Отметьте хотя бы одну переменную сегментации.");
  const k = Number(document.querySelector("#segment-k").value);
  const labels = [...document.querySelectorAll("#segment-labels .segment-label")].map(input => ({ label: input.value.trim() || input.placeholder }));
  return { variables, k, categories: labels.length === k ? labels : [] };
}

async function suggestSegmentCount() {
  const box = document.querySelector("#segment-options");
  const variables = chosenSegmentVariables();
  if (!variables.length) {
    box.innerHTML = '<p class="error">Отметьте переменные сегментации.</p>';
    return;
  }
  box.innerHTML = '<p class="muted">Считаем варианты…</p>';
  try {
    const result = await api(`/api/projects/${currentProject.id}/recodings/segments/suggest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ variables, k_min: 2, k_max: 6 }),
    });
    box.innerHTML = `<p class="muted">База без пропусков: ${result.base.toLocaleString("ru-RU")}. Силуэт ближе к 1 — сегменты лучше разделены.</p><table class="segment-table"><tr><th>Сегментов</th><th>Силуэт</th><th>Размеры</th><th></th></tr>${result.options.map(option => `<tr class="${option.k === result.best ? "best" : ""}"><td>${option.k}</td><td>${option.silhouette.toLocaleString("ru-RU", { maximumFractionDigits: 3 })}</td><td>${option.sizes.join(" · ")}</td><td><button type="button" class="text-button" data-segment-k="${option.k}">${option.k === result.best ? "Лучший — выбрать" : "Выбрать"}</button></td></tr>`).join("")}</table>`;
  } catch (error) {
    box.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

document.querySelector("#suggest-segments").addEventListener("click", () => { void suggestSegmentCount(); });
document.querySelector("#segment-options").addEventListener("click", event => {
  const button = event.target.closest("[data-segment-k]");
  if (!button) return;
  document.querySelector("#segment-k").value = button.dataset.segmentK;
  markInspectorDirty(recodeEditor);
});

// Логическая переменная: категория — подпись и группа условий, собранная тем
// же редактором, что фильтр. Порядок важен: респондент попадает в первую
// подходящую категорию.
function defaultConditionCategories() {
  return [
    { label: "", rule: { operator: "and", items: [] } },
    { label: "", rule: { operator: "and", items: [] } },
  ];
}

function renderConditionCategories(categories) {
  document.querySelector("#condition-category-list").innerHTML = "";
  categories.forEach(category => addConditionCategory(category));
}

function addConditionCategory(category = { label: "", rule: { operator: "and", items: [] } }) {
  const element = document.createElement("div");
  element.className = "condition-category";
  element.innerHTML = `<div class="condition-category-head">
      <span class="condition-category-number" aria-hidden="true"></span>
      <input class="condition-category-label" maxlength="250" placeholder="Название категории" aria-label="Название категории" value="${escapeAttribute(category.label || "")}" />
      <button type="button" class="icon-button" data-remove-condition-category aria-label="Удалить категорию">×</button>
    </div>`;
  document.querySelector("#condition-category-list").append(element);
  addFilterGroup(category.rule || {}, element);
  numberConditionCategories();
}

function numberConditionCategories() {
  document.querySelectorAll("#condition-category-list .condition-category-number").forEach((node, index) => {
    node.textContent = String(index + 1);
  });
}

function collectConditionCategories() {
  const elements = [...document.querySelectorAll("#condition-category-list .condition-category")];
  if (elements.length < 2) throw new Error("Добавьте минимум две категории.");
  return elements.map(element => {
    const label = element.querySelector(".condition-category-label").value.trim();
    if (!label) throw new Error("У каждой категории должно быть название.");
    return { label, rule: collectFilterItem(element.querySelector(".filter-group")) };
  });
}

document.querySelector("#add-condition-category").addEventListener("click", () => addConditionCategory());
document.querySelector("#condition-category-list").addEventListener("click", event => {
  const remove = event.target.closest("[data-remove-condition-category]");
  if (!remove) return;
  remove.closest(".condition-category").remove();
  numberConditionCategories();
});

// Логические переменные не принадлежат одному вопросу, поэтому открываются
// с панели раздела «Данные», а не из карточки вопроса.
function renderLogicVariablePicker() {
  const select = document.querySelector("#logic-variables");
  const logic = configuredRecodings().filter(item => item.mode === "conditions" || item.mode === "segments");
  select.innerHTML = `<option value="">${logic.length ? `Логические переменные · ${logic.length}` : "Логические переменные"}</option>`
    + logic.map(item => `<option value="${escapeAttribute(item.id)}">${item.mode === "segments" ? "◎ " : ""}${escapeHtml(item.code)} — ${escapeHtml(item.name)}</option>`).join("")
    + '<option value="new">+ Новая логическая переменная</option>'
    + '<option value="new-segments">+ Новая сегментация (k-means)</option>';
  select.value = "";
}

document.querySelector("#logic-variables").addEventListener("change", event => {
  const value = event.target.value;
  event.target.value = "";
  if (!value) return;
  if (value === "new") openRecoding(null, { mode: "conditions" });
  else if (value === "new-segments") openRecoding(null, { mode: "segments", suggestName: "Сегменты" });
  else openRecoding(value);
});

function addRangeRow(category = {}) {
  const row = document.createElement("div");
  row.className = "range-row";
  row.innerHTML = `
    <input class="range-label" aria-label="Название категории" placeholder="Название" value="${escapeAttribute(category.label || "")}" required />
    <input class="range-lower" aria-label="От" type="number" step="any" placeholder="От" value="${category.lower ?? ""}" />
    <span>—</span>
    <input class="range-upper" aria-label="До" type="number" step="any" placeholder="До" value="${category.upper ?? ""}" />
    <button type="button" data-remove-range title="Удалить категорию">×</button>`;
  document.querySelector("#range-list").append(row);
}

function collectRanges() {
  const rows = [...document.querySelectorAll("#range-list .range-row")];
  if (rows.length < 2) throw new Error("Добавьте минимум две категории.");
  return rows.map(row => {
    const label = row.querySelector(".range-label").value.trim();
    const lowerRaw = row.querySelector(".range-lower").value;
    const upperRaw = row.querySelector(".range-upper").value;
    if (!label) throw new Error("У каждой категории должно быть название.");
    if (lowerRaw === "" && upperRaw === "") throw new Error(`У категории «${label}» нет границ.`);
    return {
      label,
      lower: lowerRaw === "" ? null : Number(lowerRaw),
      upper: upperRaw === "" ? null : Number(upperRaw),
    };
  });
}

// Группы собираются раскладкой ответов: из пула «Не распределены» ответ
// перетаскивается в группу. С клавиатуры и для нескольких ответов сразу ответ
// выбирается щелчком и переносится кнопкой «Сюда». Частоты ответов и базы групп
// видны до сохранения — их отдаёт `GET …/recodings/source-values`.
const recodeSourceValuesCache = new Map();
let categoryEditorToken = 0;
let categoryEditorLoading = false;
let draggedValueChips = [];

function defaultCategoryGroups() {
  return [{ label: "Группа 1", values: [] }, { label: "Группа 2", values: [] }];
}

function recodeSourceValues(variableName) {
  const key = `${currentProject.id}:${variableName}`;
  if (!recodeSourceValuesCache.has(key)) {
    const request = api(`/api/projects/${currentProject.id}/recodings/source-values?variable=${encodeURIComponent(variableName)}`);
    request.catch(() => recodeSourceValuesCache.delete(key));
    recodeSourceValuesCache.set(key, request);
  }
  return recodeSourceValuesCache.get(key);
}

async function renderCategoryEditor(groups) {
  const token = ++categoryEditorToken;
  const pool = document.querySelector("#category-pool");
  const variableName = document.querySelector("#recode-source").value;
  document.querySelector("#category-group-list").innerHTML = "";
  if (!variableName) {
    pool.innerHTML = '<p class="muted">Нет переменных с подписями значений.</p>';
    categoryEditorLoading = false;
    return;
  }
  pool.innerHTML = '<p class="muted">Загружаем ответы…</p>';
  categoryEditorLoading = true;
  let source;
  try {
    source = await recodeSourceValues(variableName);
  } catch (error) {
    if (token === categoryEditorToken) {
      pool.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
      categoryEditorLoading = false;
    }
    return;
  }
  if (token !== categoryEditorToken) return;
  // Группируются только подписанные коды: значение без подписи сервер
  // отвергнет. Неподписанные остаются вне групп и названы под пулом.
  const labelled = source.values.filter(item => item.labelled);
  const taken = [];
  groups.forEach(group => {
    const options = (group.values || []).map(value =>
      labelled.find(item => String(item.value) === String(value))
        || { value, label: String(value), code: String(value), count: 0 });
    options.forEach(option => taken.push(option.value));
    addCategoryGroup(group.label, options);
  });
  const free = labelled.filter(item => !containsComparable(taken, item.value));
  const unlabelled = source.values.filter(item => !item.labelled);
  const unlabelledCount = unlabelled.reduce((total, item) => total + item.count, 0);
  const note = unlabelled.length
    ? `<p class="category-pool-note">Коды без подписи (${unlabelled.map(item => escapeHtml(item.code)).join(", ")}) у ${unlabelledCount.toLocaleString("ru-RU")} чел. в группы не входят.</p>`
    : "";
  pool.innerHTML = `<div class="category-zone category-pool-zone" data-zone="pool">
      <div class="category-zone-head"><strong>Не распределены</strong><span class="category-zone-count"></span><button type="button" class="zone-move" hidden>Сюда</button></div>
      <div class="value-chips">${free.map(valueChipMarkup).join("")}</div>
    </div>${note}`;
  categoryEditorLoading = false;
  refreshCategoryZones();
}

function valueChipMarkup(option) {
  const code = option.code != null && option.code !== option.label ? `<code>${escapeHtml(option.code)}</code>` : "";
  return `<button type="button" class="value-chip" draggable="true" aria-pressed="false" data-source-value="${escapeAttribute(JSON.stringify(option.value))}" data-count="${option.count}" title="Щелчок — выбрать, перетаскивание — перенести в группу"><span>${escapeHtml(option.label)}</span>${code}<em>${option.count.toLocaleString("ru-RU")}</em></button>`;
}

function addCategoryGroup(label, options = []) {
  const group = document.createElement("div");
  group.className = "category-group category-zone";
  group.dataset.zone = "group";
  group.innerHTML = `<div class="category-group-head"><input class="category-group-label" aria-label="Название новой категории" value="${escapeAttribute(label || "")}" placeholder="Название группы" /><button type="button" data-remove-category-group title="Удалить группу" aria-label="Удалить группу">×</button></div>
    <div class="category-zone-head"><span class="category-zone-count"></span><button type="button" class="zone-move" hidden>Сюда</button></div>
    <div class="value-chips">${options.map(valueChipMarkup).join("")}</div>`;
  document.querySelector("#category-group-list").append(group);
}

function moveValueChips(chips, zone) {
  const target = zone.querySelector(".value-chips");
  chips.forEach(chip => {
    chip.setAttribute("aria-pressed", "false");
    target.append(chip);
  });
  refreshCategoryZones();
}

function refreshCategoryZones() {
  const pressed = document.querySelectorAll('#category-editor .value-chip[aria-pressed="true"]').length;
  document.querySelectorAll("#category-editor .category-zone").forEach(zone => {
    const chips = [...zone.querySelector(".value-chips").children];
    const people = chips.reduce((total, chip) => total + Number(chip.dataset.count || 0), 0);
    zone.querySelector(".category-zone-count").textContent = chips.length
      ? `${plural(chips.length, "ответ", "ответа", "ответов")} · ${people.toLocaleString("ru-RU")} чел.`
      : (zone.dataset.zone === "pool" ? "все ответы разложены" : "");
    const move = zone.querySelector(".zone-move");
    move.hidden = pressed === 0;
    move.textContent = pressed > 1 ? `Сюда · ${pressed}` : "Сюда";
  });
}

function collectCategoryGroups() {
  if (categoryEditorLoading) throw new Error("Дождитесь, пока загрузятся ответы.");
  const groups = [...document.querySelectorAll("#category-group-list .category-group")];
  if (groups.length < 2) throw new Error("Добавьте минимум две новые категории.");
  return groups.map(group => {
    const label = group.querySelector(".category-group-label").value.trim();
    const values = [...group.querySelectorAll(".value-chip")].map(chip => JSON.parse(chip.dataset.sourceValue));
    if (!label) throw new Error("У каждой новой категории должно быть название.");
    if (!values.length) throw new Error(`В категории «${label}» нет ни одного ответа.`);
    return { label, values };
  });
}

async function loadRecodePreview() {
  if (!currentProject || !currentRecodingId) return;
  const container = document.querySelector("#recode-preview");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const preview = await api(`/api/projects/${currentProject.id}/recodings/${currentRecodingId}/preview`);
    recodePreviewCache.set(recodePreviewKey(currentRecodingId), preview);
    container.innerHTML = renderRecodePreview(preview);
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

function renderRecodePreview(preview) {
  const base = `<div class="base-line"><span>Total <strong>${preview.total_base.toLocaleString("ru-RU")}</strong></span><span>Пропуски <strong>${preview.source_missing_count}</strong></span><span>Вне диапазонов <strong>${preview.out_of_range_count}</strong></span></div>`;
  const rows = `<div class="preview-rows">${preview.rows.map(row => `
    <div><span>${escapeHtml(row.label)}</span><strong>${row.count}</strong><em>${formatPercent(row.percent_total)}</em><em>${escapeHtml(preview.mode === "categories" ? `${row.source_values.length} знач.` : preview.mode === "conditions" ? "по правилу" : preview.mode === "segments" ? "центр" : formatRange(row))}</em></div>`).join("")}</div>`;
  if (preview.mode === "segments") {
    // Профиль — средние переменных в исходных единицах: по нему сегмент называют.
    const codes = preview.variables;
    const profile = `<table class="segment-table"><tr><th>Сегмент</th>${codes.map(code => `<th>${escapeHtml(code)}</th>`).join("")}</tr>${preview.rows.map(row => `<tr><td>${escapeHtml(row.label)}</td>${codes.map(code => `<td>${row.means[code] == null ? "—" : Number(row.means[code]).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}</td>`).join("")}</tr>`).join("")}</table>`;
    return `<div class="base-line"><span>Total <strong>${preview.total_base.toLocaleString("ru-RU")}</strong></span><span>Без сегмента <strong>${preview.missing_count}</strong></span></div>${rows}${profile}`;
  }
  return base + rows;
}

async function deleteRecoding() {
  if (!currentRecodingId || !confirm("Удалить эту перекодировку? Исходная переменная не изменится.")) return;
  const recodeError = document.querySelector("#recode-error");
  try {
    recodePreviewCache.delete(recodePreviewKey(currentRecodingId));
    currentProject = await api(`/api/projects/${currentProject.id}/recodings/${currentRecodingId}`, { method: "DELETE" });
    closeRecoding();
    renderProject();
    showToast("Перекодировка удалена");
  } catch (error) {
    showError(recodeError, error);
  }
}
