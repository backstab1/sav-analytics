/* Раздел «Открытые ответы» (PQ.17, решение 034). Классический скрипт после
   app.js: пользуется его состоянием (currentProject, api, renderProject).

   Открытые вопросы стоят строками в порядке анкеты. Строка раскрывается:
   слева справочник кодов (две ступени: группа → коды) с настройками и
   правкой через ИИ, справа ответы с плитками ревью, фильтрами и правкой
   кодов. Коды ставит модель фоновой задачей; правка человека пишется в
   словарь «текст → коды» и не перетирается перекодированием «с учётом
   правок». Тональность ставит та же модель тем же вызовом; правка тона
   пишется в свой словарь «текст → тон». Считает и хранит всё сервер; экран
   держит только несохранённый черновик справочника. */

const codingOpen = new Set();
const codingState = new Map();
let textCandidates = null;
let newCodeCounter = 0;

const CODING_VIEWS = [
  ["all", "Уникальных"],
  ["dictionary", "Из словаря"],
  ["ai", "ИИ"],
  ["low", "Низкая"],
  ["uncoded", "Без кода"],
];
const CODING_TILE_KEYS = { all: "unique", dictionary: "dictionary", ai: "ai", low: "low", uncoded: "uncoded" };
const TILE_TITLES = {
  all: "Уникальные тексты ответов",
  dictionary: "Коды из словаря правок человека",
  ai: "Закодировал ИИ",
  low: "Низкая уверенность ИИ — проверьте в первую очередь",
  uncoded: "Ответы без кода",
};
const SOURCE_LABELS = { ai: "ИИ", manual: "вручную", dictionary: "словарь" };
const TONES = [
  ["positive", "Положительная", "Позитив"],
  ["neutral", "Нейтральная", "Нейтрально"],
  ["mixed", "Смешанная", "Смешанно"],
  ["negative", "Отрицательная", "Негатив"],
];
const TONE_SHORT = Object.fromEntries(TONES.map(([key, , short]) => [key, short]));

function answerColumns(codeframe) {
  return codeframe.sentiment ? 5 : 4;
}

function codeframes() {
  const order = new Map(configuredQuestions().map((question, index) => [question.code, index]));
  return [...(currentProject.configuration.codeframes || [])]
    .sort((left, right) => (order.get(left.question_code) ?? 1e9) - (order.get(right.question_code) ?? 1e9));
}

function codeframeById(id) {
  return (currentProject.configuration.codeframes || []).find(item => item.id === id) || null;
}

function questionLabel(code) {
  return configuredQuestions().find(question => question.code === code)?.label || "";
}

function stateFor(codeframe) {
  if (!codingState.has(codeframe.id)) {
    codingState.set(codeframe.id, {
      view: "all", themeId: "", tone: "", search: "", offset: 0, rows: [], total: 0,
      summary: null, draft: null, dirty: false, notice: "",
    });
  }
  return codingState.get(codeframe.id);
}

function codingJob(codeframeId) {
  const jobs = [...(aiJobsKnown?.values?.() || [])].filter(job => job.subject === codeframeId);
  return jobs.find(job => job.status === "queued" || job.status === "running")
    || jobs.sort((left, right) => (right.created_at || "").localeCompare(left.created_at || ""))[0]
    || null;
}

/* ---------------- Список вопросов ---------------- */

async function renderTextSection() {
  if (!currentProject) return;
  if (codingState.project !== currentProject.id) {
    codingState.clear();
    codingOpen.clear();
    codingState.project = currentProject.id;
    textCandidates = null;
  }
  const frames = codeframes();
  document.querySelector("#coding-empty").hidden = frames.length > 0;
  document.querySelector("#recognize-open-top").hidden = frames.length === 0;
  const list = document.querySelector("#coding-list");
  list.innerHTML = frames.map(codeframe => `
    <article class="cq${codingOpen.has(codeframe.id) ? " is-open" : ""}" data-cf="${escapeAttribute(codeframe.id)}">
      <button type="button" class="cq-head" aria-expanded="${codingOpen.has(codeframe.id)}" data-cq-toggle>
        <span class="cq-caret" aria-hidden="true"></span>
        <code>${escapeHtml(codeframe.question_code)}</code>
        <span class="cq-label">${escapeHtml(questionLabel(codeframe.question_code))}</span>
        <span class="cq-status" data-cq-status></span>
      </button>
      <div class="cq-body" data-cq-body ${codingOpen.has(codeframe.id) ? "" : "hidden"}></div>
    </article>`).join("");
  updateCodingStatuses();
  for (const codeframe of frames) {
    if (codingOpen.has(codeframe.id)) renderCodeframeBody(codeframe);
  }
}

