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
  setHeadingText(document.querySelector("#recode-editor-title"), recoding ? recoding.code : "Новая переменная");
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
  renderVariableKinds(variableKindOf(document.querySelector("#recode-mode").value), Boolean(recoding));
  document.querySelector("#delete-recoding").hidden = !recoding;
  document.querySelector("#refresh-recode-preview").hidden = !recoding;
  document.querySelector("#recode-error").hidden = true;
  document.querySelector("#recode-preview").innerHTML = recoding
    ? '<p class="muted">Считаем…</p>'
    : '<p class="muted">Появится после сохранения.</p>';
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
  // Диапазоны и объединение — два вида группировки одной переменной; логику
  // и сегменты выбирает переключатель способа, а не этот список.
  document.querySelector("#recode-mode-field").hidden = sourceless;
  document.querySelectorAll("[data-recode-mode]").forEach(button => {
    const on = button.dataset.recodeMode === mode;
    button.classList.toggle("on", on);
    button.setAttribute("aria-checked", String(on));
  });
}

// Диапазоны или объединение — сегменты вместо списка: вариантов два, и
// видеть второй полезнее, чем прятать его за стрелкой.
document.querySelector("#recode-mode-field").addEventListener("click", event => {
  const button = event.target.closest("[data-recode-mode]");
  if (!button || button.classList.contains("on")) return;
  const select = document.querySelector("#recode-mode");
  select.value = button.dataset.recodeMode;
  select.dispatchEvent(new Event("change", { bubbles: true }));
  markInspectorDirty(recodeEditor);
  recodeEditor.dataset.built = "1";
});

/* Новая переменная (решение 026): один вход и переключатель способа, как
   Logic · Bucketing · Formula у Qualtrics. Способы сохраняются разными
   объектами — формула отдельно, остальное перекодировкой, — и друг в друга
   без потерь не переводятся. Поэтому способ выбирают, пока переменная не
   сохранена; у сохранённой он виден, но заблокирован. */
const VARIABLE_KINDS = [
  { kind: "conditions", label: "Логика", placeholder: "Например, «Частые клиенты»",
    hint: "Каждая категория — свои условия. Кто подходит под несколько, попадает в первую по порядку." },
  { kind: "grouping", label: "Группировка", placeholder: "Например, «Возрастные группы»",
    hint: "Новые категории из одной переменной: диапазоны числа или объединение ответов." },
  { kind: "formula", label: "Формула", placeholder: "Например, «Индекс удовлетворённости»",
    hint: "Число, которое считается по выражению из других полей." },
  { kind: "segments", label: "Сегменты", placeholder: "Например, «Сегменты»",
    hint: "Похожие респонденты собираются в группы по числовым вопросам (k-means)." },
];

function variableKindOf(mode) {
  return mode === "ranges" || mode === "categories" ? "grouping" : mode;
}

// Переключатель, подсказка под ним и надпись над заголовком. Для новой
// переменной заголовок — «Новая переменная», и способ называет переключатель;
// у сохранённой заголовок — её код, а способ стоит надписью над ним.
function renderVariableKinds(active, locked) {
  const current = VARIABLE_KINDS.find(item => item.kind === active);
  document.querySelectorAll("[data-kind-switch]").forEach(box => {
    box.innerHTML = VARIABLE_KINDS.map(item => {
      const on = item.kind === active;
      const blocked = locked && !on;
      return `<button type="button" role="radio" data-variable-kind="${item.kind}" aria-checked="${on}" class="${on ? "on" : ""}"
        ${blocked ? 'disabled title="Способ сохранённой переменной не меняется"' : ""}>${escapeHtml(item.label)}</button>`;
    }).join("");
  });
  document.querySelectorAll("[data-kind-hint]").forEach(node => { node.textContent = current?.hint || ""; });
  document.querySelectorAll("[data-variable-kicker]").forEach(node => {
    node.textContent = current?.label || "";
    node.hidden = !locked;
  });
  document.querySelector("#recode-name").placeholder = current?.placeholder || "";
  document.querySelector("#formula-label").placeholder = current?.placeholder || "";
}

