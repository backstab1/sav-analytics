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
}

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
      <span>${escapeHtml(EFFECT_LABELS[card.effect_kind] || "Эффект")} <b>${analysisNumber(card.effect)}</b> — ${escapeHtml(card.magnitude || "")}</span>
      <span>p <b>${analysisP(card.p_value)}</b></span>
      <span>p с поправкой BH <b>${analysisP(card.p_adjusted)}</b></span>
    </p>` : "";
  const notes = [card.note_method, card.note, card.filtered ? "Учтён общий фильтр отчёта." : null]
    .filter(Boolean).map(note => `<p class="analysis-note">${escapeHtml(note)}</p>`).join("");
  let detail = "";
  if (card.groups?.length) {
    detail = `<details class="analysis-detail"><summary>Средние по группам</summary><table><tr><th>Группа</th><th>N</th><th>Среднее</th></tr>${card.groups.map(group => `<tr><td>${escapeHtml(group.label)}</td><td>${group.n}</td><td>${analysisNumber(group.mean)}</td></tr>`).join("")}</table></details>`;
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
