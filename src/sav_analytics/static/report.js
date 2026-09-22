/* Раздел «Отчёт»: свойства книги и статистические настройки. Классический скрипт после app.js (см. решение 016): пользуется
   его состоянием, а app.js вызывает эти функции только после запуска. */

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
    meta = `${calculatedWeightSummary(calculated)}${bounds}`;
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
          ${statToggle("counts-sheet", "Лист «Счётчики»", settings.counts_sheet,
            "Отдельный лист книги: те же строки числами ответивших, без долей и тестов")}
          ${statToggle("correlations", "Лист Correlations", settings.correlations,
            "Отдельный лист книги: связи числовых вопросов между собой, с поправкой на множественность")}
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
        <div class="stat-controls stat-row"><span class="stat-label">Ранжирование</span>
          ${rankingMetricOptions.map(option => statToggle(`ranking:${option.value}`, option.label, settings.ranking_metrics.includes(option.value))).join("")}
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
  <details id="header-preview" class="header-preview"><summary>Превью шапки книги</summary><div id="header-preview-body"><p class="analysis-note">Раскройте — посчитаем колонки и базы до сборки.</p></div></details>
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
    ranking_metrics: settings.ranking_metrics,
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
    correlations: settings.correlations,
    counts_sheet: settings.counts_sheet,
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
  if (name === "correlations") return { correlations: value === "on" };
  if (name === "counts-sheet") return { counts_sheet: value === "on" };
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
  if (name.startsWith("scale:") || name.startsWith("numeric:") || name.startsWith("ranking:")) {
    const [group, metric] = name.split(":");
    const key = `${group}_metrics`;
    const options = group === "ranking" ? rankingMetricOptions : group === "scale" ? scaleMetricOptions : numericMetricOptions;
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

/* Превью шапки книги: какие колонки и базы получит Excel — до сборки.
   Колонки считает тот же расчёт баннера, что и книга. */
document.querySelector("#entity-list").addEventListener("toggle", event => {
  if (event.target?.id === "header-preview" && event.target.open) void loadHeaderPreview();
}, true);

async function loadHeaderPreview() {
  const body = document.querySelector("#header-preview-body");
  const bannerId = selectedReportBannerId();
  if (!bannerId) {
    const rows = currentProject.inspection.row_count;
    body.innerHTML = `<p class="analysis-note">Баннер не выбран: в книге будет только колонка Total, N ${rows.toLocaleString("ru-RU")}.</p>`;
    return;
  }
  body.innerHTML = '<p class="analysis-note">Считаем…</p>';
  try {
    const preview = await api(`/api/projects/${currentProject.id}/banners/${bannerId}/preview`);
    const minimumBase = configuredReportSettings().minimum_base;
    const wide = preview.columns.length >= 50
      ? `<p class="analysis-note">Колонок ${preview.columns.length}: книга станет неудобной для чтения.</p>` : "";
    const overlaps = (preview.overlaps || []).map(item =>
      `<p class="analysis-note">«${escapeHtml(item.block)}»: колонки пересекаются у ${item.respondents.toLocaleString("ru-RU")} респондентов.</p>`).join("");
    body.innerHTML = `${wide}${overlaps}<div class="header-preview-rows">${preview.columns.map((column, index) => {
      const small = column.base > 0 && column.base < minimumBase;
      const block = index === 0 ? "" : `${escapeHtml(column.block || "")} · `;
      return `<div class="header-preview-row ${small ? "small-base" : ""}"><span title="${escapeAttribute(column.label)}">${block}${escapeHtml(column.label)}</span><em>${column.base.toLocaleString("ru-RU")}${small ? " · малая база" : ""}</em></div>`;
    }).join("")}</div>`;
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