function openNewVariable(kind, carry = {}) {
  delete recodeEditor.dataset.built;
  delete formulaEditor.dataset.built;
  if (kind === "formula") {
    openFormula(null, { label: carry.name });
    return;
  }
  openRecoding(null, {
    mode: kind === "grouping" ? "ranges" : kind,
    suggestName: carry.name || (kind === "segments" ? "Сегменты" : ""),
  });
}

// Название переходит в новый способ: его вводят первым, и терять его при
// переключении обидно. Поэтому ввод одного названия не считается потерей —
// спрашивают, только если уже собрано то, что при смене способа пропадёт.
// Код не переходит: у формулы и перекодировки свои подсказки свободного имени.
const CARRIED_FIELDS = new Set(["recode-name", "formula-label"]);
[recodeEditor, formulaEditor].forEach(panel => ["input", "change"].forEach(type => {
  panel.addEventListener(type, event => {
    if (event.isTrusted && !CARRIED_FIELDS.has(event.target.id)) panel.dataset.built = "1";
  });
}));

document.querySelector("#new-variable").addEventListener("click", () => openNewVariable("conditions"));
document.addEventListener("click", event => {
  const button = event.target.closest("[data-variable-kind]");
  if (!button || button.disabled || button.classList.contains("on")) return;
  const panel = formulaEditor.hidden ? recodeEditor : formulaEditor;
  const name = document.querySelector(panel === formulaEditor ? "#formula-label" : "#recode-name").value.trim();
  if (!panel.dataset.built) markInspectorClean(panel);
  openNewVariable(button.dataset.variableKind, { name });
});

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

/* Логическая переменная: категория читается фразой «Если … → категория».
   Условия собирает тот же конструктор, что фильтр; его группа здесь не
   рисуется своей рамкой (`display: contents` в styles.css), а её строки
   встают в сетку карточки категории. Кто не подошёл ни к одной категории,
   решает строка под списком: пропуск или своя категория «Иначе» — на
   сервере это последняя категория без условия. */
function defaultConditionCategories() {
  return [
    { label: "", rule: { operator: "and", items: [] } },
    { label: "", rule: { operator: "and", items: [] } },
  ];
}

function renderConditionCategories(categories) {
  document.querySelector("#condition-category-list").innerHTML = "";
  const otherwise = categories.find(category => category.otherwise);
  categories.filter(category => !category.otherwise).forEach(category => addConditionCategory(category));
  setOtherwise(otherwise ? otherwise.label : null);
}

function addConditionCategory(category = { label: "", rule: { operator: "and", items: [] } }, after = null) {
  const list = document.querySelector("#condition-category-list");
  const element = document.createElement("div");
  element.className = "condition-category";
  element.innerHTML = `
    <span class="condition-category-number" aria-hidden="true"></span>
    <input class="condition-category-label" maxlength="250" placeholder="Название категории" aria-label="Название категории" value="${escapeAttribute(category.label || "")}" />
    <span class="condition-category-tools">
      <button type="button" class="icon-button" data-copy-condition-category aria-label="Копировать условия в новую категорию" title="Копировать условия в новую категорию ниже">⧉</button>
      <button type="button" class="icon-button" data-remove-condition-category aria-label="Удалить категорию" title="Удалить категорию">×</button>
    </span>`;
  if (after) after.after(element);
  else list.append(element);
  addFilterGroup(category.rule || {}, element);
  element.querySelector("[data-add-group-condition]").textContent = "+ Ещё условие";
  numberConditionCategories();
  return element;
}

function numberConditionCategories() {
  document.querySelectorAll("#condition-category-list .condition-category").forEach((element, index) => {
    element.querySelector(".condition-category-number").textContent = String(index + 1);
    const lead = element.querySelector(".filter-group-head label > span");
    if (lead) lead.textContent = "Попадают, если";
  });
}

