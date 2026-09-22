/* Раздел «Анализ». Классический скрипт после app.js: пользуется его состоянием
   (currentProject, api, renderProject) и объявляет функции, которые app.js
   вызывает только по действию аналитика. */

/* Раздел «Анализ»: карточки связи двух переменных. Экран ничего не считает —
   тест, эффект, поправку BH и вывод отдаёт сервер одним запросом на все
   карточки, потому что поправка зависит от их числа. */
const ANALYSIS_TYPES = ["single_choice", "scale", "numeric"];
const EFFECT_LABELS = { cramers_v: "V Крамера", cohens_d: "d Коэна", cohens_f: "f Коэна", r: "r" };

function analysisSources() {
  const questions = configuredQuestions()
    .filter(question => ANALYSIS_TYPES.includes(question.question_type) && question.source_variables.length === 1)
    .map(question => ({ value: `question:${question.code}`, label: `${question.code} — ${question.label}` }));
  const recodings = configuredRecodings()
    .map(recoding => ({ value: `recoding:${recoding.id}`, label: `↳ ${recoding.code} — ${recoding.name}` }));
  return [...questions, ...recodings];
}

function renderAnalysisSection() {
  if (!currentProject) return;
  const variableSelect = document.querySelector("#variable-source");
  const previousVariable = variableSelect.value;
  variableSelect.innerHTML = analysisSources()
    .map(item => `<option value="${escapeAttribute(item.value)}">${escapeHtml(item.label)}</option>`).join("");
  if (previousVariable) variableSelect.value = previousVariable;
  const options = analysisSources()
    .map(item => `<option value="${escapeAttribute(item.value)}">${escapeHtml(item.label)}</option>`).join("");
  ["#analysis-a", "#analysis-b"].forEach((selector, index) => {
    const select = document.querySelector(selector);
    const previous = select.value;
    select.innerHTML = options;
    if (previous) select.value = previous;
    else if (select.options.length > index) select.selectedIndex = index;
  });
  void loadAnalysisCards();
  renderModelForm();
  void loadAnalysisModels();
  renderMethodFields();
}

/* Методы исследования (PQ.15): TURF, Van Westendorp, Gabor–Granger. Считает
   сервер, по запросу; экран собирает параметры и показывает результат. */
function questionOptions(filter) {
  return configuredQuestions().filter(filter)
    .map(question => `<option value="${escapeAttribute(question.code)}">${escapeHtml(question.code)} — ${escapeHtml(question.label)}</option>`).join("");
}

const PRICE_ROLES = [
  ["too_cheap", "Слишком дёшево"], ["cheap", "Дёшево, выгодно"],
  ["expensive", "Дорого, но можно купить"], ["too_expensive", "Слишком дорого"],
];

function renderMethodFields() {
  const kind = document.querySelector("#method-kind").value;
  const box = document.querySelector("#method-fields");
  const numeric = questionOptions(question => ["numeric", "scale"].includes(question.question_type) && question.source_variables.length === 1);
  if (kind === "turf") {
    const multiple = questionOptions(question => ["multiple_choice_dichotomy", "multiple_choice_categorical"].includes(question.question_type));
    box.innerHTML = multiple
      ? `<label>Вопрос с несколькими ответами<select id="turf-question">${multiple}</select></label><label>Портфель до<input id="turf-size" type="number" min="1" max="8" value="3" /></label>`
      : '<p class="analysis-note">В проекте нет вопросов с несколькими ответами.</p>';
  } else if (kind === "van-westendorp") {
    box.innerHTML = PRICE_ROLES.map(([role, label], index) =>
      `<label>${label}<select data-price-role="${role}">${numeric}</select></label>`).join("");
    box.querySelectorAll("select").forEach((select, index) => {
      if (select.options.length > index) select.selectedIndex = index;
    });
  } else {
    box.innerHTML = '<div id="gg-steps" class="gg-steps"></div><button id="add-gg-step" class="secondary compact-button" type="button">+ Цена</button>';
    addGaborStep();
    addGaborStep();
  }
}