function updateCodingStatuses() {
  if (!currentProject || document.querySelector("#section-text").hidden) return;
  document.querySelectorAll("#coding-list .cq").forEach(article => {
    const codeframe = codeframeById(article.dataset.cf);
    if (!codeframe) return;
    const job = codingJob(codeframe.id);
    const status = article.querySelector("[data-cq-status]");
    const busy = job && (job.status === "queued" || job.status === "running");
    article.classList.toggle("is-busy", Boolean(busy));
    if (busy) {
      status.innerHTML = `<span class="cq-chip is-busy">${escapeHtml(job.stage)} · ${job.progress}%</span>`;
    } else if (job && job.status === "failed" && job.kind === "coding") {
      status.innerHTML = `<span class="cq-chip is-failed" title="${escapeAttribute(job.error || "")}">Ошибка кодирования</span>`;
    } else if (codeframe.coding_ref) {
      const leaves = codeframe.themes.filter(theme => !codeframe.themes.some(child => child.parent_id === theme.id));
      status.innerHTML = `<span class="cq-chip is-done">${plural(leaves.length, "код", "кода", "кодов")}</span>`;
    } else {
      status.innerHTML = '<span class="cq-chip">Не закодирован</span>';
    }
    article.querySelectorAll("[data-code-mode]").forEach(button => { button.disabled = Boolean(busy); });
  });
}

document.querySelector("#coding-list").addEventListener("click", event => {
  const toggle = event.target.closest("[data-cq-toggle]");
  if (!toggle) return;
  const article = toggle.closest(".cq");
  const id = article.dataset.cf;
  const open = !codingOpen.has(id);
  // Раскрыт один вопрос: раскрытая строка занимает экран целиком.
  document.querySelectorAll("#coding-list .cq.is-open").forEach(other => {
    if (other === article) return;
    codingOpen.delete(other.dataset.cf);
    other.classList.remove("is-open");
    other.querySelector("[data-cq-toggle]").setAttribute("aria-expanded", "false");
    other.querySelector("[data-cq-body]").hidden = true;
  });
  if (open) codingOpen.add(id); else codingOpen.delete(id);
  article.classList.toggle("is-open", open);
  toggle.setAttribute("aria-expanded", String(open));
  const body = article.querySelector("[data-cq-body]");
  body.hidden = !open;
  if (open) {
    renderCodeframeBody(codeframeById(id));
    article.scrollIntoView({ block: "start" });
  }
});

/* ---------------- Тело строки ---------------- */

function renderCodeframeBody(codeframe) {
  const article = document.querySelector(`#coding-list .cq[data-cf="${CSS.escape(codeframe.id)}"]`);
  if (!article) return;
  const state = stateFor(codeframe);
  if (!state.draft) state.draft = draftFrom(codeframe);
  article.querySelector("[data-cq-body]").innerHTML = `
    <div class="cq-grid">
      <section class="cq-codes">
        <div class="cq-sub"><strong>Справочник</strong><span class="muted" data-cq-codes-count></span></div>
        ${state.notice ? `<p class="cq-notice">${escapeHtml(state.notice)}</p>` : ""}
        <div class="code-tree" data-code-tree></div>
        <div class="cq-row-actions">
          <button type="button" class="text-button" data-add-code>+ Код</button>
          <button type="button" class="text-button" data-add-group>+ Группа</button>
        </div>
        <div class="cq-revise">
          <textarea rows="2" data-revise-text placeholder="Попросите ИИ изменить справочник: «объедини цену и стоимость», «выдели бренды отдельными кодами»" aria-label="Просьба к ИИ о справочнике"></textarea>
          <button type="button" class="secondary" data-revise>Изменить с ИИ</button>
        </div>
        <details class="cq-settings">
          <summary>Настройки кодирования</summary>
          <label class="f">Инструкция по кодированию<textarea rows="3" data-setting="instruction" placeholder="Например: бренды выделяй отдельными кодами; негатив о цене — отдельный код">${escapeHtml(state.draft.instruction)}</textarea></label>
          <label class="checkbox"><input type="checkbox" data-setting="multi" ${state.draft.multi ? "checked" : ""} /> Несколько кодов у одного ответа</label>
          <label class="checkbox" title="Модель ставит ответу тон тем же вызовом, что и коды. Тон — отдельный вопрос для таблиц и баннера"><input type="checkbox" data-setting="sentiment" ${state.draft.sentiment ? "checked" : ""} /> Тональность ответов</label>
          <label class="f cq-threshold">Коды реже, %, уходят в «Другое» при построении справочника<input type="number" min="0" max="20" step="0.5" data-setting="other_threshold" value="${Math.round(state.draft.other_threshold * 1000) / 10}" /></label>
        </details>
        <div class="cq-save">
          <span class="muted" data-dirty-note>${state.dirty ? "Есть несохранённые изменения" : ""}</span>
          <button type="button" data-save-codes ${state.dirty ? "" : "disabled"}>Сохранить справочник</button>
        </div>
        <div class="cq-links">
          <a class="text-button" href="/api/projects/${currentProject.id}/codeframes/${codeframe.id}/export" download="кодификатор_${escapeAttribute(codeframe.question_code)}.json">Скачать справочник и словарь</a>
          <label class="text-button">Загрузить из файла<input type="file" accept="application/json,.json" data-import hidden /></label>
        </div>
      </section>
      <section class="cq-answers">
        <div class="cq-tiles" role="group" aria-label="Ревью ответов" data-tiles></div>
        <div class="cq-tones" role="group" aria-label="Тональность ответов" data-tones ${codeframe.sentiment ? "" : "hidden"}></div>
        <div class="coding-filter">
          <select data-theme-filter aria-label="Ответы с кодом"></select>
          <input type="search" data-search placeholder="Найти в ответах" aria-label="Поиск по ответам" value="${escapeAttribute(state.search)}" />
        </div>
        <p class="muted cq-count" data-answer-count></p>
        <div class="answers-wrap">
          <table class="answers-table">
            <thead><tr><th>Ответ</th><th class="num" title="Сколько респондентов так ответили">Раз</th><th>Коды</th>${codeframe.sentiment ? '<th class="ans-tone-head">Тон</th>' : ""}<th class="ans-actions-head"><span class="sr-only">Действия</span></th></tr></thead>
            <tbody data-answers></tbody>
          </table>
        </div>
        <button type="button" class="secondary compact-button" data-more hidden>Показать ещё</button>
        <div class="cq-code-actions">
          <button type="button" data-code-mode="new" title="Закодировать ответы, у которых ещё нет кодов: новая волна, новые коды справочника">${codeframe.coding_ref ? "Докодировать новые" : "Закодировать"}</button>
          <button type="button" class="secondary" data-code-mode="keep_edits" ${codeframe.coding_ref ? "" : "hidden"} title="Модель заново кодирует всё, кроме ответов из словаря правок">Перекодировать с учётом правок</button>
          <button type="button" class="text-button danger-text cq-reset" data-code-mode="reset" ${codeframe.coding_ref ? "" : "hidden"} title="Сбросить справочник и словарь, построить справочник и закодировать с нуля">Сбросить всё…</button>
        </div>
      </section>
    </div>`;
  renderCodeTree(codeframe);
  updateCodingStatuses();
  void refreshCodeframe(codeframe);
}

