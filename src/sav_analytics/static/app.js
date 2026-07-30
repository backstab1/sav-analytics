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
let currentProject = null;
let currentQuestionCode = null;
let currentRecodingId = null;
let currentBannerId = null;
let currentFilterId = null;
let currentWeightId = null;
let currentView = "questions";
let structureMode = "questions";

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
  document.querySelector("#workspace").hidden = true;
  document.querySelector("#start").hidden = false;
  form.reset();
  fileTitle.textContent = "Перетащите SAV сюда";
  fileCaption.textContent = "или нажмите, чтобы выбрать файл";
  loadProjects();
});

document.querySelector("#refresh-projects").addEventListener("click", loadProjects);
document.querySelector("#close-editor").addEventListener("click", () => {
  editor.hidden = true;
  currentQuestionCode = null;
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
document.querySelector("#new-recoding").addEventListener("click", () => openRecoding());
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
document.querySelector("#new-banner").addEventListener("click", () => openBanner());
document.querySelector("#close-banner-editor").addEventListener("click", closeBanner);
document.querySelector("#add-banner-block").addEventListener("click", () => addBannerBlock());
document.querySelector("#delete-banner").addEventListener("click", deleteBanner);
document.querySelector("#refresh-banner-preview").addEventListener("click", loadBannerPreview);
document.querySelector("#new-filter").addEventListener("click", () => openFilter());
document.querySelector("#close-filter-editor").addEventListener("click", closeFilter);
document.querySelector("#add-filter-condition").addEventListener("click", () => addFilterCondition());
document.querySelector("#add-filter-group").addEventListener("click", () => addFilterGroup());
document.querySelector("#delete-filter").addEventListener("click", deleteFilter);
document.querySelector("#copy-filter").addEventListener("click", copyFilter);
document.querySelector("#refresh-filter-preview").addEventListener("click", loadFilterPreview);
document.querySelector("#report-filter").addEventListener("change", assignReportFilter);
document.querySelector("#report-banner").addEventListener("change", assignReportBanner);
document.querySelector("#new-weight").addEventListener("click", () => openWeight());
document.querySelector("#close-weight-editor").addEventListener("click", closeWeight);
document.querySelector("#add-weight-dimension").addEventListener("click", () => addWeightDimension());
document.querySelector("#delete-weight").addEventListener("click", deleteWeight);
document.querySelector("#refresh-weight-preview").addEventListener("click", loadWeightPreview);
document.querySelector("#weight-trimming").addEventListener("change", renderWeightTrimming);
document.querySelector("#weight-dimension-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-weight-dimension]");
  if (button) button.closest(".weight-dimension").remove();
});
document.querySelector("#weight-dimension-list").addEventListener("change", event => {
  if (event.target.matches(".weight-dimension-source")) {
    renderWeightTargets(event.target.closest(".weight-dimension"));
  }
});
document.querySelectorAll("[data-structure-mode]").forEach(button => button.addEventListener("click", () => {
  structureMode = button.dataset.structureMode;
  document.querySelectorAll("[data-structure-mode]").forEach(item => item.classList.toggle("active", item === button));
  editor.hidden = true;
  currentQuestionCode = null;
  renderTable();
}));
document.querySelector("#banner-block-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-banner-block]");
  if (button) button.closest(".banner-block").remove();
});
document.querySelector("#range-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-range]");
  if (button) button.closest(".range-row").remove();
});
document.querySelector("#category-group-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-category-group]");
  if (button) button.closest(".category-group").remove();
});
document.querySelector("#filter-condition-list").addEventListener("click", event => {
  const removeCondition = event.target.closest("button[data-remove-filter-condition]");
  if (removeCondition) removeCondition.closest(".filter-condition").remove();
  const removeGroup = event.target.closest("button[data-remove-filter-group]");
  if (removeGroup) removeGroup.closest(".filter-group").remove();
  const addNested = event.target.closest("button[data-add-group-condition]");
  if (addNested) addFilterCondition({}, addNested.closest(".filter-group").querySelector(".filter-group-items"));
});

