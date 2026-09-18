/* Редактор фильтра: конструктор условий и предпросмотр базы. Классический скрипт после app.js (см. решение 016): пользуется
   его состоянием, а app.js вызывает эти функции только после запуска. */

function openFilter(filterId = null) {
  if (!confirmDiscard(openInspectorPanel())) return;
  currentFilterId = filterId;
  currentQuestionCode = null;
  currentRecodingId = null;
  currentBannerId = null;
  showInspector(filterEditor);
  const filter = filterId ? configuredFilters().find(item => item.id === filterId) : null;
  setHeadingText(document.querySelector("#filter-editor-title"), filter?.name || "Новое правило");
  document.querySelector("#filter-name").value = filter?.name || "";
  document.querySelector("#filter-operator").value = filter?.rule.operator || "and";
  syncFilterOperatorButtons();
  const list = document.querySelector("#filter-condition-list");
  list.innerHTML = "";
  const items = filter?.rule.items || [];
  if (items.length) items.forEach(item => addFilterItem(item, list));
  else addFilterCondition();
  document.querySelector("#delete-filter").hidden = !filter;
  document.querySelector("#copy-filter").hidden = !filter;
  document.querySelector("#filter-error").hidden = true;
  // Предпросмотр запускает сама загрузка ответов условия: до неё правило
  // собрать нельзя.
  document.querySelector("#filter-preview").innerHTML = '<p class="muted">Считаем…</p>';
  renderTable();
}

function closeFilter() {
  clearTimeout(filterPreviewTimer);
  filterEditor.hidden = true;
  currentFilterId = null;
  renderTable();
}

function addFilterItem(item, container) {
  if (item.kind === "group") addFilterGroup(item, container);
  else addFilterCondition(item, container);
}

function addFilterCondition(condition = {}, container = document.querySelector("#filter-condition-list")) {
  const element = document.createElement("div");
  element.className = "filter-condition";
  const sourceValue = condition.source ? `${condition.source.kind}:${condition.source.ref}` : "";
  element.innerHTML = `<select class="filter-source" aria-label="Вопрос">${filterSourceOptions(sourceValue)}</select><div class="filter-condition-details"><select class="filter-operation" aria-label="Условие"></select><div class="filter-values"></div></div><button type="button" data-remove-filter-condition title="Удалить условие" aria-label="Удалить условие">×</button>`;
  container.append(element);
  refreshFilterJoins(container);
  void loadFilterConditionSource(element, condition);
}

function addFilterGroup(group = {}, container = document.querySelector("#filter-condition-list")) {
  const element = document.createElement("div");
  element.className = "filter-group";
  element.innerHTML = `<div class="filter-group-head"><label><span>В этой группе</span><select class="filter-group-operator" aria-label="Как должны выполняться условия группы"><option value="and" ${(group.operator || "and") === "and" ? "selected" : ""}>выполнены все условия</option><option value="or" ${group.operator === "or" ? "selected" : ""}>выполнено хотя бы одно</option></select></label><button type="button" data-remove-filter-group title="Удалить вариант" aria-label="Удалить вариант">×</button></div><div class="filter-group-items"></div><button type="button" class="secondary compact-button" data-add-group-condition>+ Добавить ещё один вариант</button>`;
  container.append(element);
  const nested = element.querySelector(".filter-group-items");
  const items = group.items || [];
  if (items.length) items.forEach(item => addFilterCondition(item, nested));
  else addFilterCondition({}, nested);
  refreshFilterJoins(container);
}

// Варианты ответа и допустимые операции приходят с сервера вместе с частотами
// (`GET …/filters/source-options`): код SPSS в основном пути больше не
// вводится и остаётся подсказкой рядом с подписью.
const filterSourceOptionsCache = new Map();

const filterOperatorLabels = {
  in: "Один из ответов", not_in: "Кроме ответов", between: "Между", gt: "Больше", lt: "Меньше",
  filled: "Ответ есть", missing: "Пропуск",
  selected_any: "Выбран хотя бы один", selected_all: "Выбраны все", selected_none: "Не выбран ни один",
};

