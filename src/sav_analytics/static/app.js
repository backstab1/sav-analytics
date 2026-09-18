const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file");
const fileTitle = document.querySelector("#file-title");
const fileCaption = document.querySelector("#file-caption");
const errorBox = document.querySelector("#form-error");
const submit = document.querySelector("#submit");
const dropZone = document.querySelector("#drop-zone");
const editor = document.querySelector("#question-editor");
const recodeEditor = document.querySelector("#recode-editor");
const formulaEditor = document.querySelector("#formula-editor");
const bannerEditor = document.querySelector("#banner-editor");
const filterEditor = document.querySelector("#filter-editor");
const weightEditor = document.querySelector("#weight-editor");
const reportSettingsForm = document.querySelector("#report-settings-form");
const notApplicableEditor = document.querySelector("#not-applicable-editor");
let currentProject = null;
let currentQuestionCode = null;
let currentRecodingId = null;
let currentFormulaId = null;
let currentBannerId = null;
let currentFilterId = null;
let currentWeightId = null;
let currentView = "data";
let structureMode = "questions";
let structureSearch = "";
let structureStatusFilter = null;
let bannerFormDirty = false;
const recodePreviewCache = new Map();
const filterPreviewCache = new Map();
const filterPreviewRequests = new Map();
let filterCardHydration = null;
let filterPreviewTimer = null;

const defaultReportSettings = Object.freeze({
  compare_to_total: false,
  compare_target: "rest",
  compare_pairwise: false,
  confidence_level: 0.95,
  bonferroni: false,
  show_p_values: false,
  minimum_base: 30,
  weight_variable: null,
  calculated_weight_id: null,
  wave_comparison: "none",
  wave_control_value: null,
  scale_metrics: ["distribution", "mean", "top2", "bottom2"],
  numeric_metrics: ["mean", "median", "min", "max", "std", "stderr"],
  percent_decimals: 0,
  mean_decimals: 1,
  scale_box: 2,
  show_counts: false,
  row_percents: false,
  table_percents: false,
  show_charts: false,
  secondary_confidence_level: null,
  overall_tests: false,
  correlations: false,
  counts_sheet: false,
});

const scaleMetricOptions = [
  { value: "distribution", label: "Распределение" },
  { value: "mean", label: "Среднее" },
  { value: "top2", label: "Top-2" },
  { value: "bottom2", label: "Bottom-2" },
];
// Ключи `top2` и `bottom2` хранятся как есть, а подпись следует настройке
// «крайних кодов»: для семибалльной шкалы привычен Top-3.
function scaleMetricLabel(option, size) {
  if (option.value === "top2") return `Top-${size}`;
  if (option.value === "bottom2") return `Bottom-${size}`;
  return option.label;
}

const numericMetricOptions = [
  { value: "mean", label: "Среднее" },
  { value: "median", label: "Медиана" },
  { value: "min", label: "Мин." },
  { value: "max", label: "Макс." },
  { value: "std", label: "SD" },
  { value: "stderr", label: "SE" },
];

// Набор вывода — не сохраняемое поле, а совпадение отметок с образцом:
// у настройки одно место хранения, и «свой» набор не расходится с «клиентским»,
// из которого его собрали.
const outputProfiles = {
  standard: {
    label: "Стандарт",
    title: "Всё, что книга выводила до появления настроек",
    settings: { scale_metrics: ["distribution", "mean", "top2", "bottom2"], numeric_metrics: ["mean", "median", "min", "max", "std", "stderr"], percent_decimals: 0, mean_decimals: 1, show_p_values: false },
  },
  client: {
    label: "Клиенту",
    title: "Распределение, среднее и Top-2; для числовых — среднее и медиана",
    settings: { scale_metrics: ["distribution", "mean", "top2"], numeric_metrics: ["mean", "median"], percent_decimals: 0, mean_decimals: 1, show_p_values: false },
  },
  quick: {
    label: "Быстрый",
    title: "Без распределения шкал: среднее, Top-2 и Bottom-2",
    settings: { scale_metrics: ["mean", "top2", "bottom2"], numeric_metrics: ["mean"], percent_decimals: 0, mean_decimals: 1, show_p_values: false },
  },
  audit: {
    label: "Аудит",
    title: "Все показатели, лишний знак и p-value в примечаниях",
    settings: { scale_metrics: ["distribution", "mean", "top2", "bottom2"], numeric_metrics: ["mean", "median", "min", "max", "std", "stderr"], percent_decimals: 1, mean_decimals: 2, show_p_values: true },
  },
};

function currentOutputProfile(settings) {
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  return Object.keys(outputProfiles).find(name => {
    const profile = outputProfiles[name].settings;
    return Object.keys(profile).every(key => same(settings[key], profile[key]));
  }) || "custom";
}

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

const typeIcons = {
  single_choice:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.6"/><circle cx="10" cy="10" r="3.2" fill="currentColor"/></svg>',
  multiple_choice_dichotomy:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="4" y="4" width="12" height="12" rx="3" stroke="currentColor" stroke-width="1.6"/><path d="M7.2 10l2 2 3.8-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  multiple_choice_categorical:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="4" y="4" width="12" height="12" rx="3" stroke="currentColor" stroke-width="1.6"/><path d="M7.2 10l2 2 3.8-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  scale:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="3" y="9" width="14" height="2" rx="1" stroke="currentColor" stroke-width="1.5"/><circle cx="12.5" cy="10" r="3" fill="currentColor"/></svg>',
  numeric:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M6 14V8M10 14V5M14 14V10" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>',
  ranking:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 6h9M4 10h12M4 14h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M16 5v4M14.5 7.5L16 9l1.5-1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  matrix:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="3" y="3" width="14" height="14" rx="3" stroke="currentColor" stroke-width="1.6"/><path d="M3 7.5h14M3 12h14M7.5 3v14M12 3v14" stroke="currentColor" stroke-width="1" opacity=".45"/></svg>',
  open_text:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M5 6h10M5 10h7M5 14h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  technical:
    '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="6.5" stroke="currentColor" stroke-width="1.5"/><path d="M10 7v3.2l2 1.8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
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
    showToast("Проект создан");
  } catch (error) {
    showError(errorBox, error);
  } finally {
    setBusy(submit, false, "Создать проект");
  }
});

document.querySelector("#new-project").addEventListener("click", () => {
  if (!confirmDiscard(openInspectorPanel())) return;
  currentProject = null;
  currentQuestionCode = null;
  currentRecodingId = null;
  currentBannerId = null;
  currentFilterId = null;
  currentWeightId = null;
  showInspector(null);
  closeSheet();
  document.querySelector("#workspace").hidden = true;
  document.querySelector("#start").hidden = false;
  window.Shell.setProjectOpen(false);
  writeRoute();
  window.scrollTo(0, 0);
  form.reset();
  fileTitle.textContent = "Перетащите SAV, CSV или XLSX сюда";
  fileCaption.textContent = "или нажмите, чтобы выбрать файл";
  loadProjects();
});

// Стрелка, а не ссылка: loadProjects объявлена в library.js, после app.js.
document.querySelector("#refresh-projects").addEventListener("click", () => loadProjects());
// Одна и та же процедура на два входа: меню выгрузки и полоса запуска
// раздела «Отчёт». Вид артефакта берётся из data-report-kind.
document.querySelectorAll("#download-report, #download-statistics, #launch-report, #launch-statistics")
  .forEach(link => {
    link.dataset.defaultLabel = link.textContent;
    link.addEventListener("click", downloadPreparedReport);
  });
document.querySelector("#close-editor").addEventListener("click", () => {
  if (confirmDiscard(editor)) closeQuestionEditor();
});
document.querySelector("#refresh-preview").addEventListener("click", loadPreview);
document.querySelector("#refresh-structure").addEventListener("click", refreshStructure);
document.querySelector("#question-type").addEventListener("change", () => {
  const question = findQuestion(currentQuestionCode);
  if (question) renderSpecialAnswers({
    ...question,
    question_type: document.querySelector("#question-type").value,
  });
  if (question) renderSpecialMetric({
    ...question,
    question_type: document.querySelector("#question-type").value,
  });
});
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
document.querySelector("#close-banner-editor").addEventListener("click", () => closeBanner());
document.querySelector("#add-banner-block").addEventListener("click", () => {
  addBannerBlock();
  setBannerFormDirty(true);
});
document.querySelector("#delete-banner").addEventListener("click", (...args) => deleteBanner(...args));
document.querySelector("#refresh-banner-preview").addEventListener("click", (...args) => loadBannerPreview(...args));
document.querySelector("#close-filter-editor").addEventListener("click", () => {
  if (confirmDiscard(filterEditor)) closeFilter();
});
document.querySelector("#add-filter-condition").addEventListener("click", () => {
  addFilterCondition();
  scheduleFilterPreview();
});
document.querySelector("#delete-filter").addEventListener("click", (...args) => deleteFilter(...args));
document.querySelector("#copy-filter").addEventListener("click", (...args) => copyFilter(...args));
document.querySelector("#close-weight-editor").addEventListener("click", () => {
  if (confirmDiscard(weightEditor)) closeWeight();
});
document.querySelector("#add-weight-dimension").addEventListener("click", () => addWeightDimension());
document.querySelector("#delete-weight").addEventListener("click", (...args) => deleteWeight(...args));
document.querySelector("#refresh-weight-preview").addEventListener("click", (...args) => loadWeightPreview(...args));
document.querySelector("#weight-trimming").addEventListener("change", (...args) => renderWeightTrimming(...args));
document.querySelector("#report-weight").addEventListener("change", loadReportWeightDiagnostics);
document.querySelector("#report-weight-declare-button").addEventListener("click", declareSelectedWeight);
document.querySelector("#weight-dimension-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-weight-dimension]");
  if (button) button.closest(".weight-dimension").remove();
});
document.querySelector("#weight-dimension-list").addEventListener("change", event => {
  if (event.target.matches(".weight-dimension-source")) {
    renderWeightTargets(event.target.closest(".weight-dimension"));
  }
});
document.querySelector("#weight-dimension-list").addEventListener("input", event => {
  if (event.target.matches(".weight-target input")) {
    updateWeightDimensionStatus(event.target.closest(".weight-dimension"));
  }
});
document.querySelectorAll("[data-structure-mode]").forEach(button => button.addEventListener("click", () => {
  structureMode = button.dataset.structureMode;
  document.querySelectorAll("[data-structure-mode]").forEach(item => item.classList.toggle("active", item === button));
  editor.hidden = true;
  currentQuestionCode = null;
  renderTable();
}));