function addGaborStep() {
  const single = questionOptions(question => question.question_type === "single_choice" && question.source_variables.length === 1);
  const row = document.createElement("div");
  row.className = "gg-step";
  row.innerHTML = `<label>Вопрос о покупке<select class="gg-question">${single}</select></label><label>Цена<input class="gg-price" type="number" min="0.01" step="0.01" required /></label><div class="gg-codes"></div>`;
  document.querySelector("#gg-steps").append(row);
  const select = row.querySelector(".gg-question");
  const steps = document.querySelectorAll("#gg-steps .gg-step").length;
  if (select.options.length >= steps) select.selectedIndex = steps - 1;
  renderGaborCodes(row);
}

function renderGaborCodes(row) {
  const code = row.querySelector(".gg-question").value;
  const question = configuredQuestions().find(item => item.code === code);
  const variable = currentProject.inspection.variables.find(item => item.name === question?.source_variables?.[0]);
  row.querySelector(".gg-codes").innerHTML = `<span>«Куплю» —</span>${(variable?.value_labels || []).map((item, index) =>
    `<label class="checkbox"><input type="checkbox" value="${escapeAttribute(String(item.value))}" ${index === 0 ? "checked" : ""} /> ${escapeHtml(item.label)}</label>`).join("")}`;
}

function methodPayload(kind) {
  if (kind === "turf") {
    const question = document.querySelector("#turf-question");
    if (!question) throw new Error("В проекте нет вопросов с несколькими ответами.");
    return { code: question.value, max_size: Number(document.querySelector("#turf-size").value) || 3 };
  }
  if (kind === "van-westendorp") {
    const payload = {};
    document.querySelectorAll("[data-price-role]").forEach(select => { payload[select.dataset.priceRole] = select.value; });
    if (new Set(Object.values(payload)).size < 4) throw new Error("Для четырёх ценовых вопросов нужны четыре разных вопроса.");
    return payload;
  }
  const steps = [...document.querySelectorAll("#gg-steps .gg-step")].map(row => ({
    code: row.querySelector(".gg-question").value,
    price: Number(row.querySelector(".gg-price").value),
    buy_values: [...row.querySelectorAll(".gg-codes input:checked")].map(input => Number.isNaN(Number(input.value)) ? input.value : Number(input.value)),
  }));
  if (steps.some(step => !step.price)) throw new Error("Укажите цену у каждого вопроса.");
  if (steps.some(step => !step.buy_values.length)) throw new Error("Отметьте ответы «куплю» у каждого вопроса.");
  return { steps };
}

