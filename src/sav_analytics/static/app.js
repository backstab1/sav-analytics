const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file");
const fileTitle = document.querySelector("#file-title");
const fileCaption = document.querySelector("#file-caption");
const errorBox = document.querySelector("#form-error");
const submit = document.querySelector("#submit");
const dropZone = document.querySelector("#drop-zone");
const editor = document.querySelector("#question-editor");
const recodeEditor = document.querySelector("#recode-editor");
const bannerEditor = document.querySelector("#banner-editor");
const filterEditor = document.querySelector("#filter-editor");
const weightEditor = document.querySelector("#weight-editor");
const reportSettingsForm = document.querySelector("#report-settings-form");
const notApplicableEditor = document.querySelector("#not-applicable-editor");
let currentProject = null;
let currentQuestionCode = null;
let currentRecodingId = null;
let currentBannerId = null;
let currentFilterId = null;
let currentWeightId = null;
let currentView = "questions";
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
});

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
  currentProject = null;
  currentQuestionCode = null;
  currentRecodingId = null;
  currentBannerId = null;
  currentFilterId = null;
  currentWeightId = null;
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = true;
  filterEditor.hidden = true;
  weightEditor.hidden = true;
  closeSheet();
  document.querySelector("#workspace").hidden = true;
  document.querySelector("#start").hidden = false;
  window.Shell.setProjectOpen(false);
  window.scrollTo(0, 0);
  form.reset();
  fileTitle.textContent = "Перетащите SAV сюда";
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
document.querySelector("#close-editor").addEventListener("click", closeQuestionEditor);
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
document.querySelector("#close-recode-editor").addEventListener("click", closeRecoding);
document.querySelector("#add-range").addEventListener("click", () => addRangeRow());
document.querySelector("#add-category-group").addEventListener("click", () => addCategoryGroup());
document.querySelector("#recode-mode").addEventListener("change", () => {
  fillRecodeSources();
  document.querySelector("#category-group-list").innerHTML = "";
  renderRecodeMode();
});
document.querySelector("#recode-source").addEventListener("change", () => {
  if (document.querySelector("#recode-mode").value === "categories") {
    document.querySelector("#category-group-list").innerHTML = "";
    renderRecodeMode();
  }
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
document.querySelector("#close-filter-editor").addEventListener("click", closeFilter);
document.querySelector("#add-filter-condition").addEventListener("click", () => {
  addFilterCondition();
  scheduleFilterPreview();
});
document.querySelector("#delete-filter").addEventListener("click", deleteFilter);
document.querySelector("#copy-filter").addEventListener("click", copyFilter);
document.querySelector("#close-weight-editor").addEventListener("click", closeWeight);
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
    currentView = "questions";
    structureMode = "questions";
    renderSectionHead("questions");
    document.querySelectorAll(".tabs button[data-view]").forEach(button => {
      button.classList.toggle("active", button.dataset.view === "questions");
    });
    document.querySelectorAll("[data-structure-mode]").forEach(button => {
      button.classList.toggle("active", button.dataset.structureMode === "questions");
    });
    syncSectionChrome("questions");
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
  questions: {
    title: "Структура массива",
    lead: "Типы и названия вопросов определены автоматически. Проверьте отмеченное.",
  },
  report: {
    title: "Отчёт",
    lead: "Всё, что определяет книгу Excel: колонки, база, вес, статистика и состав вопросов.",
  },
};

function renderSectionHead(view) {
  const head = sectionHeads[view] || sectionHeads.questions;
  document.querySelector("#section-title").textContent = head.title;
  document.querySelector("#section-lead").textContent = head.lead;
  document.querySelector("#find-not-applicable").hidden = view !== "questions";
  // Сводка — фильтр таблицы вопросов, в других разделах ей нечего фильтровать.
  document.querySelector("#summary").hidden = view !== "questions";
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
document.querySelector("#category-group-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-category-group]");
  if (button) button.closest(".category-group").remove();
});
document.querySelector("#filter-condition-list").addEventListener("click", event => {
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
});
document.querySelector("#filter-condition-list").addEventListener("change", event => {
  if (event.target.matches(".filter-operation")) syncFilterConditionValue(event.target.closest(".filter-condition"));
  if (event.target.matches(".filter-group-operator")) {
    refreshFilterJoins(event.target.closest(".filter-group").querySelector(".filter-group-items"));
  }
});
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
function closeAllInspectors() {
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = true;
  filterEditor.hidden = true;
  weightEditor.hidden = true;
  currentQuestionCode = null;
  currentRecodingId = null;
  currentBannerId = null;
  currentFilterId = null;
  currentWeightId = null;
}

