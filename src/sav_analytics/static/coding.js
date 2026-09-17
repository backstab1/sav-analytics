/* Раздел «Открытые ответы». Классический скрипт после app.js: пользуется его состоянием
   (currentProject, api, renderProject) и объявляет функции, которые app.js
   вызывает только по действию аналитика. */

/* Раздел «Открытые ответы»: кодификатор слева, ответы справа. Совпадения
   запросов, отметки и счётчики считает сервер; экран хранит только то, что
   аналитик ещё не сохранил в списке тем. */
const answerPage = { offset: 0, limit: 50 };

function openTextQuestions() {
  return configuredQuestions().filter(question => question.question_type === "open_text" && question.source_variables.length === 1);
}

function currentCodeframe() {
  const code = document.querySelector("#text-question").value;
  return (currentProject.configuration.codeframes || []).find(item => item.question_code === code) || null;
}

let textCandidates = null;

async function renderTextSection() {
  if (!currentProject) return;
  const select = document.querySelector("#text-question");
  const previous = select.value;
  const questions = openTextQuestions();
  // Порядок и группы — по содержимому: служебные поля (логин, дата, телефон)
  // тоже текст, но в них нет ответов из нескольких слов.
  if (!textCandidates || textCandidates.project !== currentProject.id) {
    try {
      const result = await api(`/api/projects/${currentProject.id}/codeframes/candidates`);
      textCandidates = { project: currentProject.id, ...result };
    } catch {
      textCandidates = { project: currentProject.id, questions: [], wordy_share: 0 };
    }
  }
  const profiles = new Map(textCandidates.questions.map(item => [item.code, item]));
  const rank = new Map(textCandidates.questions.map((item, index) => [item.code, index]));
  const ordered = [...questions].sort((left, right) => (rank.get(left.code) ?? 1e9) - (rank.get(right.code) ?? 1e9));
  const option = question => {
    const profile = profiles.get(question.code);
    const hint = profile ? ` · ${profile.answered} отв.` : "";
    return `<option value="${escapeAttribute(question.code)}" title="${escapeAttribute(profile?.example || "")}">${escapeHtml(question.code)} — ${escapeHtml(question.label)}${hint}</option>`;
  };
  const answers = ordered.filter(question => profiles.get(question.code)?.respondent_answers ?? true);
  const service = ordered.filter(question => !answers.includes(question));
  select.innerHTML = `${answers.length ? `<optgroup label="Ответы респондентов">${answers.map(option).join("")}</optgroup>` : ""}${service.length ? `<optgroup label="Служебные и короткие поля">${service.map(option).join("")}</optgroup>` : ""}`;
  if (previous && questions.some(question => question.code === previous)) select.value = previous;
  const empty = document.querySelector("#text-empty");
  empty.hidden = questions.length > 0;
  empty.textContent = "Открытых вопросов в проекте нет. Отметьте тип «Открытый текст» у вопроса в структуре.";
  const codeframe = questions.length ? currentCodeframe() : null;
  document.querySelector("#create-codeframe").hidden = !questions.length || Boolean(codeframe);
  document.querySelector("#coding-body").hidden = !codeframe;
  if (!codeframe) return;
  renderThemeRows(codeframe.themes);
  const exportLink = document.querySelector("#export-codeframe");
  exportLink.href = `/api/projects/${currentProject.id}/codeframes/${codeframe.id}/export`;
  exportLink.download = `кодификатор_${codeframe.question_code}.json`;
  answerPage.offset = 0;
  void refreshCoding();
}

