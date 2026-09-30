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
let dataRowsOffset = 0;
let dataRowsFilterId = "";
let dataRowsRequest = 0;
let dataRowsSearchTimer = null;
const DATA_ROWS_LIMIT = 50;
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
  note_skip_reasons: false,
  minimum_base: 30,
  weight_variable: null,
  calculated_weight_id: null,
  wave_comparison: "none",
  wave_control_value: null,
  scale_metrics: ["distribution", "mean", "top2", "bottom2"],
  ranking_metrics: ["distribution", "mean"],
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

const rankingMetricOptions = [
  { value: "distribution", label: "Распределение мест" },
  { value: "mean", label: "Средний ранг" },
];

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
document.querySelector("#close-editor").addEventListener("click", () => {
  if (confirmDiscard(editor)) closeQuestionEditor();
});
document.querySelector("#refresh-preview").addEventListener("click", loadPreview);
document.querySelector("#refresh-structure").addEventListener("click", refreshStructure);
document.querySelector("#question-type").addEventListener("change", () => {
  document.querySelector("#ranking-encoding-field").hidden = document.querySelector("#question-type").value !== "ranking";
  const question = findQuestion(currentQuestionCode);
  if (question) renderQuestionOutput({
    ...question, question_type: document.querySelector("#question-type").value, output_metrics: [],
  });
  if (document.querySelector("#question-type").value === "ranking") {
    document.querySelector("#question-nets").hidden = true;
  }
  if (question) renderSpecialAnswers({
    ...question,
    question_type: document.querySelector("#question-type").value,
  });
  if (question) renderSpecialMetric({
    ...question,
    question_type: document.querySelector("#question-type").value,
  });
});
document.querySelectorAll("[data-structure-mode]").forEach(button => button.addEventListener("click", () => {
  structureMode = button.dataset.structureMode;
  dataRowsOffset = 0;
  document.querySelectorAll("[data-structure-mode]").forEach(item => item.classList.toggle("active", item === button));
  editor.hidden = true;
  currentQuestionCode = null;
  syncDataRowsControls();
  renderTable();
}));