// Хром раздела: подсветка в рельсе, тулбар панели и полоса запуска.
function syncSectionChrome(view) {
  document.querySelectorAll(".tabs button").forEach(item => {
    item.classList.toggle("active", item.dataset.view === view);
  });
  document.querySelector("#structure-toolbar").hidden = view !== "questions";
  document.querySelector("#report-toolbar").hidden = view !== "report";
  document.querySelector("#report-launch").hidden = view !== "report";
}

function setView(view) {
  currentView = view;
  renderSectionHead(view);
  syncSectionChrome(view);
  closePicker();
  closeAllInspectors();
  renderTable();
  if (view === "report") void loadReportPreflight();
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
    if (stat.getAttribute("aria-checked") !== "true") {
      void applyStatSetting(statPatch(stat.dataset.stat, stat.dataset.value));
    }
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
  if (!handle || currentView !== "questions" || structureMode !== "questions" || structureFiltered()) return;
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
    categories = mode === "ranges" ? collectRanges() : collectCategoryGroups();
  } catch (error) {
    showError(recodeError, error);
    return;
  }
  const payload = {
    mode,
    code: document.querySelector("#recode-code").value.trim(),
    name: document.querySelector("#recode-name").value.trim(),
    source_variable: document.querySelector("#recode-source").value,
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
    showToast("Настройки вопроса сохранены");
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
  currentRecodingId = null;
  currentBannerId = null;
  currentFilterId = null;
  currentWeightId = null;
  currentView = "questions";
  structureMode = "questions";
  resetStructureSearch({ render: false });
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = true;
  filterEditor.hidden = true;
  weightEditor.hidden = true;
  renderSectionHead("questions");
  syncSectionChrome("questions");
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
}

// Конструктор берёт список переменных отсюда: своей загрузки у него нет,
// иначе один и тот же проект читался бы дважды.
function publishVariablesToShell(inspection, questions) {
  window.Shell.setProjectVariables(
    questions
      .filter(question => question.included_in_report)
      .map(question => ({
        code: question.code,
        label: question.label,
        type: question.type,
        categories: (inspection.variables
          .find(item => item.name === question.source_variables?.[0])?.value_labels || [])
          .map(item => item.label),
      })),
    inspection.row_count,
  );
}

function questionStatus(question) {
  if (!question.included_in_report) return "excluded";
  return question.recognition === "auto_review" ? "review" : "ready";
}

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

// Плитка вместо строки: холст шириной 1180px, а свойств книги четыре —
// в столбик они занимали высоту экрана, в ряд занимают одну полосу.
// Состав вынесен отдельной сводкой над плитками: это не настройка,
// а итог структуры, и правится он в другом разделе.
function reportTileMarkup({ key, title, value, off, chips = [], meta, picker, action }) {
  const chipLine = chips.length ? `<span class="tile-chips">${chips.join("")}</span>` : "";
  const valueMarkup = picker
    ? `<button type="button" class="tile-val ${off ? "off" : ""}" data-picker="${picker}"
        aria-haspopup="dialog" aria-expanded="false">${value}<span class="caret" aria-hidden="true"></span></button>`
    : `<span class="tile-val ${off ? "off" : ""}">${value}</span>`;
  return `<article class="tile" data-block="${key}">
    <span class="tile-key">${escapeHtml(title)}${action}</span>
    ${valueMarkup}
    ${chipLine}
    <span class="tile-meta">${meta}</span>
  </article>`;
}

// Чипы в плитке живут в одну строку: лишние сворачиваются в счётчик,
// иначе плитка растёт от длины названий и ряд перестаёт быть ровным.
function tileChips(chips, limit = 3) {
  if (chips.length <= limit) return chips;
  return chips.slice(0, limit).concat(
    `<span class="chip muted">…ещё ${chips.length - limit}</span>`
  );
}