function themeRowHtml(theme, themes) {
  const parents = themes.filter(item => !item.parent_id && item.id !== theme.id);
  return `<div class="theme-row" data-theme-id="${escapeAttribute(theme.id)}">
    <div class="theme-row-head">
      <input class="theme-name" value="${escapeAttribute(theme.name || "")}" placeholder="Название темы" aria-label="Название темы" />
      <span class="theme-count" data-count-for="${escapeAttribute(theme.id)}"></span>
      <button type="button" class="del" data-remove-theme aria-label="Удалить тему">×</button>
    </div>
    <select class="theme-parent" aria-label="Родительская тема"><option value="">Без родителя</option>${parents.map(item => `<option value="${escapeAttribute(item.id)}" ${theme.parent_id === item.id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select>
    <textarea class="theme-queries" rows="2" placeholder="Запросы — по одному в строке" aria-label="Запросы темы">${escapeHtml((theme.queries || []).join("\n"))}</textarea>
  </div>`;
}

function renderThemeRows(themes) {
  document.querySelector("#theme-list").innerHTML = themes.map(theme => themeRowHtml(theme, themes)).join("")
    || '<p class="analysis-note">Тем пока нет. Добавьте первую.</p>';
}

function collectThemes() {
  return [...document.querySelectorAll("#theme-list .theme-row")].map(row => ({
    id: row.dataset.themeId,
    name: row.querySelector(".theme-name").value.trim(),
    parent_id: row.querySelector(".theme-parent").value || null,
    queries: row.querySelector(".theme-queries").value.split("\n").map(line => line.trim()).filter(Boolean),
  }));
}

let newThemeCounter = 0;
document.querySelector("#add-theme").addEventListener("click", () => {
  const themes = collectThemes();
  newThemeCounter += 1;
  themes.push({ id: `new-${newThemeCounter}`, name: "", parent_id: null, queries: [] });
  renderThemeRows(themes);
  document.querySelector("#theme-list .theme-row:last-child .theme-name").focus();
});

document.querySelector("#theme-list").addEventListener("click", event => {
  if (!event.target.closest("[data-remove-theme]")) return;
  const row = event.target.closest(".theme-row");
  const themes = collectThemes().filter(theme => theme.id !== row.dataset.themeId)
    .map(theme => theme.parent_id === row.dataset.themeId ? { ...theme, parent_id: null } : theme);
  renderThemeRows(themes);
});

document.querySelector("#text-question").addEventListener("change", () => void renderTextSection());

document.querySelector("#create-codeframe").addEventListener("click", async () => {
  const errorBox = document.querySelector("#text-error");
  errorBox.hidden = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/codeframes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_code: document.querySelector("#text-question").value }),
    });
    renderProject();
    textCandidates = null;
    await renderTextSection();
  } catch (error) {
    showError(errorBox, error);
  }
});

document.querySelector("#save-themes").addEventListener("click", async () => {
  const codeframe = currentCodeframe();
  const errorBox = document.querySelector("#text-error");
  errorBox.hidden = true;
  if (!codeframe) return;
  const button = document.querySelector("#save-themes");
  button.disabled = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: codeframe.label, themes: collectThemes() }),
    });
    renderProject();
    await renderTextSection();
    showToast("Темы сохранены");
  } catch (error) {
    showError(errorBox, error);
  } finally {
    button.disabled = false;
  }
});

async function refreshCoding() {
  const codeframe = currentCodeframe();
  if (!codeframe) return;
  const filter = document.querySelector("#answer-filter");
  const previous = filter.value;
  filter.innerHTML = '<option value="">Все ответы</option><option value="uncoded">Без темы</option>'
    + codeframe.themes.map(theme => `<option value="${escapeAttribute(theme.id)}">${escapeHtml(theme.name)}</option>`).join("");
  if ([...filter.options].some(option => option.value === previous)) filter.value = previous;
  try {
    const summary = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}/summary`);
    document.querySelector("#coding-stats").textContent = `Ответили ${summary.answered.toLocaleString("ru-RU")} · без темы ${summary.uncoded.toLocaleString("ru-RU")}`;
    summary.themes.forEach(item => {
      const cell = document.querySelector(`[data-count-for="${CSS.escape(item.id)}"]`);
      if (cell) cell.textContent = `${item.count.toLocaleString("ru-RU")}${item.manual ? ` · вручную ${item.manual}` : ""}`;
    });
  } catch (error) {
    document.querySelector("#coding-stats").textContent = error.message;
  }
  await loadAnswers(false);
}