function setOtherwise(label) {
  const own = label != null;
  document.querySelectorAll("#otherwise-mode [data-otherwise]").forEach(button => {
    const on = (button.dataset.otherwise === "category") === own;
    button.classList.toggle("on", on);
    button.setAttribute("aria-checked", String(on));
  });
  const input = document.querySelector("#otherwise-label");
  input.hidden = !own;
  input.value = own ? label : "";
}

function collectConditionCategories() {
  const elements = [...document.querySelectorAll("#condition-category-list .condition-category")];
  if (!elements.length) throw new Error("Добавьте хотя бы одну категорию с условием.");
  const categories = elements.map(element => {
    const label = element.querySelector(".condition-category-label").value.trim();
    if (!label) throw new Error("У каждой категории должно быть название.");
    return { label, rule: collectFilterItem(element.querySelector(".filter-group")) };
  });
  const input = document.querySelector("#otherwise-label");
  if (!input.hidden) {
    const label = input.value.trim();
    if (!label) throw new Error("Назовите категорию для тех, кто не подошёл ни к одной.");
    categories.push({ label, otherwise: true });
  }
  if (categories.length < 2) {
    throw new Error("Нужны две категории: добавьте ещё одну или соберите не подошедших в свою.");
  }
  return categories;
}

function markConditionsBuilt() {
  markInspectorDirty(recodeEditor);
  recodeEditor.dataset.built = "1";
}

document.querySelector("#add-condition-category").addEventListener("click", () => {
  addConditionCategory().querySelector(".condition-category-label").focus();
  markConditionsBuilt();
});
document.querySelector("#otherwise-mode").addEventListener("click", event => {
  const button = event.target.closest("[data-otherwise]");
  if (!button || button.classList.contains("on")) return;
  const own = button.dataset.otherwise === "category";
  setOtherwise(own ? "Остальные" : null);
  if (own) document.querySelector("#otherwise-label").select();
  markConditionsBuilt();
});
document.querySelector("#condition-category-list").addEventListener("click", event => {
  // Копия условий — Copy Above Condition Set у Qualtrics: соседние категории
  // часто отличаются одним условием, и собирать их заново дольше, чем править.
  const copy = event.target.closest("[data-copy-condition-category]");
  if (copy) {
    const source = copy.closest(".condition-category");
    const error = document.querySelector("#recode-error");
    let rule;
    try {
      rule = collectFilterItem(source.querySelector(".filter-group"));
    } catch (problem) {
      error.textContent = problem.message;
      error.hidden = false;
      return;
    }
    error.hidden = true;
    addConditionCategory({ label: "", rule }, source).querySelector(".condition-category-label").focus();
    markConditionsBuilt();
    return;
  }
  const remove = event.target.closest("[data-remove-condition-category]");
  if (remove) {
    remove.closest(".condition-category").remove();
    numberConditionCategories();
    markConditionsBuilt();
  }
});

// Логические переменные не принадлежат одному вопросу, поэтому открываются
// с панели раздела «Данные», а не из карточки вопроса.
function renderLogicVariablePicker() {
  const select = document.querySelector("#logic-variables");
  const logic = configuredRecodings().filter(item => item.mode === "conditions" || item.mode === "segments");
  // Новые переменные заводит «+ Переменная»; здесь только открывают
  // сохранённые, поэтому без них список не показывается.
  select.innerHTML = `<option value="">Логические переменные · ${logic.length}</option>`
    + logic.map(item => `<option value="${escapeAttribute(item.id)}">${item.mode === "segments" ? "◎ " : ""}${escapeHtml(item.code)} — ${escapeHtml(item.name)}</option>`).join("");
  select.value = "";
  select.closest(".pill-select").classList.toggle("is-empty", !logic.length);
}

document.querySelector("#logic-variables").addEventListener("change", event => {
  const value = event.target.value;
  event.target.value = "";
  if (value) openRecoding(value);
});