const structureSearchInput = document.querySelector("#structure-search");
structureSearchInput.addEventListener("input", () => {
  structureSearch = structureSearchInput.value.trim();
  document.querySelector("#structure-search-clear").hidden = !structureSearchInput.value;
  if (structureMode === "rows") {
    dataRowsOffset = 0;
    clearTimeout(dataRowsSearchTimer);
    dataRowsSearchTimer = setTimeout(renderTable, 180);
  } else renderTable();
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
    syncDataRowsControls();
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

function syncDataRowsControls() {
  const rows = structureMode === "rows";
  document.querySelector("#row-view-controls").hidden = !rows;
  document.querySelector("#new-variable").hidden = rows;
  document.querySelector("#structure-search").placeholder = rows
    ? "Столбцы по коду или названию"
    : "Код или название";
  if (!rows || !currentProject) return;
  const filters = configuredFilters();
  if (dataRowsFilterId && !filters.some(item => item.id === dataRowsFilterId)) {
    dataRowsFilterId = "";
  }
  document.querySelector("#row-filter").innerHTML = '<option value="">Все строки</option>'
    + filters.map(item => `<option value="${escapeAttribute(item.id)}" ${item.id === dataRowsFilterId ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
}

document.querySelector("#row-filter").addEventListener("change", event => {
  dataRowsFilterId = event.target.value;
  dataRowsOffset = 0;
  renderTable();
});
document.querySelector("#row-prev").addEventListener("click", () => {
  dataRowsOffset = Math.max(0, dataRowsOffset - DATA_ROWS_LIMIT);
  renderTable();
});
document.querySelector("#row-next").addEventListener("click", () => {
  dataRowsOffset += DATA_ROWS_LIMIT;
  renderTable();
});

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
  // Логические переменные открываются из «Данных»; в других разделах
  // список над окном был чужим и только добавлял шума.
  document.querySelector("#logic-variables").closest(".pill-select").hidden = view !== "data";
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

// Escape закрывает открытый редактор при любой ширине окна (GAP-028): у
// клавиатуры другого короткого пути из панели нет. Несохранённое — с вопросом.
function closeOpenEditor() {
  const open = openEditors();
  if (!open.length) return;
  if (!confirmDiscard(openInspectorPanel())) return;
  open.forEach(([, close]) => close());
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
  if (document.querySelector("dialog[open]")) return;
  closeOpenEditor();
});

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

/* Фокус в редакторах (GAP-028). Открытый редактор получает фокус на первом
   поле, закрытый возвращает его туда, откуда редактор открыли: иначе после
   Escape или «Сохранить» клавиатура оказывалась в начале страницы. */
let inspectorOpener = null;
// Последний фокус вне редакторов: открытие вопроса перерисовывает таблицу
// раньше, чем показывается редактор, и к тому моменту фокус уже на body.
let lastOutsideFocus = null;
document.addEventListener("focusin", event => {
  if (!INSPECTORS.some(item => item.contains(event.target))) lastOutsideFocus = event.target;
});

function showInspector(panel) {
  const previous = INSPECTORS.find(item => !item.hidden) || null;
  if (panel && panel !== previous && !INSPECTORS.some(item => item.contains(document.activeElement))) {
    const active = document.activeElement;
    inspectorOpener = active && active !== document.body ? active : lastOutsideFocus;
  }
  INSPECTORS.forEach(item => { item.hidden = item !== panel; });
  if (panel) markInspectorClean(panel);
  if (panel && panel !== previous) {
    // Поля редактора заполняются следом за показом — фокус ставим после них.
    window.setTimeout(() => {
      if (panel.hidden || panel.contains(document.activeElement)) return;
      const target = panel.querySelector(
        "input:not([type=hidden]):not([disabled]):not([hidden]), select:not([disabled]), textarea:not([disabled])"
      );
      (target && target.offsetParent !== null ? target : panel.querySelector("button"))?.focus();
    }, 0);
  }
}

// Редакторы закрываются разными функциями, и все прячут панель атрибутом
// hidden. Скрытая панель роняет фокус на body — тогда он возвращается туда,
// откуда редактор открыли.
const inspectorFocusObserver = new MutationObserver(records => {
  const closed = records.some(record => record.target.hidden);
  if (!closed || INSPECTORS.some(item => !item.hidden)) return;
  // Браузер снимает фокус со скрытого поля не сразу: фокус внутри уже
  // скрытой панели — тоже потерянный.
  const active = document.activeElement;
  const lost = !active || active === document.body
    || INSPECTORS.some(panel => panel.hidden && panel.contains(active));
  if (!lost) return;
  const opener = inspectorOpener;
  inspectorOpener = null;
  if (opener?.isConnected && opener.offsetParent !== null) {
    opener.focus();
    return;
  }
  // Строку таблицы перерисовали — ищем её заново по коду вопроса.
  const code = opener?.closest?.("tr[data-code]")?.dataset.code;
  if (code) document.querySelector(`#table-body tr[data-code="${CSS.escape(code)}"] .q-title`)?.focus();
});
INSPECTORS.forEach(panel => inspectorFocusObserver.observe(panel, {
  attributes: true, attributeFilter: ["hidden"],
}));

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
// «Данные» и «Отчёты» живут на общем холсте, остальные разделы — своими
// секциями во второй колонке оболочки.
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
  }
  writeRoute();
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
          ...(document.querySelector("#question-type").value === "ranking"
            ? { ranking_encoding: document.querySelector("#ranking-encoding").value } : {}),
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
    // Сохранено: дальше только перерисовка. Пока идёт превью, аналитик уже
    // видит новую таблицу и может открыть другой вопрос — без вопроса о
    // «несохранённых изменениях».
    markInspectorClean(editor);
    renderProject();
    fillEditor(findQuestion(currentQuestionCode));
    await loadPreview();
    showToast("Настройки вопроса сохранены");
  } catch (error) {
    showError(editorError, error);
  } finally {
    setBusy(saveButton, false, questionSaveLabel(findQuestion(currentQuestionCode)));
  }
});

function showProject(project, view = "data") {
  if (!confirmDiscard(openInspectorPanel())) return;
  window.setTimeout(() => refreshProjectHistory(), 0);
  window.setTimeout(() => resumeReportJob(), 0);
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
  dataRowsOffset = 0;
  dataRowsFilterId = "";
  dataRowsRequest += 1;
  resetStructureSearch({ render: false });
  showInspector(null);
  renderSectionHead("data");
  syncSectionChrome("data");
  closeSheet();
  closePicker();
  document.querySelectorAll("[data-structure-mode]").forEach(button => {
    button.classList.toggle("active", button.dataset.structureMode === "questions");
  });
  syncDataRowsControls();
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
  banners() {
    return currentProject?.configuration?.banners || [];
  },
  /* Запись таблицы «Таблиц». Раскладка сохраняется на каждую правку, и
     перерисовывать ради неё весь проект незачем: достаточно принять новую
     ревизию, иначе следующая запись из любого раздела упадёт конфликтом. */
  async saveTables(url, options) {
    const project = await api(url, options);
    currentProject = project;
    return project;
  },
};

