/* Карточка вопроса: тип, база, NET, набор вывода, пропуски и предпросмотр. Классический скрипт после app.js (см. решение 016): пользуется
   его состоянием, а app.js вызывает эти функции только после запуска. */

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
  document.querySelector("#ranking-encoding").value = question.ranking_encoding || "rank_per_item";
  document.querySelector("#ranking-encoding-field").hidden = question.question_type !== "ranking";
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
  // Разобрать можно любую группу из переменных SAV, а не только собранную
  // руками: ошибка автоопределения исправляется тем же действием.
  const splittable = !question.formula_id && !question.codeframe_id;
  const ungroup = splittable
    ? `<button type="button" class="text-button" data-ungroup="${escapeAttribute(question.code)}">Разгруппировать</button>`
    : "";
  container.innerHTML = items.length < 2 ? "" : `<div><strong>Состав блока · ${items.length}</strong><small>Общие настройки выше применяются ко всем пунктам.</small>${ungroup}</div><div class="member-list">${items.map(item => `<p><code>${escapeHtml(item.variable)}</code><span>${escapeHtml(item.label)}</span></p>`).join("")}</div>`;
}

document.querySelector("#question-members").addEventListener("click", async event => {
  const button = event.target.closest("[data-ungroup]");
  if (!button || !currentProject) return;
  const code = button.dataset.ungroup;
  const question = configuredQuestions().find(item => item.code === code);
  const count = question?.source_variables.length || 0;
  if (!confirm(`Разобрать ${code} на ${plural(count, "отдельный вопрос", "отдельных вопроса", "отдельных вопросов")}? Настройки группы — NET, набор вывода, база — не перейдут.`)) return;
  button.disabled = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/questions/${encodeURIComponent(code)}/ungroup`, { method: "POST" });
    closeQuestionEditor();
    renderProject();
    showToast(`${code} разгруппирован`);
  } catch (error) {
    alert(error.message);
    button.disabled = false;
  }
});

// Подпись говорит, что пометка меняет на самом деле: у одиночного выбора она
// не меняла ни одного числа, поэтому панели у него нет (GAP-003).
const specialAnswerEffects = {
  multiple_choice_dichotomy: "Помечаются в предпросмотре; предупредим, если выбраны вместе с обычным вариантом. Числа книги не меняются",
  scale: "Остаются в распределении и базе. Не входят в среднее и Top/Bottom; на листе от ответивших Top/Bottom считается без них",
  matrix: "Остаются в распределении и базе. Не входят в среднее и Top/Bottom; на листе от ответивших Top/Bottom считается без них",
};

function renderSpecialAnswers(question) {
  const section = document.querySelector("#special-answers");
  const list = document.querySelector("#special-answer-list");
  const effect = specialAnswerEffects[question.question_type];
  if (effect) section.querySelector(".grp-cap small").textContent = effect;
  if (question.question_type === "multiple_choice_dichotomy") {
    const items = question.items || [];
    section.hidden = items.length === 0;
    list.innerHTML = items.map(item => `<label class="checkbox"><input type="checkbox" data-special-item="${escapeAttribute(item.variable)}" ${(question.special_items || []).includes(item.variable) ? "checked" : ""} /> ${escapeHtml(item.label)}</label>`).join("");
    return;
  }
  if (!effect) {
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
const NET_TYPES = ["single_choice", "scale", "multiple_choice_dichotomy", "multiple_choice_categorical", "matrix"];
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
  if (type === "ranking") return rankingMetricOptions;
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
  const reportSet = question.question_type === "ranking" ? settings.ranking_metrics
    : question.question_type === "numeric" ? settings.numeric_metrics : settings.scale_metrics;
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
