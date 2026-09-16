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
  fileTitle.textContent = "Перетащите SAV или CSV сюда";
  fileCaption.textContent = "или нажмите, чтобы выбрать файл";
  loadProjects();
});

document.querySelector("#refresh-projects").addEventListener("click", loadProjects);
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
document.querySelector("#refresh-recode-preview").addEventListener("click", loadRecodePreview);
document.querySelector("#delete-recoding").addEventListener("click", deleteRecoding);
document.querySelector("#close-banner-editor").addEventListener("click", () => closeBanner());
document.querySelector("#add-banner-block").addEventListener("click", () => {
  addBannerBlock();
  setBannerFormDirty(true);
});
document.querySelector("#delete-banner").addEventListener("click", deleteBanner);
document.querySelector("#refresh-banner-preview").addEventListener("click", loadBannerPreview);
document.querySelector("#close-filter-editor").addEventListener("click", () => {
  if (confirmDiscard(filterEditor)) closeFilter();
});
document.querySelector("#add-filter-condition").addEventListener("click", () => {
  addFilterCondition();
  scheduleFilterPreview();
});
document.querySelector("#delete-filter").addEventListener("click", deleteFilter);
document.querySelector("#copy-filter").addEventListener("click", copyFilter);
document.querySelector("#close-weight-editor").addEventListener("click", () => {
  if (confirmDiscard(weightEditor)) closeWeight();
});
document.querySelector("#add-weight-dimension").addEventListener("click", () => addWeightDimension());
document.querySelector("#delete-weight").addEventListener("click", deleteWeight);
document.querySelector("#refresh-weight-preview").addEventListener("click", loadWeightPreview);
document.querySelector("#weight-trimming").addEventListener("change", renderWeightTrimming);
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
document.querySelector("#filter-form").addEventListener("input", scheduleFilterPreview);
document.querySelector("#filter-form").addEventListener("change", scheduleFilterPreview);

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
  document.querySelector("#section-soon").hidden = onCanvas || view === "tables";
  syncSectionChrome(view);
  if (onCanvas) {
    renderSectionHead(view);
    renderTable();
    if (view === "reports") void loadReportPreflight();
  } else if (view === "tables") {
    window.Shell.activateTables();
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

/* Библиотека проектов: поиск и порядок — на стороне экрана, над уже
   загруженным списком; переименование, копия и корзина — на сервере. */
let libraryProjects = [];
let libraryTrash = [];
let libraryShowTrash = false;

async function loadProjects() {
  const library = document.querySelector("#project-library");
  const list = document.querySelector("#project-list");
  try {
    [libraryProjects, libraryTrash] = await Promise.all([
      api("/api/projects"),
      api("/api/projects/trash"),
    ]);
    library.hidden = !libraryProjects.length && !libraryTrash.length;
    renderLibrary();
  } catch (error) {
    library.hidden = false;
    list.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function renderLibrary() {
  const list = document.querySelector("#project-list");
  const toggle = document.querySelector("#project-trash-toggle");
  toggle.textContent = libraryTrash.length ? `Корзина · ${libraryTrash.length}` : "Корзина";
  toggle.setAttribute("aria-pressed", String(libraryShowTrash));
  const query = document.querySelector("#project-search").value.trim().toLowerCase();
  const order = document.querySelector("#project-sort").value;
  const shown = (libraryShowTrash ? libraryTrash : libraryProjects)
    .filter(project => !query || `${project.name} ${project.original_filename}`.toLowerCase().includes(query))
    .sort((left, right) => {
      if (order === "name") return left.name.localeCompare(right.name, "ru");
      if (order === "old") return left.created_at.localeCompare(right.created_at);
      return right.created_at.localeCompare(left.created_at);
    });
  if (!shown.length) {
    const empty = libraryShowTrash ? "Корзина пуста." : query ? "Ничего не нашлось." : "Проектов пока нет.";
    list.innerHTML = `<p class="muted library-empty">${empty}</p>`;
    return;
  }
  list.innerHTML = shown.map(project => (libraryShowTrash ? trashCard(project) : projectCard(project))).join("");
}

function projectCard(project) {
  return `<div class="project-card" data-card-id="${escapeAttribute(project.id)}">
    <button class="project-open" type="button" data-project-id="${escapeAttribute(project.id)}">
      <span><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.original_filename)}</small></span>
      <time>${formatDate(project.created_at)}</time>
    </button>
    <span class="project-actions">
      <button type="button" class="text-button" data-project-rename="${escapeAttribute(project.id)}">Переименовать</button>
      <button type="button" class="text-button" data-project-copy="${escapeAttribute(project.id)}">Копия</button>
      <button type="button" class="text-button danger-text" data-project-trash="${escapeAttribute(project.id)}">В корзину</button>
    </span>
  </div>`;
}

function trashCard(project) {
  return `<div class="project-card trashed">
    <span class="project-open">
      <span><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.original_filename)} · в корзине с ${formatDate(project.trashed_at)}</small></span>
    </span>
    <span class="project-actions">
      <button type="button" class="text-button" data-project-restore="${escapeAttribute(project.id)}">Восстановить</button>
    </span>
  </div>`;
}

function startProjectRename(id) {
  const card = document.querySelector(`[data-card-id="${CSS.escape(id)}"]`);
  const project = libraryProjects.find(item => item.id === id);
  if (!card || !project) return;
  const form = document.createElement("form");
  form.className = "project-rename-form";
  form.innerHTML = `<input maxlength="200" aria-label="Новое название проекта" value="${escapeAttribute(project.name)}" />
    <button type="submit">Сохранить</button>
    <button type="button" class="secondary" data-rename-cancel>Отмена</button>`;
  card.querySelector(".project-open").replaceWith(form);
  card.querySelector(".project-actions").hidden = true;
  const input = form.querySelector("input");
  input.focus();
  input.select();
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") renderLibrary();
  });
  form.querySelector("[data-rename-cancel]").addEventListener("click", renderLibrary);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    try {
      await api(`/api/projects/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: input.value }),
      });
      showToast("Проект переименован");
      await loadProjects();
    } catch (error) {
      showError(errorBox, error);
    }
  });
}

document.querySelector("#project-list").addEventListener("click", async event => {
  const open = event.target.closest("[data-project-id]");
  const rename = event.target.closest("[data-project-rename]");
  const copy = event.target.closest("[data-project-copy]");
  const trash = event.target.closest("[data-project-trash]");
  const restore = event.target.closest("[data-project-restore]");
  if (rename) {
    startProjectRename(rename.dataset.projectRename);
    return;
  }
  const button = open || copy || trash || restore;
  if (!button) return;
  button.disabled = true;
  try {
    if (open) {
      showProject(await api(`/api/projects/${open.dataset.projectId}`));
      return;
    }
    if (copy) {
      await api(`/api/projects/${copy.dataset.projectCopy}/duplicate`, { method: "POST" });
      showToast("Копия проекта создана");
    } else if (trash) {
      await api(`/api/projects/${trash.dataset.projectTrash}`, { method: "DELETE" });
      showToast("Проект перемещён в корзину");
    } else {
      await api(`/api/projects/trash/${restore.dataset.projectRestore}/restore`, { method: "POST" });
      showToast("Проект восстановлен");
    }
    await loadProjects();
  } catch (error) {
    showError(errorBox, error);
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#project-search").addEventListener("input", renderLibrary);
document.querySelector("#project-sort").addEventListener("change", renderLibrary);
document.querySelector("#project-trash-toggle").addEventListener("click", () => {
  libraryShowTrash = !libraryShowTrash;
  renderLibrary();
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
const TABLE_ROW_TYPES = ["single_choice", "scale", "numeric", "multiple_choice_dichotomy", "matrix"];

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
          || question.question_type === "multiple_choice_dichotomy",
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

/* ================================================================
   РАЗДЕЛ «ОТЧЁТ»

   Колонки, база, вес, статистика и состав — не пять разделов, а пять
   свойств одной книги. Раньше у каждого был свой экран со списком на
   0–3 карточки и пустым состоянием во весь холст; теперь свойство —
   строка, а список свёрнут в поповер выбора, открывающийся из неё же.
   Редакторы остались прежними инспекторами слева.
   ================================================================ */

const pickerElement = document.querySelector("#picker");
let pickerKind = null;
// Preflight держится до следующей правки конфигурации: он читает данные
// целиком, а ревизия меняется только при записи.
let preflightCache = { revision: null, report: null };
// Разбор готового веса по переменной: тот же endpoint, что и в листе настроек.
const readyWeightCache = new Map();

// Числа в подписях раздела стоят рядом с существительным, поэтому его
// приходится склонять: «1 блок · 3 колонки», а не «1 блоков · 3 колонок».
function plural(count, one, few, many) {
  const tens = Math.abs(count) % 100;
  const units = count % 10;
  if (tens > 10 && tens < 20) return `${count} ${many}`;
  if (units === 1) return `${count} ${one}`;
  if (units >= 2 && units <= 4) return `${count} ${few}`;
  return `${count} ${many}`;
}

function reportBannerColumnCount(banner) {
  return 1 + banner.blocks.reduce(
    (total, block) => total + block.sources.reduce(
      (count, source) => count * bannerSourceCategoryCount(source), 1
    ),
    0,
  );
}

// Строка вместо плитки: свойства книги стоят узкой колонкой слева, а
// статистика — своей колонкой справа. Холст шириной 1180px, и ярусами
// во всю ширину в нём писали только потому, что раскладку задавал
// список: ни одной из половин эта ширина не нужна.
//
// Значение — оно же кнопка выбора: щёлкать по названию естественнее,
// чем искать отдельную пилюлю. Соседняя кнопка ведёт в редактор.
function reportPropRow({ key, title, value, off, meta, picker, hint = "", action = "" }) {
  // Строка метрик обрезается по ширине колонки, поэтому длинный хвост —
  // перечисление блоков баннера — дублируется подсказкой.
  const hintAttribute = hint ? ` title="${escapeAttribute(hint)}"` : "";
  const text = `<strong class="${off ? "off" : ""}">${value}</strong><small>${meta}</small>`;
  const valueMarkup = picker
    ? `<button type="button" class="prop-val" data-picker="${picker}"${hintAttribute}
        aria-haspopup="dialog" aria-expanded="false">${text}</button>`
    : `<span class="prop-val"${hintAttribute}>${text}</span>`;
  return `<div class="prop" data-block="${key}">
    <span class="prop-key">${escapeHtml(title)}</span>
    ${valueMarkup}
    ${action}
  </div>`;
}

// Состав — не настройка книги, а итог структуры: его не выбирают здесь,
// его правят в другом разделе. Поэтому у строки нет поповера, а есть
// переход.
function reportContentRow() {
  const questions = configuredQuestions();
  const included = questions.filter(question => question.included_in_report);
  const review = included.filter(question => questionStatus(question) === "review");
  const withBase = included.filter(question => question.base_filter_id);
  const excluded = questions.length - included.length;
  const meta = [
    review.length ? `требуют проверки <b>${review.length}</b>` : "",
    withBase.length ? `со своей базой <b>${withBase.length}</b>` : "",
    excluded ? `исключено <b>${excluded}</b>` : "",
  ].filter(Boolean).join(" · ") || "Все вопросы массива идут в книгу";
  return reportPropRow({
    key: "content", title: "Состав",
    value: `${plural(included.length, "вопрос", "вопроса", "вопросов")} из ${questions.length}`,
    meta,
    action: '<button type="button" class="prop-act" data-goto="data">к структуре</button>',
  });
}

function reportColumnsRow() {
  const banner = configuredBanners().find(item => item.id === selectedReportBannerId()) || null;
  const blocks = (banner?.blocks || []).map(block =>
    block.label || block.sources.map(bannerSourceLabel).join(" → ")
  ).join(", ");
  return reportPropRow({
    key: "banner", title: "Колонки", picker: "banner",
    value: banner ? escapeHtml(banner.name) : "Только Total",
    off: !banner,
    meta: banner
      ? `Блоков <b>${banner.blocks.length}</b> · колонок <b>${reportBannerColumnCount(banner)}</b> · ${escapeHtml(blocks)}`
      : "Разбивки нет, одна колонка",
    hint: blocks,
    action: banner
      ? `<button type="button" class="prop-act" data-edit="banner" data-id="${escapeAttribute(banner.id)}">править</button>`
      : '<button type="button" class="prop-act" data-new="banner">новый</button>',
  });
}

function reportBaseRow() {
  const filter = configuredFilters().find(item => item.id === selectedReportFilterId()) || null;
  const total = currentProject.inspection.row_count;
  const preview = filter ? filterPreviewCache.get(filterPreviewKey(filter)) : null;
  const sample = preview
    ? `выборка <b>${preview.selected.toLocaleString("ru-RU")}</b> из ${preview.total.toLocaleString("ru-RU")}`
    : "выборка <b>считается…</b>";
  return reportPropRow({
    key: "filter", title: "База", picker: "filter",
    value: filter ? escapeHtml(filter.name) : "Все респонденты",
    off: !filter,
    // Правило — тем же текстом, что в редакторе и statistics.txt: его строит
    // один форматтер на сервере. Пока предпросмотр не пришёл, видно число условий.
    meta: filter
      ? (preview
        ? `${escapeHtml(preview.description)} · ${sample}`
        : `${plural(countFilterConditions(filter.rule), "условие", "условия", "условий")} · ${sample}`)
      : `Все <b>${total.toLocaleString("ru-RU")}</b> респондентов`,
    hint: preview?.description || "",
    action: filter
      ? `<button type="button" class="prop-act" data-edit="filter" data-id="${escapeAttribute(filter.id)}">править</button>`
      : '<button type="button" class="prop-act" data-new="filter">новое</button>',
  });
}

function reportWeightRow(settings) {
  const calculated = settings.calculated_weight_id
    ? configuredWeights().find(item => item.id === settings.calculated_weight_id)
    : null;
  const ready = settings.weight_variable || null;
  let value = "Без веса";
  let meta = "Показатели и базы невзвешенные";
  let action = '<button type="button" class="prop-act" data-open-sheet="report-settings">настроить</button>';
  if (calculated) {
    value = escapeHtml(calculated.name);
    const bounds = calculated.lower_bound == null
      ? ""
      : ` · границы <b>${formatWeightNumber(calculated.lower_bound)}–${formatWeightNumber(calculated.upper_bound)}</b>`;
    meta = `raking / IPF · ${plural(calculated.dimensions.length, "распределение", "распределения", "распределений")}${bounds}`;
    action = `<button type="button" class="prop-act" data-edit="weight" data-id="${escapeAttribute(calculated.id)}">править</button>`;
  } else if (ready) {
    value = escapeHtml(ready);
    const diagnostics = readyWeightCache.get(ready)?.diagnostics;
    meta = diagnostics
      ? `Готовый из массива · эфф. база <b>${formatWeightNumber(diagnostics.effective_base)}</b> · DEFF <b>${formatWeightNumber(diagnostics.design_effect)}</b>`
      : "Готовый из массива · разбор распределения <b>считается…</b>";
  }
  return reportPropRow({
    key: "weight", title: "Вес", picker: "weight",
    value, off: !calculated && !ready, meta, action,
  });
}

// Листы — не свойство, которое здесь выбирают, а следствие состава:
// второй топлайн существует ради вопросов, заданных не всем. Правило то
// же, что на сервере (core/reporting/data.py): объявленный пропуск SPSS
// или код, помеченный как «не применимо».
function reportSheetsRow() {
  const partial = configuredQuestions().filter(question =>
    question.included_in_report
    && (question.missing_count > 0 || (question.not_applicable_values || []).length > 0)
  ).length;
  return reportPropRow({
    key: "sheets", title: "Листы", off: true,
    value: "Содержание · topline_main · topline_filter",
    meta: partial
      ? `Второй топлайн — для <b>${partial}</b> ${plural(partial, "вопроса", "вопросов", "вопросов")} с пропусками`
      : "Вопросов с пропусками нет, второй топлайн останется пустым",
  });
}

// Статистика — не строка. У остальных свойств значение одно («этот
// баннер», «этот фильтр», «этот вес»), а здесь их семь, и прятать их за
// словом «изменить» значит держать половину настроек отчёта в закрытом
// ящике. Своей колонкой они помещаются целиком: каждый переключатель
// виден и уходит в конфигурацию сразу.
function statSegment(name, options, value) {
  return `<span class="seg" role="radiogroup">${options.map(option => `
    <button type="button" role="radio" data-stat="${name}" data-value="${escapeAttribute(option.value)}"
      aria-checked="${option.value === value}" class="${option.value === value ? "on" : ""}">${escapeHtml(option.label)}</button>`).join("")}</span>`;
}

function statToggle(name, label, checked, title = "") {
  return `<button type="button" class="opt ${checked ? "on" : ""}" data-stat="${name}"
    data-value="${checked ? "off" : "on"}" aria-pressed="${checked}" title="${escapeAttribute(title)}">
    <span class="opt-mark" aria-hidden="true"></span>${escapeHtml(label)}</button>`;
}

function reportStatisticsColumn(settings) {
  // Схема сравнения — это два поля сразу: считать ли подгруппы и с кем.
  // Раздельно они давали выключенный селект рядом с выключенным флажком.
  const scheme = !settings.compare_to_total
    ? "off"
    : settings.compare_target === "total" ? "total" : "rest";
  const waveQuestion = configuredQuestions().find(question => question.role === "wave");
  const waveVariable = waveQuestion
    ? currentProject.inspection.variables
      .find(variable => variable.name === waveQuestion.source_variables?.[0])
    : null;
  const waveOptions = (waveVariable?.value_labels || []).map(item => {
    const value = JSON.stringify(item.value);
    const selected = String(item.value) === String(settings.wave_control_value) ? "selected" : "";
    return `<option value="${escapeAttribute(value)}" ${selected}>${escapeHtml(item.label)}</option>`;
  }).join("");
  const profile = currentOutputProfile(settings);
  const totalWarning = scheme === "total"
    ? '<p class="stat-hint warning-hint">Total включает саму подгруппу: выборки пересекаются, различия занижаются. Выбирайте, только если этого требует шаблон заказчика.</p>'
    : "";
  return `<section class="col stat-col" id="stat-panel">
    <div class="col-head">
      <h3>Статистика</h3>
      <span class="col-note">действует на весь отчёт</span>
      <span id="stat-saved" class="stat-saved" role="status" hidden>Сохранено</span>
    </div>
    <div class="stat-stack">

      <div class="stat-block">
        <p>Сравнение подгрупп</p>
        <div class="stat-controls">
          ${statSegment("scheme", [
            { value: "off", label: "Не считать" },
            { value: "rest", label: "С остатком (Rest)" },
            { value: "total", label: "С Total" },
          ], scheme)}
          ${statToggle("pairwise", "Попарные внутри блока", settings.compare_pairwise)}
        </div>
        ${totalWarning}
      </div>

      <div class="stat-block">
        <p>Уровень доверия и пороги</p>
        <div class="stat-controls">
          ${statSegment("confidence", [
            { value: "0.9", label: "90%" },
            { value: "0.95", label: "95%" },
            { value: "0.99", label: "99%" },
          ], String(settings.confidence_level))}
          <span class="stat-label-inline">второй</span>
          ${statSegment("secondary", [
            { value: "", label: "нет" },
            { value: "0.9", label: "90%" },
            { value: "0.8", label: "80%" },
          ], String(settings.secondary_confidence_level ?? ""))}
          ${statToggle("overall", "Общие тесты", settings.overall_tests,
            "Хи-квадрат для распределений и Welch ANOVA для средних по каждому блоку баннера")}
          ${statToggle("bonferroni", "Поправка Bonferroni", settings.bonferroni,
            "Корректирует alpha на число сравнений внутри блока")}
          <label class="stat-number">Малая база &lt;
            <input id="stat-minimum-base" type="number" min="1" max="100000" value="${settings.minimum_base}" />
          </label>
        </div>
      </div>

      <div class="stat-block">
        <p>Сравнение волн</p>
        <div class="stat-controls">
          ${statSegment("wave", [
            { value: "none", label: "Не сравнивать" },
            { value: "previous", label: "С предыдущей" },
            { value: "control", label: "С контрольной" },
          ], settings.wave_comparison)}
          ${settings.wave_comparison === "control"
            ? `<label class="stat-number">Контрольная
                <select id="stat-wave-control">${waveOptions}</select></label>`
            : ""}
        </div>
      </div>

      <div class="stat-block">
        <p>Вывод в книге</p>
        <div class="stat-controls">
          ${statSegment("profile", Object.entries(outputProfiles).map(([value, profile]) => ({ value, label: profile.label })), profile)}
          ${profile === "custom" ? '<span class="stat-custom">свой набор</span>' : ""}
        </div>
        <div class="stat-controls stat-row"><span class="stat-label">Шкалы</span>
          ${scaleMetricOptions.map(option => statToggle(`scale:${option.value}`, scaleMetricLabel(option, settings.scale_box), settings.scale_metrics.includes(option.value))).join("")}
          <span class="stat-label-inline">крайних кодов</span>
          ${statSegment("scale-box", ["1", "2", "3"].map(value => ({ value, label: value })), String(settings.scale_box))}
        </div>
        <div class="stat-controls stat-row"><span class="stat-label">Числовые</span>
          ${numericMetricOptions.map(option => statToggle(`numeric:${option.value}`, option.label, settings.numeric_metrics.includes(option.value))).join("")}
        </div>
        <div class="stat-controls stat-row"><span class="stat-label">Знаков</span>
          <span class="stat-label-inline">доли</span>
          ${statSegment("percent-decimals", ["0", "1", "2"].map(value => ({ value, label: value })), String(settings.percent_decimals))}
          <span class="stat-label-inline">средние</span>
          ${statSegment("mean-decimals", ["0", "1", "2", "3"].map(value => ({ value, label: value })), String(settings.mean_decimals))}
        </div>
        <div class="stat-controls stat-row">
          ${statToggle("charts", "Графики в книге", settings.show_charts,
            "Лист «Графики»: распределения вопросов родными графиками Excel, связанными с ячейками")}
          ${statToggle("counts", "N под долями", settings.show_counts,
            "Под каждой строкой долей — сколько человек дали этот ответ")}
          ${statToggle("row-percents", "% по строке", settings.row_percents,
            "Под долей — какая часть давших ответ приходится на колонку; в Total 100")}
          ${statToggle("table-percents", "% от общего", settings.table_percents,
            "Под долей — доля давших ответ и попавших в колонку от всей базы вопроса")}
          ${statToggle("pvalues", "p-value в примечании", settings.show_p_values,
            "Полный протокол теста в примечании к ячейке; книга заметно тяжелее")}
        </div>
        <p class="stat-hint muted">База выводится всегда: без неё значимость в книге не на чем проверить. NPS и CSAT выводятся полностью.</p>
      </div>

    </div>
  </section>`;
}

function renderReportBlocks() {
  const container = document.querySelector("#entity-list");
  const settings = configuredReportSettings();
  container.className = "entity-list report-blocks";
  container.innerHTML = `<div class="split">
    <section class="col">
      <div class="col-head"><h3>Содержимое книги</h3><span class="col-note">что войдёт в Excel</span></div>
      ${[
        reportContentRow(),
        reportColumnsRow(),
        reportBaseRow(),
        reportWeightRow(settings),
        reportSheetsRow(),
      ].join("")}
    </section>
    ${reportStatisticsColumn(settings)}
  </div>
  <section class="runs">
    <div class="col-head"><h3>История запусков</h3><span class="col-note">каждая сборка хранится неизменной и скачивается снова</span></div>
    <div id="report-runs" class="runs-list"><p class="runs-empty">Загружаем…</p></div>
  </section>`;
  void loadRunHistory();
  const activeBanner = configuredBanners().find(item => item.id === selectedReportBannerId());
  const included = configuredQuestions().filter(question => question.included_in_report).length;
  const columns = activeBanner ? reportBannerColumnCount(activeBanner) : 1;
  document.querySelector("#report-revision").textContent =
    `${plural(included, "вопрос", "вопроса", "вопросов")} · ${plural(columns, "колонка", "колонки", "колонок")}`;
  const filters = configuredFilters();
  if (filters.length) void hydrateFilterCards(filters);
  if (settings.weight_variable) void hydrateReadyWeight(settings.weight_variable);
}

// Разбор готового веса нужен блоку так же, как листу настроек: аналитик
// должен видеть эффективную базу до сборки, а не после отказа.
async function hydrateReadyWeight(variable) {
  if (readyWeightCache.has(variable)) return;
  const projectId = currentProject.id;
  try {
    const assessment = await api(
      `/api/projects/${projectId}/weights/ready/${encodeURIComponent(variable)}/diagnostics`
    );
    readyWeightCache.set(variable, assessment);
  } catch {
    readyWeightCache.set(variable, null);
  }
  if (currentProject?.id === projectId && currentView === "reports") renderReportBlocks();
}

// Настройки отчёта пишутся целиком: endpoint принимает полный набор,
// поэтому любой переключатель отправляет текущие значения с одной
// заменённой парой. Так же поступает и лист выбора веса.
function reportSettingsPayload(settings) {
  return {
    compare_to_total: settings.compare_to_total,
    compare_target: settings.compare_target,
    compare_pairwise: settings.compare_pairwise,
    confidence_level: settings.confidence_level,
    bonferroni: settings.bonferroni,
    show_p_values: settings.show_p_values,
    minimum_base: settings.minimum_base,
    weight_variable: settings.weight_variable || null,
    calculated_weight_id: settings.calculated_weight_id || null,
    wave_comparison: settings.wave_comparison,
    wave_control_value: settings.wave_comparison === "control"
      ? settings.wave_control_value
      : null,
    scale_metrics: settings.scale_metrics,
    numeric_metrics: settings.numeric_metrics,
    percent_decimals: settings.percent_decimals,
    mean_decimals: settings.mean_decimals,
    scale_box: settings.scale_box,
    show_counts: settings.show_counts,
    row_percents: settings.row_percents,
    table_percents: settings.table_percents,
    show_charts: settings.show_charts,
    secondary_confidence_level: settings.secondary_confidence_level ?? null,
    overall_tests: settings.overall_tests,
  };
}

async function patchReportSettings(partial, { toast = null } = {}) {
  const payload = reportSettingsPayload({ ...configuredReportSettings(), ...partial });
  currentProject = await api(`/api/projects/${currentProject.id}/report-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  renderProject();
  if (currentView === "reports") void loadReportPreflight();
  if (toast) showToast(toast);
  return currentProject;
}

// Первая волна в списке — значение по умолчанию: контрольное сравнение без
// выбранной волны сервер не примет.
function firstWaveValue() {
  const waveQuestion = configuredQuestions().find(question => question.role === "wave");
  const variable = waveQuestion
    ? currentProject.inspection.variables
      .find(item => item.name === waveQuestion.source_variables?.[0])
    : null;
  return variable?.value_labels?.[0]?.value ?? null;
}

function statPatch(name, value) {
  if (name === "scheme") {
    return value === "off"
      ? { compare_to_total: false }
      : { compare_to_total: true, compare_target: value };
  }
  if (name === "confidence") return { confidence_level: Number(value) };
  if (name === "secondary") return { secondary_confidence_level: value ? Number(value) : null };
  if (name === "overall") return { overall_tests: value === "on" };
  if (name === "pairwise") return { compare_pairwise: value === "on" };
  if (name === "bonferroni") return { bonferroni: value === "on" };
  if (name === "pvalues") return { show_p_values: value === "on" };
  if (name === "profile") return { ...outputProfiles[value].settings };
  if (name === "percent-decimals") return { percent_decimals: Number(value) };
  if (name === "mean-decimals") return { mean_decimals: Number(value) };
  if (name === "scale-box") return { scale_box: Number(value) };
  if (name === "counts") return { show_counts: value === "on" };
  if (name === "row-percents") return { row_percents: value === "on" };
  if (name === "table-percents") return { table_percents: value === "on" };
  if (name === "charts") return { show_charts: value === "on" };
  if (name.startsWith("scale:") || name.startsWith("numeric:")) {
    const [group, metric] = name.split(":");
    const key = `${group}_metrics`;
    const options = group === "scale" ? scaleMetricOptions : numericMetricOptions;
    const chosen = new Set(configuredReportSettings()[key]);
    if (value === "on") chosen.add(metric);
    else chosen.delete(metric);
    if (!chosen.size) {
      alert(group === "scale"
        ? "Оставьте для шкал хотя бы один показатель."
        : "Оставьте для числовых вопросов хотя бы один показатель.");
      return null;
    }
    return { [key]: options.map(option => option.value).filter(item => chosen.has(item)) };
  }
  if (name === "wave") {
    return value === "control"
      ? {
        wave_comparison: "control",
        wave_control_value: configuredReportSettings().wave_control_value ?? firstWaveValue(),
      }
      : { wave_comparison: value };
  }
  return {};
}

function flashSaved() {
  const badge = document.querySelector("#stat-saved");
  if (!badge) return;
  badge.hidden = false;
  window.clearTimeout(flashSaved.timer);
  flashSaved.timer = window.setTimeout(() => {
    const current = document.querySelector("#stat-saved");
    if (current) current.hidden = true;
  }, 1800);
}

async function applyStatSetting(partial) {
  try {
    await patchReportSettings(partial);
    flashSaved();
  } catch (error) {
    alert(error.message);
    renderReportBlocks();
  }
}

document.querySelector("#entity-list").addEventListener("change", event => {
  const base = event.target.closest("#stat-minimum-base");
  if (base) {
    const value = Math.min(100000, Math.max(1, Math.round(Number(base.value) || 30)));
    void applyStatSetting({ minimum_base: value });
    return;
  }
  const wave = event.target.closest("#stat-wave-control");
  if (wave) void applyStatSetting({ wave_control_value: JSON.parse(wave.value) });
});

/* ---------------- Поповер выбора ---------------- */

function pickerRows(kind) {
  if (kind === "banner") {
    const active = selectedReportBannerId();
    return [{ value: "", title: "Только Total", note: "без разбивки", checked: !active }].concat(
      configuredBanners().map(banner => ({
        value: banner.id,
        title: banner.name,
        note: `${plural(banner.blocks.length, "блок", "блока", "блоков")} · ${plural(reportBannerColumnCount(banner), "колонка", "колонки", "колонок")}`,
        checked: banner.id === active,
        edit: { kind: "banner", id: banner.id },
      }))
    );
  }
  if (kind === "filter") {
    const active = selectedReportFilterId();
    const total = currentProject.inspection.row_count;
    return [{
      value: "", title: "Все респонденты",
      note: `без общего фильтра · ${total.toLocaleString("ru-RU")}`,
      checked: !active,
    }].concat(
      configuredFilters().map(filter => {
        const preview = filterPreviewCache.get(filterPreviewKey(filter));
        const sample = preview ? preview.selected.toLocaleString("ru-RU") : "…";
        return {
          value: filter.id,
          title: filter.name,
          note: `${plural(countFilterConditions(filter.rule), "условие", "условия", "условий")} · выборка ${sample}`,
          checked: filter.id === active,
          edit: { kind: "filter", id: filter.id },
        };
      })
    );
  }
  const settings = configuredReportSettings();
  const selection = settings.calculated_weight_id
    ? `calculated:${settings.calculated_weight_id}`
    : settings.weight_variable ? `ready:${settings.weight_variable}` : "";
  return [{ value: "", title: "Без веса", note: "невзвешенные показатели", checked: !selection }]
    .concat(declaredWeightVariables().map(variable => ({
      value: `ready:${variable.name}`,
      title: variable.name,
      note: `готовый из массива — ${variable.label}`,
      checked: selection === `ready:${variable.name}`,
    })))
    .concat(configuredWeights().map(weight => ({
      value: `calculated:${weight.id}`,
      title: weight.name,
      note: `raking / IPF · ${plural(weight.dimensions.length, "распределение", "распределения", "распределений")}`,
      checked: selection === `calculated:${weight.id}`,
      edit: { kind: "weight", id: weight.id },
    })));
}

const pickerHeads = {
  banner: { caption: "Баннеры проекта", create: "+ Новый баннер" },
  filter: { caption: "Базы и фильтры", create: "+ Новое правило" },
  weight: { caption: "Веса проекта", create: "+ Новый вес (raking)" },
};

function renderPicker(kind) {
  const head = pickerHeads[kind];
  const rows = pickerRows(kind);
  const items = rows.map(row => {
    return `<div class="picker-row ${row.checked ? "checked" : ""}">
      <button type="button" class="picker-pick" data-pick="${escapeAttribute(row.value)}" aria-checked="${row.checked}" role="radio">
        <span class="dot" aria-hidden="true"></span>
        <span class="picker-text"><strong>${escapeHtml(row.title)}</strong><small>${escapeHtml(row.note)}</small></span>
      </button>
      ${row.edit ? `<button type="button" class="picker-edit" data-edit="${row.edit.kind}" data-id="${escapeAttribute(row.edit.id)}">править</button>` : ""}
    </div>`;
  }).join("");
  return `<div class="picker-head"><strong>${escapeHtml(head.caption)}</strong><span>${rows.length - 1}</span></div>
    <div class="picker-list" role="radiogroup">${items}</div>
    <div class="picker-foot"><button type="button" class="btn ghost compact" data-new="${kind}">${escapeHtml(head.create)}</button></div>`;
}

function openPicker(kind, anchor) {
  if (pickerKind === kind && !pickerElement.hidden) {
    closePicker();
    return;
  }
  closePicker();
  pickerKind = kind;
  pickerElement.innerHTML = renderPicker(kind);
  pickerElement.hidden = false;
  const rect = anchor.getBoundingClientRect();
  const width = pickerElement.offsetWidth;
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
  pickerElement.style.top = `${Math.round(rect.bottom + 6)}px`;
  pickerElement.style.left = `${Math.round(left)}px`;
  anchor.setAttribute("aria-expanded", "true");
}

function closePicker() {
  if (!pickerElement || pickerElement.hidden) return;
  pickerElement.hidden = true;
  pickerElement.innerHTML = "";
  pickerKind = null;
  document.querySelectorAll("[data-picker]").forEach(button => button.setAttribute("aria-expanded", "false"));
}

function openEntityEditor(kind, id = null) {
  if (kind === "banner") openBanner(id);
  else if (kind === "filter") openFilter(id);
  else if (kind === "weight") openWeight(id);
  else if (kind === "recoding") openRecoding(id);
}

async function applyPick(kind, value) {
  const anchor = document.querySelector(`[data-picker="${kind}"]`);
  closePicker();
  if (kind === "banner") await assignReportBanner(value || null, anchor);
  else if (kind === "filter") await assignReportFilter(value || null, anchor);
  else await assignReportWeight(value);
}

// Вес живёт в report_settings целиком, поэтому переключение отправляет
// текущие настройки с заменённым весом: сервер заодно проверит пригодность.
async function assignReportWeight(value) {
  const payload = reportSettingsPayload({
    ...configuredReportSettings(),
    weight_variable: value.startsWith("ready:") ? value.slice(6) : null,
    calculated_weight_id: value.startsWith("calculated:") ? value.slice(11) : null,
  });
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/report-settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderProject();
    showToast(value ? "Вес применён к отчёту" : "Отчёт считается без веса");
  } catch (error) {
    alert(error.message);
  }
}

pickerElement.addEventListener("click", event => {
  const editButton = event.target.closest("[data-edit]");
  if (editButton) {
    closePicker();
    openEntityEditor(editButton.dataset.edit, editButton.dataset.id);
    return;
  }
  const newButton = event.target.closest("[data-new]");
  if (newButton) {
    const kind = newButton.dataset.new;
    closePicker();
    openEntityEditor(kind);
    return;
  }
  const pick = event.target.closest("[data-pick]");
  if (pick) void applyPick(pickerKind, pick.dataset.pick);
});

document.addEventListener("click", event => {
  if (pickerElement.hidden) return;
  if (event.target.closest("#picker") || event.target.closest("[data-picker]")) return;
  closePicker();
});
window.addEventListener("resize", closePicker);

/* ---------------- Полоса запуска ---------------- */

async function loadReportPreflight() {
  if (!currentProject) return;
  const container = document.querySelector("#report-preflight");
  const revision = currentProject.configuration?.revision ?? null;
  if (preflightCache.report && preflightCache.revision === revision) {
    renderLaunchStatus(preflightCache.report);
    return;
  }
  container.innerHTML = '<span class="pf">Проверяем настройки отчёта…</span>';
  const projectId = currentProject.id;
  try {
    const report = await api(`/api/projects/${projectId}/reports/preflight`);
    preflightCache = { revision, report };
    if (currentProject?.id === projectId && currentView === "reports") renderLaunchStatus(report);
  } catch (error) {
    container.innerHTML = `<span class="pf error"><i></i>${escapeHtml(error.message)}</span>`;
  }
}

// История запусков — индекс неизменных сборок проекта. Кэшируется на проект
// и сбрасывается после каждой сборки: только тогда в ней что-то меняется.
const runHistoryCache = new Map();

async function loadRunHistory(force = false) {
  if (!currentProject) return;
  const projectId = currentProject.id;
  if (!force && runHistoryCache.has(projectId)) {
    renderRunHistory(runHistoryCache.get(projectId));
    return;
  }
  try {
    const history = await api(`/api/projects/${projectId}/reports/history`);
    runHistoryCache.set(projectId, history.runs);
    if (currentProject?.id === projectId && currentView === "reports") renderRunHistory(history.runs);
  } catch (error) {
    const box = document.querySelector("#report-runs");
    if (box) box.innerHTML = `<p class="runs-empty">${escapeHtml(error.message)}</p>`;
  }
}

function renderRunHistory(runs) {
  const box = document.querySelector("#report-runs");
  if (!box) return;
  if (!runs.length) {
    box.innerHTML = '<p class="runs-empty">Отчёт ещё не собирался. Каждая сборка появится здесь и останется доступной.</p>';
    return;
  }
  box.innerHTML = runs.map(run => {
    const when = new Date(run.created_at).toLocaleString("ru-RU", {
      day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
    });
    const parts = [`ревизия <b>${escapeHtml(run.configuration_revision)}</b>`];
    const summary = run.summary;
    if (summary) {
      parts.push(escapeHtml(plural(summary.questions, "вопрос", "вопроса", "вопросов")));
      parts.push(summary.banner ? `баннер «${escapeHtml(summary.banner)}»` : "только Total");
      if (summary.filter) parts.push(`фильтр «${escapeHtml(summary.filter)}»`);
      if (summary.weight) parts.push(`вес ${escapeHtml(summary.weight)}`);
    }
    const current = run.current ? '<span class="run-current">текущие настройки</span>' : "";
    return `<div class="run">
      <time datetime="${escapeAttribute(run.created_at)}">${escapeHtml(when)}${current}</time>
      <span class="run-meta">${parts.join(" · ")}</span>
      <span class="run-links"><a href="${escapeAttribute(run.downloads.topline)}">Excel</a><a href="${escapeAttribute(run.downloads.statistics)}">statistics.txt</a></span>
    </div>`;
  }).join("");
}

function renderLaunchStatus(report) {
  const container = document.querySelector("#report-preflight");
  const rows = [
    ...(report.errors || []).map(item => ({ item, kind: "error" })),
    ...(report.warnings || []).map(item => ({ item, kind: "warn" })),
  ];
  container.innerHTML = rows.length
    ? rows.map(({ item, kind }) => `<span class="pf ${kind}"><i></i>${escapeHtml(item.message)}</span>`).join("")
    : '<span class="pf ok"><i></i>Проверка пройдена: отчёт можно собирать</span>';
  document.querySelector("#launch-report").disabled = !report.can_prepare;
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

/* Массовые операции над вопросами. Выбор живёт на экране; правка уходит
   одним запросом и одной ревизией, всё или ничего. */
const selectedQuestionCodes = new Set();

// Последняя массовая правка: прежние значения полей по вопросам, чтобы
// «Отменить» вернул каждому своё. Подтверждение распознавания не отменяется.
let lastBulkUndo = null;
const BULK_FIELDS = ["role", "question_type", "base_filter_id", "included_in_report"];

function updateBulkBar() {
  const known = new Set(configuredQuestions().map(question => question.code));
  [...selectedQuestionCodes].forEach(code => { if (!known.has(code)) selectedQuestionCodes.delete(code); });
  const bar = document.querySelector("#bulk-bar");
  bar.hidden = selectedQuestionCodes.size === 0 && !lastBulkUndo;
  // Полоса действий занимает место кнопки формулы: вместе ряд не помещается.
  document.querySelector("#new-formula").hidden = !bar.hidden;
  document.querySelector("#bulk-count").textContent = `Выбрано: ${selectedQuestionCodes.size}`;
  document.querySelector("#bulk-undo").hidden = !lastBulkUndo;
  const base = document.querySelector("#bulk-base");
  base.innerHTML = '<option value="">База…</option><option value="standard">Стандартная база</option>'
    + configuredFilters().map(filter => `<option value="${escapeAttribute(filter.id)}">${escapeHtml(filter.name)}</option>`).join("");
  document.querySelectorAll("#bulk-bar button:not([data-bulk=undo]):not([data-bulk=clear]), #bulk-bar select")
    .forEach(control => { control.disabled = selectedQuestionCodes.size === 0; });
}

async function patchQuestions(body) {
  return api(`/api/projects/${currentProject.id}/questions`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function bulkSnapshot(codes, changed) {
  // Роль волны или веса снимает вопрос с отчёта, поэтому включение
  // запоминается при любой правке.
  const fields = BULK_FIELDS.filter(field => changed.includes(field) || field === "included_in_report");
  const byCode = new Map(configuredQuestions().map(question => [question.code, question]));
  return codes.map(code => {
    const question = byCode.get(code);
    return Object.fromEntries([["code", code], ...fields.map(field => [field, question[field] ?? null])]);
  });
}

async function undoBulk(snapshot) {
  // Возвращаем поле за полем, группируя вопросы с одинаковым прежним значением:
  // сначала роль и тип, потом база и включение, которое роль могла сбросить.
  for (const field of BULK_FIELDS) {
    const groups = new Map();
    snapshot.filter(item => field in item).forEach(item => {
      const key = JSON.stringify(item[field]);
      groups.set(key, [...(groups.get(key) || []), item.code]);
    });
    for (const [key, codes] of groups) {
      currentProject = await patchQuestions({ codes, [field]: JSON.parse(key) });
    }
  }
}

// Флажок строки не должен открывать карточку вопроса: щелчок останавливается
// на фазе перехвата, до обработчиков строки.
document.querySelector("#table-body").addEventListener("click", event => {
  if (event.target.closest(".select-cell")) event.stopPropagation();
}, true);
document.querySelector("#table-body").addEventListener("change", event => {
  const box = event.target.closest(".select-question");
  if (!box) return;
  if (box.checked) selectedQuestionCodes.add(box.dataset.selectCode);
  else selectedQuestionCodes.delete(box.dataset.selectCode);
  const all = document.querySelector("#select-all-questions");
  if (all) {
    const boxes = [...document.querySelectorAll("#table-body .select-question")];
    all.checked = boxes.length > 0 && boxes.every(item => item.checked);
  }
  updateBulkBar();
});
document.querySelector("#table-head").addEventListener("change", event => {
  if (event.target.id !== "select-all-questions") return;
  document.querySelectorAll("#table-body .select-question").forEach(box => {
    box.checked = event.target.checked;
    if (box.checked) selectedQuestionCodes.add(box.dataset.selectCode);
    else selectedQuestionCodes.delete(box.dataset.selectCode);
  });
  updateBulkBar();
});
document.querySelector("#bulk-bar").addEventListener("click", async event => {
  const button = event.target.closest("[data-bulk]");
  if (!button || !currentProject) return;
  const action = button.dataset.bulk;
  if (action === "clear") {
    selectedQuestionCodes.clear();
    renderTable();
    updateBulkBar();
    return;
  }
  if (action === "undo") {
    const snapshot = lastBulkUndo;
    lastBulkUndo = null;
    button.disabled = true;
    try {
      await undoBulk(snapshot);
      showToast("Массовая правка отменена");
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
      renderProject();
      updateBulkBar();
    }
    return;
  }
  const body = { codes: [...selectedQuestionCodes] };
  if (action === "include") body.included_in_report = true;
  if (action === "exclude") body.included_in_report = false;
  if (action === "confirm") body.confirm_review = true;
  button.disabled = true;
  await runBulk(body, {
    include: "В отчёт",
    exclude: "Исключено",
    confirm: "Распознавание подтверждено",
  }[action]);
  button.disabled = false;
});

async function runBulk(body, caption) {
  const changed = Object.keys(body).filter(field => BULK_FIELDS.includes(field));
  const snapshot = changed.length ? bulkSnapshot(body.codes, changed) : null;
  try {
    currentProject = await patchQuestions(body);
    lastBulkUndo = snapshot;
    renderProject();
    showToast(`${caption}: ${plural(body.codes.length, "вопрос", "вопроса", "вопросов")}`);
  } catch (error) {
    alert(error.message);
  } finally {
    updateBulkBar();
  }
}

document.querySelector("#bulk-bar").addEventListener("change", async event => {
  const select = event.target.closest(".bulk-select");
  if (!select || !select.value || !currentProject) return;
  const body = { codes: [...selectedQuestionCodes] };
  let caption;
  if (select.id === "bulk-type") {
    body.question_type = select.value;
    caption = `Тип «${typeLabels[select.value]}»`;
  } else if (select.id === "bulk-role") {
    body.role = select.value;
    caption = `Роль «${select.selectedOptions[0].textContent}»`;
  } else {
    body.base_filter_id = select.value === "standard" ? null : select.value;
    caption = `База «${select.selectedOptions[0].textContent}»`;
  }
  select.value = "";
  await runBulk(body, caption);
});

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
    .filter(item => ["single_choice", "scale", "numeric", "multiple_choice_dichotomy"].includes(item.question_type));
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

/* Категории блока баннера: порядок, подпись и скрытие в этом баннере, без
   новой перекодировки. Список грузится при раскрытии — с базами. */
async function loadBannerCategories(details) {
  const block = details.closest(".banner-block");
  const level = Number(details.dataset.level);
  const select = block.querySelector(level ? ".banner-source-second" : ".banner-source-first");
  const list = details.querySelector(".banner-category-list");
  if (!select.value) {
    list.innerHTML = '<p class="muted">Сначала выберите переменную.</p>';
    return;
  }
  if (list.dataset.source === select.value) return;
  list.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const options = await api(`/api/projects/${currentProject.id}/banners/source-categories`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parseBannerSource(select.value)),
    });
    const settings = block.bannerCategories[level] || [];
    const byKey = new Map(options.categories.map(item => [item.key, item]));
    const ordered = [
      ...settings.filter(item => byKey.has(item.key)).map(item => ({ ...byKey.get(item.key), setting: item })),
      ...options.categories.filter(item => !settings.some(setting => setting.key === item.key)).map(item => ({ ...item, setting: null })),
    ];
    list.dataset.source = select.value;
    list.innerHTML = ordered.map(item => `
      <div class="banner-category" data-key="${escapeAttribute(item.key)}">
        <input type="checkbox" class="banner-category-shown" ${item.setting?.hidden ? "" : "checked"} aria-label="Показывать «${escapeAttribute(item.label)}»" />
        <input class="banner-category-label" value="${escapeAttribute(item.setting?.label || "")}" placeholder="${escapeAttribute(item.label)}" aria-label="Подпись колонки" />
        <em>${item.base.toLocaleString("ru-RU")}</em>
        <button type="button" class="icon-button" data-move-category="-1" aria-label="Выше">↑</button>
        <button type="button" class="icon-button" data-move-category="1" aria-label="Ниже">↓</button>
      </div>`).join("") || '<p class="muted">Категорий нет.</p>';
  } catch (error) {
    list.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

function collectBannerCategories(block, level) {
  const list = block.querySelectorAll(".banner-category-list")[level];
  const rows = [...list.querySelectorAll(".banner-category")];
  if (!list.dataset.source) return block.bannerCategories[level] || null;
  const settings = rows.map(row => ({
    key: row.dataset.key,
    label: row.querySelector(".banner-category-label").value.trim() || null,
    hidden: !row.querySelector(".banner-category-shown").checked,
  }));
  // Список, не отличающийся от данных, не сохраняем: так в баннер не попадает
  // замороженный порядок, и новые категории встают на свои места.
  const untouched = settings.every(item => !item.label && !item.hidden)
    && list.dataset.initialOrder === rows.map(row => row.dataset.key).join("\n")
    && !block.bannerCategories[level];
  return untouched ? null : settings;
}

document.querySelector("#banner-block-list").addEventListener("toggle", event => {
  const details = event.target;
  if (!details.matches?.(".banner-categories") || !details.open) return;
  void loadBannerCategories(details).then(() => {
    const list = details.querySelector(".banner-category-list");
    if (list.dataset.initialOrder === undefined) {
      list.dataset.initialOrder = [...list.querySelectorAll(".banner-category")].map(row => row.dataset.key).join("\n");
    }
  });
}, true);

document.querySelector("#banner-block-list").addEventListener("click", event => {
  const button = event.target.closest("[data-move-category]");
  if (!button) return;
  const row = button.closest(".banner-category");
  const step = Number(button.dataset.moveCategory);
  const sibling = step < 0 ? row.previousElementSibling : row.nextElementSibling;
  if (!sibling) return;
  if (step < 0) sibling.before(row);
  else sibling.after(row);
  setBannerFormDirty(true);
});

document.querySelector("#banner-block-list").addEventListener("change", event => {
  const select = event.target.closest(".banner-source-first, .banner-source-second");
  if (!select) return;
  const block = select.closest(".banner-block");
  const level = select.matches(".banner-source-second") ? 1 : 0;
  // Другая переменная — другие категории: прежние настройки к ней не относятся.
  block.bannerCategories[level] = null;
  const details = block.querySelectorAll(".banner-categories")[level];
  const list = details.querySelector(".banner-category-list");
  list.innerHTML = "";
  delete list.dataset.source;
  delete list.dataset.initialOrder;
  details.open = false;
  if (level === 1) details.hidden = !select.value;
});

function bannerSourceOptions(selected, allowEmpty) {
  const selectedValue = selected ? `${selected.kind}:${selected.ref}` : "";
  const options = [];
  if (allowEmpty) options.push('<option value="">Без вложения</option>');
  configuredQuestions()
    .filter(item => (item.question_type === "single_choice" && item.source_variables.length === 1)
      || item.question_type === "multiple_choice_dichotomy")
    .forEach(item => options.push(`<option value="question:${escapeAttribute(item.code)}" ${selectedValue === `question:${item.code}` ? "selected" : ""}>${escapeHtml(item.code)} — ${escapeHtml(item.label)}</option>`));
  configuredRecodings().forEach(item => options.push(`<option value="recoding:${item.id}" ${selectedValue === `recoding:${item.id}` ? "selected" : ""}>↳ ${escapeHtml(item.code)} — ${escapeHtml(item.name)}</option>`));
  return options.join("");
}

function collectBannerBlocks() {
  const elements = [...document.querySelectorAll("#banner-block-list .banner-block")];
  if (!elements.length) throw new Error("Добавьте хотя бы один блок баннера.");
  return elements.map(element => {
    const first = parseBannerSource(element.querySelector(".banner-source-first").value);
    const secondValue = element.querySelector(".banner-source-second").value;
    const firstCategories = collectBannerCategories(element, 0);
    if (firstCategories) first.categories = firstCategories;
    const sources = [first];
    if (secondValue) {
      const second = parseBannerSource(secondValue);
      const secondCategories = collectBannerCategories(element, 1);
      if (secondCategories) second.categories = secondCategories;
      sources.push(second);
    }
    return {
      label: element.querySelector(".banner-block-label").value.trim() || null,
      sources,
    };
  });
}

function parseBannerSource(value) {
  const separator = value.indexOf(":");
  if (separator < 1) throw new Error("Выберите источник баннера.");
  return { kind: value.slice(0, separator), ref: value.slice(separator + 1) };
}

function bannerSourceLabel(source) {
  if (source.kind === "question") return findQuestion(source.ref)?.label || source.ref;
  return configuredRecodings().find(item => item.id === source.ref)?.name || source.ref;
}

async function loadBannerPreview() {
  if (!currentProject || !currentBannerId) return;
  const container = document.querySelector("#banner-preview");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const preview = await api(`/api/projects/${currentProject.id}/banners/${currentBannerId}/preview`);
    container.innerHTML = renderBannerPreview(preview);
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

function renderBannerPreview(preview) {
  const minimumBase = configuredReportSettings().minimum_base;
  document.querySelector("#banner-preview-count").textContent = `${preview.columns.length} колонок`;
  // Перекрытие multiple в баннере видно числом до отчёта, а не по буквам в нём.
  const overlaps = (preview.overlaps || []).map(item => `
    <p class="banner-overlap">«${escapeHtml(item.block)}»: колонки пересекаются у ${item.respondents.toLocaleString("ru-RU")} респондентов — внутри блока сравнение только с остальными, без попарных букв.</p>`).join("");
  return `${overlaps}<div class="col-preview">${preview.columns.map((column, index) => {
    const label = index === 0 ? "Total" : `${column.block ? `${column.block} · ` : ""}${column.label}`;
    const smallBase = column.base > 0 && column.base < minimumBase;
    return `<div class="col-line ${index === 0 ? "total" : ""} ${smallBase ? "small-base" : ""}"><span title="${escapeAttribute(label)}">${escapeHtml(label)}</span><em>База ${column.base.toLocaleString("ru-RU")}</em></div>`;
  }).join("")}</div>`;
}

async function deleteBanner() {
  if (!currentBannerId || !confirm("Удалить этот баннер?")) return;
  const errorBox = document.querySelector("#banner-error");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/banners/${currentBannerId}`, { method: "DELETE" });
    setBannerFormDirty(false);
    closeBanner();
    renderProject();
    showToast("Баннер удалён");
  } catch (error) {
    showError(errorBox, error);
  }
}

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
  if (document.querySelector("#recode-mode").value === "categories") {
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
  // У логической переменной нет одной исходной переменной: её категории — правила.
  document.querySelector("#recode-source-field").hidden = mode === "conditions";
  document.querySelector("#recode-source").disabled = mode === "conditions";
}

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
  const logic = configuredRecodings().filter(item => item.mode === "conditions");
  select.innerHTML = `<option value="">${logic.length ? `Логические переменные · ${logic.length}` : "Логические переменные"}</option>`
    + logic.map(item => `<option value="${escapeAttribute(item.id)}">${escapeHtml(item.code)} — ${escapeHtml(item.name)}</option>`).join("")
    + '<option value="new">+ Новая логическая переменная</option>';
  select.value = "";
}

document.querySelector("#logic-variables").addEventListener("change", event => {
  const value = event.target.value;
  event.target.value = "";
  if (!value) return;
  if (value === "new") openRecoding(null, { mode: "conditions" });
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
    <div><span>${escapeHtml(row.label)}</span><strong>${row.count}</strong><em>${formatPercent(row.percent_total)}</em><em>${escapeHtml(preview.mode === "categories" ? `${row.source_values.length} знач.` : preview.mode === "conditions" ? "по правилу" : formatRange(row))}</em></div>`).join("")}</div>`;
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

function openQuestion(code) {
  if (!confirmDiscard(openInspectorPanel())) return;
  currentQuestionCode = code;
  fillEditor(findQuestion(code));
  showInspector(editor);
  renderTable();
  loadPreview();
}

function fillEditor(question) {
  if (!question) return;
  const grouped = question.source_variables.length > 1;
  setHeadingText(document.querySelector("#editor-code"), `${question.code} — ${question.label}`);
  document.querySelector("#question-label-caption").textContent = grouped
    ? "Название блока для Excel"
    : "Название для отчёта";
  document.querySelector("#question-label-help").textContent = grouped
    ? "Общий заголовок для всех пунктов блока в содержании и топлайне Excel."
    : "Это название попадёт в содержание и топлайн Excel.";
  document.querySelector("#question-label").value = question.label;
  document.querySelector("#question-type").value = question.question_type;
  document.querySelector("#question-role").value = question.role;
  document.querySelector("#question-included").checked = question.included_in_report;
  document.querySelector("#question-base-filter").innerHTML = '<option value="">Стандартная база</option>' + configuredFilters().map(filter => `<option value="${filter.id}" ${question.base_filter_id === filter.id ? "selected" : ""}>${escapeHtml(filter.name)}</option>`).join("");
  document.querySelector("#editor-error").hidden = true;
  renderQuestionReview(question);
  renderQuestionMembers(question);
  renderSpecialAnswers(question);
  renderSpecialMetric(question);
  renderQuestionRecodings(question);
  renderQuestionFormula(question);
}

function renderQuestionReview(question) {
  const notice = document.querySelector("#question-review");
  const pending = questionStatus(question) === "review";
  notice.hidden = !pending;
  notice.innerHTML = pending
    ? `<p class="question-review-title">Распознавание не подтверждено</p>${(question.warnings || []).map(item => `<p>⚑ ${escapeHtml(item)}</p>`).join("")}<p class="question-review-hint">Проверьте тип и состав. Сохранение подтверждает распознавание.</p>`
    : "";
  document.querySelector("#save-question").textContent = questionSaveLabel(question);
}

function questionSaveLabel(question) {
  return question && questionStatus(question) === "review" ? "Подтвердить и сохранить" : "Сохранить";
}

function recodingsForQuestion(question) {
  const sources = new Set(question?.source_variables || []);
  return configuredRecodings().filter(item => sources.has(item.source_variable));
}

// Группировка строится по одному столбцу: у блока из нескольких столбцов
// источник неоднозначен, поэтому там её не предлагаем.
function questionRecodeSource(question) {
  if (!question || question.source_variables.length !== 1) return null;
  const variable = currentProject.inspection.variables
    .find(item => item.name === question.source_variables[0]);
  if (!variable) return null;
  // Одиночный выбор с подписями группируют ответами, даже если коды числовые:
  // диапазоны по кодам «1–2» для марок и регионов смысла не имеют.
  const labelled = variable.value_labels?.length > 0;
  if (labelled && question.question_type === "single_choice") return { variable, mode: "categories" };
  if (variable.storage_type === "numeric") return { variable, mode: "ranges" };
  return labelled ? { variable, mode: "categories" } : null;
}

function renderQuestionRecodings(question) {
  const section = document.querySelector("#question-recodings");
  const recodings = recodingsForQuestion(question);
  const source = questionRecodeSource(question);
  section.hidden = !recodings.length && !source;
  if (section.hidden) {
    section.innerHTML = "";
    return;
  }
  const rows = recodings.map(recoding => `
    <button type="button" class="recode-row" data-open-recoding="${escapeAttribute(recoding.id)}">
      <span class="recode-row-text">
        <strong>${escapeHtml(recoding.name)}</strong>
        <small><code>${escapeHtml(recoding.code)}</code> ${recoding.mode === "categories" ? "объединение категорий" : recoding.mode === "conditions" ? "логическая переменная" : "числовые диапазоны"} · ${plural(recoding.categories.length, "категория", "категории", "категорий")}</small>
      </span>
      <span class="recode-row-go" aria-hidden="true">→</span>
    </button>`).join("");
  const add = source
    ? `<button type="button" class="secondary compact-button add-row-button"
        data-new-recoding="${escapeAttribute(source.variable.name)}" data-mode="${source.mode}"
        data-name="${escapeAttribute(question.label)}">+ Новая группировка</button>`
    : "";
  section.innerHTML = `<div class="grp-cap"><strong>Группировки</strong><small>В колонки баннера и условия фильтра</small></div>${rows}${add}`;
}

document.querySelector("#question-recodings").addEventListener("click", event => {
  const from = currentQuestionCode;
  const open = event.target.closest("[data-open-recoding]");
  if (open) {
    openRecoding(open.dataset.openRecoding, { returnTo: from });
    return;
  }
  const create = event.target.closest("[data-new-recoding]");
  if (!create) return;
  openRecoding(null, {
    returnTo: from,
    sourceVariable: create.dataset.newRecoding,
    mode: create.dataset.mode,
    suggestName: `${create.dataset.name} — группы`.slice(0, 500),
  });
});

function renderSpecialMetric(question) {
  const label = document.querySelector("#special-metric-label");
  const available = question.question_type === "scale" && question.source_variables.length === 1;
  label.hidden = !available;
  document.querySelector("#question-special-metric").value = available
    ? (question.special_metric || "none")
    : "none";
}

function renderQuestionMembers(question) {
  const container = document.querySelector("#question-members");
  const items = question.items?.length
    ? question.items
    : question.source_variables.map(name => ({
      variable: name,
      label: currentProject.inspection.variables.find(item => item.name === name)?.label || name,
    }));
  container.hidden = items.length < 2;
  container.innerHTML = items.length < 2 ? "" : `<div><strong>Состав блока · ${items.length}</strong><small>Общие настройки выше применяются ко всем пунктам.</small></div><div class="member-list">${items.map(item => `<p><code>${escapeHtml(item.variable)}</code><span>${escapeHtml(item.label)}</span></p>`).join("")}</div>`;
}

function renderSpecialAnswers(question) {
  const section = document.querySelector("#special-answers");
  const list = document.querySelector("#special-answer-list");
  if (question.question_type === "multiple_choice_dichotomy") {
    const items = question.items || [];
    section.hidden = items.length === 0;
    list.innerHTML = items.map(item => `<label class="checkbox"><input type="checkbox" data-special-item="${escapeAttribute(item.variable)}" ${(question.special_items || []).includes(item.variable) ? "checked" : ""} /> ${escapeHtml(item.label)}</label>`).join("");
    return;
  }
  if (!["scale", "matrix", "single_choice"].includes(question.question_type)) {
    section.hidden = true;
    list.innerHTML = "";
    return;
  }
  const labels = valueLabelsForQuestion(question);
  section.hidden = labels.length === 0;
  list.innerHTML = labels.map(item => `<label class="checkbox"><input type="checkbox" data-special-value="${escapeAttribute(JSON.stringify(item.value))}" ${containsComparable(question.special_values || [], item.value) ? "checked" : ""} /> <code>${escapeHtml(item.value)}</code> ${escapeHtml(item.label)}</label>`).join("");
}

document.querySelector("#find-not-applicable").addEventListener("click", async () => {
  if (!currentProject) return;
  const button = document.querySelector("#find-not-applicable");
  const container = document.querySelector("#not-applicable-groups");
  const errorBox = document.querySelector("#not-applicable-error");
  errorBox.hidden = true;
  openSheet(notApplicableEditor);
  container.innerHTML = '<p class="muted">Ищем…</p>';
  setBusy(button, true, "Ищем…");
  try {
    const found = await api(`/api/projects/${currentProject.id}/questions/not-applicable-suggestions`);
    container.innerHTML = renderNotApplicableGroups(found.groups || []);
  } catch (error) {
    container.innerHTML = "";
    showError(errorBox, error);
  } finally {
    setBusy(button, false, "Пропуски по анкете");
  }
});

document.querySelector("#close-not-applicable").addEventListener("click", closeSheet);

function renderNotApplicableGroups(groups) {
  if (!groups.length) {
    return '<p class="muted">Кандидатов не нашлось. Если заглушка всё же есть, отметьте её в карточке вопроса — по данным она неотличима от осмысленного значения.</p>';
  }
  return groups.map((group, index) => {
    const codes = [...new Set(group.candidates.map(item => item.value))]
      .map(value => `<code>${escapeHtml(value)}</code>`).join(" ");
    const marked = group.candidates.every(item => item.already_marked);
    const kind = group.candidates.length > 1
      ? `Блок из ${group.candidates.length} вопросов`
      : "Один вопрос";
    const rows = group.candidates.map(item =>
      `<p><code>${escapeHtml(item.question_code)}</code><span title="${escapeAttribute(item.question_label)}">${escapeHtml(item.question_label)}</span></p>`
    ).join("");
    const payload = escapeAttribute(JSON.stringify(
      group.candidates.map(item => ({ code: item.question_code, value: item.value }))
    ));
    return `<article class="na-group ${marked ? "na-marked" : ""}">
      <label class="checkbox na-group-head">
        <input type="checkbox" data-na-group="${index}" data-na-payload="${payload}" ${marked ? "checked" : ""} />
        <span><strong>${kind}</strong> · код ${codes} у одних и тех же <b>${group.respondents.toLocaleString("ru-RU")}</b> чел. (${formatPercent(group.share)})</span>
      </label>
      <div class="na-group-body">${rows}</div>
    </article>`;
  }).join("");
}

document.querySelector("#not-applicable-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = document.querySelector("#apply-not-applicable");
  const errorBox = document.querySelector("#not-applicable-error");
  errorBox.hidden = true;
  // Помечаются выбранные группы, снимается пометка с невыбранных: панель
  // показывает итоговое состояние, а не только добавления.
  const marks = new Map();
  document.querySelectorAll("[data-na-group]").forEach(checkbox => {
    JSON.parse(checkbox.dataset.naPayload).forEach(({ code, value }) => {
      const current = marks.get(code) || new Set();
      if (checkbox.checked) current.add(value);
      marks.set(code, current);
    });
  });
  if (!marks.size) {
    showError(errorBox, new Error("Нечего применять: кандидатов нет."));
    return;
  }
  setBusy(button, true, "Помечаем…");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/questions/not-applicable`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        marks: [...marks].map(([code, values]) => ({ code, values: [...values] })),
        // Лист предлагает только неподписанные коды вне диапазона подписей
        // и показывает, у скольких респондентов они стоят. Отметка группы
        // здесь и есть подтверждение с базой перед глазами.
        confirm_substantive: true,
      }),
    });
    renderProject();
    showToast("Коды помечены");
    closeSheet();
  } catch (error) {
    showError(errorBox, error);
  } finally {
    setBusy(button, false, "Пометить выбранные");
  }
});