function draftFrom(codeframe) {
  return {
    themes: codeframe.themes.map(theme => ({ id: theme.id, name: theme.name, parent_id: theme.parent_id || null, description: theme.description || "" })),
    instruction: codeframe.instruction || "",
    multi: codeframe.multi !== false,
    sentiment: Boolean(codeframe.sentiment),
    other_threshold: codeframe.other_threshold ?? 0.01,
  };
}

function articleFor(codeframeId) {
  return document.querySelector(`#coding-list .cq[data-cf="${CSS.escape(codeframeId)}"]`);
}

function renderCodeTree(codeframe) {
  const article = articleFor(codeframe.id);
  if (!article) return;
  const state = stateFor(codeframe);
  const themes = state.draft.themes;
  const counts = new Map((state.summary?.themes || []).map(item => [item.id, item]));
  const groups = themes.filter(theme => themes.some(child => child.parent_id === theme.id) || theme.group);
  const top = themes.filter(theme => !theme.parent_id);
  const row = (theme, nested) => {
    const count = counts.get(theme.id);
    const isGroup = themes.some(child => child.parent_id === theme.id) || theme.group;
    const parentOptions = isGroup ? "" : `<select data-code-parent aria-label="Группа кода"><option value="">Без группы</option>${groups.filter(group => group.id !== theme.id).map(group => `<option value="${escapeAttribute(group.id)}" ${theme.parent_id === group.id ? "selected" : ""}>${escapeHtml(group.name || "Группа")}</option>`).join("")}</select>`;
    return `<div class="code-row${nested ? " is-nested" : ""}${isGroup ? " is-group" : ""}" data-code-id="${escapeAttribute(theme.id)}">
      <input class="code-name" value="${escapeAttribute(theme.name)}" title="${escapeAttribute(theme.description || "")}" placeholder="${isGroup ? "Название группы" : "Название кода"}" aria-label="${isGroup ? "Название группы" : "Название кода"}" data-code-name />
      <span class="code-count">${count ? `${count.count.toLocaleString("ru-RU")}${count.share != null ? ` · ${Math.round(count.share * 100)}%` : ""}` : ""}</span>
      ${parentOptions}
      <button type="button" class="del" data-code-remove aria-label="Удалить">×</button>
      ${isGroup ? "" : `<input class="code-desc" value="${escapeAttribute(theme.description || "")}" placeholder="Что относится к коду" aria-label="Описание кода" data-code-desc />`}
    </div>`;
  };
  const html = top.map(theme => row(theme, false) + themes.filter(child => child.parent_id === theme.id).map(child => row(child, true)).join("")).join("");
  article.querySelector("[data-code-tree]").innerHTML = html || '<p class="muted">Кодов нет. Нажмите «Закодировать» — ИИ построит справочник сам, или добавьте коды вручную.</p>';
  const leaves = themes.filter(theme => !themes.some(child => child.parent_id === theme.id) && !theme.group);
  article.querySelector("[data-cq-codes-count]").textContent = leaves.length ? ` · ${plural(leaves.length, "код", "кода", "кодов")}` : "";
}