const structureSearchInput = document.querySelector("#structure-search");
structureSearchInput.addEventListener("input", () => {
  structureSearch = structureSearchInput.value.trim();
  document.querySelector("#structure-search-clear").hidden = !structureSearchInput.value;
  renderTable();
});
structureSearchInput.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !structureSearchInput.value) return;
  event.stopPropagation();
  resetStructureSearch();
});
document.querySelector("#structure-search-clear").addEventListener("click", () => {
  resetStructureSearch();
  structureSearchInput.focus();
});

function resetStructureSearch({ render = true } = {}) {
  structureSearch = "";
  structureStatusFilter = null;
  structureSearchInput.value = "";
  document.querySelector("#structure-search-clear").hidden = true;
  if (render && currentProject) renderTable();
}

// Карточки сводки работают как фильтр таблицы: повторный клик снимает его.
document.querySelector("#summary").addEventListener("click", event => {
  const card = event.target.closest("button[data-status-filter]");
  if (!card || !currentProject) return;
  // Пустой ключ — чип «Все»: он снимает фильтр, а не ставит свой.
  const key = card.dataset.statusFilter || null;
  structureStatusFilter = structureStatusFilter === key ? null : key;
  if (structureStatusFilter) {
    currentView = "data";
    structureMode = "questions";
    renderSectionHead("data");
    document.querySelectorAll(".tabs button[data-view]").forEach(button => {
      button.classList.toggle("active", button.dataset.view === "data");
    });
    document.querySelectorAll("[data-structure-mode]").forEach(button => {
      button.classList.toggle("active", button.dataset.structureMode === "questions");
    });
    syncSectionChrome("data");
  }
  renderSummary(currentProject.inspection, configuredQuestions());
  renderTable();
});

function matchesStatusFilter(question) {
  if (!structureStatusFilter) return true;
  return questionStatus(question) === structureStatusFilter;
}

function matchesStructureSearch(...parts) {
  if (!structureSearch) return true;
  const haystack = parts.filter(Boolean).join(" ").toLowerCase();
  return structureSearch.toLowerCase().split(/\s+/).filter(Boolean)
    .every(token => haystack.includes(token));
}

function structureFiltered() {
  return Boolean(structureSearch || structureStatusFilter);
}

// Счётчик показывается всегда, а не только под фильтром: «96 из 96» —
// это ещё и размер списка, за которым иначе надо лезть в сводку.
function updateStructureSearchCount(shown, total) {
  const counter = document.querySelector("#structure-search-count");
  counter.textContent = `${shown} из ${total}`;
}

/* ================================================================
   Шапка холста

   Раздел называет себя сам: заголовок и подпись стоят над окном
   списка, а не ужаты в ряд .toolbar внутри него. Действие раздела
   («Пропуски по анкете») относится к структуре, поэтому в остальных
   разделах его нет.
   ================================================================ */
const sectionHeads = {
  data: {
    eyebrow: "Данные",
    title: "Структура массива",
    lead: "Типы и названия вопросов определены автоматически. Проверьте отмеченное.",
  },
  reports: {
    eyebrow: "Отчёты",
    title: "Книга Excel",
    lead: "Слева — что войдёт в книгу, справа — как считаются различия.",
  },
};

function renderSectionHead(view) {
  const head = sectionHeads[view] || sectionHeads.data;
  document.querySelector("#section-eyebrow").textContent = head.eyebrow;
  document.querySelector("#section-title").textContent = head.title;
  document.querySelector("#section-lead").textContent = head.lead;
  document.querySelector("#find-not-applicable").hidden = view !== "data";
  // Сводка — фильтр таблицы вопросов, в других разделах ей нечего фильтровать.
  document.querySelector("#summary").hidden = view !== "data";
}

// Заголовки и ячейки обрезаются многоточием, поэтому дублируем текст в подсказку.
function setHeadingText(element, text) {
  element.textContent = text;
  element.title = text;
}

function closeQuestionEditor() {
  editor.hidden = true;
  currentQuestionCode = null;
  if (currentProject) renderTable();
}

/* Ниже этой ширины инспектор перестаёт помещаться третьей колонкой и
   выезжает поверх списка. Порог один на все инспекторы: раньше у
   баннера был свой, потому что он был шире остальных. */
const slideOverQuery = window.matchMedia("(max-width: 1080px)");

function openEditors() {
  return [
    [editor, closeQuestionEditor],
    [recodeEditor, closeRecoding],
    [bannerEditor, closeBanner],
    [filterEditor, closeFilter],
    [weightEditor, closeWeight],
  ].filter(([element]) => !element.hidden);
}

function slideOverOpen() {
  return slideOverQuery.matches && openEditors().length > 0;
}

function closeSlideOver() {
  if (!confirmDiscard(openInspectorPanel())) return;
  if (!slideOverOpen()) return;
  openEditors().forEach(([, close]) => close());
}

/* ================================================================
   Листы: настройка отчёта и пропуски по анкете
   ================================================================ */
const sheetVeil = document.querySelector("#sheet-veil");

function openSheet(sheet) {
  sheetVeil.querySelectorAll(".sheet").forEach(item => { item.hidden = item !== sheet; });
  sheetVeil.hidden = false;
}

function closeSheet() {
  if (sheetVeil.hidden) return;
  sheetVeil.hidden = true;
  sheetVeil.querySelectorAll(".sheet").forEach(item => { item.hidden = true; });
}

function openReportSettingsSheet() {
  if (!currentProject) return;
  renderReportSettings();
  openSheet(reportSettingsForm);
}

sheetVeil.addEventListener("click", event => {
  if (event.target === sheetVeil || event.target.closest("[data-close-sheet]")) closeSheet();
});

document.querySelector("#editor-backdrop").addEventListener("click", closeSlideOver);
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  if (!pickerElement.hidden) {
    closePicker();
    return;
  }
  if (!sheetVeil.hidden) {
    closeSheet();
    return;
  }
  closeSlideOver();
});
document.querySelector("#banner-block-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-banner-block]");
  if (button) {
    button.closest(".banner-block").remove();
    setBannerFormDirty(true);
  }
});

const bannerForm = document.querySelector("#banner-form");
bannerForm.addEventListener("input", () => setBannerFormDirty(true));
bannerForm.addEventListener("change", () => setBannerFormDirty(true));
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
// Редактор условий один на фильтр и на категории логической переменной:
// те же обработчики навешиваются на оба списка.
const conditionEditorRoots = [
  document.querySelector("#filter-condition-list"),
  document.querySelector("#condition-category-list"),
];
conditionEditorRoots.forEach(root => root.addEventListener("click", event => {
  const join = event.target.closest("button[data-filter-join]");
  if (join) {
    const group = join.closest(".filter-group");
    const operator = group?.querySelector(".filter-group-operator") || document.querySelector("#filter-operator");
    operator.value = operator.value === "and" ? "or" : "and";
    refreshFilterJoins(group?.querySelector(".filter-group-items") || document.querySelector("#filter-condition-list"));
    scheduleFilterPreview();
    return;
  }
  const removeCondition = event.target.closest("button[data-remove-filter-condition]");
  if (removeCondition) {
    const container = removeCondition.closest(".filter-condition").parentElement;
    removeCondition.closest(".filter-condition").remove();
    refreshFilterJoins(container);
    scheduleFilterPreview();
    return;
  }
  const removeGroup = event.target.closest("button[data-remove-filter-group]");
  if (removeGroup) {
    const container = removeGroup.closest(".filter-group").parentElement;
    removeGroup.closest(".filter-group").remove();
    refreshFilterJoins(container);
    scheduleFilterPreview();
    return;
  }
  const addNested = event.target.closest("button[data-add-group-condition]");
  if (addNested) {
    addFilterCondition({}, addNested.closest(".filter-group").querySelector(".filter-group-items"));
    scheduleFilterPreview();
  }
}));
conditionEditorRoots.forEach(root => root.addEventListener("change", event => {
  const condition = event.target.closest(".filter-condition");
  if (event.target.matches(".filter-source")) void loadFilterConditionSource(condition, {});
  if (event.target.matches(".filter-operation")) renderFilterConditionValues(condition, filterConditionDraft(condition));
  if (event.target.matches(".filter-group-operator")) {
    refreshFilterJoins(event.target.closest(".filter-group").querySelector(".filter-group-items"));
  }
}));
conditionEditorRoots.forEach(root => root.addEventListener("input", event => {
  if (!event.target.matches(".filter-option-search")) return;
  const query = event.target.value.trim().toLowerCase();
  event.target.closest(".filter-values").querySelectorAll(".filter-option").forEach(option => {
    option.hidden = Boolean(query) && !option.dataset.search.includes(query);
  });
}));
document.querySelector("#filter-form").addEventListener("input", (...args) => scheduleFilterPreview(...args));
document.querySelector("#filter-form").addEventListener("change", (...args) => scheduleFilterPreview(...args));

