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
let currentProject = null;
let currentQuestionCode = null;
let currentRecodingId = null;
let currentBannerId = null;
let currentView = "questions";

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
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = true;
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

document.querySelectorAll(".tabs button[data-view]").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach(item => item.classList.remove("active"));
  button.classList.add("active");
  currentView = button.dataset.view;
  document.querySelector("#recode-toolbar").hidden = currentView !== "recodings";
  document.querySelector("#banner-toolbar").hidden = currentView !== "banners";
  if (currentView === "recodings") {
    editor.hidden = true;
    bannerEditor.hidden = true;
    currentQuestionCode = null;
    currentBannerId = null;
  } else if (currentView === "banners") {
    editor.hidden = true;
    recodeEditor.hidden = true;
    currentQuestionCode = null;
    currentRecodingId = null;
  } else {
    recodeEditor.hidden = true;
    bannerEditor.hidden = true;
    currentRecodingId = null;
    currentBannerId = null;
  }
  renderTable();
}));

document.querySelector("#table-body").addEventListener("click", event => {
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
  const moveButton = event.target.closest("button[data-move]");
  if (moveButton) {
    moveQuestion(moveButton.dataset.code, Number(moveButton.dataset.move));
    return;
  }
  const row = event.target.closest("tr[data-code]");
  if (row && currentView !== "variables") openQuestion(row.dataset.code);
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
  const payload = { name: document.querySelector("#banner-name").value.trim(), blocks };
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
      currentBannerId = configuredBanners().find(item => item.name === payload.name)?.id;
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
          ...collectSpecialAnswers(),
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
  currentView = "questions";
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = true;
  document.querySelector("#recode-toolbar").hidden = true;
  document.querySelector("#banner-toolbar").hidden = true;
  document.querySelectorAll(".tabs button").forEach(button => {
    button.classList.toggle("active", button.dataset.view === "questions");
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
  document.querySelector("#summary").innerHTML = [
    [inspection.row_count.toLocaleString("ru-RU"), "респондентов"],
    [inspection.variable_count.toLocaleString("ru-RU"), "переменных"],
    [questions.length.toLocaleString("ru-RU"), "вопросов"],
    [questions.filter(item => item.included_in_report).length, "включено в отчёт"],
  ].map(([value, label]) => `<article><strong>${value}</strong><span>${label}</span></article>`).join("");
  const warningBox = document.querySelector("#warnings");
  warningBox.hidden = inspection.warnings.length === 0;
  warningBox.innerHTML = inspection.warnings.map(text => `<p>⚑ ${escapeHtml(text)}</p>`).join("");
  renderTable();
}

function renderTable() {
  if (!currentProject) return;
  if (currentView === "banners") {
    const banners = configuredBanners();
    document.querySelector("#table-head").innerHTML = "<th>Название</th><th>Блоков</th><th>Структура</th>";
    document.querySelector("#table-body").innerHTML = banners.length ? banners.map(banner => `
      <tr class="question-row ${banner.id === currentBannerId ? "selected" : ""}" data-banner-id="${banner.id}">
        <td><strong>${escapeHtml(banner.name)}</strong></td><td>${banner.blocks.length}</td>
        <td>${banner.blocks.map(block => `<small>${block.sources.map(source => escapeHtml(bannerSourceLabel(source))).join(" → ")}</small>`).join("")}</td>
      </tr>`).join("") : '<tr><td colspan="3" class="empty-state">Баннеров пока нет. Total будет добавлен автоматически.</td></tr>';
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
  if (currentView === "variables") {
    document.querySelector("#table-head").innerHTML = "<th>Имя</th><th>Метка</th><th>Формат</th><th>Measurement</th><th>Уникальных</th><th>Валидная база</th><th>Пропуски</th>";
    document.querySelector("#table-body").innerHTML = currentProject.inspection.variables.map(variable => `
      <tr>
        <td><code>${escapeHtml(variable.name)}</code></td><td><strong>${escapeHtml(variable.label)}</strong></td>
        <td>${escapeHtml(variable.original_format || variable.storage_type)}</td><td>${escapeHtml(variable.measurement_level || "—")}</td>
        <td>${variable.unique_count.toLocaleString("ru-RU")}</td><td>${variable.valid_count.toLocaleString("ru-RU")}</td><td>${variable.missing_count.toLocaleString("ru-RU")}</td>
      </tr>`).join("");
    return;
  }
  const allQuestions = configuredQuestions();
  const questions = currentView === "excluded" ? allQuestions.filter(item => !item.included_in_report) : allQuestions;
  document.querySelector("#table-head").innerHTML = "<th>Порядок</th><th>Код</th><th>Вопрос</th><th>Тип</th><th>Переменные</th><th>База</th><th>Статус</th>";
  document.querySelector("#table-body").innerHTML = questions.map((question, visibleIndex) => {
    const absoluteIndex = allQuestions.findIndex(item => item.code === question.code);
    return `<tr class="question-row ${question.code === currentQuestionCode ? "selected" : ""}" data-code="${escapeHtml(question.code)}">
      <td class="order-buttons"><button type="button" data-code="${escapeHtml(question.code)}" data-move="-1" ${absoluteIndex === 0 ? "disabled" : ""}>↑</button><button type="button" data-code="${escapeHtml(question.code)}" data-move="1" ${absoluteIndex === allQuestions.length - 1 ? "disabled" : ""}>↓</button><span>${visibleIndex + 1}</span></td>
      <td><code>${escapeHtml(question.code)}</code></td>
      <td><strong>${escapeHtml(question.label)}</strong>${question.warnings.map(w => `<small>${escapeHtml(w)}</small>`).join("")}</td>
      <td><span class="type">${typeLabels[question.question_type] || escapeHtml(question.question_type)}</span></td>
      <td>${question.source_variables.length}</td><td>${question.valid_count.toLocaleString("ru-RU")}</td>
      <td><span class="status ${question.recognition === "auto_review" ? "review" : ""}">${question.included_in_report ? (question.recognition === "auto_review" ? "Проверить" : "В отчёте") : "Исключён"}</span></td>
    </tr>`;
  }).join("");
}

function openBanner(bannerId = null) {
  currentBannerId = bannerId;
  currentQuestionCode = null;
  currentRecodingId = null;
  editor.hidden = true;
  recodeEditor.hidden = true;
  bannerEditor.hidden = false;
  const banner = bannerId ? configuredBanners().find(item => item.id === bannerId) : null;
  document.querySelector("#banner-editor-title").textContent = banner ? banner.name : "Новый";
  document.querySelector("#banner-name").value = banner?.name || "Основной баннер";
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
  document.querySelector("#editor-code").textContent = question.code;
  document.querySelector("#question-label").value = question.label;
  document.querySelector("#question-type").value = question.question_type;
  document.querySelector("#question-role").value = question.role;
  document.querySelector("#question-included").checked = question.included_in_report;
  document.querySelector("#editor-error").hidden = true;
  renderQuestionMembers(question);
  renderSpecialAnswers(question);
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