document.querySelectorAll(".tabs button[data-view]").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach(item => item.classList.remove("active"));
  button.classList.add("active");
  currentView = button.dataset.view;
  document.querySelector("#recode-toolbar").hidden = currentView !== "recodings";
  document.querySelector("#banner-toolbar").hidden = currentView !== "banners";
  document.querySelector("#structure-toolbar").hidden = currentView !== "questions";
  document.querySelector("#filter-toolbar").hidden = currentView !== "filters";
  document.querySelector("#weight-toolbar").hidden = currentView !== "weights";
  if (currentView === "recodings") {
    editor.hidden = true;
    bannerEditor.hidden = true;
    filterEditor.hidden = true;
    weightEditor.hidden = true;
    currentQuestionCode = null;
    currentBannerId = null;
    currentFilterId = null;
    currentWeightId = null;
  } else if (currentView === "banners") {
    editor.hidden = true;
    recodeEditor.hidden = true;
    filterEditor.hidden = true;
    weightEditor.hidden = true;
    currentQuestionCode = null;
    currentRecodingId = null;
    currentFilterId = null;
    currentWeightId = null;
  } else if (currentView === "filters") {
    editor.hidden = true;
    recodeEditor.hidden = true;
    bannerEditor.hidden = true;
    currentQuestionCode = null;
    currentRecodingId = null;
    currentBannerId = null;
    weightEditor.hidden = true;
    currentWeightId = null;
  } else if (currentView === "weights") {
    editor.hidden = true;
    recodeEditor.hidden = true;
    bannerEditor.hidden = true;
    filterEditor.hidden = true;
    currentQuestionCode = null;
    currentRecodingId = null;
    currentBannerId = null;
    currentFilterId = null;
  } else {
    recodeEditor.hidden = true;
    bannerEditor.hidden = true;
    currentRecodingId = null;
    currentBannerId = null;
    filterEditor.hidden = true;
    currentFilterId = null;
    weightEditor.hidden = true;
    currentWeightId = null;
  }
  renderTable();
}));

document.querySelector("#table-body").addEventListener("click", event => {
  const labelButton = event.target.closest("button[data-edit-label]");
  if (labelButton) {
    openQuestion(labelButton.dataset.editLabel);
    const input = document.querySelector("#question-label");
    input.focus();
    input.select();
    return;
  }
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
  const filterRow = event.target.closest("tr[data-filter-id]");
  if (filterRow) {
    openFilter(filterRow.dataset.filterId);
    return;
  }
  const weightRow = event.target.closest("tr[data-weight-id]");
  if (weightRow) {
    openWeight(weightRow.dataset.weightId);
    return;
  }
  const moveButton = event.target.closest("button[data-move]");
  if (moveButton) {
    moveQuestion(moveButton.dataset.code, Number(moveButton.dataset.move));
    return;
  }
  if (event.target.closest(".inline-members")) return;
  const row = event.target.closest("tr[data-code]");
  if (row) openQuestion(row.dataset.code);
});

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
  const weightSelection = document.querySelector("#banner-weight").value;
  const waveMode = document.querySelector("#banner-wave-comparison").value;
  const waveControl = document.querySelector("#banner-wave-control").value;
  const payload = {
    name: document.querySelector("#banner-name").value.trim(),
    blocks,
    compare_to_total: document.querySelector("#banner-compare-total").checked,
    compare_pairwise: document.querySelector("#banner-compare-pairwise").checked,
    confidence_level: Number(document.querySelector("#banner-confidence").value) / 100,
    bonferroni: document.querySelector("#banner-bonferroni").checked,
    minimum_base: Number(document.querySelector("#banner-minimum-base").value),
    weight_variable: weightSelection.startsWith("ready:") ? weightSelection.slice(6) : null,
    calculated_weight_id: weightSelection.startsWith("calculated:") ? weightSelection.slice(11) : null,
    wave_comparison: waveMode,
    wave_control_value: waveMode === "control" && waveControl
      ? JSON.parse(waveControl)
      : null,
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
  } catch (error) {
    showError(bannerError, error);
  } finally {
    setBusy(saveButton, false, "Сохранить баннер");
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
  } catch (error) {
    showError(filterError, error);
  } finally {
    setBusy(saveButton, false, "Сохранить правило");
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
    renderProject();
    openRecoding(currentRecodingId);
    await loadRecodePreview();
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
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = true;
  filterEditor.hidden = true;
  weightEditor.hidden = true;
  document.querySelector("#recode-toolbar").hidden = true;
  document.querySelector("#banner-toolbar").hidden = true;
  document.querySelector("#structure-toolbar").hidden = false;
  document.querySelector("#filter-toolbar").hidden = true;
  document.querySelector("#weight-toolbar").hidden = true;
  document.querySelectorAll(".tabs button").forEach(button => {
    button.classList.toggle("active", button.dataset.view === "questions");
  });
  document.querySelectorAll("[data-structure-mode]").forEach(button => {
    button.classList.toggle("active", button.dataset.structureMode === "questions");
  });
  renderProject();
  document.querySelector("#start").hidden = true;
  document.querySelector("#workspace").hidden = false;
}