/* ================================================================
   ПЕРЕКЛЮЧЕНИЕ РАЗДЕЛОВ

   Разделов осталось три: структура, перекодировки и отчёт. Баннер, база,
   вес и статистика перестали быть разделами — это свойства одной книги,
   и живут строками раздела «Отчёт» (renderReportBlocks). Их списки
   свёрнуты в поповер выбора, поэтому собственных экранов у них нет.
   Контракт `.tabs button[data-view]` сохранён.
   ================================================================ */
// Колонка редактора в панели одна, поэтому и открытый инспектор один:
// показать любой — значит закрыть остальные.
/* Несохранённое в редакторах. Ввод в форме отмечает её изменённой;
   открытие и успешное сохранение снимают отметку. Перед тем как изменённый
   редактор закроется — крестиком, Escape, открытием другого, сменой раздела,
   переходом к новому проекту или уходом со страницы — аналитика спрашивают.
   У баннера своя отметка с видимым предупреждением, её читают так же. */
const INSPECTORS = [editor, recodeEditor, formulaEditor, bannerEditor, filterEditor, weightEditor];
const dirtyInspectors = new Set();

function openInspectorPanel() {
  return INSPECTORS.find(panel => !panel.hidden) || null;
}

function markInspectorDirty(panel) {
  dirtyInspectors.add(panel);
}

function markInspectorClean(panel) {
  dirtyInspectors.delete(panel);
  if (panel === bannerEditor) setBannerFormDirty(false);
}

function inspectorIsDirty(panel) {
  return panel === bannerEditor ? bannerFormDirty : dirtyInspectors.has(panel);
}

// true — можно продолжать: редактор чистый или аналитик согласился потерять ввод.
function confirmDiscard(panel) {
  if (!panel || panel.hidden || !inspectorIsDirty(panel)) return true;
  if (!confirm("Есть несохранённые изменения. Закрыть редактор без сохранения?")) return false;
  markInspectorClean(panel);
  return true;
}

[[editor, "#question-form"], [recodeEditor, "#recode-form"], [formulaEditor, "#formula-form"], [filterEditor, "#filter-form"], [weightEditor, "#weight-form"]]
  .forEach(([panel, selector]) => {
    const form = document.querySelector(selector);
    // Только ввод аналитика: значения, которые подставляет сам экран, событий не шлют.
    ["input", "change"].forEach(type => form.addEventListener(type, event => {
      if (event.isTrusted) markInspectorDirty(panel);
    }));
  });

window.addEventListener("beforeunload", event => {
  const panel = openInspectorPanel();
  if (panel && inspectorIsDirty(panel)) {
    event.preventDefault();
    event.returnValue = "";
  }
});

function showInspector(panel) {
  INSPECTORS.forEach(item => { item.hidden = item !== panel; });
  if (panel) markInspectorClean(panel);
}

function closeAllInspectors() {
  showInspector(null);
  currentQuestionCode = null;
  currentRecodingId = null;
  currentFormulaId = null;
  currentBannerId = null;
  currentFilterId = null;
  currentWeightId = null;
}

// Хром раздела: подсветка в рельсе, тулбар панели и полоса запуска.
function syncSectionChrome(view) {
  document.querySelectorAll(".tabs button").forEach(item => {
    const active = item.dataset.view === view;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  document.querySelector("#structure-toolbar").hidden = view !== "data";
  document.querySelector("#report-toolbar").hidden = view !== "reports";
  document.querySelector("#report-launch").hidden = view !== "reports";
}

// Разделы проекта по макету v8. «Данные» и «Отчёты» живут на холсте,
// «Таблицы» — бывший конструктор, «Анализ» и «Открытые ответы» ещё не
// построены и показывают только, что в них будет.
const SECTION_VIEWS = ["data", "tables", "analysis", "text", "reports"];
const CANVAS_VIEWS = ["data", "reports"];

function setView(view) {
  if (!SECTION_VIEWS.includes(view)) view = "data";
  if (!confirmDiscard(openInspectorPanel())) {
    // Адрес мог уже смениться кнопкой браузера — возвращаем тот, где остались.
    history.replaceState(null, "", currentRoute());
    return;
  }
  currentView = view;
  closePicker();
  closeAllInspectors();
  const onCanvas = CANVAS_VIEWS.includes(view);
  document.querySelector("#app-shell > .canvas").hidden = !onCanvas;
  document.querySelector("#section-tables").hidden = view !== "tables";
  document.querySelector("#section-analysis").hidden = view !== "analysis";
  document.querySelector("#section-text").hidden = view !== "text";
  document.querySelector("#section-soon").hidden = onCanvas || ["tables", "analysis", "text"].includes(view);
  syncSectionChrome(view);
  if (onCanvas) {
    renderSectionHead(view);
    renderTable();
    if (view === "reports") void loadReportPreflight();
  } else if (view === "tables") {
    window.Shell.activateTables();
  } else if (view === "analysis") {
    renderAnalysisSection();
  } else if (view === "text") {
    void renderTextSection();
  } else {
    renderSoonSection(view);
  }
  writeRoute();
}

const soonSections = {
  analysis: {
    eyebrow: "Анализ · PQ.10–PQ.11",
    title: "Описать, связать, найти драйверы",
    lead: "Раздел строится. Числа здесь появятся, когда их станет считать расчётное ядро: своих формул у экрана не будет.",
    items: [
      "карточка переменной: распределение, среднее и пропуски",
      "связь двух переменных с выбором теста по типам и размером эффекта",
      "контроль ложных открытий по всем карточкам рабочей области",
      "регрессия с весом, важность драйверов и сегменты, сохраняемые переменной",
    ],
    note: () => `Вопросов в отчёте: ${configuredQuestions().filter(item => item.included_in_report).length}. Их и можно будет анализировать.`,
  },
  text: {
    eyebrow: "Открытые ответы · PQ.12",
    title: "Кодификатор и темы",
    lead: "Раздел строится. Ответы разбираются на сервере, без внешних AI-сервисов.",
    items: [
      "кодификатор с иерархией тем",
      "темы по запросам со словоформами",
      "ручная правка отнесения с пометкой, кто её сделал",
      "тема становится переменной и работает в таблицах, фильтрах и баннере",
    ],
    note: () => `Открытых вопросов в проекте: ${configuredQuestions().filter(item => item.question_type === "open_text").length}. Пока они только исключаются из отчёта.`,
  },
};

function renderSoonSection(view) {
  const section = soonSections[view];
  document.querySelector("#soon-eyebrow").textContent = section.eyebrow;
  document.querySelector("#soon-title").textContent = section.title;
  document.querySelector("#soon-lead").textContent = section.lead;
  document.querySelector("#soon-list").innerHTML = section.items.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  document.querySelector("#soon-note").textContent = currentProject ? section.note() : "";
}

document.querySelectorAll(".tabs button[data-view]").forEach(
  button => button.addEventListener("click", () => setView(button.dataset.view))
);

document.querySelector("#table-body").addEventListener("click", event => {
  if (event.target.closest("[data-drag-code]")) return;
  const ownerButton = event.target.closest("button[data-open-question]");
  if (ownerButton) {
    structureMode = "questions";
    document.querySelectorAll("[data-structure-mode]").forEach(button => button.classList.toggle("active", button.dataset.structureMode === "questions"));
    openQuestion(ownerButton.dataset.openQuestion);
    return;
  }
  const recodeRow = event.target.closest("tr[data-recode-id]");
  if (recodeRow) {
    openRecoding(recodeRow.dataset.recodeId);
    return;
  }
  const bannerRow = event.target.closest("tr[data-banner-id]");
  if (bannerRow) {
    openBanner(bannerRow.dataset.bannerId);
    return;
  }
  const filterCard = event.target.closest("[data-filter-id]");
  if (filterCard) {
    openFilter(filterCard.dataset.filterId);
    return;
  }
  const weightRow = event.target.closest("tr[data-weight-id]");
  if (weightRow) {
    openWeight(weightRow.dataset.weightId);
    return;
  }
  const row = event.target.closest("tr[data-code]");
  if (row) openQuestion(row.dataset.code);
});

document.querySelector("#entity-list").addEventListener("click", event => {
  // Строки раздела «Отчёт»: пилюля открывает поповер выбора, соседняя
  // кнопка — редактор, лист настроек или структуру.
  const pickerAnchor = event.target.closest("[data-picker]");
  if (pickerAnchor) {
    openPicker(pickerAnchor.dataset.picker, pickerAnchor);
    return;
  }
  const editButton = event.target.closest("[data-edit]");
  if (editButton) {
    openEntityEditor(editButton.dataset.edit, editButton.dataset.id);
    return;
  }
  const newButton = event.target.closest("[data-new]");
  if (newButton) {
    openEntityEditor(newButton.dataset.new);
    return;
  }
  if (event.target.closest('[data-open-sheet="report-settings"]')) {
    openReportSettingsSheet();
    return;
  }
  const goto = event.target.closest("[data-goto]");
  if (goto) {
    setView(goto.dataset.goto);
    return;
  }
  const stat = event.target.closest("[data-stat]");
  if (stat) {
    const patch = stat.getAttribute("aria-checked") === "true"
      ? null
      : statPatch(stat.dataset.stat, stat.dataset.value);
    if (patch) void applyStatSetting(patch);
    return;
  }
  const recodeCard = event.target.closest("[data-recode-id]");
  if (recodeCard) {
    openRecoding(recodeCard.dataset.recodeId);
    return;
  }
  const bannerCard = event.target.closest("[data-banner-id]");
  if (bannerCard) {
    openBanner(bannerCard.dataset.bannerId);
    return;
  }
  const filterCard = event.target.closest("[data-filter-id]");
  if (filterCard) {
    openFilter(filterCard.dataset.filterId);
    return;
  }
  const weightCard = event.target.closest("[data-weight-id]");
  if (weightCard) openWeight(weightCard.dataset.weightId);
});

// Карточка — не <button> (внутри лежит своя кнопка), поэтому клавиатуру включаем вручную.
document.querySelector("#entity-list").addEventListener("keydown", event => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest('[role="button"]');
  if (!card || card !== event.target) return;
  event.preventDefault();
  card.click();
});

let draggedQuestionCode = null;

document.querySelector("#table-body").addEventListener("dragstart", event => {
  const handle = event.target.closest("[data-drag-code]");
  if (!handle || currentView !== "data" || structureMode !== "questions" || structureFiltered()) return;
  draggedQuestionCode = handle.dataset.dragCode;
  handle.closest("tr[data-code]")?.classList.add("is-dragging");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", draggedQuestionCode);
});

document.querySelector("#table-body").addEventListener("dragover", event => {
  if (!draggedQuestionCode) return;
  const row = event.target.closest("tr[data-code]");
  clearQuestionDropMarkers();
  if (!row || row.dataset.code === draggedQuestionCode) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  const placeAfter = event.clientY > row.getBoundingClientRect().top + row.offsetHeight / 2;
  row.classList.add(placeAfter ? "drop-after" : "drop-before");
});

document.querySelector("#table-body").addEventListener("drop", async event => {
  if (!draggedQuestionCode) return;
  const row = event.target.closest("tr[data-code]");
  const sourceCode = draggedQuestionCode;
  const placeAfter = Boolean(row?.classList.contains("drop-after"));
  clearQuestionDragState();
  if (!row || row.dataset.code === sourceCode) return;
  event.preventDefault();
  await moveQuestionTo(sourceCode, row.dataset.code, placeAfter);
});

document.querySelector("#table-body").addEventListener("dragend", clearQuestionDragState);

function clearQuestionDropMarkers() {
  document.querySelectorAll("#table-body .drop-before, #table-body .drop-after")
    .forEach(row => row.classList.remove("drop-before", "drop-after"));
}

function clearQuestionDragState() {
  document.querySelectorAll("#table-body .is-dragging")
    .forEach(row => row.classList.remove("is-dragging"));
  clearQuestionDropMarkers();
  draggedQuestionCode = null;
}

document.querySelector("#banner-form").addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-banner");
  const bannerError = document.querySelector("#banner-error");
  bannerError.hidden = true;
  let blocks;
  try {
    blocks = collectBannerBlocks();
  } catch (error) {
    showError(bannerError, error);
    return;
  }
  const payload = {
    name: document.querySelector("#banner-name").value.trim(),
    blocks,
  };
  setBusy(saveButton, true, "Сохраняем…");
  try {
    const url = currentBannerId
      ? `/api/projects/${currentProject.id}/banners/${currentBannerId}`
      : `/api/projects/${currentProject.id}/banners`;
    currentProject = await api(url, {
      method: currentBannerId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!currentBannerId) {
      currentBannerId = configuredBanners().at(-1)?.id;
    }
    markInspectorClean(bannerEditor);
    renderProject();
    openBanner(currentBannerId);
    await loadBannerPreview();
    showToast("Баннер сохранён");
  } catch (error) {
    showError(bannerError, error);
  } finally {
    setBusy(saveButton, false, "Сохранить");
  }
});