function percent(value) {
  return `${(value * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
}

function renderMethodResult(kind, result) {
  const weighted = result.weighted ? '<p class="analysis-note">Взвешено весом отчёта.</p>' : "";
  if (kind === "turf") {
    return `<article class="analysis-card method-card"><header><span><strong>TURF</strong> · ${escapeHtml(result.question)}</span></header>
      <p class="analysis-facts"><span>База <b>${result.base.toLocaleString("ru-RU")}</b></span></p>${weighted}
      <table class="model-table"><tr><th>Вариантов</th><th>Лучший портфель</th><th>Охват</th><th>Частота</th><th>Отбор</th></tr>${result.portfolios.map(item => `<tr><td>${item.size}</td><td>${item.items.map(escapeHtml).join(" + ")}</td><td>${percent(item.reach)}</td><td>${analysisNumber(item.frequency)}</td><td>${escapeHtml(item.method)}</td></tr>`).join("")}</table>
      <p class="analysis-note">Охват — доля выбравших хотя бы один вариант портфеля, частота — среднее число выбранных из него.</p></article>`;
  }
  if (kind === "van-westendorp") {
    const points = result.points;
    const money = value => value == null ? "—" : analysisNumber(value, 2);
    return `<article class="analysis-card method-card"><header><span><strong>Van Westendorp</strong></span></header>
      <p class="analysis-facts"><span>База <b>${result.consistent_base.toLocaleString("ru-RU")}</b>${result.excluded_inconsistent ? ` · исключено непоследовательных ${result.excluded_inconsistent}` : ""}</span></p>${weighted}
      <table class="model-table"><tr><th>Точка</th><th>Цена</th></tr>
        <tr><td>Нижняя граница приемлемого (PMC)</td><td>${money(points.marginal_cheapness)}</td></tr>
        <tr class="significant"><td>Оптимальная цена (OPP)</td><td>${money(points.optimal)}</td></tr>
        <tr><td>Безразличная цена (IPP)</td><td>${money(points.indifference)}</td></tr>
        <tr><td>Верхняя граница приемлемого (PME)</td><td>${money(points.marginal_expensiveness)}</td></tr>
      </table>${priceCurves(result.curves)}</article>`;
  }
  return `<article class="analysis-card method-card"><header><span><strong>Gabor–Granger</strong></span></header>${weighted}
    <table class="model-table"><tr><th>Цена</th><th>Ответили</th><th>Спрос</th><th>Индекс выручки</th></tr>${result.points.map(point => `<tr class="${point.price === result.optimal_price ? "significant" : ""}"><td>${analysisNumber(point.price, 2)}</td><td>${point.base}</td><td>${percent(point.demand)}</td><td>${analysisNumber(point.revenue, 2)}</td></tr>`).join("")}</table>
    <p class="analysis-note">Цена наибольшей выручки — ${analysisNumber(result.optimal_price, 2)}: спрос ${percent(result.optimal_demand)}.</p></article>`;
}

/* Четыре кривые Van Westendorp теми же долями, что посчитал сервер. */
function priceCurves(curves) {
  const width = 460;
  const height = 220;
  const pad = 34;
  const prices = curves.prices;
  if (prices.length < 2) return "";
  const minPrice = prices[0];
  const maxPrice = prices[prices.length - 1];
  const sx = value => pad + (value - minPrice) / (maxPrice - minPrice || 1) * (width - pad * 1.5);
  const sy = value => height - pad - value * (height - pad * 1.5);
  const line = (key, css) => `<polyline class="${css}" points="${prices.map((price, index) => `${sx(price).toFixed(1)},${sy(curves[key][index]).toFixed(1)}`).join(" ")}" />`;
  return `<details class="analysis-detail price-curves" open><summary>Кривые</summary><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Кривые Van Westendorp">
    <line class="axis" x1="${pad}" y1="${height - pad}" x2="${width - pad / 2}" y2="${height - pad}" />
    <line class="axis" x1="${pad}" y1="${pad / 2}" x2="${pad}" y2="${height - pad}" />
    ${line("too_cheap", "c1")}${line("cheap", "c2")}${line("expensive", "c3")}${line("too_expensive", "c4")}
    <text class="caption" x="${pad}" y="${height - 10}">${analysisNumber(minPrice, 0)}</text>
    <text class="caption" x="${width - pad / 2}" y="${height - 10}" text-anchor="end">${analysisNumber(maxPrice, 0)} цена →</text>
  </svg><p class="analysis-note curve-legend"><i class="c1"></i>слишком дёшево <i class="c2"></i>дёшево <i class="c3"></i>дорого <i class="c4"></i>слишком дорого</p></details>`;
}

document.querySelector("#method-kind").addEventListener("change", () => {
  document.querySelector("#method-result").innerHTML = "";
  renderMethodFields();
});
document.querySelector("#method-fields").addEventListener("click", event => {
  if (event.target.closest("#add-gg-step")) addGaborStep();
});
document.querySelector("#method-fields").addEventListener("change", event => {
  const select = event.target.closest(".gg-question");
  if (select) renderGaborCodes(select.closest(".gg-step"));
});
document.querySelector("#method-form").addEventListener("submit", async event => {
  event.preventDefault();
  const errorBox = document.querySelector("#method-error");
  errorBox.hidden = true;
  const kind = document.querySelector("#method-kind").value;
  const result = document.querySelector("#method-result");
  let payload;
  try {
    payload = methodPayload(kind);
  } catch (error) {
    showError(errorBox, error);
    return;
  }
  result.innerHTML = '<p class="analysis-note">Считаем…</p>';
  try {
    const response = await api(`/api/projects/${currentProject.id}/analysis/methods/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    result.innerHTML = renderMethodResult(kind, response);
  } catch (error) {
    result.innerHTML = "";
    showError(errorBox, error);
  }
});