function renderProject() {
  const inspection = currentProject.inspection;
  const questions = configuredQuestions();
  document.querySelector("#project-name").textContent = currentProject.name;
  document.querySelector("#download-source").href = `/api/projects/${currentProject.id}/source`;
  document.querySelector("#download-report").href = `/api/projects/${currentProject.id}/reports/topline.xlsx`;
  document.querySelector("#download-statistics").href = `/api/projects/${currentProject.id}/reports/statistics.txt`;
  document.querySelector("#summary").innerHTML = [
    [inspection.row_count.toLocaleString("ru-RU"), "респондентов"],
    [inspection.variable_count.toLocaleString("ru-RU"), "переменных"],
    [questions.length.toLocaleString("ru-RU"), "вопросов"],
    [questions.filter(item => item.included_in_report).length, "включено в отчёт"],
  ].map(([value, label]) => `<article><strong>${value}</strong><span>${label}</span></article>`).join("");
  const warningBox = document.querySelector("#warnings");
  warningBox.hidden = inspection.warnings.length === 0;
  warningBox.innerHTML = inspection.warnings.map(text => `<p>⚑ ${escapeHtml(text)}</p>`).join("");
  renderReportFilterControl();
  renderReportBannerControl();
  renderTable();
}

function renderTable() {
  if (!currentProject) return;
  if (currentView === "questions" && structureMode === "variables") {
    renderPhysicalVariables();
    return;
  }
  if (currentView === "weights") {
    const weights = configuredWeights();
    document.querySelector("#table-head").innerHTML = "<th>Название</th><th>Распределений</th><th>Ограничения</th>";
    document.querySelector("#table-body").innerHTML = weights.length ? weights.map(weight => `
      <tr class="question-row ${weight.id === currentWeightId ? "selected" : ""}" data-weight-id="${weight.id}">
        <td><strong>${escapeHtml(weight.name)}</strong></td>
        <td>${weight.dimensions.length}</td>
        <td>${weight.lower_bound == null ? "Без ограничений" : `${weight.lower_bound}–${weight.upper_bound}`}</td>
      </tr>`).join("") : '<tr><td colspan="3" class="empty-state">Рассчитанных весов пока нет.</td></tr>';
    return;
  }
  if (currentView === "filters") {
    const filters = configuredFilters();
    document.querySelector("#table-head").innerHTML = "<th>Название</th><th>Логика</th><th>Условий</th><th>Используется как база</th>";
    document.querySelector("#table-body").innerHTML = filters.length ? filters.map(filter => {
      const uses = configuredQuestions().filter(question => question.base_filter_id === filter.id).length;
      const usage = [];
      if (currentProject.configuration.report_filter_id === filter.id) usage.push("Общий фильтр");
      if (uses) usage.push(`${uses} вопр.`);
      return `<tr class="question-row ${filter.id === currentFilterId ? "selected" : ""}" data-filter-id="${filter.id}"><td><strong>${escapeHtml(filter.name)}</strong></td><td>${filter.rule.operator === "and" ? "И" : "ИЛИ"}</td><td>${countFilterConditions(filter.rule)}</td><td>${usage.length ? usage.join(" · ") : "—"}</td></tr>`;
    }).join("") : '<tr><td colspan="4" class="empty-state">Сохранённых баз и фильтров пока нет.</td></tr>';
    return;
  }
  if (currentView === "banners") {
    const banners = configuredBanners();
    const activeBannerId = selectedReportBannerId();
    document.querySelector("#table-head").innerHTML = "<th>Название</th><th>Статус</th><th>Блоков</th><th>Структура</th>";
    document.querySelector("#table-body").innerHTML = banners.length ? banners.map(banner => `
      <tr class="question-row ${banner.id === currentBannerId ? "selected" : ""}" data-banner-id="${banner.id}">
        <td><strong>${escapeHtml(banner.name)}</strong></td><td>${banner.id === activeBannerId ? '<span class="status">В Excel</span>' : '—'}</td><td>${banner.blocks.length}</td>
        <td>${banner.blocks.map(block => `<small>${block.sources.map(source => escapeHtml(bannerSourceLabel(source))).join(" → ")}</small>`).join("")}</td>
      </tr>`).join("") : '<tr><td colspan="4" class="empty-state">Баннеров нет. В Excel будет только Total.</td></tr>';
    return;
  }
  if (currentView === "recodings") {
    const recodings = configuredRecodings();
    document.querySelector("#table-head").innerHTML = "<th>Код</th><th>Название</th><th>Исходная переменная</th><th>Категорий</th><th>Диапазоны</th>";
    document.querySelector("#table-body").innerHTML = recodings.length ? recodings.map(recoding => `
      <tr class="question-row ${recoding.id === currentRecodingId ? "selected" : ""}" data-recode-id="${recoding.id}">
        <td><code>${escapeHtml(recoding.code)}</code></td>
        <td><strong>${escapeHtml(recoding.name)}</strong></td>
        <td><code>${escapeHtml(recoding.source_variable)}</code></td>
        <td>${recoding.categories.length}</td>
        <td>${recoding.categories.map(category => `<small>${escapeHtml(category.label)}: ${escapeHtml(formatRecodeCategory(recoding, category))}</small>`).join("")}</td>
      </tr>`).join("") : '<tr><td colspan="5" class="empty-state">Перекодировок пока нет. Создайте, например, возрастные группы.</td></tr>';
    return;
  }
  const allQuestions = configuredQuestions();
  const questions = allQuestions;
  document.querySelector("#table-head").innerHTML = "<th>Порядок</th><th>Код</th><th>Вопрос</th><th>Тип</th><th>Переменные</th><th>База</th><th>Статус</th>";
  document.querySelector("#table-body").innerHTML = questions.map((question, visibleIndex) => {
    const absoluteIndex = allQuestions.findIndex(item => item.code === question.code);
    const members = question.source_variables.length > 1 ? `<details class="inline-members"><summary>Состав блока · ${question.source_variables.length}</summary><div>${question.source_variables.map(name => { const variable = currentProject.inspection.variables.find(item => item.name === name); return `<p><code>${escapeHtml(name)}</code><span>${escapeHtml(variable?.label || name)}</span></p>`; }).join("")}</div></details>` : "";
    return `<tr class="question-row ${question.code === currentQuestionCode ? "selected" : ""}" data-code="${escapeHtml(question.code)}">
      <td class="order-buttons"><button type="button" data-code="${escapeHtml(question.code)}" data-move="-1" ${absoluteIndex === 0 ? "disabled" : ""}>↑</button><button type="button" data-code="${escapeHtml(question.code)}" data-move="1" ${absoluteIndex === allQuestions.length - 1 ? "disabled" : ""}>↓</button><span>${visibleIndex + 1}</span></td>
      <td><code>${escapeHtml(question.code)}</code></td>
      <td><div class="question-name"><strong>${escapeHtml(question.label)}</strong><button type="button" class="inline-edit" data-edit-label="${escapeAttribute(question.code)}" title="Изменить название для Excel">✎</button></div>${question.source_variables.length > 1 ? `<small class="group-hint">Название блока для Excel</small>` : ""}${members}${question.warnings.map(w => `<small>${escapeHtml(w)}</small>`).join("")}</td>
      <td><span class="type">${typeLabels[question.question_type] || escapeHtml(question.question_type)}</span></td>
      <td>${question.source_variables.length}</td><td>${question.valid_count.toLocaleString("ru-RU")}</td>
      <td><span class="status ${question.recognition === "auto_review" ? "review" : ""}">${question.included_in_report ? (question.recognition === "auto_review" ? "Проверить" : "В отчёте") : "Исключён"}</span></td>
    </tr>`;
  }).join("");
}