reportSettingsForm.addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-report-settings");
  const settingsError = document.querySelector("#report-settings-error");
  const weightSelection = document.querySelector("#report-weight").value;
  settingsError.hidden = true;
  setBusy(saveButton, true, "Сохраняем…");
  try {
    await patchReportSettings({
      weight_variable: weightSelection.startsWith("ready:") ? weightSelection.slice(6) : null,
      calculated_weight_id: weightSelection.startsWith("calculated:")
        ? weightSelection.slice(11)
        : null,
    });
    closeSheet();
    showToast("Настройки отчёта сохранены");
  } catch (error) {
    showError(settingsError, error);
  } finally {
    setBusy(saveButton, false, "Применить вес");
  }
});

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
  const trimming = document.querySelector("#weight-trimming").checked;
  const payload = {
    name: document.querySelector("#weight-name").value.trim(),
    dimensions,
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

document.querySelector("#filter-form").addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-filter");
  const filterError = document.querySelector("#filter-error");
  filterError.hidden = true;
  let rule;
  try {
    rule = collectFilterRule();
  } catch (error) {
    showError(filterError, error);
    return;
  }
  const payload = { name: document.querySelector("#filter-name").value.trim(), rule };
  setBusy(saveButton, true, "Сохраняем…");
  try {
    const url = currentFilterId
      ? `/api/projects/${currentProject.id}/filters/${currentFilterId}`
      : `/api/projects/${currentProject.id}/filters`;
    currentProject = await api(url, {
      method: currentFilterId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!currentFilterId) {
      currentFilterId = configuredFilters().find(item => item.name === payload.name)?.id;
    }
    markInspectorClean(filterEditor);
    renderProject();
    openFilter(currentFilterId);
    await loadFilterPreview();
    showToast("Правило сохранено");
  } catch (error) {
    showError(filterError, error);
  } finally {
    setBusy(saveButton, false, "Сохранить");
  }
});

document.querySelector("#recode-form").addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-recoding");
  const recodeError = document.querySelector("#recode-error");
  recodeError.hidden = true;
  let categories;
  const mode = document.querySelector("#recode-mode").value;
  try {
    categories = mode === "ranges"
      ? collectRanges()
      : mode === "conditions" ? collectConditionCategories() : collectCategoryGroups();
  } catch (error) {
    showError(recodeError, error);
    return;
  }
  const payload = {
    mode,
    code: document.querySelector("#recode-code").value.trim(),
    name: document.querySelector("#recode-name").value.trim(),
    source_variable: mode === "conditions" ? undefined : document.querySelector("#recode-source").value,
    categories,
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
    setBusy(saveButton, false, "Сохранить перекодировку");
  }
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
          special_metric: document.querySelector("#question-special-metric").value,
          ...collectSpecialAnswers(),
          ...collectNotApplicable(),
          ...collectNets(),
          ...collectQuestionOutput(),
        }),
      },
    );
    currentProject = await api(
      `/api/projects/${currentProject.id}/questions/${encodeURIComponent(currentQuestionCode)}/base`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filter_id: document.querySelector("#question-base-filter").value || null,
        }),
      },
    );
    renderProject();
    fillEditor(findQuestion(currentQuestionCode));
    await loadPreview();
    markInspectorClean(editor);
    showToast("Настройки вопроса сохранены");
  } catch (error) {
    showError(editorError, error);
  } finally {
    setBusy(saveButton, false, questionSaveLabel(findQuestion(currentQuestionCode)));
  }
});

function showProject(project, view = "data") {
  if (!confirmDiscard(openInspectorPanel())) return;
  // Выбор вопросов принадлежит проекту: в другом проекте тех кодов может не быть.
  selectedQuestionCodes.clear();
  document.querySelector("#bulk-bar").hidden = true;
  currentProject = project;
  currentQuestionCode = null;
  currentRecodingId = null;
  currentBannerId = null;
  currentFilterId = null;
  currentWeightId = null;
  currentView = "data";
  structureMode = "questions";
  resetStructureSearch({ render: false });
  showInspector(null);
  renderSectionHead("data");
  syncSectionChrome("data");
  closeSheet();
  closePicker();
  document.querySelectorAll("[data-structure-mode]").forEach(button => {
    button.classList.toggle("active", button.dataset.structureMode === "questions");
  });
  renderProject();
  document.querySelector("#start").hidden = true;
  document.querySelector("#workspace").hidden = false;
  window.Shell.setProjectOpen(true);
  window.scrollTo(0, 0);
  setView(view);
}

function renderProject() {
  const inspection = currentProject.inspection;
  const questions = configuredQuestions();
  document.querySelector("#project-name").textContent = currentProject.name;
  document.querySelector("#download-source").href = `/api/projects/${currentProject.id}/source`;
  document.querySelector("#download-derived-sav").href = `/api/projects/${currentProject.id}/export.sav`;
  document.querySelector("#download-report").href = `/api/projects/${currentProject.id}/reports/topline.xlsx`;
  document.querySelector("#download-statistics").href = `/api/projects/${currentProject.id}/reports/statistics.txt`;
  renderSummary(inspection, questions);
  renderTable();
  publishVariablesToShell(inspection, questions);
  renderLogicVariablePicker();
}