// Коды «не применимо» берутся из предпросмотра, а не из value labels: заглушка
// как раз тем и опознаётся, что подписи у неё нет.
function renderNotApplicable(question, preview) {
  const section = document.querySelector("#not-applicable");
  const list = document.querySelector("#not-applicable-list");
  const rows = preview?.rows || [];
  const supported = ["single_choice", "scale"].includes(question.question_type);
  section.hidden = !supported || rows.length === 0;
  if (section.hidden) {
    list.innerHTML = "";
    return;
  }
  list.innerHTML = rows.map(row => {
    const checked = row.is_not_applicable ? "checked" : "";
    const value = escapeAttribute(JSON.stringify(row.value));
    return `<label class="checkbox"><input type="checkbox" data-not-applicable="${value}" ${checked} /> <code>${escapeHtml(row.value)}</code> ${escapeHtml(row.label)} <em>${row.count.toLocaleString("ru-RU")}</em></label>`;
  }).join("");
  loadNotApplicableAssessment();
}

// База до и после и признак «похоже на ответ» считает сервер той же функцией,
// которой он проверяет пометку при сохранении: показанное не расходится
// с тем, что потом отклонят.
let notApplicableAssessmentToken = 0;

document.querySelector("#not-applicable-list").addEventListener("change", loadNotApplicableAssessment);