/* Модели (PQ.11). Как и карточки, экран ничего не считает: коэффициенты,
   ошибки, R² и важность драйверов приходят с сервера. */
function sourceCategoryLabels(value) {
  if (value.startsWith("recoding:")) {
    const recoding = configuredRecodings().find(item => item.id === value.slice(9));
    return (recoding?.categories || []).map(category => category.label);
  }
  const question = configuredQuestions().find(item => item.code === value.slice(9));
  const variable = currentProject.inspection.variables.find(item => item.name === question?.source_variables?.[0]);
  return (variable?.value_labels || []).map(item => item.label);
}

function renderModelForm() {
  const sources = analysisSources();
  const dependent = document.querySelector("#model-dependent");
  const previous = dependent.value;
  dependent.innerHTML = sources.map(item => `<option value="${escapeAttribute(item.value)}">${escapeHtml(item.label)}</option>`).join("");
  if (previous) dependent.value = previous;
  const checked = new Set([...document.querySelectorAll("#model-predictors input:checked")].map(input => input.value));
  document.querySelector("#model-predictors").innerHTML = sources.map(item =>
    `<label class="checkbox" data-search="${escapeAttribute(item.label.toLowerCase())}"><input type="checkbox" value="${escapeAttribute(item.value)}" ${checked.has(item.value) ? "checked" : ""} /> ${escapeHtml(item.label)}</label>`).join("");
  renderModelEvent();
}

function renderModelEvent() {
  const logistic = document.querySelector("#model-kind").value === "logistic";
  const field = document.querySelector("#model-event-field");
  field.hidden = !logistic;
  if (!logistic) return;
  const select = document.querySelector("#model-event");
  const previous = select.value;
  select.innerHTML = sourceCategoryLabels(document.querySelector("#model-dependent").value)
    .map(label => `<option value="${escapeAttribute(label)}">${escapeHtml(label)}</option>`).join("");
  if (previous) select.value = previous;
}