async function loadAnswers(append) {
  const codeframe = currentCodeframe();
  if (!codeframe) return;
  if (!append) answerPage.offset = 0;
  const filter = document.querySelector("#answer-filter").value;
  const params = new URLSearchParams({ offset: String(answerPage.offset), limit: String(answerPage.limit) });
  if (filter === "uncoded") params.set("uncoded", "true");
  else if (filter) params.set("theme_id", filter);
  const search = document.querySelector("#answer-search").value.trim();
  if (search) params.set("search", search);
  const list = document.querySelector("#answer-list");
  try {
    const result = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}/answers?${params}`);
    const names = new Map(codeframe.themes.map(theme => [theme.id, theme.name]));
    const html = result.rows.map(row => {
      const marked = new Set(row.themes.map(item => item.id));
      const chips = row.themes.map(item => `<button type="button" class="answer-chip" data-unmark="${escapeAttribute(item.id)}" data-row="${row.row}" title="${item.source === "manual" ? "Отмечено вручную" : item.source === "query" ? "Найдено запросом" : "Через дочернюю тему"}. Щёлкните, чтобы снять">${escapeHtml(names.get(item.id) || "")}<small>${item.source === "manual" ? "вручную" : item.source === "query" ? "запрос" : "дочерняя"}</small></button>`).join("");
      const options = codeframe.themes.filter(theme => !marked.has(theme.id))
        .map(theme => `<option value="${escapeAttribute(theme.id)}">${escapeHtml(theme.name)}</option>`).join("");
      return `<article class="answer-row"><p>${escapeHtml(row.text)}</p><div class="answer-marks">${chips}${options ? `<select class="answer-add" data-row="${row.row}" aria-label="Отметить тему"><option value="">+ тема</option>${options}</select>` : ""}</div></article>`;
    }).join("");
    list.innerHTML = append ? list.innerHTML + html : (html || '<p class="analysis-note">Ответов нет.</p>');
    answerPage.offset += result.rows.length;
    document.querySelector("#answer-count").textContent = `Показано ${Math.min(answerPage.offset, result.total).toLocaleString("ru-RU")} из ${result.total.toLocaleString("ru-RU")}`;
    document.querySelector("#more-answers").hidden = answerPage.offset >= result.total;
  } catch (error) {
    list.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

async function markAnswer(themeId, row, value) {
  const codeframe = currentCodeframe();
  if (!codeframe) return;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}/marks`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme_id: themeId, row, value }),
    });
    await refreshCoding();
  } catch (error) {
    alert(error.message);
  }
}

document.querySelector("#answer-list").addEventListener("click", event => {
  const chip = event.target.closest("[data-unmark]");
  if (chip) void markAnswer(chip.dataset.unmark, Number(chip.dataset.row), false);
});
document.querySelector("#answer-list").addEventListener("change", event => {
  const select = event.target.closest(".answer-add");
  if (select?.value) void markAnswer(select.value, Number(select.dataset.row), true);
});
document.querySelector("#answer-filter").addEventListener("change", () => loadAnswers(false));
let answerSearchTimer = null;
document.querySelector("#answer-search").addEventListener("input", () => {
  window.clearTimeout(answerSearchTimer);
  answerSearchTimer = window.setTimeout(() => loadAnswers(false), 300);
});
document.querySelector("#more-answers").addEventListener("click", () => loadAnswers(true));

// Кодификатор из другого проекта или волны: темы и запросы подставляются в
// список как новые — сохранить их аналитик решает сам, после просмотра.
document.querySelector("#import-codeframe").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  const errorBox = document.querySelector("#text-error");
  errorBox.hidden = true;
  if (!file) return;
  try {
    const definition = JSON.parse(await file.text());
    if (definition.format !== "sav-analytics/codeframe" || !Array.isArray(definition.themes)) {
      throw new Error("Это не файл кодификатора sav-analytics.");
    }
    const existing = collectThemes();
    const taken = new Set(existing.map(theme => theme.name.trim().toLowerCase()));
    const ids = new Map();
    const imported = [];
    definition.themes.forEach(theme => {
      if (taken.has(String(theme.name).trim().toLowerCase())) return;
      newThemeCounter += 1;
      ids.set(theme.id, `new-${newThemeCounter}`);
      imported.push({ ...theme, id: `new-${newThemeCounter}` });
    });
    imported.forEach(theme => { theme.parent_id = theme.parent_id ? ids.get(theme.parent_id) || null : null; });
    renderThemeRows([...existing, ...imported]);
    const skipped = definition.themes.length - imported.length;
    showToast(`Добавлено тем: ${imported.length}${skipped ? `, пропущено совпадающих: ${skipped}` : ""}. Сохраните темы.`);
  } catch (error) {
    showError(errorBox, error);
  }
});