// Раздел «Таблицы» берёт список переменных отсюда: своей загрузки у него
// нет, иначе один и тот же проект читался бы дважды. Строками годятся типы,
// которые раскладывает лист книги, колонками — одиночный выбор с подписями
// и группировки.
const TABLE_ROW_TYPES = ["single_choice", "scale", "numeric", "multiple_choice_dichotomy", "multiple_choice_categorical", "matrix", "ranking"];
const MULTIPLE_TYPES = ["multiple_choice_dichotomy", "multiple_choice_categorical"];

// Содержимое пункта для раскрытия в дереве «Таблиц»: у одиночного вопроса и
// шкалы — подписи кодов, у группы (multiple, матрица, ранжирование) — её
// переменные, у перекодировки — категории. Только показ: список ничего не
// выбирает, выбирается пункт целиком.
function tableItemChildren(question, variablesByName) {
  const sources = question.source_variables || [];
  if (sources.length > 1) {
    return sources.map(name => ({ code: name, label: variablesByName.get(name)?.label || name }));
  }
  return (variablesByName.get(sources[0])?.value_labels || [])
    .map(item => ({ code: String(item.value), label: item.label }));
}

function publishVariablesToShell(inspection, questions) {
  const variablesByName = new Map(inspection.variables.map(item => [item.name, item]));
  const questionItems = questions
    .filter(question => question.included_in_report)
    .map(question => {
      const labels = variablesByName.get(question.source_variables?.[0])?.value_labels || [];
      return {
        code: question.code,
        label: question.label,
        type: question.question_type,
        source: { kind: "question", ref: question.code },
        children: tableItemChildren(question, variablesByName),
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
    children: (recoding.categories || []).map((category, index) => ({ code: String(index + 1), label: category.label })),
    canRow: false,
    canCol: true,
  }));
  // Блок баннера раскрывается подписью: своей или именами источников через «×».
  const sourceName = source => (source.kind === "recoding"
    ? configuredRecodings().find(item => item.id === source.ref)?.name
    : questions.find(item => item.code === source.ref)?.label) || source.ref;
  window.Shell.setProjectVariables([...questionItems, ...recodingItems], {
    projectId: currentProject?.id || null,
    filters: configuredFilters().map(item => ({ id: item.id, name: item.name })),
    tableReports: currentProject?.configuration?.table_reports || [],
    banners: configuredBanners().map(item => ({
      id: item.id,
      name: item.name,
      children: (item.blocks || []).map((block, index) => ({
        code: String(index + 1),
        label: block.label || block.sources.map(sourceName).join(" × "),
      })),
    })),
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
  if (currentView === "data" && structureMode === "rows") {
    void renderDataRows();
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
      <td class="question-cell"><button type="button" class="q-title" title="${escapeAttribute(title)}">${escapeHtml(question.label)}</button>${sub ? `<span class="q-sub ${warnings ? "warning" : ""}" title="${escapeAttribute(sub)}">${escapeHtml(sub)}</span>` : ""}</td>
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


// Уровень измерения SPSS — по-русски, как остальной интерфейс.
const measurementLabels = { nominal: "номинальная", ordinal: "порядковая", scale: "количественная", unknown: "—" };

function renderPhysicalVariables() {
    const allVariables = currentProject.inspection.variables;
    const variables = allVariables.filter(variable => matchesStructureSearch(variable.name, variable.label));
    updateStructureSearchCount(variables.length, allVariables.length);
    // Числа — вправо и моноширинными цифрами: их сравнивают столбиком.
    // Заголовки в одну строку: «Валидная база» в две строки поднимала шапку.
    document.querySelector("#table-head").innerHTML = "<th>Имя</th><th class=\"nowrap\">Вопрос</th><th class=\"question-cell\">Метка столбца</th><th>Формат</th><th>Шкала</th><th class=\"num\">Уникальных</th><th class=\"num\">Валидных</th><th class=\"num\">Пропуски</th>";
    if (!variables.length) {
      document.querySelector("#table-body").innerHTML = emptySearchRow(8, "Столбцы не найдены.");
      return;
    }
    document.querySelector("#table-body").innerHTML = variables.map(variable => `
      <tr>
        <td><code>${escapeHtml(variable.name)}</code></td><td>${logicalOwnerButton(variable.name)}</td><td class="question-cell"><strong title="${escapeAttribute(variable.label)}">${escapeHtml(variable.label)}</strong></td>
        <td>${escapeHtml(variable.original_format || variable.storage_type)}</td><td class="nowrap">${escapeHtml(measurementLabels[variable.measurement_level] || variable.measurement_level || "—")}</td>
        <td class="num">${variable.unique_count.toLocaleString("ru-RU")}</td><td class="num">${variable.valid_count.toLocaleString("ru-RU")}</td><td class="num ${variable.missing_count ? "" : "zero"}">${variable.missing_count.toLocaleString("ru-RU")}</td>
      </tr>`).join("");
}

async function renderDataRows() {
  syncDataRowsControls();
  const allVariables = currentProject.inspection.variables;
  const matching = allVariables.filter(variable => matchesStructureSearch(variable.name, variable.label));
  const selected = matching.slice(0, 25);
  const request = ++dataRowsRequest;
  document.querySelector("#structure-search-count").textContent = `${selected.length} из ${matching.length} столбцов`;
  document.querySelector("#table-head").innerHTML = `<th class="row-number">Строка</th>${selected.map(variable => `<th title="${escapeAttribute(variable.label)}"><code>${escapeHtml(variable.name)}</code><span class="row-column-label">${escapeHtml(variable.label)}</span></th>`).join("")}`;
  document.querySelector("#table-body").innerHTML = `<tr><td colspan="${selected.length + 1}"><div class="empty-state">Читаем строки массива…</div></td></tr>`;
  if (!selected.length) {
    document.querySelector("#table-body").innerHTML = emptySearchRow(1, "Столбцы не найдены.");
    document.querySelector("#row-page").textContent = "";
    return;
  }
  const params = new URLSearchParams({ offset: String(dataRowsOffset), limit: String(DATA_ROWS_LIMIT) });
  selected.forEach(variable => params.append("columns", variable.name));
  if (dataRowsFilterId) params.set("filter_id", dataRowsFilterId);
  try {
    const page = await api(`/api/projects/${currentProject.id}/data/rows?${params}`);
    if (request !== dataRowsRequest || structureMode !== "rows") return;
    if (dataRowsOffset && dataRowsOffset >= page.total) {
      dataRowsOffset = Math.max(0, Math.floor(Math.max(0, page.total - 1) / DATA_ROWS_LIMIT) * DATA_ROWS_LIMIT);
      void renderDataRows();
      return;
    }
    const first = page.total ? page.offset + 1 : 0;
    const last = Math.min(page.offset + page.rows.length, page.total);
    document.querySelector("#row-page").textContent = `${first}–${last} из ${page.total}`;
    document.querySelector("#row-prev").disabled = page.offset === 0;
    document.querySelector("#row-next").disabled = page.offset + page.rows.length >= page.total;
    if (!page.rows.length) {
      document.querySelector("#table-body").innerHTML = `<tr><td colspan="${selected.length + 1}"><div class="empty-state">Фильтр не оставил строк.</div></td></tr>`;
      return;
    }
    document.querySelector("#table-body").innerHTML = page.rows.map(row => `<tr class="data-row">
      <td class="row-number">${escapeHtml(row.number)}</td>
      ${row.values.map(cell => `<td class="data-cell" title="${escapeAttribute(cell.display)}"><span>${escapeHtml(cell.display)}</span>${cell.label != null && String(cell.raw) !== cell.display ? `<small>${escapeHtml(cell.raw)}</small>` : ""}${cell.truncated ? '<em>обрезано</em>' : ""}</td>`).join("")}
    </tr>`).join("");
  } catch (error) {
    if (request !== dataRowsRequest) return;
    document.querySelector("#table-body").innerHTML = `<tr><td colspan="${selected.length + 1}"><div class="empty-state error">${escapeHtml(error.message)}</div></td></tr>`;
  }
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

// Клавиатурная альтернатива перетаскиванию (P2): стрелки на ручке строки.
document.querySelector("#table-body").addEventListener("keydown", async event => {
  const handle = event.target.closest?.(".drag-handle");
  if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key) || handle.getAttribute("draggable") !== "true") return;
  event.preventDefault();
  const code = handle.dataset.dragCode;
  const codes = configuredQuestions().map(item => item.code);
  const index = codes.indexOf(code);
  const neighbour = codes[index + (event.key === "ArrowUp" ? -1 : 1)];
  if (!neighbour) return;
  await moveQuestionTo(code, neighbour, event.key === "ArrowDown");
  document.querySelector(`#table-body [data-drag-code="${CSS.escape(code)}"]`)?.focus();
});

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
  const projectId = currentProject.id;
  const code = currentQuestionCode;
  // Ответ для вопроса, который уже закрыли, не должен лечь в редактор следующего.
  const stale = () => currentProject?.id !== projectId || currentQuestionCode !== code;
  try {
    const preview = await api(`/api/projects/${projectId}/questions/${encodeURIComponent(code)}/preview`);
    if (stale()) return;
    container.innerHTML = renderPreview(preview, findQuestion(code));
    renderNotApplicable(findQuestion(currentQuestionCode), preview);
    renderNets(findQuestion(currentQuestionCode), preview);
  } catch (error) {
    if (stale()) return;
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
    document.querySelector("#not-applicable").hidden = true;
    document.querySelector("#question-nets").hidden = true;
  }
}

// У блока из нескольких переменных строки — это его пункты, и начало
// «Что вы обычно заказываете: …» в каждой повторяет заголовок редактора.
// У одиночного выбора строки — ответы, их не трогаем.
function renderPreview(preview, question = null) {
  const itemised = question && (question.source_variables?.length || 0) > 1;
  const rowLabels = itemised && preview.rows?.length ? stripCommonPrefix(preview.rows.map(row => row.label)) : null;
  const itemLabels = preview.items?.length ? stripCommonPrefix(preview.items.map(item => item.label)) : null;
  const base = `<div class="base-line"><span>Total <strong>${preview.total_base.toLocaleString("ru-RU")}</strong></span><span>Валидная база <strong>${preview.valid_base.toLocaleString("ru-RU")}</strong></span></div>`;
  if (preview.items?.length) {
    return base + `<div class="matrix-preview">${preview.items.map((item, index) => `<details><summary><span title="${escapeAttribute(item.label)}"><code>${escapeHtml(item.variable)}</code> ${escapeHtml(itemLabels[index])}</span><strong>Среднее ${item.statistics.mean == null ? "—" : Number(item.statistics.mean).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}</strong></summary><div class="preview-rows">${item.rows.map(row => `<div class="${row.is_special ? "special-row" : ""}"><span>${escapeHtml(row.label)}${row.is_special ? " · спецответ" : ""}</span><strong>${row.count}</strong><em>${formatPercent(row.percent_main)}</em><em>${formatPercent(row.percent_filter)}</em></div>`).join("")}</div></details>`).join("")}</div>`;
  }
  const rows = preview.rows?.length ? `<div class="preview-rows"><div class="preview-row-head"><span>Ответ</span><strong>N</strong><em>Total</em><em>Valid</em></div>${preview.rows.map((row, index) => `
    <div class="${row.is_special ? "special-row" : ""}"><span title="${escapeAttribute(row.label)}">${escapeHtml(rowLabels ? rowLabels[index] : row.label)}${row.is_special ? " · спецответ" : ""}</span><strong>${row.count}</strong><em>${formatPercent(row.percent_main)}</em><em>${formatPercent(row.percent_filter)}</em></div>`).join("")}</div>` : "";
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


function findQuestion(code) {
  return configuredQuestions().find(item => item.code === code);
}

function updateFileLabel() {
  const file = fileInput.files[0];
  if (!file) return;
  fileTitle.textContent = file.name;
  fileCaption.textContent = `${(file.size / 1024 / 1024).toFixed(1)} МБ`;
}

/* Сборка переживает перезагрузку страницы (P2): задание идёт на сервере, а
   его номер лежит в sessionStorage вкладки. Открыв тот же проект, экран
   продолжает следить за ним и по готовности предлагает скачать файлы — сам
   скачивать не начинает: после перезагрузки аналитик мог уже не ждать. */