function renderPhysicalVariables() {
    document.querySelector("#table-head").innerHTML = "<th>Имя</th><th>Логический вопрос</th><th>Метка столбца</th><th>Формат</th><th>Measurement</th><th>Уникальных</th><th>Валидная база</th><th>Пропуски</th>";
    document.querySelector("#table-body").innerHTML = currentProject.inspection.variables.map(variable => `
      <tr>
        <td><code>${escapeHtml(variable.name)}</code></td><td>${logicalOwnerButton(variable.name)}</td><td><strong>${escapeHtml(variable.label)}</strong></td>
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
  filterEditor.hidden = false;
  const filter = filterId ? configuredFilters().find(item => item.id === filterId) : null;
  document.querySelector("#filter-editor-title").textContent = filter?.name || "Новое правило";
  document.querySelector("#filter-name").value = filter?.name || "";
  document.querySelector("#filter-operator").value = filter?.rule.operator || "and";
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
  element.innerHTML = `<select class="filter-source">${filterSourceOptions(sourceValue)}</select><select class="filter-operation">${filterOperatorOptions(condition.operator || "eq")}</select><input class="filter-value" value="${escapeAttribute(rawValue)}" placeholder="Значение или список" /><button type="button" data-remove-filter-condition title="Удалить условие">×</button>`;
  container.append(element);
}

function addFilterGroup(group = {}, container = document.querySelector("#filter-condition-list")) {
  const element = document.createElement("div");
  element.className = "filter-group";
  element.innerHTML = `<div class="filter-group-head"><strong>Вложенная группа</strong><select class="filter-group-operator"><option value="and" ${(group.operator || "and") === "and" ? "selected" : ""}>Все условия (И)</option><option value="or" ${group.operator === "or" ? "selected" : ""}>Хотя бы одно (ИЛИ)</option></select><button type="button" data-remove-filter-group title="Удалить группу">×</button></div><div class="filter-group-items"></div><button type="button" class="secondary compact-button" data-add-group-condition>+ Условие в группу</button>`;
  container.append(element);
  const nested = element.querySelector(".filter-group-items");
  const items = group.items || [];
  if (items.length) items.forEach(item => addFilterCondition(item, nested));
  else addFilterCondition({}, nested);
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

function parseFilterValue(value) {
  if (value !== "" && Number.isFinite(Number(value))) return Number(value);
  return value;
}

async function loadFilterPreview() {
  if (!currentProject) return;
  const container = document.querySelector("#filter-preview");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const payload = { name: document.querySelector("#filter-name").value.trim() || "Предпросмотр", rule: collectFilterRule() };
    const preview = await api(`/api/projects/${currentProject.id}/filters/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const warning = preview.empty ? "Пустая база — использовать её нельзя." : preview.small_base ? "Малая база: результаты будут отмечены серым." : "";
    const steps = preview.steps?.length ? `<div class="filter-steps">${preview.steps.map((step, index) => `<div><span>${index + 1}. ${escapeHtml(step.description)}</span><strong>N ${step.selected.toLocaleString("ru-RU")}</strong></div>`).join("")}</div>` : "";
    container.innerHTML = `<div class="filter-result"><strong>${preview.selected.toLocaleString("ru-RU")}</strong><span>из ${preview.total.toLocaleString("ru-RU")} · ${formatPercent(preview.share)}</span></div><p>${escapeHtml(preview.description)}</p>${steps}${warning ? `<p class="inline-warnings">${escapeHtml(warning)}</p>` : ""}`;
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
  } catch (error) {
    showError(errorBox, error);
  } finally {
    setBusy(button, false, "Сохранить как копию");
  }
}