function markDirty(codeframe) {
  const state = stateFor(codeframe);
  state.dirty = true;
  const article = articleFor(codeframe.id);
  if (!article) return;
  article.querySelector("[data-dirty-note]").textContent = "Есть несохранённые изменения";
  article.querySelector("[data-save-codes]").disabled = false;
}

async function refreshCodeframe(codeframe) {
  const state = stateFor(codeframe);
  const article = articleFor(codeframe.id);
  if (!article) return;
  try {
    state.summary = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}/summary`);
  } catch (error) {
    article.querySelector("[data-answers]").innerHTML = `<tr><td colspan="${answerColumns(codeframe)}" class="error">${escapeHtml(error.message)}</td></tr>`;
    return;
  }
  renderTiles(codeframe);
  renderTones(codeframe);
  renderCodeTree(codeframe);
  renderThemeFilter(codeframe);
  await loadCodeframeAnswers(codeframe, false);
}

function renderTiles(codeframe) {
  const state = stateFor(codeframe);
  const tiles = state.summary?.tiles || {};
  articleFor(codeframe.id).querySelector("[data-tiles]").innerHTML = CODING_VIEWS.map(([view, label]) => `
    <button type="button" class="cq-tile${state.view === view ? " is-active" : ""}${view === "low" && tiles.low ? " is-warn" : ""}" data-tile="${view}" aria-pressed="${state.view === view}" title="${TILE_TITLES[view]}">
      <b>${(tiles[CODING_TILE_KEYS[view]] ?? 0).toLocaleString("ru-RU")}</b><span>${label}</span>
    </button>`).join("");
}

/* Полоса тональности: доля каждого тона среди ответивших и фильтр списка. */
function renderTones(codeframe) {
  const box = articleFor(codeframe.id)?.querySelector("[data-tones]");
  if (!box) return;
  const state = stateFor(codeframe);
  const tones = state.summary?.tones;
  box.hidden = !codeframe.sentiment || !tones;
  if (box.hidden) return;
  const share = key => tones.shares[key] == null ? "—" : `${Math.round(tones.shares[key] * 100)}%`;
  const button = (key, label, value, title) => `
    <button type="button" class="cq-tone is-${key}${state.tone === key ? " is-active" : ""}" data-tone-filter="${key}" aria-pressed="${state.tone === key}" title="${escapeAttribute(title)}">
      <i aria-hidden="true"></i><span>${label}</span><b>${value}</b>
    </button>`;
  box.innerHTML = TONES.map(([key, label]) => button(key, label, share(key), `${plural(tones.counts[key], "респондент", "респондента", "респондентов")}. Щелчок — только такие ответы`)).join("")
    + (tones.untoned ? button("none", "Без тона", tones.untoned.toLocaleString("ru-RU"), "Ответы без тона: «Докодировать новые» поставит его") : "");
}

function renderThemeFilter(codeframe) {
  const state = stateFor(codeframe);
  const select = articleFor(codeframe.id).querySelector("[data-theme-filter]");
  select.innerHTML = '<option value="">Все коды</option>' + codeframe.themes
    .map(theme => `<option value="${escapeAttribute(theme.id)}" ${state.themeId === theme.id ? "selected" : ""}>${theme.parent_id ? "— " : ""}${escapeHtml(theme.name)}</option>`).join("");
}

async function loadCodeframeAnswers(codeframe, append) {
  const state = stateFor(codeframe);
  const article = articleFor(codeframe.id);
  if (!article) return;
  if (!append) state.offset = 0;
  const params = new URLSearchParams({ offset: String(state.offset), limit: "50", view: state.view });
  if (state.themeId) params.set("theme_id", state.themeId);
  if (state.search.trim()) params.set("search", state.search.trim());
  if (codeframe.sentiment && state.tone) params.set("tone", state.tone);
  const list = article.querySelector("[data-answers]");
  try {
    const result = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}/answers?${params}`);
    const names = new Map(codeframe.themes.map(theme => [theme.id, theme.name]));
    const html = result.rows.map(row => answerRowHtml(row, names, codeframe.sentiment)).join("");
    list.innerHTML = append ? list.innerHTML + html : (html || `<tr><td colspan="${answerColumns(codeframe)}" class="muted">Ответов нет.</td></tr>`);
    state.offset += result.rows.length;
    article.querySelector("[data-answer-count]").textContent = `Показано ${Math.min(state.offset, result.total).toLocaleString("ru-RU")} из ${result.total.toLocaleString("ru-RU")} уникальных ответов`;
    article.querySelector("[data-more]").hidden = state.offset >= result.total;
  } catch (error) {
    list.innerHTML = `<tr><td colspan="${answerColumns(codeframe)}" class="error">${escapeHtml(error.message)}</td></tr>`;
  }
}

/* Строка таблицы ревью, как у Смыслографа: ответ в одну строку (полный
   текст — по щелчку и в подсказке), сколько раз встретился, коды чипами и
   карандаш правки. Список всех кодов показывается только в редакторе. */
const VISIBLE_CODES = 3;