async function loadNotApplicableAssessment() {
  const container = document.querySelector("#not-applicable-assessment");
  if (!currentProject || !currentQuestionCode || document.querySelector("#not-applicable").hidden) {
    container.innerHTML = "";
    return;
  }
  const token = ++notApplicableAssessmentToken;
  const values = collectNotApplicable().not_applicable_values || [];
  try {
    const assessment = await api(
      `/api/projects/${currentProject.id}/questions/${encodeURIComponent(currentQuestionCode)}/not-applicable/assessment`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      },
    );
    if (token !== notApplicableAssessmentToken) return;
    container.innerHTML = renderNotApplicableAssessment(assessment);
  } catch (error) {
    if (token !== notApplicableAssessmentToken) return;
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

function renderNotApplicableAssessment(assessment) {
  const before = assessment.base_before.toLocaleString("ru-RU");
  const after = assessment.base_after.toLocaleString("ru-RU");
  const base = assessment.base_before === assessment.base_after
    ? `<p class="na-base">Валидная база <b>${before}</b></p>`
    : `<p class="na-base">Валидная база <b>${before}</b> → <b>${after}</b></p>`;
  if (!assessment.requires_confirmation) return base;
  const items = assessment.substantive.map(item => `<p>⚑ ${escapeHtml(item.description)}</p>`).join("");
  return `${base}<div class="inline-warnings na-confirm">${items}
    <label class="checkbox"><input id="not-applicable-confirm" type="checkbox" /> Это пропуск по ветке анкеты, а не ответ</label></div>`;
}

function collectNotApplicable() {
  // Панель скрыта — тип вопроса пометку не поддерживает, и поле не отправляется,
  // чтобы не затереть уже сохранённое значение пустым списком.
  if (document.querySelector("#not-applicable").hidden) return {};
  return {
    not_applicable_values: [...document.querySelectorAll("[data-not-applicable]:checked")]
      .map(item => JSON.parse(item.dataset.notApplicable)),
    // Без отметки сервер отклонит пометку содержательного кода; отметка
    // появляется только тогда, когда оценка нашла такой код.
    confirm_substantive: document.querySelector("#not-applicable-confirm")?.checked || undefined,
  };
}

// NET-группы: объединение ответов отдельной строкой книги. Варианты берутся
// из предпросмотра — тех же строк, что аналитик видит над ними; у матрицы —
// из шкалы первого элемента, она у элементов общая.
const NET_TYPES = ["single_choice", "scale", "multiple_choice_dichotomy", "matrix"];
let netOptions = [];

function renderNets(question, preview) {
  const section = document.querySelector("#question-nets");
  const list = document.querySelector("#net-list");
  netOptions = (preview?.items?.[0]?.rows || preview?.rows || [])
    .map(row => ({ value: row.value, label: row.label }));
  section.hidden = !question || !NET_TYPES.includes(question.question_type) || !netOptions.length;
  list.innerHTML = "";
  if (section.hidden) return;
  (question.nets || []).forEach(net => addNetRow(net));
}

function addNetRow(net = { label: "", values: [] }) {
  const row = document.createElement("div");
  row.className = "net-row";
  row.innerHTML = `<div class="net-head">
      <input class="net-label" maxlength="250" placeholder="Например, Довольны" aria-label="Название NET-группы" value="${escapeAttribute(net.label)}" />
      <button type="button" class="icon-button" data-remove-net aria-label="Удалить NET-группу">×</button>
    </div>
    <div class="net-options">${netOptions.map(option => `<label class="checkbox"><input type="checkbox" data-net-value="${escapeAttribute(JSON.stringify(option.value))}" ${containsComparable(net.values, option.value) ? "checked" : ""} /> ${escapeHtml(option.label)}</label>`).join("")}</div>`;
  document.querySelector("#net-list").append(row);
}

function collectNets() {
  // Панель скрыта — тип вопроса NET не поддерживает, и поле не отправляется,
  // чтобы не стереть сохранённые группы пустым списком.
  if (document.querySelector("#question-nets").hidden) return {};
  const nets = [...document.querySelectorAll("#net-list .net-row")]
    .map(row => ({
      label: row.querySelector(".net-label").value.trim(),
      values: [...row.querySelectorAll("[data-net-value]:checked")].map(input => JSON.parse(input.dataset.netValue)),
    }))
    .filter(net => net.label || net.values.length);
  if (nets.some(net => !net.label || !net.values.length)) {
    throw new Error("У каждой NET-группы должно быть название и хотя бы один ответ.");
  }
  return { nets };
}

document.querySelector("#add-net").addEventListener("click", () => addNetRow());

// Свой набор вывода вопроса. Отметки по умолчанию — набор отчёта: снять
// флажок значит вернуться к нему, а не очистить строки вопроса.
function questionOutputOptions(type) {
  if (type === "scale" || type === "matrix") return scaleMetricOptions;
  if (type === "numeric") return numericMetricOptions;
  return null;
}

function renderQuestionOutput(question) {
  const section = document.querySelector("#question-output");
  const options = question ? questionOutputOptions(question.question_type) : null;
  section.hidden = !options;
  if (!options) return;
  const settings = configuredReportSettings();
  const own = (question.output_metrics || []).length > 0;
  const reportSet = question.question_type === "numeric" ? settings.numeric_metrics : settings.scale_metrics;
  const chosen = own ? question.output_metrics : reportSet;
  document.querySelector("#question-output-own").checked = own;
  const list = document.querySelector("#question-output-list");
  list.hidden = !own;
  list.innerHTML = options.map(option => `<label class="checkbox"><input type="checkbox" data-output-metric="${option.value}" ${chosen.includes(option.value) ? "checked" : ""} /> ${escapeHtml(scaleMetricLabel(option, settings.scale_box))}</label>`).join("");
}

document.querySelector("#question-output-own").addEventListener("change", event => {
  document.querySelector("#question-output-list").hidden = !event.target.checked;
});

function collectQuestionOutput() {
  if (document.querySelector("#question-output").hidden) return {};
  if (!document.querySelector("#question-output-own").checked) return { output_metrics: [] };
  const metrics = [...document.querySelectorAll("[data-output-metric]:checked")].map(input => input.dataset.outputMetric);
  if (!metrics.length) throw new Error("Оставьте в своём наборе вывода хотя бы один показатель.");
  return { output_metrics: metrics };
}
document.querySelector("#net-list").addEventListener("click", event => {
  const remove = event.target.closest("[data-remove-net]");
  if (remove) remove.closest(".net-row").remove();
});

function collectSpecialAnswers() {
  const type = document.querySelector("#question-type").value;
  if (type === "multiple_choice_dichotomy") {
    return {
      special_items: [...document.querySelectorAll("[data-special-item]:checked")].map(item => item.dataset.specialItem),
      special_values: [],
    };
  }
  return {
    special_values: [...document.querySelectorAll("[data-special-value]:checked")].map(item => JSON.parse(item.dataset.specialValue)),
    special_items: [],
  };
}

function valueLabelsForQuestion(question) {
  const seen = new Set();
  const labels = [];
  question.source_variables.forEach(name => {
    const variable = currentProject.inspection.variables.find(item => item.name === name);
    (variable?.value_labels || []).forEach(item => {
      const key = JSON.stringify(item.value);
      if (!seen.has(key)) {
        seen.add(key);
        labels.push(item);
      }
    });
  });
  return labels;
}

function containsComparable(values, expected) {
  return values.some(value => String(value) === String(expected));
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

loadProjects();
void applyRoute();

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

document.querySelector("#new-formula").addEventListener("click", () => openFormula(null));

function openFormula(formulaId) {
  if (!currentProject || !confirmDiscard(openInspectorPanel())) return;
  const formula = formulaId ? configuredFormulas().find(item => item.id === formulaId) : null;
  currentFormulaId = formula?.id || null;
  currentQuestionCode = null;
  showInspector(formulaEditor);
  setHeadingText(document.querySelector("#formula-editor-title"), formula ? formula.name : "Новая");
  const name = document.querySelector("#formula-name");
  name.value = formula?.name || suggestFormulaName();
  name.readOnly = Boolean(formula);
  document.querySelector("#formula-label").value = formula?.label || "";
  document.querySelector("#formula-expression").value = formula?.expression || "";
  document.querySelector("#delete-formula").hidden = !formula;
  document.querySelector("#formula-error").hidden = true;
  document.querySelector("#formula-preview").innerHTML = '<p class="muted">Нажмите «Проверить»: будет видно, у скольких респондентов формула посчиталась и из-за какого поля пусто.</p>';
  renderFormulaVariables();
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
  const numeric = currentProject.inspection.variables
    .filter(item => item.storage_type === "numeric" && !item.formula_id)
    .slice(0, 60);
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