function renderReportFilterControl() {
  const select = document.querySelector("#report-filter");
  if (!select || !currentProject) return;
  select.innerHTML = '<option value="">Без общего фильтра</option>' + configuredFilters().map(filter => `<option value="${filter.id}" ${currentProject.configuration.report_filter_id === filter.id ? "selected" : ""}>${escapeHtml(filter.name)}</option>`).join("");
}

function selectedReportBannerId() {
  const banners = configuredBanners();
  return Object.prototype.hasOwnProperty.call(currentProject.configuration, "report_banner_id")
    ? currentProject.configuration.report_banner_id
    : banners.at(-1)?.id || null;
}

function renderReportBannerControl() {
  const select = document.querySelector("#report-banner");
  if (!select || !currentProject) return;
  const selectedId = selectedReportBannerId();
  select.innerHTML = '<option value="">Только Total</option>' + configuredBanners().map(banner => `<option value="${banner.id}" ${selectedId === banner.id ? "selected" : ""}>${escapeHtml(banner.name)}</option>`).join("");
}

async function assignReportBanner() {
  const select = document.querySelector("#report-banner");
  select.disabled = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/report-banner`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ banner_id: select.value || null }),
    });
    renderProject();
  } catch (error) {
    alert(error.message);
    renderReportBannerControl();
  } finally {
    select.disabled = false;
  }
}

async function assignReportFilter() {
  const select = document.querySelector("#report-filter");
  select.disabled = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/report-filter`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filter_id: select.value || null }),
    });
    renderProject();
  } catch (error) {
    alert(error.message);
    renderReportFilterControl();
  } finally {
    select.disabled = false;
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
  document.querySelector("#weight-editor-title").textContent = weight ? weight.name : "Новый вес";
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
  document.querySelector("#weight-error").hidden = true;
  document.querySelector("#weight-preview").innerHTML = weight
    ? '<p class="muted">Считаем…</p>'
    : '<p class="muted">Сохраните вес для расчёта.</p>';
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
  element.className = "weight-dimension";
  element.innerHTML = `<div class="weight-dimension-head"><select class="weight-dimension-source">${weightSourceOptions(dimension.variable)}</select><button type="button" data-remove-weight-dimension title="Удалить распределение">×</button></div><div class="weight-targets"></div>`;
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
    return `<label class="weight-target" data-value="${encoded}"><span>${escapeHtml(item.label)}</span><input type="number" min="0.0001" max="100" step="0.0001" value="${saved?.percent ?? equalTarget}" required /></label>`;
  }).join("");
}