function reportColumnsTile() {
  const banner = configuredBanners().find(item => item.id === selectedReportBannerId()) || null;
  const chips = ['<span class="chip total">A · Total</span>'].concat(
    (banner?.blocks || []).map(block => {
      const label = block.label || block.sources.map(bannerSourceLabel).join(" → ");
      const path = block.sources
        .map(source => escapeHtml(bannerSourceLabel(source)))
        .join('<span class="lvl">→</span>');
      return `<span class="chip" title="${escapeAttribute(label)}">${path}</span>`;
    })
  );
  return reportTileMarkup({
    key: "banner", title: "Колонки", picker: "banner",
    value: banner ? escapeHtml(banner.name) : "Только Total",
    off: !banner, chips: tileChips(chips),
    meta: banner
      ? `Блоков <b>${banner.blocks.length}</b> · колонок <b>${reportBannerColumnCount(banner)}</b>`
      : "Разбивки нет, одна колонка",
    action: banner
      ? `<button type="button" class="tile-edit" data-edit="banner" data-id="${escapeAttribute(banner.id)}">править</button>`
      : '<button type="button" class="tile-edit" data-new="banner">новый</button>',
  });
}

function reportBaseTile() {
  const filter = configuredFilters().find(item => item.id === selectedReportFilterId()) || null;
  const total = currentProject.inspection.row_count;
  const preview = filter ? filterPreviewCache.get(filterPreviewKey(filter)) : null;
  const chips = filter ? tileChips(filterRuleChips(filter.rule)) : [];
  return reportTileMarkup({
    key: "filter", title: "База отчёта", picker: "filter",
    value: filter ? escapeHtml(filter.name) : "Все респонденты",
    off: !filter, chips,
    meta: filter
      ? (preview
        ? `Выборка <b>${preview.selected.toLocaleString("ru-RU")}</b> из ${preview.total.toLocaleString("ru-RU")}`
        : "Выборка <b>считается…</b>")
      : `Все <b>${total.toLocaleString("ru-RU")}</b> респондентов`,
    action: filter
      ? `<button type="button" class="tile-edit" data-edit="filter" data-id="${escapeAttribute(filter.id)}">править</button>`
      : '<button type="button" class="tile-edit" data-new="filter">новое</button>',
  });
}

function reportWeightTile(settings) {
  const calculated = settings.calculated_weight_id
    ? configuredWeights().find(item => item.id === settings.calculated_weight_id)
    : null;
  const ready = settings.weight_variable || null;
  let value = "Без веса";
  let chips = [];
  let meta = "Показатели и базы невзвешенные";
  let action = '<button type="button" class="tile-edit" data-open-sheet="report-settings">настроить</button>';
  if (calculated) {
    value = escapeHtml(calculated.name);
    chips = ['<span class="chip">raking / IPF</span>'].concat(
      calculated.lower_bound == null
        ? []
        : [`<span class="chip">${formatWeightNumber(calculated.lower_bound)}–${formatWeightNumber(calculated.upper_bound)}</span>`]
    );
    meta = `Распределений <b>${calculated.dimensions.length}</b>`;
    action = `<button type="button" class="tile-edit" data-edit="weight" data-id="${escapeAttribute(calculated.id)}">править</button>`;
  } else if (ready) {
    value = escapeHtml(ready);
    chips = ['<span class="chip">готовый из массива</span>'];
    const diagnostics = readyWeightCache.get(ready)?.diagnostics;
    meta = diagnostics
      ? `Эфф. база <b>${formatWeightNumber(diagnostics.effective_base)}</b> · DEFF <b>${formatWeightNumber(diagnostics.design_effect)}</b>`
      : "Разбор распределения <b>считается…</b>";
    action = '<button type="button" class="tile-edit" data-open-sheet="report-settings">настроить</button>';
  }
  return reportTileMarkup({
    key: "weight", title: "Вес", picker: "weight",
    value, off: !calculated && !ready, chips, meta, action,
  });
}