function toneCellHtml(row) {
  if (!row.tone) return '<td class="ans-tone"><span class="muted">—</span></td>';
  const manual = row.tone_source === "manual";
  return `<td class="ans-tone"><span class="tone-mark is-${row.tone}${manual ? " is-manual" : ""}" title="${manual ? "Тон поправлен вручную" : "Тон поставил ИИ"}"><i aria-hidden="true"></i>${TONE_SHORT[row.tone]}</span></td>`;
}

function answerRowHtml(row, names, sentiment) {
  const shown = row.codes.slice(0, VISIBLE_CODES)
    .map(code => `<span class="answer-chip">${escapeHtml(names.get(code) || "")}</span>`).join("");
  const more = row.codes.length > VISIBLE_CODES
    ? `<span class="answer-more" title="${escapeAttribute(row.codes.slice(VISIBLE_CODES).map(code => names.get(code) || "").join(", "))}">+${row.codes.length - VISIBLE_CODES}</span>`
    : "";
  const badges = `${row.low ? '<span class="answer-low" title="Модель не уверена в кодах">низкая</span>' : ""}${row.source === "manual" || row.source === "dictionary" ? `<span class="answer-source is-${row.source}" title="${row.source === "manual" ? "Поправлено вручную, лежит в словаре" : "Из словаря правок"}">${SOURCE_LABELS[row.source]}</span>` : ""}`;
  return `<tr class="ans${row.low ? " is-low" : ""}" data-key="${escapeAttribute(row.key)}" data-codes="${escapeAttribute(JSON.stringify(row.codes))}" data-source="${escapeAttribute(row.source || "")}" data-tone="${escapeAttribute(row.tone || "")}" data-tone-source="${escapeAttribute(row.tone_source || "")}">
    <td class="ans-text" title="${escapeAttribute(row.text)}" data-expand-answer><span>${escapeHtml(row.text)}</span></td>
    <td class="num">${row.count.toLocaleString("ru-RU")}</td>
    <td class="ans-codes">${shown}${more}${row.codes.length ? "" : '<span class="muted">без кода</span>'}</td>
    ${sentiment ? toneCellHtml(row) : ""}
    <td class="ans-actions">${badges}<button type="button" class="icon-button ans-edit" data-edit-answer aria-label="Изменить коды ответа" title="Изменить коды">✎</button></td>
  </tr>`;
}

function answerEditorHtml(codeframe, row) {
  const codes = new Set(JSON.parse(row.dataset.codes));
  const themes = codeframe.themes;
  const parents = new Set(themes.map(theme => theme.parent_id).filter(Boolean));
  const type = codeframe.multi === false ? "radio" : "checkbox";
  const option = theme => `<label class="ans-option"><input type="${type}" name="ans-codes" value="${escapeAttribute(theme.id)}" ${codes.has(theme.id) ? "checked" : ""} /> ${escapeHtml(theme.name)}</label>`;
  const groups = themes.filter(theme => !theme.parent_id).map(theme => parents.has(theme.id)
    ? `<fieldset class="ans-group"><legend>${escapeHtml(theme.name)}</legend>${themes.filter(child => child.parent_id === theme.id).map(option).join("")}</fieldset>`
    : option(theme)).join("");
  const tone = codeframe.sentiment ? `
      <div class="ans-tones" role="radiogroup" aria-label="Тональность ответа">
        <span class="ans-tones-label">Тон</span>
        ${TONES.map(([key, label]) => `<label class="ans-tone-option is-${key}"><input type="radio" name="ans-tone" value="${key}" ${row.dataset.tone === key ? "checked" : ""} /> ${label}</label>`).join("")}
      </div>` : "";
  return `<tr class="ans-editor"><td colspan="${answerColumns(codeframe)}">
    <div class="ans-editor-box">
      <p class="ans-editor-text">${escapeHtml(row.querySelector(".ans-text").title)}</p>
      <div class="ans-options">${groups}</div>${tone}
      <div class="ans-editor-actions">
        ${row.dataset.source === "manual" ? '<button type="button" class="text-button" data-reset-answer title="Убрать правку из словаря и вернуть коды модели">Вернуть коды ИИ</button>' : ""}
        ${row.dataset.toneSource === "manual" ? '<button type="button" class="text-button" data-reset-tone title="Убрать правку тона и вернуть тон модели">Вернуть тон ИИ</button>' : ""}
        <span class="toolbar-grow"></span>
        <button type="button" class="secondary" data-cancel-answer>Отмена</button>
        <button type="button" data-save-answer>Сохранить</button>
      </div>
    </div>
  </td></tr>`;
}

async function setAnswerCodes(codeframe, key, codes) {
  await saveAnswer(codeframe, key, { codes });
}

/* `change.codes` и `change.tone`: список или null (снять правку); ключа нет —
   не трогать. Коды и тон лежат в разных словарях правок. */