function collectWeightDimensions() {
  const elements = [...document.querySelectorAll("#weight-dimension-list .weight-dimension")];
  if (!elements.length) throw new Error("Добавьте хотя бы одно целевое распределение.");
  return elements.map(element => {
    const variableName = element.querySelector(".weight-dimension-source").value;
    const variable = currentProject.inspection.variables.find(item => item.name === variableName);
    const targets = [...element.querySelectorAll(".weight-target")].map(row => ({
      label: row.querySelector("span").textContent,
      values: [JSON.parse(row.dataset.value)],
      percent: Number(row.querySelector("input").value),
    }));
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
    [preview.effective_base, "Эффективная база"], [preview.design_effect, "Design effect"],
    [preview.efficiency_percent, "Эффективность, %"], [preview.iterations, "Итераций"],
  ];
  return `<div class="weight-diagnostics">${metrics.map(([value, label]) => `<article><strong>${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 })}</strong><span>${label}</span></article>`).join("")}</div>${preview.distributions.map(dimension => `<div class="weight-distribution"><strong>${escapeHtml(dimension.label)}</strong>${dimension.categories.map(category => `<p><span>${escapeHtml(category.label)}</span><em>${category.before_percent.toFixed(1)}% → ${category.after_percent.toFixed(1)}% · цель ${category.target_percent.toFixed(1)}%</em></p>`).join("")}</div>`).join("")}`;
}