function addRangeRow(category = {}) {
  const row = document.createElement("div");
  row.className = "range-row";
  row.innerHTML = `
    <input class="range-label" aria-label="Название категории" placeholder="Название" value="${escapeAttribute(category.label || "")}" required />
    <input class="range-lower" aria-label="От" type="number" step="any" placeholder="−∞" value="${category.lower ?? ""}" />
    <span>—</span>
    <input class="range-upper" aria-label="До" type="number" step="any" placeholder="+∞" value="${category.upper ?? ""}" />
    <button type="button" class="icon-button" data-remove-range aria-label="Удалить диапазон" title="Удалить диапазон">×</button>`;
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

/* ---------------- Кнопки и форма редактора ---------------- */

document.querySelector("#close-recode-editor").addEventListener("click", () => {
  if (confirmDiscard(recodeEditor)) closeRecoding();
});
document.querySelector("#add-range").addEventListener("click", () => addRangeRow());
// Заготовка диапазонов по данным: равные по численности группы или интервалы.
document.querySelector("#suggest-ranges").addEventListener("click", async () => {
  const note = document.querySelector("#range-suggest-note");
  const variable = document.querySelector("#recode-source").value;
  note.hidden = false;
  if (!variable) {
    note.textContent = "Сначала выберите исходную переменную.";
    return;
  }
  note.textContent = "Считаем…";
  try {
    const suggestion = await api(`/api/projects/${currentProject.id}/recodings/suggest-ranges`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        variable,
        method: document.querySelector("#range-method").value,
        groups: Number(document.querySelector("#range-groups").value),
      }),
    });
    const list = document.querySelector("#range-list");
    list.innerHTML = "";
    suggestion.categories.forEach(category => addRangeRow(category));
    const counts = suggestion.categories.map(category => category.count.toLocaleString("ru-RU")).join(" · ");
    const fewer = suggestion.categories.length < suggestion.requested
      ? ` Групп ${suggestion.categories.length} вместо ${suggestion.requested}: у многих одинаковые значения.`
      : "";
    note.textContent = `Респондентов в группах: ${counts}.${fewer}`;
    markInspectorDirty(recodeEditor);
  } catch (error) {
    note.textContent = error.message;
  }
});
document.querySelector("#add-category-group").addEventListener("click", () => {
  const count = document.querySelectorAll("#category-group-list .category-group").length;
  addCategoryGroup(`Группа ${count + 1}`);
  refreshCategoryZones();
});
document.querySelector("#recode-mode").addEventListener("change", () => {
  fillRecodeSources();
  renderRecodeMode();
  if (document.querySelector("#recode-mode").value === "categories") void renderCategoryEditor(defaultCategoryGroups());
  if (document.querySelector("#recode-mode").value === "conditions") renderConditionCategories(defaultConditionCategories());
});
document.querySelector("#recode-source").addEventListener("change", () => {
  if (document.querySelector("#recode-mode").value === "categories") void renderCategoryEditor(defaultCategoryGroups());
});
document.querySelector("#refresh-recode-preview").addEventListener("click", (...args) => loadRecodePreview(...args));
document.querySelector("#delete-recoding").addEventListener("click", (...args) => deleteRecoding(...args));

/* ---------------- Удаление диапазона и раскладка ответов по группам ---------------- */