function filterSourceOptionsFor(source) {
  const key = `${currentProject.id}:${currentProject.configuration.revision}:${source.kind}:${source.ref}`;
  if (!filterSourceOptionsCache.has(key)) {
    const params = new URLSearchParams({ kind: source.kind, ref: source.ref });
    const request = api(`/api/projects/${currentProject.id}/filters/source-options?${params}`);
    request.catch(() => filterSourceOptionsCache.delete(key));
    filterSourceOptionsCache.set(key, request);
  }
  return filterSourceOptionsCache.get(key);
}

async function loadFilterConditionSource(element, draft) {
  const sourceSelect = element.querySelector(".filter-source");
  const sourceValue = sourceSelect.value;
  const operation = element.querySelector(".filter-operation");
  const box = element.querySelector(".filter-values");
  element.filterOptions = null;
  operation.innerHTML = "";
  if (!sourceValue) {
    box.innerHTML = '<p class="muted">Нет вопросов, по которым можно отбирать.</p>';
    return;
  }
  box.innerHTML = '<p class="muted">Загружаем ответы…</p>';
  try {
    const options = await filterSourceOptionsFor(parseBannerSource(sourceValue));
    if (sourceSelect.value !== sourceValue || !element.isConnected) return;
    element.filterOptions = options;
    // Сохранённые раньше «равно» и «не равно» — частный случай списка.
    const legacy = { eq: "in", ne: "not_in", selected: "selected_any" };
    const wanted = legacy[draft.operator] || draft.operator;
    const selected = options.operators.includes(wanted) ? wanted : options.operators[0];
    operation.innerHTML = options.operators
      .map(value => `<option value="${value}" ${value === selected ? "selected" : ""}>${filterOperatorLabels[value]}</option>`)
      .join("");
    renderFilterConditionValues(element, draft);
  } catch (error) {
    if (sourceSelect.value !== sourceValue) return;
    box.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
  scheduleFilterPreview();
}

function renderFilterConditionValues(element, draft) {
  const options = element.filterOptions;
  if (!options) return;
  const box = element.querySelector(".filter-values");
  const operator = element.querySelector(".filter-operation").value;
  if (operator === "filled" || operator === "missing") {
    const count = operator === "missing" ? options.missing : options.total - options.missing;
    box.innerHTML = `<p class="filter-hint">${filterOperatorLabels[operator]} у <b>${count.toLocaleString("ru-RU")}</b> из ${options.total.toLocaleString("ru-RU")}</p>`;
    return;
  }
  if (["between", "gt", "lt"].includes(operator)) {
    const bound = value => (value == null ? "" : escapeAttribute(value));
    const lower = operator === "lt" ? "" : `<label><span>${operator === "gt" ? "Больше" : "От"}</span><input class="filter-lower" type="number" step="any" value="${bound(draft.lower)}" /></label>`;
    const upper = operator === "gt" ? "" : `<label><span>${operator === "lt" ? "Меньше" : "До"}</span><input class="filter-upper" type="number" step="any" value="${bound(draft.upper)}" /></label>`;
    const range = options.minimum == null ? "" : `<p class="filter-hint">В данных от ${escapeHtml(options.minimum)} до ${escapeHtml(options.maximum)}</p>`;
    box.innerHTML = `<div class="filter-range">${lower}${upper}</div>${range}`;
    return;
  }
  const chosen = draft.values || [];
  const search = options.options.length > 8
    ? '<input class="filter-option-search" type="search" placeholder="Найти ответ" aria-label="Найти ответ" />'
    : "";
  const items = options.options.map(option => {
    const code = option.code != null && option.code !== option.label ? `<code>${escapeHtml(option.code)}</code>` : "";
    return `<label class="checkbox filter-option" data-search="${escapeAttribute(`${option.label} ${option.code ?? ""}`.toLowerCase())}"><input type="checkbox" data-filter-value="${escapeAttribute(JSON.stringify(option.value))}" ${containsComparable(chosen, option.value) ? "checked" : ""} /><span>${escapeHtml(option.label)}</span>${code}<em>${option.count.toLocaleString("ru-RU")}</em></label>`;
  }).join("");
  const empty = options.options.length ? "" : '<p class="muted">В данных нет ответов.</p>';
  const truncated = options.truncated ? '<p class="filter-hint">Показаны первые 200 ответов — остальные отбирайте диапазоном.</p>' : "";
  box.innerHTML = `${search}<div class="filter-options scroll">${items}</div>${empty}${truncated}`;
}

function filterConditionDraft(element) {
  const bound = selector => {
    const input = element.querySelector(selector);
    return input && input.value !== "" ? Number(input.value) : null;
  };
  return {
    operator: element.querySelector(".filter-operation").value,
    values: [...element.querySelectorAll("[data-filter-value]:checked")].map(input => JSON.parse(input.dataset.filterValue)),
    lower: bound(".filter-lower"),
    upper: bound(".filter-upper"),
  };
}

function refreshFilterJoins(container) {
  if (!container) return;
  container.querySelectorAll(":scope > .filter-join").forEach(join => join.remove());
  const items = [...container.children].filter(item => item.matches(".filter-condition, .filter-group"));
  const group = container.closest(".filter-group");
  const operator = group?.querySelector(".filter-group-operator")?.value || document.querySelector("#filter-operator").value;
  items.slice(1).forEach(item => {
    const join = document.createElement("button");
    join.type = "button";
    join.className = "filter-join";
    join.dataset.filterJoin = group ? "group" : "root";
    join.textContent = operator === "or" ? "ИЛИ" : "И";
    join.title = `Нажмите, чтобы заменить на ${operator === "or" ? "И" : "ИЛИ"}`;
    item.before(join);
  });
}

function filterSourceOptions(selectedValue) {
  const option = (value, text) => `<option value="${escapeAttribute(value)}" ${selectedValue === value ? "selected" : ""}>${escapeHtml(text)}</option>`;
  const usable = configuredQuestions()
    .filter(item => ["single_choice", "scale", "numeric", "multiple_choice_dichotomy", "multiple_choice_categorical"].includes(item.question_type));
  const values = [
    ...usable.map(item => `question:${item.code}`),
    ...configuredRecodings().map(item => `recoding:${item.id}`),
  ];
  // Источник сохранённого правила, который больше нельзя выбрать, остаётся
  // в списке: иначе select молча подставил бы первый вопрос.
  const orphan = selectedValue && !values.includes(selectedValue)
    ? option(selectedValue, bannerSourceLabel(parseBannerSource(selectedValue)))
    : "";
  const questions = usable.map(item => option(`question:${item.code}`, `${item.code} — ${item.label}`)).join("");
  const recodings = configuredRecodings().map(item => option(`recoding:${item.id}`, `${item.code} — ${item.name}`)).join("");
  return `${orphan}<optgroup label="Вопросы">${questions}</optgroup>${recodings ? `<optgroup label="Группировки">${recodings}</optgroup>` : ""}`;
}

function collectFilterRule() {
  const container = document.querySelector("#filter-condition-list");
  const elements = [...container.children].filter(element => element.matches(".filter-condition, .filter-group"));
  if (!elements.length) throw new Error("Добавьте хотя бы одно условие.");
  return {
    kind: "group",
    operator: document.querySelector("#filter-operator").value,
    items: elements.map(collectFilterItem),
  };
}

function collectFilterItem(element) {
  if (element.classList.contains("filter-group")) {
    const nested = [...element.querySelector(".filter-group-items").children].filter(item => item.classList.contains("filter-condition"));
    if (!nested.length) throw new Error("Добавьте условие во вложенную группу.");
    return { kind: "group", operator: element.querySelector(".filter-group-operator").value, items: nested.map(collectFilterItem) };
  }
  const source = parseBannerSource(element.querySelector(".filter-source").value);
  if (!element.filterOptions) throw new Error("Дождитесь, пока загрузятся ответы условия.");
  const draft = filterConditionDraft(element);
  const condition = { kind: "condition", source, operator: draft.operator, values: [] };
  if (["in", "not_in", "selected_any", "selected_all", "selected_none"].includes(draft.operator)) condition.values = draft.values;
  if (["gt", "between"].includes(draft.operator)) condition.lower = draft.lower;
  if (["lt", "between"].includes(draft.operator)) condition.upper = draft.upper;
  return condition;
}

function countFilterConditions(group) {
  return group.items.reduce((total, item) => total + (item.kind === "group" ? countFilterConditions(item) : 1), 0);
}

function syncFilterOperatorButtons() {
  refreshFilterJoins(document.querySelector("#filter-condition-list"));
}

function scheduleFilterPreview() {
  clearTimeout(filterPreviewTimer);
  if (filterEditor.hidden || !currentProject) return;
  filterPreviewTimer = setTimeout(() => { void loadFilterPreview(); }, 350);
}

async function loadFilterPreview() {
  if (!currentProject) return;
  clearTimeout(filterPreviewTimer);
  filterPreviewTimer = null;
  const container = document.querySelector("#filter-preview");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const payload = { name: document.querySelector("#filter-name").value.trim() || "Предпросмотр", rule: collectFilterRule() };
    const preview = await api(`/api/projects/${currentProject.id}/filters/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const warning = preview.empty
      ? (preview.emptied_after
        ? `Пустая база: выборка обнулилась на условии «${preview.emptied_after}». Использовать её нельзя.`
        : "Пустая база — использовать её нельзя.")
      : preview.small_base ? "Малая база: результаты будут отмечены серым." : "";
    // Шаги верхнего уровня накопительные: сколько осталось после условия
    // вместе со всеми предыдущими, а не сколько подходит под него одно.
    const running = (preview.steps || []).filter(step => step.running != null);
    const steps = running.length > 1
      ? `<div class="filter-steps">${running.map(step => `<div><span title="${escapeAttribute(step.description)}">${escapeHtml(step.description)}</span><strong>${step.running.toLocaleString("ru-RU")}</strong></div>`).join("")}</div>`
      : "";
    const rule = `<p class="filter-rule-text"><span>Правило</span>${escapeHtml(preview.description)}</p>`;
    container.innerHTML = `<div class="filter-result"><strong>${preview.selected.toLocaleString("ru-RU")}</strong><span>из ${preview.total.toLocaleString("ru-RU")} · ${formatPercent(preview.share)}</span></div>${rule}${steps}${warning ? `<p class="inline-warnings">${escapeHtml(warning)}</p>` : ""}`;
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

async function deleteFilter() {
  if (!currentFilterId || !confirm("Удалить это правило?")) return;
  const errorBox = document.querySelector("#filter-error");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/filters/${currentFilterId}`, { method: "DELETE" });
    closeFilter();
    renderProject();
    showToast("Правило удалено");
  } catch (error) {
    showError(errorBox, error);
  }
}

async function copyFilter() {
  if (!currentProject || !currentFilterId) return;
  const errorBox = document.querySelector("#filter-error");
  errorBox.hidden = true;
  const button = document.querySelector("#copy-filter");
  try {
    const payload = {
      name: `${document.querySelector("#filter-name").value.trim()} — копия`,
      rule: collectFilterRule(),
    };
    setBusy(button, true, "Копируем…");
    currentProject = await api(`/api/projects/${currentProject.id}/filters`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    currentFilterId = configuredFilters().at(-1)?.id;
    markInspectorClean(filterEditor);
    renderProject();
    openFilter(currentFilterId);
    showToast("Копия правила создана");
  } catch (error) {
    showError(errorBox, error);
  } finally {
    setBusy(button, false, "Копия");
  }
}