// Статистика — не плитка. У остальных трёх свойств значение одно
// («этот баннер», «этот фильтр», «этот вес»), а здесь их семь, и прятать
// их за словом «изменить» значит держать половину настроек отчёта в
// закрытом ящике. Поэтому она идёт полосой под плитками, во всю их
// ширину, и показывает варианты, а не итог: выбор виден и делается на
// месте, каждый переключатель сразу уходит в конфигурацию.
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

function reportStatisticsPanel(settings) {
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
  const totalWarning = scheme === "total"
    ? '<p class="stat-hint warning-hint">Total включает саму подгруппу: выборки пересекаются, различия занижаются. Выбирайте, только если этого требует шаблон заказчика.</p>'
    : "";
  return `<section class="stat-panel" data-block="statistics" id="stat-panel">
    <div class="stat-head">
      <span class="tile-key">Статистика</span>
      <span class="stat-note">Действует на весь отчёт</span>
      <span id="stat-saved" class="stat-saved" role="status" hidden>Сохранено</span>
    </div>

    <div class="stat-row">
      <span class="stat-key">Сравнение подгрупп</span>
      <div class="stat-controls">
        ${statSegment("scheme", [
          { value: "off", label: "Не считать" },
          { value: "rest", label: "С остатком (Rest)" },
          { value: "total", label: "С Total" },
        ], scheme)}
        ${statToggle("pairwise", "Попарные внутри блока", settings.compare_pairwise)}
      </div>
    </div>
    ${totalWarning}

    <div class="stat-row">
      <span class="stat-key">Уровень и пороги</span>
      <div class="stat-controls">
        ${statSegment("confidence", [
          { value: "0.9", label: "90%" },
          { value: "0.95", label: "95%" },
          { value: "0.99", label: "99%" },
        ], String(settings.confidence_level))}
        ${statToggle("bonferroni", "Поправка Bonferroni", settings.bonferroni,
          "Корректирует alpha на число сравнений внутри блока")}
        <label class="stat-number">Малая база &lt;
          <input id="stat-minimum-base" type="number" min="1" max="100000" value="${settings.minimum_base}" />
        </label>
      </div>
    </div>

    <div class="stat-row">
      <span class="stat-key">Волны и вывод</span>
      <div class="stat-controls">
        ${statSegment("wave", [
          { value: "none", label: "Волны не сравнивать" },
          { value: "previous", label: "С предыдущей" },
          { value: "control", label: "С контрольной" },
        ], settings.wave_comparison)}
        ${settings.wave_comparison === "control"
          ? `<label class="stat-number">Контрольная
              <select id="stat-wave-control">${waveOptions}</select></label>`
          : ""}
        ${statToggle("pvalues", "p-value в примечании", settings.show_p_values,
          "Полный протокол теста в примечании к ячейке; книга заметно тяжелее")}
      </div>
    </div>
  </section>`;
}

// Состав — не настройка книги, а итог структуры: его не выбирают здесь,
// его правят в другом разделе. Поэтому он стоит над плитками полосой,
// а не пятой плиткой в ряду.
function reportContentSummary() {
  const questions = configuredQuestions();
  const included = questions.filter(question => question.included_in_report);
  const review = included.filter(question => questionStatus(question) === "review");
  const withBase = included.filter(question => question.base_filter_id);
  const excluded = questions.length - included.length;
  const chips = [
    review.length ? `<span class="chip warn">${plural(review.length, "требует", "требуют", "требуют")} проверки</span>` : "",
    withBase.length ? `<span class="chip">${plural(withBase.length, "со своей базой", "со своей базой", "со своей базой")}</span>` : "",
    excluded ? `<span class="chip muted">${excluded} исключено</span>` : "",
  ].filter(Boolean).join("");
  return `<section class="rep-summary" data-block="content">
    <div class="rep-summary-text">
      <p class="rep-summary-key">Состав книги</p>
      <p class="rep-summary-val">${plural(included.length, "вопрос", "вопроса", "вопросов")} из ${questions.length}</p>
    </div>
    <div class="rep-summary-chips">${chips}</div>
    <button type="button" class="pill" data-goto="questions">К структуре</button>
  </section>`;
}