async function saveAnswer(codeframe, key, change) {
  const base = `/api/projects/${currentProject.id}/codeframes/${codeframe.id}/answers`;
  const put = (url, body) => api(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  try {
    if ("codes" in change) currentProject = await put(base, { key, codes: change.codes });
    if ("tone" in change) currentProject = await put(`${base}/tone`, { key, tone: change.tone });
    renderProject();
    const fresh = codeframeById(codeframe.id);
    const offset = stateFor(fresh).offset;
    stateFor(fresh).summary = await api(`/api/projects/${currentProject.id}/codeframes/${fresh.id}/summary`);
    renderTiles(fresh);
    renderTones(fresh);
    renderCodeTree(fresh);
    await loadCodeframeAnswers(fresh, false);
    // Остаться на той же странице списка, если человек пролистал дальше.
    while (stateFor(fresh).offset < offset && !articleFor(fresh.id).querySelector("[data-more]").hidden) {
      await loadCodeframeAnswers(fresh, true);
    }
  } catch (error) {
    alert(error.message);
  }
}

/* ---------------- События тела ---------------- */

function eventCodeframe(event) {
  const article = event.target.closest(".cq");
  return article ? codeframeById(article.dataset.cf) : null;
}

document.querySelector("#coding-list").addEventListener("input", event => {
  const codeframe = eventCodeframe(event);
  if (!codeframe) return;
  const state = stateFor(codeframe);
  const row = event.target.closest(".code-row");
  if (row && event.target.matches("[data-code-name], [data-code-desc]")) {
    const theme = state.draft.themes.find(item => item.id === row.dataset.codeId);
    if (event.target.matches("[data-code-name]")) theme.name = event.target.value;
    else theme.description = event.target.value;
    markDirty(codeframe);
  } else if (event.target.matches("[data-setting='instruction']")) {
    state.draft.instruction = event.target.value;
    markDirty(codeframe);
  } else if (event.target.matches("[data-setting='other_threshold']")) {
    state.draft.other_threshold = Math.max(0, Math.min(20, Number(event.target.value) || 0)) / 100;
    markDirty(codeframe);
  } else if (event.target.matches("[data-search]")) {
    state.search = event.target.value;
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => loadCodeframeAnswers(codeframe, false), 300);
  }
});

document.querySelector("#coding-list").addEventListener("change", event => {
  const codeframe = eventCodeframe(event);
  if (!codeframe) return;
  const state = stateFor(codeframe);
  if (event.target.matches("[data-code-parent]")) {
    const theme = state.draft.themes.find(item => item.id === event.target.closest(".code-row").dataset.codeId);
    theme.parent_id = event.target.value || null;
    markDirty(codeframe);
    renderCodeTree(codeframe);
  } else if (event.target.matches("[data-setting='multi']")) {
    state.draft.multi = event.target.checked;
    markDirty(codeframe);
  } else if (event.target.matches("[data-setting='sentiment']")) {
    state.draft.sentiment = event.target.checked;
    markDirty(codeframe);
  } else if (event.target.matches("[data-theme-filter]")) {
    state.themeId = event.target.value;
    void loadCodeframeAnswers(codeframe, false);
  } else if (event.target.matches("[data-import]")) {
    void importCodebook(codeframe, event.target);
  }
});

document.querySelector("#coding-list").addEventListener("click", async event => {
  const codeframe = eventCodeframe(event);
  if (!codeframe || event.target.closest("[data-cq-toggle]")) return;
  const state = stateFor(codeframe);
  const target = event.target;
  if (target.closest("[data-add-code], [data-add-group]")) {
    newCodeCounter += 1;
    const group = Boolean(target.closest("[data-add-group]"));
    state.draft.themes.push({ id: `new-${newCodeCounter}`, name: "", parent_id: null, description: "", group });
    markDirty(codeframe);
    renderCodeTree(codeframe);
    articleFor(codeframe.id).querySelector(".code-row:last-of-type [data-code-name]")?.focus();
  } else if (target.closest("[data-code-remove]")) {
    const id = target.closest(".code-row").dataset.codeId;
    state.draft.themes = state.draft.themes.filter(theme => theme.id !== id)
      .map(theme => theme.parent_id === id ? { ...theme, parent_id: null } : theme);
    markDirty(codeframe);
    renderCodeTree(codeframe);
  } else if (target.closest("[data-save-codes]")) {
    await saveCodebook(codeframe);
  } else if (target.closest("[data-revise]")) {
    await reviseCodebook(codeframe);
  } else if (target.closest("[data-tile]")) {
    state.view = target.closest("[data-tile]").dataset.tile;
    renderTiles(codeframe);
    void loadCodeframeAnswers(codeframe, false);
  } else if (target.closest("[data-tone-filter]")) {
    const tone = target.closest("[data-tone-filter]").dataset.toneFilter;
    state.tone = state.tone === tone ? "" : tone;
    renderTones(codeframe);
    void loadCodeframeAnswers(codeframe, false);
  } else if (target.closest("[data-more]")) {
    void loadCodeframeAnswers(codeframe, true);
  } else if (target.closest("[data-expand-answer]")) {
    target.closest(".ans").classList.toggle("is-expanded");
  } else if (target.closest("[data-edit-answer]")) {
    const row = target.closest(".ans");
    const open = row.nextElementSibling?.classList.contains("ans-editor");
    articleFor(codeframe.id).querySelectorAll(".ans-editor").forEach(item => item.remove());
    if (!open) {
      row.insertAdjacentHTML("afterend", answerEditorHtml(codeframe, row));
      row.nextElementSibling.querySelector("input")?.focus();
    }
  } else if (target.closest("[data-cancel-answer]")) {
    target.closest(".ans-editor").remove();
  } else if (target.closest("[data-save-answer]")) {
    const editor = target.closest(".ans-editor");
    const row = editor.previousElementSibling;
    const codes = [...editor.querySelectorAll("input[name='ans-codes']:checked")].map(input => input.value);
    const before = JSON.parse(row.dataset.codes);
    const change = {};
    // Неизменённые коды не пишутся в словарь: иначе правка тона сделала бы
    // коды модели «ручными».
    if (codes.length !== before.length || codes.some(code => !before.includes(code))) change.codes = codes;
    const tone = editor.querySelector("input[name='ans-tone']:checked")?.value;
    if (tone && tone !== row.dataset.tone) change.tone = tone;
    if (Object.keys(change).length) void saveAnswer(codeframe, row.dataset.key, change);
    else editor.remove();
  } else if (target.closest("[data-reset-tone]")) {
    void saveAnswer(codeframe, target.closest(".ans-editor").previousElementSibling.dataset.key, { tone: null });
  } else if (target.closest("[data-reset-answer]")) {
    void setAnswerCodes(codeframe, target.closest(".ans-editor").previousElementSibling.dataset.key, null);
  } else if (target.closest("[data-code-mode]")) {
    await startCoding(codeframe, target.closest("[data-code-mode]").dataset.codeMode);
  }
});