/* Раздел «Таблицы» живёт в другом файле, но умеет менять конфигурацию —
   сохранить разрез баннером. Состояние проекта держит этот файл, поэтому
   после такой правки он перечитывает проект и заново раздаёт его разделам. */
window.SavApp = {
  async refreshProject() {
    if (!currentProject) return;
    currentProject = await api(`/api/projects/${currentProject.id}`);
    renderProject();
  },
};

// Раздел «Таблицы» берёт список переменных отсюда: своей загрузки у него
// нет, иначе один и тот же проект читался бы дважды. Строками годятся типы,
// которые раскладывает лист книги, колонками — одиночный выбор с подписями
// и группировки.
const TABLE_ROW_TYPES = ["single_choice", "scale", "numeric", "multiple_choice_dichotomy", "multiple_choice_categorical", "matrix"];
const MULTIPLE_TYPES = ["multiple_choice_dichotomy", "multiple_choice_categorical"];

function publishVariablesToShell(inspection, questions) {
  const questionItems = questions
    .filter(question => question.included_in_report)
    .map(question => {
      const labels = inspection.variables
        .find(item => item.name === question.source_variables?.[0])?.value_labels || [];
      return {
        code: question.code,
        label: question.label,
        type: question.question_type,
        source: { kind: "question", ref: question.code },
        canRow: TABLE_ROW_TYPES.includes(question.question_type),
        canCol: (question.question_type === "single_choice" && labels.length > 0)
          || MULTIPLE_TYPES.includes(question.question_type),
      };
    });
  const recodingItems = configuredRecodings().map(recoding => ({
    code: `recoding:${recoding.id}`,
    display: recoding.code,
    label: recoding.name,
    type: "recoding",
    source: { kind: "recoding", ref: recoding.id },
    canRow: false,
    canCol: true,
  }));
  window.Shell.setProjectVariables([...questionItems, ...recodingItems], {
    projectId: currentProject?.id || null,
    filters: configuredFilters().map(item => ({ id: item.id, name: item.name })),
    banners: configuredBanners().map(item => ({ id: item.id, name: item.name })),
  });
}

// Статус приходит с сервера готовым: признак «требует проверки» считается
// в core/review.py, здесь его только читают — так же, как preflight.
function questionStatus(question) {
  if (!question.included_in_report) return "excluded";
  return question.needs_review ? "review" : "ready";
}

const statusLabels = { review: "Проверить", ready: "Готов", excluded: "Исключён" };

// Сводка стоит над окном списка и работает фильтром по статусу. Размер
// массива в неё не входит: он не меняется по ходу работы и потому стоит
// в шапке рядом с названием проекта.
function renderSummary(inspection, questions) {
  const ready = questions.filter(item => questionStatus(item) === "ready");
  const review = questions.filter(item => questionStatus(item) === "review");
  const excluded = questions.filter(item => questionStatus(item) === "excluded");
  document.querySelector("#project-meta").textContent = [
    `${inspection.row_count.toLocaleString("ru-RU")} респондентов`,
    `${questions.length} вопросов`,
    `${inspection.variables.length} столбцов SAV`,
  ].join(" · ");
  document.querySelector("#summary").innerHTML = [
    { value: questions.length, label: "Все", key: null },
    { value: review.length, label: "Проверить", key: "review", tone: "warn" },
    { value: ready.length, label: "Готовы", key: "ready", tone: "ok" },
    { value: excluded.length, label: "Исключены", key: "excluded", tone: "off" },
  ].map(chip => {
    const number = chip.value.toLocaleString("ru-RU");
    const active = chip.key ? structureStatusFilter === chip.key : !structureStatusFilter;
    return `<button type="button" class="stat ${chip.tone || ""} ${active ? "active" : ""}"
      data-status-filter="${chip.key || ""}" aria-pressed="${active}"
      title="${chip.key ? `Показать только: ${chip.label.toLowerCase()}` : "Показать все вопросы"}"
      ><span class="dot" aria-hidden="true"></span>${chip.label}<b>${number}</b></button>`;
  }).join("");
  renderRailCounts(questions);
  // Что уйдёт в Excel — подсказка на кнопке выгрузки. Раньше это была
  // строка под карточками; в один ярус она не помещается, а нужна ровно
  // в момент выгрузки.
  document.querySelector("#export-toggle").title = reportStateSummary();
}

// Числа разделов в рельсе: раньше их вообще не было видно, пока не
// откроешь вкладку.
function renderRailCounts(questions) {
  const counts = { questions: questions.length };
  document.querySelectorAll("#section-nav em[data-count]").forEach(slot => {
    const value = counts[slot.dataset.count];
    slot.textContent = value ? String(value) : "";
  });
}

// Что именно уйдёт в Excel при текущих настройках — одной строкой.
function reportStateSummary() {
  const banner = configuredBanners().find(item => item.id === selectedReportBannerId());
  const filter = configuredFilters().find(item => item.id === selectedReportFilterId());
  const settings = configuredReportSettings();
  return [
    `Баннер: ${banner ? banner.name : "только Total"}`,
    `Вес: ${reportWeightLabel(settings)}`,
    `Общий фильтр: ${filter ? filter.name : "нет"}`,
  ].join("\n");
}

function reportWeightLabel(settings) {
  if (settings.calculated_weight_id) {
    const weight = configuredWeights().find(item => item.id === settings.calculated_weight_id);
    return weight ? weight.name : "рассчитанный";
  }
  if (settings.weight_variable) return settings.weight_variable;
  return "нет";
}

function renderReportSettings() {
  const settings = configuredReportSettings();
  const selectedWeight = settings.calculated_weight_id
    ? `calculated:${settings.calculated_weight_id}`
    : settings.weight_variable ? `ready:${settings.weight_variable}` : "";
  renderReportWeightSelect(selectedWeight);
  document.querySelector("#report-settings-error").hidden = true;
}

// Роль берётся из конфигурации: её меняет аналитик, а `inspection` хранит
// первичное автоопределение и после ручной правки устаревает.
function questionByVariable(name) {
  return configuredQuestions().find(question => (question.source_variables || []).includes(name));
}

function declaredWeightVariables() {
  return currentProject.inspection.variables.filter(
    variable => questionByVariable(variable.name)?.role === "weight"
  );
}

// Кандидаты на объявление весом: числовые переменные, не объявленные весом.
// Составные вопросы не предлагаются — весом может быть одиночная переменная.
function weightCandidates() {
  return currentProject.inspection.variables.filter(variable => {
    if (variable.storage_type !== "numeric") return false;
    const question = questionByVariable(variable.name);
    return question && question.role !== "weight" && question.source_variables.length === 1;
  });
}

// Список весов и разбор выбранного перерисовываются отдельно от остальной
// формы: объявление веса не должно сбрасывать уже введённые настройки отчёта.
function renderReportWeightSelect(selectedWeight) {
  // В списке только объявленные весом переменные: роль — необходимое условие
  // выбора, а не подсказка. Любая другая числовая переменная становится весом
  // отдельным действием, меняющим роль в структуре.
  const declared = declaredWeightVariables();
  const readyOptions = declared
    .map(variable => `<option value="ready:${escapeAttribute(variable.name)}" ${selectedWeight === `ready:${variable.name}` ? "selected" : ""}>Готовый: ${escapeHtml(variable.name)} — ${escapeHtml(variable.label)}</option>`)
    .join("");
  // Проект, сохранённый до появления проверки, мог остаться с непригодным
  // весом. Прятать его нельзя: настройка молча разошлась бы с тем, что видно.
  const stale = selectedWeight.startsWith("ready:") ? selectedWeight.slice(6) : "";
  const staleOption = stale && !declared.some(variable => variable.name === stale)
    ? `<option value="ready:${escapeAttribute(stale)}" selected>Готовый: ${escapeHtml(stale)} — не объявлена весом</option>`
    : "";
  const calculatedOptions = configuredWeights()
    .map(weight => `<option value="calculated:${weight.id}" ${selectedWeight === `calculated:${weight.id}` ? "selected" : ""}>Рассчитанный: ${escapeHtml(weight.name)}</option>`)
    .join("");
  document.querySelector("#report-weight").innerHTML =
    '<option value="">Без веса</option>' + staleOption + readyOptions + calculatedOptions;
  document.querySelector("#report-weight-help").textContent = declared.length
    ? `Объявлено весом переменных: ${declared.length}`
    : "Ни одна переменная не объявлена весом.";
  renderWeightCandidates();
  loadReportWeightDiagnostics();
}

function renderWeightCandidates() {
  const select = document.querySelector("#report-weight-candidate");
  const candidates = weightCandidates();
  select.innerHTML = candidates
    .map(variable => `<option value="${escapeAttribute(variable.name)}">${escapeHtml(variable.name)} — ${escapeHtml(variable.label)}</option>`)
    .join("");
  const declareBlock = document.querySelector("#report-weight-declare");
  declareBlock.hidden = candidates.length === 0;
}