async function deleteWeight() {
  if (!currentProject || !currentWeightId) return;
  const errorBox = document.querySelector("#weight-error");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/weights/${currentWeightId}`, { method: "DELETE" });
    closeWeight();
    renderProject();
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
  document.querySelector("#banner-editor-title").textContent = banner?.name || "Новый баннер";
  document.querySelector("#banner-name").value = banner?.name || `Баннер ${configuredBanners().length + 1}`;
  document.querySelector("#banner-confidence").value = (banner?.confidence_level || 0.95) * 100;
  document.querySelector("#banner-compare-total").checked = banner?.compare_to_total ?? banner?.blocks.some(block => block.compare_to_total) ?? false;
  document.querySelector("#banner-compare-pairwise").checked = banner?.compare_pairwise ?? banner?.blocks.some(block => block.compare_pairwise) ?? false;
  document.querySelector("#banner-bonferroni").checked = banner?.bonferroni || false;
  document.querySelector("#banner-minimum-base").value = banner?.minimum_base || 30;
  const waveMode = banner?.wave_comparison || "none";
  document.querySelector("#banner-wave-comparison").value = waveMode;
  const waveQuestion = currentProject.configuration.questions.find(question => question.role === "wave");
  const waveVariable = waveQuestion
    ? currentProject.inspection.variables.find(variable => variable.name === waveQuestion.source_variables?.[0])
    : null;
  const controlSelect = document.querySelector("#banner-wave-control");
  controlSelect.innerHTML = (waveVariable?.value_labels || []).map(item => {
    const selected = String(item.value) === String(banner?.wave_control_value) ? "selected" : "";
    return `<option value="${escapeAttribute(JSON.stringify(item.value))}" ${selected}>${escapeHtml(item.label)}</option>`;
  }).join("");
  document.querySelector("#banner-wave-control-label").hidden = waveMode !== "control";
  const weightSelect = document.querySelector("#banner-weight");
  const selectedWeight = banner?.calculated_weight_id
    ? `calculated:${banner.calculated_weight_id}`
    : banner?.weight_variable ? `ready:${banner.weight_variable}` : "";
  const readyOptions = currentProject.inspection.variables
    .filter(variable => variable.storage_type === "numeric")
    .map(variable => `<option value="ready:${escapeAttribute(variable.name)}" ${selectedWeight === `ready:${variable.name}` ? "selected" : ""}>Готовый: ${escapeHtml(variable.name)} — ${escapeHtml(variable.label)}</option>`)
    .join("");
  const calculatedOptions = configuredWeights()
    .map(weight => `<option value="calculated:${weight.id}" ${selectedWeight === `calculated:${weight.id}` ? "selected" : ""}>Рассчитанный: ${escapeHtml(weight.name)}</option>`)
    .join("");
  weightSelect.innerHTML = '<option value="">Без веса</option>' + readyOptions + calculatedOptions;
  const list = document.querySelector("#banner-block-list");
  list.innerHTML = "";
  if (banner) banner.blocks.forEach(block => addBannerBlock(block));
  else addBannerBlock();
  document.querySelector("#delete-banner").hidden = !banner;
  document.querySelector("#banner-error").hidden = true;
  document.querySelector("#banner-preview").innerHTML = banner
    ? '<p class="muted">Считаем…</p>'
    : '<p class="muted">Сохраните баннер для расчёта.</p>';
  renderTable();
  if (banner) loadBannerPreview();
}

document.querySelector("#banner-wave-comparison").addEventListener("change", event => {
  document.querySelector("#banner-wave-control-label").hidden = event.target.value !== "control";
});

function closeBanner() {
  bannerEditor.hidden = true;
  currentBannerId = null;
  renderTable();
}

function addBannerBlock(block = {}) {
  const first = block.sources?.[0];
  const second = block.sources?.[1];
  const element = document.createElement("div");
  element.className = "banner-block";
  element.innerHTML = `<div class="banner-block-head"><input class="banner-block-label" placeholder="Название блока — необязательно" value="${escapeAttribute(block.label || "")}" /><button type="button" data-remove-banner-block title="Удалить блок">×</button></div><label>Первый уровень<select class="banner-source-first">${bannerSourceOptions(first, false)}</select></label><label>Вложить второй уровень<select class="banner-source-second">${bannerSourceOptions(second, true)}</select></label>`;
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
  return `<div class="banner-preview-list">${preview.columns.map((column, index) => `<div class="${index === 0 ? "total-column" : ""}"><span>${escapeHtml(column.block || "Общий итог")}</span><strong>${escapeHtml(column.label)}</strong><em>База ${column.base.toLocaleString("ru-RU")}</em></div>`).join("")}</div>`;
}

async function deleteBanner() {
  if (!currentBannerId || !confirm("Удалить этот баннер?")) return;
  const errorBox = document.querySelector("#banner-error");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/banners/${currentBannerId}`, { method: "DELETE" });
    closeBanner();
    renderProject();
  } catch (error) {
    showError(errorBox, error);
  }
}