async function loadAnalysisModels() {
  const container = document.querySelector("#model-list");
  if (!(currentProject.configuration.analysis_models || []).length) {
    container.innerHTML = '<p class="analysis-note">Моделей пока нет.</p>';
    return;
  }
  container.innerHTML = '<p class="analysis-note">Считаем…</p>';
  try {
    const { models } = await api(`/api/projects/${currentProject.id}/analysis/models`);
    container.innerHTML = models.map(renderModel).join("");
  } catch (error) {
    container.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function renderModel(model) {
  const kind = model.kind === "logistic" ? "Логистическая регрессия" : "Линейная регрессия";
  const head = `<header><span><strong>${escapeHtml(kind)}</strong>${model.dependent ? ` · ${escapeHtml(model.dependent)}${model.event ? ` = «${escapeHtml(model.event)}»` : ""}` : ""}</span><button type="button" data-delete-model="${escapeAttribute(model.id || "")}" aria-label="Удалить модель">×</button></header>`;
  if (!model.performed) {
    return `<article class="analysis-card model-card">${head}<p class="analysis-conclusion">${escapeHtml(model.reason || "Модель не построена.")}</p></article>`;
  }
  const excluded = model.base_before - model.base;
  const quality = model.kind === "logistic"
    ? `<span>псевдо-R² Макфаддена <b>${analysisNumber(model.pseudo_r_squared, 3)}</b></span>`
    : `<span>R² <b>${analysisNumber(model.r_squared, 3)}</b></span><span>скорр. R² <b>${analysisNumber(model.adjusted_r_squared, 3)}</b></span><span>F-тест, p <b>${analysisP(model.f_p_value)}</b></span>`;
  const facts = `<p class="analysis-facts"><span>База <b>${model.base.toLocaleString("ru-RU")}</b>${excluded ? ` из ${model.base_before.toLocaleString("ru-RU")} (исключено ${excluded.toLocaleString("ru-RU")})` : ""}</span>${model.effective_base != null ? `<span>эфф. база <b>${analysisNumber(model.effective_base, 0)}</b></span>` : ""}${quality}</p>`;
  const references = Object.entries(model.references || {});
  const notes = [
    model.method,
    references.length ? `Базовые категории: ${references.map(([name, label]) => `${name} — «${label}»`).join("; ")}.` : null,
    model.missing === "missing_category" ? "Пропуск категориального предиктора — отдельная категория." : "Неполные строки исключены.",
    model.filtered ? "Учтён общий фильтр отчёта." : null,
  ].filter(Boolean).map(note => `<p class="analysis-note">${escapeHtml(note)}</p>`).join("");
  const logistic = model.kind === "logistic";
  const rows = model.coefficients.map(row => {
    const significant = row.p_value < 0.05 && row.name !== "Константа";
    const value = logistic ? analysisNumber(row.odds_ratio, 3) : analysisNumber(row.estimate, 3);
    return `<tr class="${significant ? "significant" : ""}"><td>${escapeHtml(row.name)}</td><td>${value}</td><td>${analysisNumber(row.std_error, 3)}</td><td>${analysisP(row.p_value)}</td><td>${analysisNumber(row.ci_low, 3)} … ${analysisNumber(row.ci_high, 3)}</td></tr>`;
  }).join("");
  const table = `<table class="model-table"><tr><th>Коэффициент</th><th>${logistic ? "Отношение шансов" : "B"}</th><th>Ст. ошибка${logistic ? " (B)" : ""}</th><th>p</th><th>95% ДИ${logistic ? " (ОШ)" : ""}</th></tr>${rows}</table>`;
  const importance = (model.importance || []).slice().sort((a, b) => b.share - a.share);
  const drivers = importance.length > 1
    ? `<details class="analysis-detail model-importance" open><summary>Важность драйверов · относительные веса Джонсона</summary>${importance.map(item => `<div class="driver-row"><span title="${escapeAttribute(item.predictor)}">${escapeHtml(item.predictor)}</span><span class="driver-bar"><i style="width:${Math.round(item.share * 100)}%"></i></span><b>${Math.round(item.share * 100)}%</b></div>`).join("")}<p class="analysis-note">Доля R², приходящаяся на предиктор с учётом корреляции с остальными; в сумме — весь R².</p></details>`
    : "";
  return `<article class="analysis-card model-card" data-model-id="${escapeAttribute(model.id || "")}">${head}${facts}${notes}${table}${drivers}${driverMap(importance)}</article>`;
}

/* Карта драйверов: по горизонтали — доля в R², по вертикали — средняя оценка.
   Линии — средние по драйверам: справа снизу «важно, но оценено ниже» —
   первое, что стоит улучшать. Числа на карте — те же, что в таблице важности. */
function driverMap(importance) {
  const points = importance.filter(item => item.mean != null);
  if (points.length < 2) return "";
  const width = 420;
  const height = 240;
  const pad = 36;
  const xs = points.map(item => item.share);
  const ys = points.map(item => item.mean);
  const [xMin, xMax] = [0, Math.max(...xs) * 1.1 || 1];
  const yLow = Math.min(...ys);
  const yHigh = Math.max(...ys);
  const ySpan = yHigh - yLow || 1;
  const [yMin, yMax] = [yLow - ySpan * 0.15, yHigh + ySpan * 0.15];
  const sx = value => pad + (value - xMin) / (xMax - xMin) * (width - pad * 1.5);
  const sy = value => height - pad - (value - yMin) / (yMax - yMin) * (height - pad * 1.5);
  const meanX = xs.reduce((sum, value) => sum + value, 0) / xs.length;
  const meanY = ys.reduce((sum, value) => sum + value, 0) / ys.length;
  const dots = points.map(item => {
    const label = item.predictor.split(" ")[0];
    return `<g><circle cx="${sx(item.share).toFixed(1)}" cy="${sy(item.mean).toFixed(1)}" r="5"><title>${escapeHtml(item.predictor)}: ${Math.round(item.share * 100)}% R², среднее ${analysisNumber(item.mean)}</title></circle><text x="${(sx(item.share) + 8).toFixed(1)}" y="${(sy(item.mean) + 4).toFixed(1)}">${escapeHtml(label)}</text></g>`;
  }).join("");
  return `<details class="analysis-detail driver-map" open><summary>Карта драйверов · важность × оценка</summary>
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Карта драйверов: важность по горизонтали, средняя оценка по вертикали">
      <line class="axis" x1="${pad}" y1="${height - pad}" x2="${width - pad / 2}" y2="${height - pad}" />
      <line class="axis" x1="${pad}" y1="${pad / 2}" x2="${pad}" y2="${height - pad}" />
      <line class="quadrant" x1="${sx(meanX).toFixed(1)}" y1="${pad / 2}" x2="${sx(meanX).toFixed(1)}" y2="${height - pad}" />
      <line class="quadrant" x1="${pad}" y1="${sy(meanY).toFixed(1)}" x2="${width - pad / 2}" y2="${sy(meanY).toFixed(1)}" />
      <text class="caption" x="${width - pad / 2}" y="${height - 8}" text-anchor="end">важность, доля R² →</text>
      <text class="caption" x="6" y="${pad / 2 - 4}">оценка ↑</text>
      <text class="caption" x="${width - pad / 2 - 4}" y="${height - pad - 6}" text-anchor="end">важно, оценено ниже</text>
      ${dots}
    </svg></details>`;
}

document.querySelector("#model-kind").addEventListener("change", renderModelEvent);
document.querySelector("#model-dependent").addEventListener("change", renderModelEvent);
document.querySelector("#model-predictor-search").addEventListener("input", event => {
  const needle = event.target.value.trim().toLowerCase();
  document.querySelectorAll("#model-predictors label").forEach(label => {
    label.hidden = Boolean(needle) && !label.dataset.search.includes(needle);
  });
});

document.querySelector("#model-form").addEventListener("submit", async event => {
  event.preventDefault();
  const errorBox = document.querySelector("#model-error");
  errorBox.hidden = true;
  const parse = value => ({ kind: value.slice(0, value.indexOf(":")), ref: value.slice(value.indexOf(":") + 1) });
  const dependent = document.querySelector("#model-dependent").value;
  const predictors = [...document.querySelectorAll("#model-predictors input:checked")]
    .map(input => input.value).filter(value => value !== dependent);
  if (!predictors.length) {
    showError(errorBox, new Error("Отметьте хотя бы один предиктор, кроме зависимой."));
    return;
  }
  const kind = document.querySelector("#model-kind").value;
  const button = document.querySelector("#add-model");
  setBusy(button, true, "Строим…");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/analysis/models`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind,
        dependent: parse(dependent),
        predictors: predictors.map(parse),
        missing: document.querySelector("#model-missing").value,
        event: kind === "logistic" ? document.querySelector("#model-event").value || null : null,
      }),
    });
    await loadAnalysisModels();
  } catch (error) {
    showError(errorBox, error);
  } finally {
    setBusy(button, false, "Построить модель");
  }
});

document.querySelector("#model-list").addEventListener("click", async event => {
  const button = event.target.closest("[data-delete-model]");
  if (!button) return;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/analysis/models/${button.dataset.deleteModel}`, { method: "DELETE" });
    await loadAnalysisModels();
  } catch (error) {
    showError(document.querySelector("#model-error"), error);
  }
});