async function declareSelectedWeight() {
  const button = document.querySelector("#report-weight-declare-button");
  const errorBox = document.querySelector("#report-settings-error");
  const variable = document.querySelector("#report-weight-candidate").value;
  const question = variable ? questionByVariable(variable) : null;
  if (!question) return;
  errorBox.hidden = true;
  setBusy(button, true, "Объявляем…");
  try {
    currentProject = await api(
      `/api/projects/${currentProject.id}/questions/${encodeURIComponent(question.code)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "weight" }),
      }
    );
    renderProject();
    // Объявление — половина действия: аналитик хотел этот вес, поэтому он сразу
    // выбирается, а рядом показывается разбор распределения.
    document.querySelector("#report-weight-declare").open = false;
    renderReportWeightSelect(`ready:${variable}`);
    showToast(`${variable} объявлена весом`);
  } catch (error) {
    showError(errorBox, error);
  } finally {
    setBusy(button, false, "Объявить весом");
  }
}

// Разбор распределения показывается до применения веса, а не после отказа
// сборки: `requirements.md` §8 требует именно этого порядка.
async function loadReportWeightDiagnostics() {
  const container = document.querySelector("#report-weight-diagnostics");
  const saveButton = document.querySelector("#save-report-settings");
  const selection = document.querySelector("#report-weight").value;
  if (!selection.startsWith("ready:")) {
    container.hidden = true;
    container.innerHTML = "";
    saveButton.disabled = false;
    return;
  }
  const variable = selection.slice(6);
  container.hidden = false;
  container.innerHTML = '<p class="muted">Считаем распределение веса…</p>';
  try {
    const assessment = await api(
      `/api/projects/${currentProject.id}/weights/ready/${encodeURIComponent(variable)}/diagnostics`
    );
    if (document.querySelector("#report-weight").value !== selection) return;
    container.innerHTML = renderWeightAssessment(assessment);
    saveButton.disabled = !assessment.usable;
  } catch (error) {
    container.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
    saveButton.disabled = false;
  }
}

function renderWeightAssessment(assessment) {
  const verdict = assessment.usable
    ? '<p class="weight-ok">Вес пригоден.</p>'
    : assessment.problems
      .map(problem => `<p class="error">${escapeHtml(problem.message)}</p>`)
      .join("");
  const notes = (assessment.notes || [])
    .map(note => `<p class="muted">${escapeHtml(note)}</p>`)
    .join("");
  const diagnostics = assessment.diagnostics;
  if (!diagnostics) return verdict + notes;
  const metrics = [
    [diagnostics.minimum, "Минимум"], [diagnostics.maximum, "Максимум"],
    [diagnostics.mean, "Среднее"], [diagnostics.extreme_share_percent, "Экстремальных, %"],
    [diagnostics.effective_base, "Эффективная база"], [diagnostics.design_effect, "Design effect"],
  ];
  const grid = `<dl class="diag-grid">${metrics.map(([value, label]) => `<div><dt>${escapeHtml(label)}</dt><dd class="${assessment.usable && label === "Эффективная база" ? "ok" : ""}">${formatWeightNumber(value)}</dd></div>`).join("")}</dl>`;
  return verdict + grid + notes;
}

function renderTable() {
  if (!currentProject) return;
  const tableWrap = document.querySelector("#table-wrap");
  const entityList = document.querySelector("#entity-list");
  const cardView = currentView === "reports";
  tableWrap.hidden = cardView;
  entityList.hidden = !cardView;
  if (currentView === "data" && structureMode === "variables") {
    renderPhysicalVariables();
    return;
  }
  if (currentView === "reports") {
    renderReportBlocks();
    return;
  }
  const allQuestions = configuredQuestions();
  const questions = allQuestions.filter(question =>
    matchesStatusFilter(question)
    && matchesStructureSearch(question.code, question.label, originalQuestionLabel(question))
  );
  updateStructureSearchCount(questions.length, allQuestions.length);
  // Код вынесен в свою колонку: раньше он был приклеен к названию, и
  // колонка «Вопрос» забирала 76% ширины ни на что.
  const shownSelected = questions.length > 0
    && questions.every(question => selectedQuestionCodes.has(question.code));
  document.querySelector("#table-head").innerHTML =
    `<th class="select-cell"><input type="checkbox" id="select-all-questions" aria-label="Выбрать все показанные вопросы" ${shownSelected ? "checked" : ""} /></th>`
    + "<th class=\"drag-cell\" aria-label=\"Порядок\"></th>"
    + "<th class=\"code-column\">Код</th>"
    + "<th class=\"question-cell\">Вопрос</th>"
    + "<th class=\"type-column\">Тип</th>"
    + "<th class=\"count-column\">Перем.</th>"
    + "<th class=\"status-column\">Статус</th>";
  if (!questions.length) {
    document.querySelector("#table-body").innerHTML = emptySearchRow(7, "Вопросы не найдены.");
    return;
  }
  document.querySelector("#table-body").innerHTML = questions.map(question => {
    const sourceLabel = originalQuestionLabel(question);
    // Подтверждённое предупреждение больше не подсвечивается: иначе рядом
    // окажутся «требует проверки» в строке и «Готов» в статусе.
    const status = questionStatus(question);
    const warnings = status === "review" ? (question.warnings || []).join(" · ") : "";
    const title = `${question.code} — ${question.label}`;
    // Группировки видны прямо в списке: иначе о них знает только тот,
    // кто откроет карточку исходного вопроса.
    const groupings = recodingsForQuestion(question);
    const sub = [
      warnings || sourceLabel,
      groupings.length ? plural(groupings.length, "группировка", "группировки", "группировок") : "",
    ].filter(Boolean).join(" · ");
    // Пока список отфильтрован, порядок менять нельзя: соседи в выдаче не соседи в отчёте.
    const draggable = structureFiltered() ? "false" : "true";
    const checked = selectedQuestionCodes.has(question.code) ? "checked" : "";
    return `<tr class="question-row ${question.code === currentQuestionCode ? "selected" : ""}" data-code="${escapeHtml(question.code)}">
      <td class="select-cell"><input type="checkbox" class="select-question" data-select-code="${escapeAttribute(question.code)}" aria-label="Выбрать ${escapeAttribute(question.code)}" ${checked} /></td>
      <td class="drag-cell"><button type="button" class="drag-handle" draggable="${draggable}" data-drag-code="${escapeAttribute(question.code)}" aria-label="Перетащить ${escapeAttribute(question.code)}" title="${structureFiltered() ? "Сбросьте фильтр, чтобы менять порядок" : "Перетащите, чтобы изменить порядок"}"><span aria-hidden="true">⋮⋮</span></button></td>
      <td class="code-column"><code>${escapeHtml(question.code)}</code></td>
      <td class="question-cell"><span class="q-title" title="${escapeAttribute(title)}">${escapeHtml(question.label)}</span>${sub ? `<span class="q-sub ${warnings ? "warning" : ""}" title="${escapeAttribute(sub)}">${escapeHtml(sub)}</span>` : ""}</td>
      <td class="type-column"><span class="type-icon" role="img" aria-label="${escapeAttribute(typeLabels[question.question_type] || question.question_type)}">${typeIcons[question.question_type] || typeIcons.technical}<span class="type-label" aria-hidden="true">${escapeHtml(typeLabels[question.question_type] || question.question_type)}</span></span></td>
      <td class="count-column"><span class="count">${question.source_variables.length}</span></td>
      <td class="status-column"><span class="status ${status === "ready" ? "" : status}">${statusLabels[status]}</span></td>
    </tr>`;
  }).join("");
}

function emptySearchRow(columns, message) {
  const reason = structureSearch
    ? `По запросу «${escapeHtml(structureSearch)}» ничего не совпало.`
    : "Под выбранный фильтр ничего не подходит.";
  return `<tr class="empty-row"><td colspan="${columns}"><div class="empty-state">${escapeHtml(message)} ${reason}</div></td></tr>`;
}

function filterPreviewKey(filter) {
  return `${currentProject?.id || ""}:${filter.id}:${JSON.stringify(filter.rule)}`;
}

async function getFilterCardPreview(filter, projectId) {
  const key = `${projectId}:${filter.id}:${JSON.stringify(filter.rule)}`;
  if (filterPreviewCache.has(key)) return filterPreviewCache.get(key);
  if (filterPreviewRequests.has(key)) return filterPreviewRequests.get(key);
  const request = api(`/api/projects/${projectId}/filters/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: filter.name, rule: filter.rule }),
  }).then(preview => {
    filterPreviewCache.set(key, preview);
    return preview;
  }).catch(() => {
    filterPreviewCache.set(key, null);
    return null;
  }).finally(() => filterPreviewRequests.delete(key));
  filterPreviewRequests.set(key, request);
  return request;
}

function hydrateFilterCards(filters) {
  if (filterCardHydration) return filterCardHydration;
  const projectId = currentProject.id;
  const pending = filters.filter(filter => !filterPreviewCache.has(`${projectId}:${filter.id}:${JSON.stringify(filter.rule)}`));
  if (!pending.length) return Promise.resolve();
  filterCardHydration = (async () => {
    for (let index = 0; index < pending.length; index += 4) {
      await Promise.all(pending.slice(index, index + 4).map(filter => getFilterCardPreview(filter, projectId)));
    }
    if (currentProject?.id === projectId && currentView === "reports") renderReportBlocks();
  })().finally(() => { filterCardHydration = null; });
  return filterCardHydration;
}

function bannerSourceCategoryCount(source) {
  if (source.kind === "recoding") {
    return configuredRecodings().find(item => item.id === source.ref)?.categories.length || 0;
  }
  const question = findQuestion(source.ref);
  const variableName = question?.source_variables?.[0];
  const variable = currentProject.inspection.variables.find(item => item.name === variableName);
  return variable?.value_labels?.length || 0;
}

function recodePreviewKey(recodingId) {
  return `${currentProject?.id || ""}:${recodingId}`;
}