async function saveCodebook(codeframe) {
  const state = stateFor(codeframe);
  const errorBox = document.querySelector("#text-error");
  errorBox.hidden = true;
  const themes = state.draft.themes.filter(theme => theme.name.trim()).map(theme => ({
    id: theme.id, name: theme.name.trim(), parent_id: theme.parent_id, description: theme.description || "",
  }));
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        label: codeframe.label,
        themes,
        instruction: state.draft.instruction,
        multi: state.draft.multi,
        sentiment: state.draft.sentiment,
        other_threshold: state.draft.other_threshold,
      }),
    });
    renderProject();
    const fresh = codeframeById(codeframe.id);
    const added = fresh.themes.length > codeframe.themes.length;
    // Подсказка нужна, только если у части ответов тона нет: после
    // выключения и включения прежние тоны модели остаются.
    const owned = configuredQuestions().filter(question => question.codeframe_id === fresh.id);
    const toneQuestion = owned.find(question => question.codeframe_tone);
    const codesQuestion = owned.find(question => !question.codeframe_tone);
    const toneOn = fresh.sentiment && !codeframe.sentiment && toneQuestion && codesQuestion
      && toneQuestion.valid_count < codesQuestion.valid_count;
    state.draft = draftFrom(fresh);
    state.dirty = false;
    if (!fresh.sentiment) state.tone = "";
    state.notice = added && fresh.coding_ref ? "Новые коды получат ответы после «Перекодировать с учётом правок»."
      : toneOn && fresh.coding_ref ? "Тон получат ответы после «Докодировать новые»: коды при этом не меняются." : "";
    renderCodeframeBody(fresh);
    showToast("Справочник сохранён");
  } catch (error) {
    showError(errorBox, error);
  }
}

async function startCoding(codeframe, mode) {
  if (mode === "reset" && !confirm("Сбросить справочник и все правки и закодировать вопрос заново? Словарь правок будет очищен. «Отменить» вернёт прежнее состояние.")) return;
  if (stateFor(codeframe).dirty && !confirm("В справочнике есть несохранённые изменения. Кодировать по сохранённому справочнику?")) return;
  try {
    if (!(await ensureAiAllowed())) return;
    const job = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}/code`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    aiJobsKnown.set(job.job_id, job);
    updateCodingStatuses();
    void pollAiJobs();
  } catch (error) {
    alert(error.message);
  }
}

async function reviseCodebook(codeframe) {
  const article = articleFor(codeframe.id);
  const text = article.querySelector("[data-revise-text]").value.trim();
  if (!text) {
    article.querySelector("[data-revise-text]").focus();
    return;
  }
  try {
    if (!(await ensureAiAllowed())) return;
    const job = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}/revise`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: text }),
    });
    aiJobsKnown.set(job.job_id, job);
    stateFor(codeframe).notice = "ИИ правит справочник…";
    renderCodeframeBody(codeframe);
    void pollAiJobs();
  } catch (error) {
    alert(error.message);
  }
}