function renderReportBlocks() {
  const container = document.querySelector("#entity-list");
  const settings = configuredReportSettings();
  container.className = "entity-list report-blocks";
  container.innerHTML = reportContentSummary()
    + `<div class="tiles">${[
      reportColumnsTile(),
      reportBaseTile(),
      reportWeightTile(settings),
    ].join("")}</div>`
    + reportStatisticsPanel(settings);
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
  if (currentProject?.id === projectId && currentView === "report") renderReportBlocks();
}

// Настройки отчёта пишутся целиком: endpoint принимает полный набор,
// поэтому любой переключатель отправляет текущие значения с одной
// заменённой парой. Так же поступает и лист выбора веса.
async function patchReportSettings(partial, { toast = null } = {}) {
  const settings = { ...configuredReportSettings(), ...partial };
  const payload = {
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
  };
  currentProject = await api(`/api/projects/${currentProject.id}/report-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  renderProject();
  if (currentView === "report") void loadReportPreflight();
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
  if (name === "pairwise") return { compare_pairwise: value === "on" };
  if (name === "bonferroni") return { bonferroni: value === "on" };
  if (name === "pvalues") return { show_p_values: value === "on" };
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
  const settings = configuredReportSettings();
  const payload = {
    compare_to_total: settings.compare_to_total,
    compare_target: settings.compare_target,
    compare_pairwise: settings.compare_pairwise,
    confidence_level: settings.confidence_level,
    bonferroni: settings.bonferroni,
    show_p_values: settings.show_p_values,
    minimum_base: settings.minimum_base,
    weight_variable: value.startsWith("ready:") ? value.slice(6) : null,
    calculated_weight_id: value.startsWith("calculated:") ? value.slice(11) : null,
    wave_comparison: settings.wave_comparison,
    wave_control_value: settings.wave_comparison === "control" ? settings.wave_control_value : null,
  };
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
    if (currentProject?.id === projectId && currentView === "report") renderLaunchStatus(report);
  } catch (error) {
    container.innerHTML = `<span class="pf error"><i></i>${escapeHtml(error.message)}</span>`;
  }
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
  const cardView = currentView === "report";
  tableWrap.hidden = cardView;
  entityList.hidden = !cardView;
  if (currentView === "questions" && structureMode === "variables") {
    renderPhysicalVariables();
    return;
  }
  if (currentView === "report") {
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
  document.querySelector("#table-head").innerHTML =
    "<th class=\"drag-cell\" aria-label=\"Порядок\"></th>"
    + "<th class=\"code-column\">Код</th>"
    + "<th class=\"question-cell\">Вопрос</th>"
    + "<th class=\"type-column\">Тип</th>"
    + "<th class=\"count-column\">Перем.</th>"
    + "<th class=\"status-column\">Статус</th>";
  if (!questions.length) {
    document.querySelector("#table-body").innerHTML = emptySearchRow(6, "Вопросы не найдены.");
    return;
  }
  document.querySelector("#table-body").innerHTML = questions.map(question => {
    const sourceLabel = originalQuestionLabel(question);
    const warnings = (question.warnings || []).join(" · ");
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
    return `<tr class="question-row ${question.code === currentQuestionCode ? "selected" : ""}" data-code="${escapeHtml(question.code)}">
      <td class="drag-cell"><button type="button" class="drag-handle" draggable="${draggable}" data-drag-code="${escapeAttribute(question.code)}" aria-label="Перетащить ${escapeAttribute(question.code)}" title="${structureFiltered() ? "Сбросьте фильтр, чтобы менять порядок" : "Перетащите, чтобы изменить порядок"}"><span aria-hidden="true">⋮⋮</span></button></td>
      <td class="code-column"><code>${escapeHtml(question.code)}</code></td>
      <td class="question-cell"><span class="q-title" title="${escapeAttribute(title)}">${escapeHtml(question.label)}</span>${sub ? `<span class="q-sub ${warnings ? "warning" : ""}" title="${escapeAttribute(sub)}">${escapeHtml(sub)}</span>` : ""}</td>
      <td class="type-column"><span class="type-icon" role="img" aria-label="${escapeAttribute(typeLabels[question.question_type] || question.question_type)}">${typeIcons[question.question_type] || typeIcons.technical}<span class="type-label" aria-hidden="true">${escapeHtml(typeLabels[question.question_type] || question.question_type)}</span></span></td>
      <td class="count-column"><span class="count">${question.source_variables.length}</span></td>
      <td class="status-column"><span class="status ${!question.included_in_report ? "excluded" : (question.recognition === "auto_review" ? "review" : "")}">${question.included_in_report ? (question.recognition === "auto_review" ? "Проверить" : "Готов") : "Исключён"}</span></td>
    </tr>`;
  }).join("");
}

function emptySearchRow(columns, message) {
  const reason = structureSearch
    ? `По запросу «${escapeHtml(structureSearch)}» ничего не совпало.`
    : "Под выбранный фильтр ничего не подходит.";
  return `<tr class="empty-row"><td colspan="${columns}"><div class="empty-state">${escapeHtml(message)} ${reason}</div></td></tr>`;
}

// Чипы правила фильтра списком: плитке нужно уметь показать первые три
// и свернуть остальные в счётчик.
function filterRuleChips(rule) {
  const chips = [];
  if ((rule.items || []).length > 1) chips.push(`<span class="operator-chip">${rule.operator === "or" ? "ИЛИ" : "И"}</span>`);
  (rule.items || []).forEach(item => {
    if (item.kind === "group") {
      if ((item.items || []).length > 1) chips.push(`<span class="operator-chip">${item.operator === "or" ? "ИЛИ" : "И"}</span>`);
      (item.items || []).forEach(condition => chips.push(renderFilterConditionChip(condition)));
    } else {
      chips.push(renderFilterConditionChip(item));
    }
  });
  return chips;
}

function renderFilterConditionChip(condition) {
  const source = condition.source?.kind === "recoding"
    ? configuredRecodings().find(item => item.id === condition.source.ref)?.code || condition.source.ref
    : condition.source?.ref || "?";
  const operators = {
    eq: "=", ne: "≠", in: "∈", not_in: "∉", gt: ">", lt: "<", between: "между",
    filled: "заполнено", missing: "пропущено", selected_any: "выбран любой",
    selected_all: "выбраны все", selected_none: "не выбрано",
  };
  let values = (condition.values || []).join(", ");
  if (condition.operator === "between") {
    values = [condition.lower, condition.upper].filter(value => value !== undefined && value !== null && value !== "").join("–");
  } else if (condition.operator === "gt") {
    values = condition.lower ?? "";
  } else if (condition.operator === "lt") {
    values = condition.upper ?? "";
  }
  const text = [source, operators[condition.operator] || condition.operator, values].filter(Boolean).join(" ");
  return `<span title="${escapeAttribute(text)}">${escapeHtml(text)}</span>`;
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
    if (currentProject?.id === projectId && currentView === "report") renderReportBlocks();
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
  currentFilterId = filterId;
  currentQuestionCode = null;
  currentRecodingId = null;
  currentBannerId = null;
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = true;
  weightEditor.hidden = true;
  filterEditor.hidden = false;
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
  document.querySelector("#filter-preview").innerHTML = filter
    ? '<p class="muted">Считаем…</p>'
    : '<p class="muted">Сохраните правило для расчёта.</p>';
  renderTable();
  if (filter) loadFilterPreview();
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
  const rawValue = condition.values?.join(", ")
    || (condition.operator === "between" ? `${condition.lower ?? ""}, ${condition.upper ?? ""}` : condition.lower ?? condition.upper ?? "");
  element.innerHTML = `<select class="filter-source" aria-label="Вопрос">${filterSourceOptions(sourceValue)}</select><div class="filter-condition-details"><select class="filter-operation" aria-label="Условие">${filterOperatorOptions(condition.operator || "eq")}</select><input class="filter-value" value="${escapeAttribute(rawValue)}" aria-label="Значение" /></div><button type="button" data-remove-filter-condition title="Удалить условие" aria-label="Удалить условие">×</button>`;
  container.append(element);
  syncFilterConditionValue(element);
  refreshFilterJoins(container);
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

function syncFilterConditionValue(condition) {
  const operation = condition.querySelector(".filter-operation").value;
  const value = condition.querySelector(".filter-value");
  value.hidden = ["filled", "missing"].includes(operation);
  const placeholders = {
    in: "Значения через запятую", not_in: "Значения через запятую",
    selected_any: "Варианты через запятую", selected_all: "Варианты через запятую", selected_none: "Варианты через запятую",
    between: "От, до", gt: "Число", lt: "Число",
  };
  value.placeholder = placeholders[operation] || "Значение";
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
  const options = [];
  configuredQuestions()
    .filter(item => !["open_text", "technical"].includes(item.question_type))
    .forEach(item => options.push(`<option value="question:${escapeAttribute(item.code)}" ${selectedValue === `question:${item.code}` ? "selected" : ""}>${escapeHtml(item.code)} — ${escapeHtml(item.label)}</option>`));
  configuredRecodings().forEach(item => options.push(`<option value="recoding:${item.id}" ${selectedValue === `recoding:${item.id}` ? "selected" : ""}>↳ ${escapeHtml(item.code)} — ${escapeHtml(item.name)}</option>`));
  return options.join("");
}

function filterOperatorOptions(selected) {
  const labels = { eq: "Равно", ne: "Не равно", in: "Входит в список", not_in: "Не входит в список", gt: "Больше", lt: "Меньше", between: "Между", filled: "Заполнено", missing: "Пропущено", selected_any: "Выбран хотя бы один вариант", selected_all: "Выбраны все варианты", selected_none: "Не выбран ни один" };
  return Object.entries(labels).map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
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
  const operator = element.querySelector(".filter-operation").value;
  const values = element.querySelector(".filter-value").value.split(",").map(value => value.trim()).filter(Boolean).map(parseFilterValue);
  const condition = { kind: "condition", source, operator, values: [] };
  if (["eq", "ne", "in", "not_in", "selected_any", "selected_all", "selected_none"].includes(operator)) condition.values = values;
  if (operator === "gt") condition.lower = Number(values[0]);
  if (operator === "lt") condition.upper = Number(values[0]);
  if (operator === "between") [condition.lower, condition.upper] = values.map(Number);
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

function parseFilterValue(value) {
  if (value !== "" && Number.isFinite(Number(value))) return Number(value);
  return value;
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
    const warning = preview.empty ? "Пустая база — использовать её нельзя." : preview.small_base ? "Малая база: результаты будут отмечены серым." : "";
    const steps = preview.steps?.length ? `<div class="filter-steps">${preview.steps.map((step, index) => `<div><span>После условия ${index + 1}</span><strong>${step.selected.toLocaleString("ru-RU")}</strong></div>`).join("")}</div>` : "";
    container.innerHTML = `<div class="filter-result"><strong>${preview.selected.toLocaleString("ru-RU")}</strong><span>из ${preview.total.toLocaleString("ru-RU")} · ${formatPercent(preview.share)}</span></div>${steps}${warning ? `<p class="inline-warnings">${escapeHtml(warning)}</p>` : ""}`;
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
  currentWeightId = weightId;
  currentQuestionCode = null;
  currentRecodingId = null;
  currentBannerId = null;
  currentFilterId = null;
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = true;
  filterEditor.hidden = true;
  weightEditor.hidden = false;
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
  currentBannerId = bannerId;
  currentQuestionCode = null;
  currentRecodingId = null;
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = false;
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
  element.innerHTML = `<div class="banner-block-head block-row-head"><input class="banner-block-label" placeholder="Название блока — необязательно" value="${escapeAttribute(block.label || "")}" /><button class="del" type="button" data-remove-banner-block title="Удалить блок" aria-label="Удалить блок">×</button></div><div class="block-lvls"><label>Первый уровень<select class="banner-source-first">${bannerSourceOptions(first, false)}</select></label><label>Второй уровень<select class="banner-source-second">${bannerSourceOptions(second, true)}</select></label></div>`;
  document.querySelector("#banner-block-list").append(element);
}

function bannerSourceOptions(selected, allowEmpty) {
  const selectedValue = selected ? `${selected.kind}:${selected.ref}` : "";
  const options = [];
  if (allowEmpty) options.push('<option value="">Без вложения</option>');
  configuredQuestions()
    .filter(item => item.question_type === "single_choice" && item.source_variables.length === 1)
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
    const sources = [first];
    if (secondValue) sources.push(parseBannerSource(secondValue));
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
  return `<div class="col-preview">${preview.columns.map((column, index) => {
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
  if ("returnTo" in options) recodeReturnTo = options.returnTo || null;
  currentRecodingId = recodingId;
  currentQuestionCode = null;
  editor.hidden = true;
  recodeEditor.hidden = false;
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
  if (recoding?.mode === "categories") {
    recoding.categories.forEach(category => addCategoryGroup(category));
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
  const categorical = document.querySelector("#recode-mode").value === "categories";
  document.querySelector("#range-editor").hidden = categorical;
  document.querySelector("#category-editor").hidden = !categorical;
  if (categorical && document.querySelectorAll("#category-group-list .category-group").length === 0) {
    addCategoryGroup({ label: "Группа 1", values: [] });
    addCategoryGroup({ label: "Группа 2", values: [] });
  }
}

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

function addCategoryGroup(category = {}) {
  const source = document.querySelector("#recode-source").value;
  const variable = currentProject.inspection.variables.find(item => item.name === source);
  const values = category.values || [];
  const group = document.createElement("div");
  group.className = "category-group";
  group.innerHTML = `<div class="category-group-head"><input class="category-group-label" aria-label="Название новой категории" value="${escapeAttribute(category.label || "")}" placeholder="Название группы" /><button type="button" data-remove-category-group title="Удалить группу">×</button></div><div class="source-value-list">${(variable?.value_labels || []).map(item => `<label class="checkbox"><input type="checkbox" data-source-value="${escapeAttribute(JSON.stringify(item.value))}" ${containsComparable(values, item.value) ? "checked" : ""} /><code>${escapeHtml(item.value)}</code> ${escapeHtml(item.label)}</label>`).join("")}</div>`;
  document.querySelector("#category-group-list").append(group);
}

function collectCategoryGroups() {
  const groups = [...document.querySelectorAll("#category-group-list .category-group")];
  if (groups.length < 2) throw new Error("Добавьте минимум две новые категории.");
  return groups.map(group => {
    const label = group.querySelector(".category-group-label").value.trim();
    const values = [...group.querySelectorAll("[data-source-value]:checked")]
      .map(item => JSON.parse(item.dataset.sourceValue));
    if (!label) throw new Error("У каждой новой категории должно быть название.");
    if (!values.length) throw new Error(`В категории «${label}» ничего не выбрано.`);
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
    <div><span>${escapeHtml(row.label)}</span><strong>${row.count}</strong><em>${formatPercent(row.percent_total)}</em><em>${escapeHtml(preview.mode === "categories" ? `${row.source_values.length} знач.` : formatRange(row))}</em></div>`).join("")}</div>`;
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
  currentQuestionCode = code;
  fillEditor(findQuestion(code));
  editor.hidden = false;
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
  renderQuestionMembers(question);
  renderSpecialAnswers(question);
  renderSpecialMetric(question);
  renderQuestionRecodings(question);
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
  if (variable.storage_type === "numeric") return { variable, mode: "ranges" };
  return variable.value_labels?.length ? { variable, mode: "categories" } : null;
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
        <small><code>${escapeHtml(recoding.code)}</code> ${recoding.mode === "categories" ? "объединение категорий" : "числовые диапазоны"} · ${plural(recoding.categories.length, "категория", "категории", "категорий")}</small>
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
}

function collectNotApplicable() {
  // Панель скрыта — тип вопроса пометку не поддерживает, и поле не отправляется,
  // чтобы не затереть уже сохранённое значение пустым списком.
  if (document.querySelector("#not-applicable").hidden) return {};
  return {
    not_applicable_values: [...document.querySelectorAll("[data-not-applicable]:checked")]
      .map(item => JSON.parse(item.dataset.notApplicable)),
  };
}

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
  const container = document.querySelector("#preview-content");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const preview = await api(`/api/projects/${currentProject.id}/questions/${encodeURIComponent(currentQuestionCode)}/preview`);
    container.innerHTML = renderPreview(preview);
    renderNotApplicable(findQuestion(currentQuestionCode), preview);
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
    document.querySelector("#not-applicable").hidden = true;
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
    if (currentView === "report") void loadReportPreflight();
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

loadProjects();