function renderPhysicalVariables() {
    const allVariables = currentProject.inspection.variables;
    const variables = allVariables.filter(variable => matchesStructureSearch(variable.name, variable.label));
    updateStructureSearchCount(variables.length, allVariables.length);
    document.querySelector("#table-head").innerHTML = "<th>Имя</th><th>Логический вопрос</th><th>Метка столбца</th><th>Формат</th><th>Measurement</th><th>Уникальных</th><th>Валидная база</th><th>Пропуски</th>";
    if (!variables.length) {
      document.querySelector("#table-body").innerHTML = emptySearchRow(8, "Столбцы не найдены.");
      return;
    }
    document.querySelector("#table-body").innerHTML = variables.map(variable => `
      <tr>
        <td><code>${escapeHtml(variable.name)}</code></td><td>${logicalOwnerButton(variable.name)}</td><td><strong title="${escapeAttribute(variable.label)}">${escapeHtml(variable.label)}</strong></td>
        <td>${escapeHtml(variable.original_format || variable.storage_type)}</td><td>${escapeHtml(variable.measurement_level || "—")}</td>
        <td>${variable.unique_count.toLocaleString("ru-RU")}</td><td>${variable.valid_count.toLocaleString("ru-RU")}</td><td>${variable.missing_count.toLocaleString("ru-RU")}</td>
      </tr>`).join("");
}

function logicalOwnerButton(variableName) {
  const question = configuredQuestions().find(item => item.source_variables.includes(variableName));
  if (!question) return "—";
  return `<button type="button" class="owner-link" data-open-question="${escapeAttribute(question.code)}">${escapeHtml(question.code)}</button>`;
}

function selectedReportFilterId() {
  return currentProject?.configuration?.report_filter_id || null;
}

function selectedReportBannerId() {
  const banners = configuredBanners();
  return Object.prototype.hasOwnProperty.call(currentProject.configuration, "report_banner_id")
    ? currentProject.configuration.report_banner_id
    : banners.at(-1)?.id || null;
}

async function assignReportBanner(bannerId, button) {
  button.disabled = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/report-banner`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ banner_id: bannerId }),
    });
    renderProject();
    showToast(bannerId ? "Баннер будет использован в Excel" : "В Excel останется только Total");
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
  }
}

async function assignReportFilter(filterId, button) {
  button.disabled = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/report-filter`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filter_id: filterId }),
    });
    renderProject();
    showToast(filterId ? "Правило применено ко всему отчёту" : "Общий фильтр отчёта снят");
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
  }
}

function openBanner(bannerId = null) {
  if (!confirmDiscard(openInspectorPanel())) return;
  currentBannerId = bannerId;
  currentQuestionCode = null;
  currentRecodingId = null;
  showInspector(bannerEditor);
  const banner = bannerId ? configuredBanners().find(item => item.id === bannerId) : null;
  setHeadingText(document.querySelector("#banner-editor-title"), banner?.name || "Новый баннер");
  document.querySelector("#banner-name").value = banner?.name || `Баннер ${configuredBanners().length + 1}`;
  const list = document.querySelector("#banner-block-list");
  list.innerHTML = "";
  if (banner) banner.blocks.forEach(block => addBannerBlock(block));
  else addBannerBlock();
  document.querySelector("#delete-banner").hidden = !banner;
  document.querySelector("#banner-error").hidden = true;
  document.querySelector("#banner-preview-count").textContent = "";
  document.querySelector("#banner-preview").innerHTML = banner
    ? '<p class="muted">Считаем…</p>'
    : '<p class="muted">Сохраните баннер для расчёта.</p>';
  setBannerFormDirty(false);
  renderTable();
  if (banner) loadBannerPreview();
}

function closeBanner() {
  if (bannerFormDirty && !confirm("Есть несохранённые изменения. Закрыть редактор без сохранения?")) return;
  setBannerFormDirty(false);
  bannerEditor.hidden = true;
  currentBannerId = null;
  renderTable();
}

function setBannerFormDirty(dirty) {
  bannerFormDirty = dirty;
  const warning = document.querySelector("#banner-unsaved-warning");
  warning.hidden = !dirty;
  document.querySelector("#save-banner").classList.toggle("has-unsaved-changes", dirty);
}

function addBannerBlock(block = {}) {
  const first = block.sources?.[0];
  const second = block.sources?.[1];
  const element = document.createElement("div");
  element.className = "banner-block block-row";
  element.innerHTML = `<div class="banner-block-head block-row-head"><input class="banner-block-label" placeholder="Название блока — необязательно" value="${escapeAttribute(block.label || "")}" /><button class="del" type="button" data-remove-banner-block title="Удалить блок" aria-label="Удалить блок">×</button></div><div class="block-lvls"><label>Первый уровень<select class="banner-source-first">${bannerSourceOptions(first, false)}</select></label><label>Второй уровень<select class="banner-source-second">${bannerSourceOptions(second, true)}</select></label></div>
    <details class="banner-categories" data-level="0"><summary>Категории первого уровня</summary><div class="banner-category-list"></div></details>
    <details class="banner-categories" data-level="1" ${second ? "" : "hidden"}><summary>Категории второго уровня</summary><div class="banner-category-list"></div></details>`;
  // Сохранённые настройки категорий живут на блоке, пока список не открыли:
  // неоткрытый список не должен их терять при сохранении.
  element.bannerCategories = [first?.categories || null, second?.categories || null];
  document.querySelector("#banner-block-list").append(element);
}

async function moveQuestionTo(code, targetCode, placeAfter) {
  const codes = configuredQuestions().map(item => item.code);
  const index = codes.indexOf(code);
  if (index < 0 || code === targetCode) return;
  codes.splice(index, 1);
  const target = codes.indexOf(targetCode);
  if (target < 0) return;
  codes.splice(target + (placeAfter ? 1 : 0), 0, code);
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/questions/order`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codes }),
    });
    renderProject();
    showToast("Порядок вопросов обновлён");
  } catch (error) {
    alert(error.message);
  }
}

async function refreshStructure() {
  if (!currentProject || !confirm("Заново распознать multiple и matrix? Названия и настройки существующих блоков будут сохранены, где это возможно.")) return;
  const button = document.querySelector("#refresh-structure");
  setBusy(button, true, "Распознаём…");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/structure/refresh`, { method: "POST" });
    currentQuestionCode = null;
    editor.hidden = true;
    renderProject();
    showToast("Структура перераспознана");
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(button, false, "Перераспознать структуру");
  }
}

async function loadPreview() {
  if (!currentProject || !currentQuestionCode) return;
  renderQuestionOutput(findQuestion(currentQuestionCode));
  const container = document.querySelector("#preview-content");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const preview = await api(`/api/projects/${currentProject.id}/questions/${encodeURIComponent(currentQuestionCode)}/preview`);
    container.innerHTML = renderPreview(preview);
    renderNotApplicable(findQuestion(currentQuestionCode), preview);
    renderNets(findQuestion(currentQuestionCode), preview);
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
    document.querySelector("#not-applicable").hidden = true;
    document.querySelector("#question-nets").hidden = true;
  }
}

function renderPreview(preview) {
  const base = `<div class="base-line"><span>Total <strong>${preview.total_base.toLocaleString("ru-RU")}</strong></span><span>Валидная база <strong>${preview.valid_base.toLocaleString("ru-RU")}</strong></span></div>`;
  if (preview.items?.length) {
    return base + `<div class="matrix-preview">${preview.items.map(item => `<details><summary><span><code>${escapeHtml(item.variable)}</code> ${escapeHtml(item.label)}</span><strong>Среднее ${item.statistics.mean == null ? "—" : Number(item.statistics.mean).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}</strong></summary><div class="preview-rows">${item.rows.map(row => `<div class="${row.is_special ? "special-row" : ""}"><span>${escapeHtml(row.label)}${row.is_special ? " · спецответ" : ""}</span><strong>${row.count}</strong><em>${formatPercent(row.percent_main)}</em><em>${formatPercent(row.percent_filter)}</em></div>`).join("")}</div></details>`).join("")}</div>`;
  }
  const rows = preview.rows?.length ? `<div class="preview-rows"><div class="preview-row-head"><span>Ответ</span><strong>N</strong><em>Total</em><em>Valid</em></div>${preview.rows.map(row => `
    <div class="${row.is_special ? "special-row" : ""}"><span>${escapeHtml(row.label)}${row.is_special ? " · спецответ" : ""}</span><strong>${row.count}</strong><em>${formatPercent(row.percent_main)}</em><em>${formatPercent(row.percent_filter)}</em></div>`).join("")}</div>` : "";
  const statistics = preview.statistics ? `<dl class="stats">
    ${stat("Среднее", preview.statistics.mean)}${stat("Медиана", preview.statistics.median)}
    ${stat("Минимум", preview.statistics.minimum)}${stat("Максимум", preview.statistics.maximum)}
    ${stat("Ст. отклонение", preview.statistics.stddev)}${stat("Ст. ошибка", preview.statistics.stderr)}
  </dl>` : "";
  const warnings = preview.warnings?.length ? `<div class="inline-warnings">${preview.warnings.map(item => `<p>⚑ ${escapeHtml(item)}</p>`).join("")}</div>` : "";
  return base + warnings + rows + statistics;
}

function stat(label, value) {
  return `<div><dt>${label}</dt><dd>${value == null ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 })}</dd></div>`;
}

function configuredQuestions() {
  return currentProject.configuration?.questions || currentProject.inspection.questions;
}