async function importCodebook(codeframe, input) {
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  try {
    const definition = JSON.parse(await file.text());
    currentProject = await api(`/api/projects/${currentProject.id}/codeframes/${codeframe.id}/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(definition),
    });
    renderProject();
    const fresh = codeframeById(codeframe.id);
    stateFor(fresh).draft = draftFrom(fresh);
    stateFor(fresh).dirty = false;
    renderCodeframeBody(fresh);
    showToast("Справочник и словарь загружены");
  } catch (error) {
    alert(error.message);
  }
}

/* Завершение фоновой задачи (зовёт ai.js). */
async function onCodingJobFinished(job) {
  if (!currentProject || job.project_id !== currentProject.id) return;
  if (job.kind === "codebook_revision") {
    const codeframe = codeframeById(job.subject);
    if (!codeframe) return;
    const state = stateFor(codeframe);
    if (job.status === "complete") {
      const full = await api(`/api/projects/${currentProject.id}/jobs/${job.job_id}`);
      state.draft.themes = full.result.themes.map(theme => ({ id: theme.id, name: theme.name, parent_id: theme.parent_id || null, description: theme.description || "" }));
      state.dirty = true;
      state.notice = `ИИ предложил справочник${full.result.notes ? `: ${full.result.notes}` : ""}. Проверьте и сохраните; затем перекодируйте с учётом правок.`;
    } else {
      state.notice = job.error || "Правка справочника не удалась.";
    }
    if (codingOpen.has(codeframe.id)) renderCodeframeBody(codeframe);
    return;
  }
  if (job.kind !== "coding") return;
  currentProject = await api(`/api/projects/${currentProject.id}`);
  renderProject();
  const codeframe = codeframeById(job.subject);
  if (codeframe && !stateFor(codeframe).dirty) stateFor(codeframe).draft = draftFrom(codeframe);
  if (currentView === "text") await renderTextSection();
}

/* ---------------- Распознать открытые ---------------- */

async function openRecognizeSheet() {
  if (!currentProject) return;
  const body = document.querySelector("#recognize-body");
  const apply = document.querySelector("#recognize-apply");
  apply.disabled = true;
  body.innerHTML = '<p class="muted">Ищем открытые вопросы…</p>';
  openSheet(document.querySelector("#recognize-sheet"));
  try {
    textCandidates = await api(`/api/projects/${currentProject.id}/codeframes/candidates`);
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
    return;
  }
  const items = textCandidates.questions;
  if (!items.length) {
    body.innerHTML = '<p class="muted">Открытых вопросов в массиве не нашлось. Если вопрос открытый, а распознан иначе, поменяйте ему тип на «Открытый текст» в «Данных».</p>';
    return;
  }
  const row = item => `<label class="recognize-row${item.has_codeframe ? " is-done" : ""}">
      <input type="checkbox" value="${escapeAttribute(item.code)}" ${item.has_codeframe ? "disabled" : item.respondent_answers ? "checked" : ""} />
      <span><b><code>${escapeHtml(item.code)}</code> ${escapeHtml(item.label)}</b>
      <small>${item.has_codeframe ? "уже в «Открытых ответах» · " : ""}${item.answered.toLocaleString("ru-RU")} отв.${item.example ? ` · «${escapeHtml(item.example)}»` : ""}</small></span>
    </label>`;
  const answers = items.filter(item => item.respondent_answers);
  const service = items.filter(item => !item.respondent_answers);
  body.innerHTML = `
    ${answers.length ? `<section class="recognize-group"><h4>Ответы респондентов · ${answers.length}</h4>${answers.map(row).join("")}</section>` : ""}
    ${service.length ? `<section class="recognize-group"><h4>Служебные и короткие поля · ${service.length}</h4><p class="muted">Логины, даты, телефоны и ответы из одного слова. Обычно их не кодируют.</p>${service.map(row).join("")}</section>` : ""}`;
  updateRecognizeApply();
}

function updateRecognizeApply() {
  const count = document.querySelectorAll("#recognize-body input:checked:not(:disabled)").length;
  const apply = document.querySelector("#recognize-apply");
  apply.disabled = count === 0;
  apply.textContent = count ? `Кодировать ${count}` : "Кодировать";
}

document.querySelector("#recognize-body").addEventListener("change", updateRecognizeApply);

document.querySelector("#recognize-apply").addEventListener("click", async () => {
  const codes = [...document.querySelectorAll("#recognize-body input:checked:not(:disabled)")].map(input => input.value);
  if (!codes.length) return;
  const apply = document.querySelector("#recognize-apply");
  try {
    if (!(await ensureAiAllowed())) return;
    setBusy(apply, true, "Запускаем…");
    const result = await api(`/api/projects/${currentProject.id}/codeframes/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_codes: codes }),
    });
    currentProject = result.project;
    result.jobs.forEach(job => aiJobsKnown.set(job.job_id, job));
    closeSheet();
    renderProject();
    if (currentView !== "text") setView("text");
    else await renderTextSection();
    void pollAiJobs();
    showToast(`Кодирование запущено: ${plural(codes.length, "вопрос", "вопроса", "вопросов")}`);
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(apply, false, "Кодировать");
    updateRecognizeApply();
  }
});

["#recognize-open", "#recognize-open-top", "#recognize-open-data"].forEach(selector => {
  document.querySelector(selector).addEventListener("click", () => void openRecognizeSheet());
});