function openRecoding(recodingId = null) {
  currentRecodingId = recodingId;
  currentQuestionCode = null;
  editor.hidden = true;
  recodeEditor.hidden = false;
  const recoding = recodingId ? configuredRecodings().find(item => item.id === recodingId) : null;
  document.querySelector("#recode-editor-title").textContent = recoding ? recoding.code : "Новая";
  document.querySelector("#recode-code").value = recoding?.code || suggestRecodeCode();
  document.querySelector("#recode-name").value = recoding?.name || "";
  document.querySelector("#recode-mode").value = recoding?.mode || "ranges";
  fillRecodeSources(recoding?.source_variable);
  const rangeList = document.querySelector("#range-list");
  rangeList.innerHTML = "";
  const categoryList = document.querySelector("#category-group-list");
  categoryList.innerHTML = "";
  if (recoding?.mode === "categories") {
    recoding.categories.forEach(category => addCategoryGroup(category));
  } else if (recoding) {
    recoding.categories.forEach(category => addRangeRow(category));
  } else {
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
    currentProject = await api(`/api/projects/${currentProject.id}/recodings/${currentRecodingId}`, { method: "DELETE" });
    closeRecoding();
    renderProject();
  } catch (error) {
    showError(recodeError, error);
  }
}

async function moveQuestion(code, direction) {
  const codes = configuredQuestions().map(item => item.code);
  const index = codes.indexOf(code);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= codes.length) return;
  [codes[index], codes[target]] = [codes[target], codes[index]];
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/questions/order`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codes }),
    });
    renderProject();
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
  document.querySelector("#editor-code").textContent = question.code;
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
}

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
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
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

function configuredRecodings() {
  return currentProject.configuration?.recodings || [];
}

function configuredBanners() {
  return currentProject.configuration?.banners || [];
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

async function api(url, options = {}) {
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