function originalQuestionLabel(question) {
  if (question.source_variables.length !== 1) return "";
  const source = currentProject.inspection.variables.find(
    variable => variable.name === question.source_variables[0]
  );
  const sourceLabel = source?.label?.trim() || "";
  return sourceLabel && sourceLabel !== question.label.trim() ? sourceLabel : "";
}

function configuredRecodings() {
  return currentProject.configuration?.recodings || [];
}

function configuredBanners() {
  return currentProject.configuration?.banners || [];
}

function configuredReportSettings() {
  const activeBanner = configuredBanners().find(
    banner => banner.id === selectedReportBannerId()
  ) || {};
  const legacy = {};
  Object.keys(defaultReportSettings).forEach(key => {
    if (Object.prototype.hasOwnProperty.call(activeBanner, key)) {
      legacy[key] = activeBanner[key];
    }
  });
  if (!Object.prototype.hasOwnProperty.call(activeBanner, "compare_to_total")) {
    legacy.compare_to_total = activeBanner.blocks?.some(
      block => block.compare_to_total
    ) || false;
  }
  if (!Object.prototype.hasOwnProperty.call(activeBanner, "compare_pairwise")) {
    legacy.compare_pairwise = activeBanner.blocks?.some(
      block => block.compare_pairwise
    ) || false;
  }
  return {
    ...defaultReportSettings,
    ...legacy,
    ...(currentProject.configuration?.report_settings || {}),
  };
}

function configuredFilters() {
  return currentProject.configuration?.filters || [];
}

function configuredWeights() {
  return currentProject.configuration?.calculated_weights || [];
}

function suggestRecodeCode() {
  const used = new Set(configuredRecodings().map(item => item.code.toUpperCase()));
  let index = used.size + 1;
  while (used.has(`RECODE_${index}`)) index += 1;
  return `RECODE_${index}`;
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
    while (result.status === "queued" || result.status === "running") {
      progress.value = result.progress || 0;
      status.textContent = `${result.stage} · ${result.progress || 0}%`;
      await new Promise(resolve => window.setTimeout(resolve, 500));
      result = await api(
        `/api/projects/${currentProject.id}/reports/jobs/${result.job_id}`
      );
    }
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

async function api(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const projectPrefix = currentProject ? `/api/projects/${currentProject.id}` : null;
  const revision = currentProject?.configuration?.revision;
  if (projectPrefix && url.startsWith(projectPrefix) && method !== "GET" && revision) {
    const headers = new Headers(options.headers || {});
    headers.set("If-Match", String(revision));
    options = { ...options, headers };
  }
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

function showToast(message) {
  const container = document.querySelector("#toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.innerHTML = '<svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden="true"><circle cx="9" cy="9" r="8" stroke="#2eae68" stroke-width="1.6"/><path d="M5.5 9.5L7.5 11.5L12.5 6.5" stroke="#2eae68" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  toast.append(document.createTextNode(message));
  container.append(toast);
  window.setTimeout(() => {
    toast.classList.add("toast-out");
    toast.addEventListener("animationend", () => toast.remove(), { once: true });
  }, 2100);
}

function formatDate(value) {
  return new Date(value).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

function formatPercent(value) {
  return value == null ? "—" : `${(value * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
}

function formatRange(category) {
  const lower = category.lower == null ? "−∞" : Number(category.lower).toLocaleString("ru-RU");
  const upper = category.upper == null ? "+∞" : Number(category.upper).toLocaleString("ru-RU");
  return `${lower}…${upper}`;
}

function formatRecodeCategory(recoding, category) {
  if ((recoding.mode || "ranges") === "categories") {
    return `${category.values.length} исходных знач.`;
  }
  return formatRange(category);
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

/* ================================================================
   АДРЕСА

   `#/projects/<id>/<раздел>` — открытый проект и раздел, `#/home` —
   лендинг, `#/` — старт. Адрес пишется при каждой смене проекта, раздела
   и экрана, а при загрузке и кнопках «Назад» / «Вперёд» читается обратно:
   перезагрузка страницы возвращает туда же (GAP-007).
   ================================================================ */
let applyingRoute = false;

function currentRoute() {
  if (!document.querySelector("#screen-home").hidden) return "#/home";
  if (currentProject && !document.querySelector("#workspace").hidden) {
    return `#/projects/${currentProject.id}/${currentView}`;
  }
  return "#/";
}

function writeRoute() {
  if (applyingRoute) return;
  const hash = currentRoute();
  if (location.hash !== hash) history.pushState(null, "", hash);
}

async function applyRoute() {
  const match = location.hash.match(/^#\/projects\/([0-9a-f-]{36})(?:\/([a-z]+))?$/);
  applyingRoute = true;
  try {
    if (location.hash === "#/home") {
      window.Shell.showScreen("home");
      return;
    }
    window.Shell.showScreen("manual");
    if (!match) {
      if (currentProject) document.querySelector("#new-project").click();
      return;
    }
    const view = SECTION_VIEWS.includes(match[2]) ? match[2] : "data";
    if (currentProject?.id === match[1]) {
      if (currentView !== view) setView(view);
      return;
    }
    try {
      showProject(await api(`/api/projects/${match[1]}`), view);
    } catch (error) {
      history.replaceState(null, "", "#/");
      showError(errorBox, error);
    }
  } finally {
    applyingRoute = false;
  }
  writeRoute();
}

document.addEventListener("shell:screen", writeRoute);
window.addEventListener("popstate", () => { void applyRoute(); });


/* SAV с производными скачивается через fetch: если длинные тексты ломают
   запись, сервер отказывает с объяснением, и экран предлагает выгрузку без них
   вместо страницы с JSON ошибки. */
document.querySelector("#download-derived-sav").addEventListener("click", async event => {
  event.preventDefault();
  if (!currentProject) return;
  const url = `/api/projects/${currentProject.id}/export.sav`;
  const stem = (currentProject.original_filename || "project").replace(/\.[^.]+$/, "");
  showToast("Готовим SAV…");
  try {
    let response = await fetch(url);
    if (response.status === 422 && response.headers.get("X-Long-Text")) {
      const { detail } = await response.json();
      if (!confirm(`${detail}\n\nВыгрузить без этих переменных?`)) return;
      response = await fetch(`${url}?long_text=omit`);
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `Ошибка сервера ${response.status}`);
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = `${stem}_производные.sav`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 60_000);
  } catch (error) {
    alert(error.message);
  }
});

/* Новая волна: разбор расхождений структуры до замены данных. Файл уходит
   на сервер дважды — сначала разбором, потом заменой: держать загруженное
   между запросами значило бы хранить чужие данные дольше нужного. */
let waveFile = null;

document.querySelector("#wave-file").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || !currentProject) return;
  waveFile = file;
  const body = document.querySelector("#wave-body");
  const apply = document.querySelector("#wave-apply");
  apply.disabled = true;
  body.innerHTML = '<p class="muted">Разбираем структуру нового файла…</p>';
  openSheet(document.querySelector("#wave-sheet"));
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const diff = await api(`/api/projects/${currentProject.id}/source/diff`, {
      method: "POST",
      body: form,
    });
    body.innerHTML = renderWaveDiff(diff, file);
    apply.disabled = !diff.can_replace;
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
});

function renderWaveDiff(diff, file) {
  const group = (title, items, empty) => items.length
    ? `<section class="wave-group"><h4>${title} · ${items.length}</h4><ul>${items.slice(0, 40).map(item => `<li><code>${escapeHtml(item.name)}</code> ${escapeHtml(item.label)}${item.changes.length ? ` — ${escapeHtml(item.changes.join("; "))}` : ""}</li>`).join("")}${items.length > 40 ? `<li>…ещё ${items.length - 40}</li>` : ""}</ul></section>`
    : `<section class="wave-group"><h4>${title}</h4><p class="wave-rows">${empty}</p></section>`;
  const blocking = diff.blocking.length
    ? `<div class="wave-blocking"><b>Замена невозможна.</b><ul>${diff.blocking.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>Снимите связи в структуре, баннерах или фильтрах и попробуйте снова.</div>`
    : "";
  return `${blocking}
    <p class="wave-rows">Файл <b>${escapeHtml(file.name)}</b>: респондентов было ${diff.rows_before.toLocaleString("ru-RU")}, станет ${diff.rows_after.toLocaleString("ru-RU")}. Настройки вопросов, перекодировки, фильтры, баннеры, формулы и кодификаторы сохранятся.</p>
    <div class="wave-groups">
      ${group("Новые переменные", diff.added, "новых переменных нет")}
      ${group("Исчезли", diff.removed, "ничего не исчезло")}
      ${group("Изменились", diff.changed, "подписи и коды прежние")}
    </div>`;
}

document.querySelector("#wave-apply").addEventListener("click", async () => {
  if (!waveFile || !currentProject) return;
  const apply = document.querySelector("#wave-apply");
  const body = document.querySelector("#wave-body");
  const form = new FormData();
  form.append("file", waveFile, waveFile.name);
  setBusy(apply, true, "Заменяем…");
  try {
    const result = await api(`/api/projects/${currentProject.id}/source`, {
      method: "PUT",
      body: form,
    });
    currentProject = result.project;
    waveFile = null;
    closeSheet();
    renderProject();
    void loadReportPreflight();
    showToast("Данные проекта заменены новой волной");
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>` + body.innerHTML;
  } finally {
    setBusy(apply, false, "Заменить данные");
  }
});