async function loadAnalysisCards() {
  const container = document.querySelector("#analysis-cards");
  if (!(currentProject.configuration.analysis_cards || []).length) {
    container.innerHTML = '<p class="analysis-note">Карточек пока нет. Выберите две переменные и добавьте первую.</p>';
    return;
  }
  container.innerHTML = '<p class="analysis-note">Считаем…</p>';
  try {
    const { cards } = await api(`/api/projects/${currentProject.id}/analysis/cards`);
    container.innerHTML = cards.map(renderAnalysisCard).join("");
  } catch (error) {
    container.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function analysisNumber(value, digits = 2) {
  return value == null ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function analysisP(value) {
  return value == null ? "—" : value < 0.001 ? "< 0,001" : analysisNumber(value, 3);
}

function renderAnalysisCard(card) {
  const title = `<strong>${escapeHtml(card.a_label || card.a.ref)}</strong> × <strong>${escapeHtml(card.b_label || card.b.ref)}</strong>`;
  const facts = card.performed ? `
    <p class="analysis-facts">
      <span>Метод <b>${escapeHtml(card.method)}</b></span>
      <span>N <b>${Number(card.n).toLocaleString("ru-RU")}</b></span>
      ${card.effective_base != null ? `<span>эфф. база <b>${analysisNumber(card.effective_base)}</b></span>` : ""}
      <span>${escapeHtml(EFFECT_LABELS[card.effect_kind] || "Эффект")} <b>${analysisNumber(card.effect)}</b> — ${escapeHtml(card.magnitude || "")}</span>
      <span>p <b>${analysisP(card.p_value)}</b></span>
      <span>p с поправкой BH <b>${analysisP(card.p_adjusted)}</b></span>
    </p>` : "";
  const notes = [card.note_method, card.note, card.filtered ? "Учтён общий фильтр отчёта." : null]
    .filter(Boolean).map(note => `<p class="analysis-note">${escapeHtml(note)}</p>`).join("");
  let detail = "";
  if (card.groups?.length) {
    const effective = card.groups.some(group => group.effective_base != null);
    detail = `<details class="analysis-detail"><summary>Средние по группам</summary><table><tr><th>Группа</th><th>N</th>${effective ? "<th>Эфф. база</th>" : ""}<th>Среднее</th></tr>${card.groups.map(group => `<tr><td>${escapeHtml(group.label)}</td><td>${group.n}</td>${effective ? `<td>${analysisNumber(group.effective_base)}</td>` : ""}<td>${analysisNumber(group.mean)}</td></tr>`).join("")}</table></details>`;
    // После Welch ANOVA — какие именно пары различаются (Games–Howell).
    if (card.posthoc?.length) {
      detail += `<details class="analysis-detail analysis-posthoc" open><summary>Какие группы различаются · Games–Howell</summary><table><tr><th>Пара</th><th>Разница</th><th>p</th></tr>${card.posthoc.map(pair => `<tr class="${pair.significant ? "significant" : ""}"><td>${escapeHtml(pair.a)} — ${escapeHtml(pair.b)}</td><td>${analysisNumber(pair.difference)}</td><td>${analysisP(pair.p_value)}${pair.significant ? " ✓" : ""}</td></tr>`).join("")}</table></details>`;
    }
  } else if (card.table?.length) {
    detail = `<details class="analysis-detail"><summary>Таблица сопряжённости</summary><table><tr><th></th>${card.columns.map(label => `<th>${escapeHtml(label)}</th>`).join("")}</tr>${card.table.map((row, index) => `<tr><td>${escapeHtml(card.rows[index])}</td>${row.map(value => `<td>${value}</td>`).join("")}</tr>`).join("")}</table></details>`;
  }
  return `<article class="analysis-card" data-card-id="${escapeAttribute(card.id || "")}">
    <header><span>${title}</span><button type="button" data-delete-card="${escapeAttribute(card.id || "")}" aria-label="Удалить карточку">×</button></header>
    <p class="analysis-conclusion ${card.significant ? "significant" : ""}">${escapeHtml(card.conclusion || card.reason || "")}</p>
    ${facts}${notes}${detail}
  </article>`;
}

document.querySelector("#analysis-form").addEventListener("submit", async event => {
  event.preventDefault();
  const errorBox = document.querySelector("#analysis-error");
  errorBox.hidden = true;
  const [a, b] = ["#analysis-a", "#analysis-b"].map(selector => parseBannerSource(document.querySelector(selector).value));
  const button = document.querySelector("#add-analysis-card");
  button.disabled = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/analysis/cards`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ a, b }),
    });
    await loadAnalysisCards();
  } catch (error) {
    showError(errorBox, error);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#analysis-cards").addEventListener("click", async event => {
  const button = event.target.closest("[data-delete-card]");
  if (!button) return;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/analysis/cards/${button.dataset.deleteCard}`, { method: "DELETE" });
    await loadAnalysisCards();
  } catch (error) {
    alert(error.message);
  }
});

// Карточка переменной: что в ней есть, прежде чем искать связи.
document.querySelector("#describe-variable").addEventListener("click", async () => {
  const body = document.querySelector("#variable-body");
  const value = document.querySelector("#variable-source").value;
  if (!value) return;
  const source = parseBannerSource(value);
  body.innerHTML = '<p class="analysis-note">Считаем…</p>';
  try {
    const profile = await api(`/api/projects/${currentProject.id}/analysis/variable?kind=${encodeURIComponent(source.kind)}&ref=${encodeURIComponent(source.ref)}`);
    body.innerHTML = renderVariableProfile(profile);
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
});

function renderVariableProfile(profile) {
  const head = `<p class="analysis-facts"><span>Ответили <b>${profile.answered.toLocaleString("ru-RU")}</b> из ${profile.total.toLocaleString("ru-RU")}</span><span>Пропуски <b>${profile.missing.toLocaleString("ru-RU")}</b>${profile.total ? ` · ${analysisNumber(profile.missing / profile.total * 100, 1)}%` : ""}</span>${profile.filtered ? "<span>Учтён общий фильтр отчёта</span>" : ""}</p>`;
  if (profile.kind === "categorical") {
    const top = profile.categories.slice(0, 12);
    const rows = top.map(item => `
      <div class="variable-row">
        <span title="${escapeAttribute(item.label)}">${escapeHtml(item.label)}</span>
        <em>${item.count.toLocaleString("ru-RU")}</em>
        <em>${item.share == null ? "—" : `${analysisNumber(item.share * 100, 1)}%`}</em>
        <div class="variable-bar-fill"><i style="width: ${Math.round((item.share || 0) * 100)}%"></i></div>
      </div>`).join("");
    const rest = profile.categories.length - top.length;
    return `${head}<div class="variable-rows">${rows}</div>${rest > 0 ? `<p class="analysis-note">Ещё категорий: ${rest}</p>` : ""}`;
  }
  const stats = profile.statistics;
  if (!stats) return `${head}<p class="analysis-note">Нет заполненных значений.</p>`;
  const cell = (label, value) => `<span>${label} <b>${analysisNumber(value)}</b></span>`;
  return `${head}<p class="analysis-facts">${cell("Среднее", stats.mean)}${cell("Медиана", stats.median)}${stats.std == null ? "" : cell("Ст. отклонение", stats.std)}${cell("Мин.", stats.min)}${cell("Q1", stats.q1)}${cell("Q3", stats.q3)}${cell("Макс.", stats.max)}</p>`;
}