document.querySelector("#range-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-range]");
  if (button) button.closest(".range-row").remove();
});
const categoryEditorElement = document.querySelector("#category-editor");
categoryEditorElement.addEventListener("click", event => {
  const remove = event.target.closest("button[data-remove-category-group]");
  if (remove) {
    const group = remove.closest(".category-group");
    const pool = document.querySelector('#category-pool [data-zone="pool"]');
    if (pool) moveValueChips([...group.querySelectorAll(".value-chip")], pool);
    group.remove();
    refreshCategoryZones();
    return;
  }
  const move = event.target.closest(".zone-move");
  if (move) {
    moveValueChips([...categoryEditorElement.querySelectorAll('.value-chip[aria-pressed="true"]')], move.closest(".category-zone"));
    return;
  }
  const chip = event.target.closest(".value-chip");
  if (chip) {
    chip.setAttribute("aria-pressed", chip.getAttribute("aria-pressed") === "true" ? "false" : "true");
    refreshCategoryZones();
  }
});
categoryEditorElement.addEventListener("dragstart", event => {
  const chip = event.target.closest?.(".value-chip");
  if (!chip) return;
  // Выбранные ответы тянутся вместе, если среди них тот, за который взялись.
  const pressed = [...categoryEditorElement.querySelectorAll('.value-chip[aria-pressed="true"]')];
  draggedValueChips = pressed.includes(chip) ? pressed : [chip];
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", chip.dataset.sourceValue);
  draggedValueChips.forEach(item => item.classList.add("dragging"));
});
categoryEditorElement.addEventListener("dragover", event => {
  const zone = event.target.closest?.(".category-zone");
  if (!zone || !draggedValueChips.length) return;
  event.preventDefault();
  zone.classList.add("drop-target");
});
categoryEditorElement.addEventListener("dragleave", event => {
  const zone = event.target.closest?.(".category-zone");
  if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove("drop-target");
});
categoryEditorElement.addEventListener("drop", event => {
  const zone = event.target.closest?.(".category-zone");
  if (!zone || !draggedValueChips.length) return;
  event.preventDefault();
  zone.classList.remove("drop-target");
  moveValueChips(draggedValueChips, zone);
});
categoryEditorElement.addEventListener("dragend", () => {
  draggedValueChips.forEach(item => item.classList.remove("dragging"));
  draggedValueChips = [];
  categoryEditorElement.querySelectorAll(".drop-target").forEach(zone => zone.classList.remove("drop-target"));
});

/* ---------------- Сохранение ---------------- */

document.querySelector("#recode-form").addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-recoding");
  const recodeError = document.querySelector("#recode-error");
  recodeError.hidden = true;
  let categories;
  const mode = document.querySelector("#recode-mode").value;
  let segments = null;
  try {
    if (mode === "segments") {
      segments = collectSegmentDefinition();
      categories = segments.categories;
    } else {
      categories = mode === "ranges"
        ? collectRanges()
        : mode === "conditions" ? collectConditionCategories() : collectCategoryGroups();
    }
  } catch (error) {
    showError(recodeError, error);
    return;
  }
  const payload = {
    mode,
    code: document.querySelector("#recode-code").value.trim(),
    name: document.querySelector("#recode-name").value.trim(),
    source_variable: mode === "conditions" || mode === "segments" ? undefined : document.querySelector("#recode-source").value,
    categories,
    ...(segments ? { variables: segments.variables, k: segments.k } : {}),
  };
  setBusy(saveButton, true, "Сохраняем…");
  try {
    const url = currentRecodingId
      ? `/api/projects/${currentProject.id}/recodings/${currentRecodingId}`
      : `/api/projects/${currentProject.id}/recodings`;
    const method = currentRecodingId ? "PUT" : "POST";
    currentProject = await api(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!currentRecodingId) {
      currentRecodingId = configuredRecodings().find(item => item.code === payload.code)?.id;
    }
    recodePreviewCache.delete(recodePreviewKey(currentRecodingId));
    markInspectorClean(recodeEditor);
    renderProject();
    openRecoding(currentRecodingId);
    await loadRecodePreview();
    showToast("Перекодировка сохранена");
  } catch (error) {
    showError(recodeError, error);
  } finally {
    setBusy(saveButton, false, "Сохранить");
  }
});

/* ---------------- Ключ кэша предпросмотра ---------------- */

function recodePreviewKey(recodingId) {
  return `${currentProject?.id || ""}:${recodingId}`;
}

/* ---------------- Свободный код новой перекодировки ---------------- */

function suggestRecodeCode() {
  const used = new Set(configuredRecodings().map(item => item.code.toUpperCase()));
  let index = used.size + 1;
  while (used.has(`RECODE_${index}`)) index += 1;
  return `RECODE_${index}`;
}
