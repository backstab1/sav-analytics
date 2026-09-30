/* Раздел «Таблицы»: живой кросстаб. Самостоятельный модуль без доступа к
 * оболочке; загружается перед shell.js, и оболочка вызывает его через
 * TablesSection — activate() при входе в раздел и setVariables() при
 * открытии и закрытии проекта.
 *
 * Раскладка — строки, разрез, фильтр — уходит на сервер, а таблица
 * приходит посчитанной тем же кодом, что пишет книгу Excel
 * (`core/reporting/live.py`). Своих формул у экрана нет: число здесь
 * равно числу в выгрузке, и цвет значимости, стрелка волны и число
 * знаков тоже берутся из книги.
 *
 * Таблица сохраняется в проект сама: строки, разрез, фильтр, вид и NET
 * пишутся в `configuration.table_reports` через полсекунды после правки.
 * Таблиц в проекте несколько, текущая выбирается в меню «Таблица».
 */
const TablesSection = (() => {
  "use strict";

  const KIND_LABEL = {
    single_choice: "один",
    multiple_choice_dichotomy: "мульти",
    multiple_choice_categorical: "мульти",
    scale: "шкала",
    numeric: "число",
    ranking: "ранг",
    matrix: "матрица",
    open_text: "текст",
    technical: "тех.",
    recoding: "группы",
  };
  // Пилюля параметра показывает значение, а не приглашение: пустое
  // состояние — это тоже значение, «только Total» и «вся выборка».
  const ZONE_EMPTY = {
    rows: "не выбраны",
    cols: "только Total",
    filter: "вся выборка",
  };
  const ZONE_TITLE = {
    rows: "Строки таблицы",
    cols: "Колонки — разрез",
    filter: "Фильтр выборки",
  };
  const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

  const list = document.querySelector("#bld-list");
  const search = document.querySelector("#bld-search");
  const count = document.querySelector("#bld-count");
  const picker = document.querySelector("#bld-picker");
  const pickerTitle = document.querySelector("#bld-picker-title");
  const params = document.querySelectorAll(".bld-param[data-zone]");
  const wrap = document.querySelector("#bld-grid-wrap");
  const note = document.querySelector("#bld-stage-note");
  const sheetSelect = document.querySelector("#bld-sheet");
  const measureSelect = document.querySelector("#bld-measure");
  const boxSelect = document.querySelector("#bld-box");
  const questionBoxSelect = document.querySelector("#bld-qbox");
  const invertButton = document.querySelector("#bld-q-invert");
  const groupsMenu = document.querySelector("#bld-groups-menu");
  const netList = document.querySelector("#bld-net-list");
  const blockList = document.querySelector("#bld-blocks");
  const saveCutButton = document.querySelector("#bld-save-cut");
  const exportButton = document.querySelector("#bld-export");
  const exportMenu = document.querySelector("#bld-export-menu");
  const testsSlot = document.querySelector("#bld-tests");
  const viewButton = document.querySelector("#bld-view");
  const viewMenu = document.querySelector("#bld-view-menu");
  const assistantToggle = document.querySelector("#bld-assistant-toggle");
  const assistantBody = document.querySelector("#bld-assistant-body");
  const log = document.querySelector("#bld-log");
  const form = document.querySelector("#bld-form");
  const input = document.querySelector("#bld-input");
  const reportButton = document.querySelector("#bld-report");
  const reportName = document.querySelector("#bld-report-name");
  const reportState = document.querySelector("#bld-report-state");
  const reportMenu = document.querySelector("#bld-report-menu");
  const reportList = document.querySelector("#bld-report-list");
  const renameForm = document.querySelector("#bld-report-rename");
  const renameInput = document.querySelector("#bld-report-rename-input");

  let variables = [];
  let byCode = new Map();
  let projectId = null;
  let filters = [];
  // Таблица пересчитывается, только когда раздел виден; изменения,
  // пришедшие в скрытый раздел, помечают её устаревшей до показа.
  let stale = true;
  let requestToken = 0;
  let lastTable = null;
  // Сообщение о действии над таблицей держится до следующей правки раскладки:
  // пересчёт после сохранения иначе стёр бы его своей подписью.
  let notice = "";
  // Разрез задаётся либо сохранённым баннером, либо блоками колонок.
  let banners = [];
  let bannerId = null;
  // Колонки — блоки: массив из одного кода или из двух, внешнего и
  // внутреннего. Блок из двух — полное пересечение категорий, как
  // двухуровневый блок баннера в книге. Код стоит не больше чем в одном
  // блоке, поэтому первый код и служит именем блока.
  const layout = { rows: [], cols: [], filter: [] };

  /* Сохранённые таблицы проекта. `loadedKey` — таблица в том виде, в каком
     её последний раз прочитали или записали: если в проекте она стала
     другой (отмена, вторая вкладка), экран перечитывает раскладку.
     `savedLayout` — раскладка экрана на момент последней записи: пока
     текущая с ней совпадает, писать нечего. */
  let reports = [];
  let currentReportId = null;
  let loadedKey = null;
  let savedLayout = null;
  let saveTimer = null;
  let saving = null;
  const SAVE_DELAY = 500;

  function setVariables(next, context = {}) {
    variables = next;
    byCode = new Map(next.map(item => [item.code, item]));
    const nextProject = context.projectId || null;
    if (nextProject !== projectId) {
      // Другой проект: недописанная правка прежнего уже не его.
      window.clearTimeout(saveTimer);
      saveTimer = null;
      currentReportId = null;
      loadedKey = null;
    }
    projectId = nextProject;
    filters = context.filters || [];
    banners = context.banners || [];
    reports = context.tableReports || [];
    // Пока правка ждёт записи, раскладку экрана не трогаем: иначе ответ,
    // пришедший из другого раздела, стёр бы только что отмеченное.
    const pending = saveTimer !== null || saving !== null;
    const report = reports.find(item => item.id === currentReportId) || null;
    if (!pending && !report) {
      const chosen = reports.find(item => item.id === rememberedReport()) || reports[0] || null;
      currentReportId = chosen?.id || null;
      applyLayout(chosen);
    } else if (!pending && JSON.stringify(storedPayload(report)) !== loadedKey) {
      applyLayout(report);
    } else {
      if (bannerId && !banners.some(item => item.id === bannerId)) bannerId = null;
      layout.rows = layout.rows.filter(code => byCode.get(code)?.canRow);
      layout.cols = usableBlocks(layout.cols);
      layout.filter = layout.filter.filter(id => filters.some(item => item.id === id));
    }
    treesStale = true;
    renderReportControl();
    render();
  }

  /* ---- Сохранённые таблицы ----
     Разрез хранится так, как его собирают на экране: баннером или
     блоками источников колонок. Строки — коды вопросов. */
  function sourceCode(source) {
    return source.kind === "recoding" ? `recoding:${source.ref}` : source.ref;
  }

  function colCodes() {
    return layout.cols.flat();
  }

  // Переменная, которой больше нет, уходит из своего блока; внешний уровень
  // без внутреннего остаётся одиночным блоком.
  function usableBlocks(blocks) {
    return blocks
      .map(block => block.filter(code => byCode.get(code)?.canCol))
      .filter(block => block.length);
  }

  // Блоки идут в порядке анкеты по внешней переменной, а не в порядке щелчков.
  function sortBlocks() {
    const order = new Map(variables.map((item, index) => [item.code, index]));
    layout.cols.sort((left, right) => order.get(left[0]) - order.get(right[0]));
  }

  function storedPayload(report) {
    return {
      rows: [...(report?.rows || [])],
      banner_id: report?.banner_id || null,
      cols: (report?.cols || []).map(block => ({
        sources: (block.sources || []).map(source => ({ kind: source.kind, ref: source.ref })),
      })),
      filter_id: report?.filter_id || null,
      sheet: report?.sheet || "main",
      measure: report?.measure || "value",
      scale_box: report?.scale_box ?? null,
      boxes: report?.boxes || {},
      inverted: [...(report?.inverted || [])],
      nets: report?.nets || {},
    };
  }

  function layoutPayload() {
    const nets = {};
    liveNets.forEach((items, code) => {
      if (items.length) nets[code] = items.map(net => ({ label: net.label, values: net.values }));
    });
    return {
      rows: [...layout.rows],
      banner_id: bannerId,
      cols: bannerId ? [] : layout.cols
        .map(block => ({
          sources: block
            .map(code => byCode.get(code)?.source)
            .filter(Boolean)
            .map(source => ({ kind: source.kind, ref: source.ref })),
        }))
        .filter(block => block.sources.length),
      filter_id: layout.filter[0] || null,
      sheet: sheetSelect.value,
      measure: measureSelect.value,
      scale_box: boxSelect.value ? Number(boxSelect.value) : null,
      boxes: Object.fromEntries(liveBoxes),
      inverted: [...liveInverted],
      nets,
    };
  }

  function isEmptyLayout(payload) {
    return !payload.rows.length && !payload.cols.length && !payload.banner_id && !payload.filter_id;
  }

  // Переменные, которых в отчёте больше нет, не показываются, но из
  // сохранённой таблицы не вычёркиваются, пока её не поправят.
  function applyLayout(report) {
    const stored = storedPayload(report);
    layout.rows = stored.rows.filter(code => byCode.get(code)?.canRow);
    layout.cols = usableBlocks(stored.cols.map(block => block.sources.map(sourceCode)));
    bannerId = stored.banner_id && banners.some(item => item.id === stored.banner_id) ? stored.banner_id : null;
    layout.filter = stored.filter_id && filters.some(item => item.id === stored.filter_id) ? [stored.filter_id] : [];
    sheetSelect.value = stored.sheet;
    measureSelect.value = stored.measure;
    boxSelect.value = stored.scale_box ? String(stored.scale_box) : "";
    liveNets.clear();
    Object.entries(stored.nets).forEach(([code, nets]) => liveNets.set(code, nets));
    liveBoxes.clear();
    Object.entries(stored.boxes).forEach(([code, size]) => liveBoxes.set(code, size));
    liveInverted.clear();
    stored.inverted.forEach(code => liveInverted.add(code));
    notice = "";
    lastTable = null;
    loadedKey = report ? JSON.stringify(stored) : null;
    savedLayout = JSON.stringify(layoutPayload());
  }

  function reportKey() {
    return `sav-analytics:table-report:${projectId}`;
  }

  function rememberedReport() {
    try {
      return localStorage.getItem(reportKey());
    } catch {
      return null;
    }
  }

  function rememberReport() {
    try {
      if (currentReportId) localStorage.setItem(reportKey(), currentReportId);
    } catch {
      // Без хранилища откроется первая таблица проекта.
    }
  }

  function scheduleSave() {
    if (!projectId || JSON.stringify(layoutPayload()) === savedLayout) return;
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => { void flushSave(); }, SAVE_DELAY);
  }

  function setReportState(text, title = "") {
    reportState.textContent = text;
    reportState.title = title;
    reportState.classList.toggle("error", Boolean(title));
  }

  function tablesRequest(url, method, body) {
    return window.SavApp.saveTables(url, {
      method,
      headers: { "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  }

  function acceptReports(project) {
    reports = project.configuration.table_reports || [];
  }

  // Записать раскладку сейчас. Первая содержательная правка в проекте без
  // таблиц заводит «Таблицу 1»: работа не пропадает при перезагрузке.
  async function flushSave() {
    window.clearTimeout(saveTimer);
    saveTimer = null;
    if (saving) await saving;
    const payload = layoutPayload();
    const key = JSON.stringify(payload);
    if (!projectId || key === savedLayout) return;
    if (!currentReportId && isEmptyLayout(payload)) {
      savedLayout = key;
      return;
    }
    let saved = false;
    const base = `/api/projects/${projectId}/tables/reports`;
    saving = (async () => {
      setReportState("Сохраняем…");
      try {
        const project = currentReportId
          ? await tablesRequest(`${base}/${currentReportId}`, "PUT", payload)
          : await tablesRequest(base, "POST", payload);
        acceptReports(project);
        if (!currentReportId) currentReportId = reports.at(-1)?.id || null;
        rememberReport();
        loadedKey = JSON.stringify(storedPayload(reports.find(item => item.id === currentReportId)));
        savedLayout = key;
        saved = true;
        setReportState("");
      } catch (error) {
        setReportState("Не сохранено", error.message);
      } finally {
        saving = null;
        renderReportControl();
      }
    })();
    await saving;
    // Правки, сделанные во время записи, уходят следующей.
    if (saved) scheduleSave();
  }

  async function reportAction(run) {
    closeReportMenu();
    await flushSave();
    try {
      await run();
    } catch (error) {
      setReportState("Не выполнено", error.message);
    }
    renderReportControl();
    render();
  }

  function openReport(id) {
    const report = reports.find(item => item.id === id) || null;
    currentReportId = report?.id || null;
    rememberReport();
    applyLayout(report);
    treesStale = true;
  }

  const REPORT_ACTIONS = {
    async new() {
      acceptReports(await tablesRequest(`/api/projects/${projectId}/tables/reports`, "POST", {}));
      openReport(reports.at(-1).id);
    },
    async copy() {
      const index = reports.findIndex(item => item.id === currentReportId);
      acceptReports(await tablesRequest(`/api/projects/${projectId}/tables/reports/${currentReportId}/copy`, "POST"));
      openReport(reports[index + 1].id);
    },
    // Очистка — обычная правка раскладки: название остаётся, а вернуть
    // прежнее можно кнопкой «Отменить».
    async clear() {
      layout.rows = [];
      layout.cols = [];
      layout.filter = [];
      bannerId = null;
      liveNets.clear();
      liveBoxes.clear();
      liveInverted.clear();
      treesStale = true;
    },
    async delete() {
      const report = reports.find(item => item.id === currentReportId);
      if (!report || !confirm(`Удалить таблицу «${report.name}»? Вернуть её можно кнопкой «Отменить».`)) return;
      const index = reports.indexOf(report);
      acceptReports(await tablesRequest(`/api/projects/${projectId}/tables/reports/${report.id}`, "DELETE"));
      openReport((reports[index] || reports[index - 1])?.id);
    },
  };

  function describeReport(report) {
    const rows = (report.rows || []).length;
    const parts = [rows ? `${rows} ${plural(rows, "вопрос", "вопроса", "вопросов")}` : "пустая"];
    if (report.banner_id) parts.push("баннер");
    else if ((report.cols || []).length) parts.push(`блоков: ${report.cols.length}`);
    if (report.filter_id) parts.push("фильтр");
    return parts.join(" · ");
  }

  function renderReportControl() {
    const current = reports.find(item => item.id === currentReportId);
    reportName.textContent = current?.name || "не сохранена";
    reportButton.classList.toggle("off", !current);
    reportButton.title = current ? `Таблица «${current.name}» — сохранённые таблицы проекта` : "Сохранённые таблицы проекта";
    reportButton.disabled = !projectId;
    if (reportMenu.hidden) return;
    reportList.innerHTML = reports.length
      ? reports.map(item => `<button type="button" role="option" class="bld-report-item" data-report="${escapeHtml(item.id)}" aria-selected="${item.id === currentReportId}">` +
        `<span>${escapeHtml(item.name)}</span><small>${escapeHtml(describeReport(item))}</small></button>`).join("")
      : '<p class="bld-report-empty">Сохранённых таблиц нет. Первая сохранится сама, как только вы отметите строки.</p>';
    reportMenu.querySelectorAll("[data-report-action]").forEach(button => {
      button.disabled = button.dataset.reportAction !== "new" && !current;
    });
  }

  function openReportMenu() {
    closePicker();
    closeViewMenu();
    closeGroupsMenu();
    closeExportMenu();
    renameForm.hidden = true;
    reportMenu.hidden = false;
    reportButton.setAttribute("aria-expanded", "true");
    renderReportControl();
    placePopover(reportMenu, reportButton);
  }

  function closeReportMenu() {
    if (reportMenu.hidden) return;
    reportMenu.hidden = true;
    renameForm.hidden = true;
    reportButton.setAttribute("aria-expanded", "false");
  }

  reportButton.addEventListener("click", event => {
    event.stopPropagation();
    if (reportMenu.hidden) openReportMenu();
    else closeReportMenu();
  });
  reportMenu.addEventListener("click", event => {
    event.stopPropagation();
    const item = event.target.closest("[data-report]");
    if (item) {
      if (item.dataset.report === currentReportId) closeReportMenu();
      else void reportAction(async () => openReport(item.dataset.report));
      return;
    }
    const action = event.target.closest("[data-report-action]")?.dataset.reportAction;
    if (!action) return;
    if (action === "rename") {
      renameInput.value = reports.find(report => report.id === currentReportId)?.name || "";
      renameForm.hidden = false;
      renameInput.select();
      return;
    }
    void reportAction(REPORT_ACTIONS[action]);
  });
  renameForm.addEventListener("submit", event => {
    event.preventDefault();
    const name = renameInput.value.trim();
    if (!name || !currentReportId) return;
    void reportAction(async () => {
      acceptReports(await tablesRequest(
        `/api/projects/${projectId}/tables/reports/${currentReportId}`, "PATCH", { name },
      ));
    });
  });
  renameInput.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    renameForm.hidden = true;
  });
  document.addEventListener("click", event => {
    if (!event.target.closest("#bld-report-menu") && !event.target.closest("#bld-report")) closeReportMenu();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeReportMenu();
  });
  // Уходя со страницы, недописанную правку отправляем сразу.
  window.addEventListener("pagehide", () => { if (saveTimer) void flushSave(); });

  // Строкой может стать вопрос, который умеет лист книги; колонкой — то,
  // у чего есть категории: одиночный выбор с подписями или группировка.
  function usable(code, zone) {
    const item = byCode.get(code);
    return Boolean(item && (zone === "rows" ? item.canRow : item.canCol));
  }

  function addToZone(code, zone) {
    notice = "";
    if (zone === "filter") {
      layout.filter = filters.some(item => item.id === code) ? [code] : [];
      render();
      return;
    }
    if (!usable(code, zone)) return;
    // Переменная в колонках и сохранённый баннер — два способа задать один
    // разрез, поэтому выбор одного снимает другой.
    if (zone === "cols") {
      bannerId = null;
      // Отмеченная переменная встаёт отдельным блоком; вложить её — действие
      // над блоком, а не порядок галочек.
      if (!colCodes().includes(code)) layout.cols.push([code]);
      sortBlocks();
      render();
      return;
    }
    const order = new Map(variables.map((item, index) => [item.code, index]));
    layout[zone] = [...layout[zone].filter(item => item !== code), code]
      .sort((left, right) => order.get(left) - order.get(right));
    render();
  }

  function removeFromZone(code, zone) {
    notice = "";
    if (zone === "cols") layout.cols = layout.cols.map(block => block.filter(item => item !== code)).filter(block => block.length);
    else layout[zone] = layout[zone].filter(item => item !== code);
    render();
  }

  /* Палитра переменных живёт в поповере: экран отдан таблице, а
     «строки / колонки / фильтр» стали полосой параметров под ней.
     Поповер знает свою зону, поэтому строка списка — переключатель:
     щелчок кладёт переменную в зону, повторный забирает. */
  /* Поповер знает две вещи: из какой пилюли открыт (`pickerZone`) и
     что в нём сейчас — список переменных или сборка NET (`pickerMode`).
     Раньше режим NET не отмечался нигде, кроме data-атрибута, и
     Escape с щелчком мимо его не закрывали: `closePicker` выходил по
     пустой зоне, а панель оставалась висеть поверх таблицы. */
  let pickerZone = null;
  let pickerMode = null;
  // Блок, во что вкладывают, пока поповер в режиме «nest».
  let nestOuter = null;

  /* Место поповера считается по месту на экране, а не по одной
     выбранной стороне. Прежняя версия цеплялась `bottom` за верх
     пилюли, и когда раздел не помещался в высоту окна, поповер
     уезжал под нижний край — пилюля нажималась, а список «не
     открывался». */
  function placePopover(element, anchor) {
    const margin = 10;
    const gap = 6;
    element.style.maxHeight = "";
    element.style.bottom = "auto";
    const box = anchor.getBoundingClientRect();
    const below = window.innerHeight - box.bottom - margin - gap;
    const above = box.top - margin - gap;
    const natural = element.offsetHeight;
    const up = natural > below && above > below;
    const room = Math.min(520, Math.max(180, up ? above : below));
    element.style.maxHeight = `${Math.round(room)}px`;
    const height = Math.min(natural, room);
    const top = up ? box.top - gap - height : box.bottom + gap;
    const width = element.offsetWidth;
    element.style.left = `${Math.round(Math.max(margin, Math.min(box.left, window.innerWidth - width - margin)))}px`;
    element.style.top = `${Math.round(Math.max(margin, Math.min(top, window.innerHeight - height - margin)))}px`;
  }

  function openPicker(zone, anchor) {
    if (pickerMode === "zone" && pickerZone === zone) {
      closePicker();
      return;
    }
    closeViewMenu();
    closeReportMenu();
    pickerMode = "zone";
    pickerZone = zone;
    delete picker.dataset.mode;
    pickerTitle.textContent = ZONE_TITLE[zone];
    search.value = "";
    picker.hidden = false;
    renderPalette();
    placePopover(picker, anchor);
    params.forEach(item => item.setAttribute("aria-expanded", String(item.dataset.zone === zone)));
    search.focus();
  }

  function closePicker() {
    if (!pickerMode) return;
    pickerMode = null;
    pickerZone = null;
    nestOuter = null;
    picker.hidden = true;
    delete picker.dataset.mode;
    params.forEach(item => item.setAttribute("aria-expanded", "false"));
  }

  function renderPalette() {
    // Пока в поповере собирают NET, список фильтров его не затирает.
    if (pickerMode === "net") return;
    list.innerHTML = "";
    const query = search.value.trim().toLowerCase();
    if (pickerMode === "nest") renderNestPalette(query);
    else renderFilterPalette(query);
  }

  /* ---- Деревья слева: строки и колонки ----
     Пункт — галочка и стрелка. Галочка кладёт вопрос в таблицу, стрелка
     раскрывает содержимое: коды, переменные группы, блоки баннера. Так
     раскладку видно целиком, а не только по подписи пилюли, и выбор не
     прячется в поповер, который надо открывать ради каждого вопроса.
     Порядок в таблице — порядок анкеты, а не порядок щелчков. */
  const trees = {
    rows: {
      root: document.querySelector("#bld-rows-tree"),
      search: document.querySelector("#bld-rows-search"),
      count: document.querySelector("#bld-rows-count"),
      clear: document.querySelector('[data-clear-zone="rows"]'),
    },
    cols: {
      root: document.querySelector("#bld-cols-tree"),
      search: document.querySelector("#bld-cols-search"),
      count: document.querySelector("#bld-cols-count"),
      clear: document.querySelector('[data-clear-zone="cols"]'),
    },
  };
  const expanded = { rows: new Set(), cols: new Set() };
  // Деревья, как и таблица, собираются только на видимом экране: на
  // массиве в 3 000 переменных пересборка скрытого списка стоила секунды.
  let treesStale = true;

  // Одноимённые баннеры иначе шли столбиком одинаковых строк.
  function numberedBanners() {
    const seen = new Map();
    return banners.map(item => {
      const index = (seen.get(item.name) || 0) + 1;
      seen.set(item.name, index);
      return { ...item, name: index > 1 ? `${item.name} (${index})` : item.name };
    });
  }

  function treeNode({ zone, key, code, label, children, checked, title }) {
    const kids = children || [];
    const open = expanded[zone].has(key);
    const node = document.createElement("div");
    node.className = "bld-node";
    node.setAttribute("role", "treeitem");
    node.dataset.key = key;
    if (kids.length) node.setAttribute("aria-expanded", String(open));
    node.innerHTML =
      '<div class="bld-node-row">' +
      `<button type="button" class="bld-twist" tabindex="-1" aria-label="Показать содержимое"${kids.length ? "" : " disabled"}></button>` +
      '<label class="bld-node-main"><input type="checkbox" class="bld-check" /><span class="bld-node-code"></span><span class="bld-node-name"></span></label>' +
      (zone === "rows" && code ? '<button type="button" class="bld-node-tune" hidden></button>' : "") +
      "</div>";
    const input = node.querySelector(".bld-check");
    input.checked = checked;
    if (code) input.dataset.code = code;
    const tune = node.querySelector(".bld-node-tune");
    if (tune) {
      tune.dataset.tune = code;
      tune.title = "Top/Bottom и NET-группы этого вопроса";
    }
    node.querySelector(".bld-node-code").textContent = code ? (byCode.get(code)?.display || code) : "";
    node.querySelector(".bld-node-name").textContent = label;
    node.querySelector(".bld-node-main").title = title || label;
    node.kids = kids;
    if (open) fillKids(node);
    return node;
  }

  function fillKids(node) {
    const box = document.createElement("ul");
    box.className = "bld-kids";
    box.setAttribute("role", "group");
    node.kids.forEach(kid => {
      const item = document.createElement("li");
      item.innerHTML = '<span class="bld-kid-code"></span><span class="bld-kid-name"></span>';
      item.querySelector(".bld-kid-code").textContent = kid.code;
      item.querySelector(".bld-kid-name").textContent = kid.label;
      item.title = kid.label;
      box.append(item);
    });
    node.append(box);
  }

  function treeNote(className, text) {
    const note = document.createElement("p");
    note.className = className;
    note.textContent = text;
    return note;
  }

  function itemMatches(item, query) {
    return !query
      || (item.display || item.code || "").toLowerCase().includes(query)
      || (item.label || item.name || "").toLowerCase().includes(query);
  }

  function kindTitle(item) {
    return KIND_LABEL[item.type] ? `${item.label} · ${KIND_LABEL[item.type]}` : item.label;
  }

  function renderTrees() {
    if (!trees.rows.root.offsetParent) {
      treesStale = true;
      return;
    }
    treesStale = false;

    const rowsQuery = trees.rows.search.value.trim().toLowerCase();
    const rowItems = variables.filter(item => item.canRow);
    const rows = document.createDocumentFragment();
    rowItems.filter(item => itemMatches(item, rowsQuery)).forEach(item => {
      rows.append(treeNode({
        zone: "rows", key: item.code, code: item.code, label: item.label, children: item.children,
        checked: layout.rows.includes(item.code), title: kindTitle(item),
      }));
    });
    if (!rows.childNodes.length) {
      rows.append(treeNote("bld-tree-empty", !projectId
        ? "Откройте проект — вопросы появятся здесь."
        : rowItems.length ? "Ничего не найдено." : "В отчёте нет вопросов, которые раскладываются в таблицу."));
    }
    trees.rows.root.replaceChildren(rows);

    const colsQuery = trees.cols.search.value.trim().toLowerCase();
    const cols = document.createDocumentFragment();
    variables.filter(item => item.canCol && itemMatches(item, colsQuery)).forEach(item => {
      cols.append(treeNode({
        zone: "cols", key: item.code, code: item.code, label: item.label, children: item.children,
        checked: !bannerId && colCodes().includes(item.code), title: kindTitle(item),
      }));
    });
    // Баннеры после переменных: их в проекте накапливается больше, чем
    // помещается в панель, и сверху они закрыли бы сами разрезы.
    const bannerItems = numberedBanners().filter(item => itemMatches(item, colsQuery));
    if (bannerItems.length) {
      cols.append(treeNote("bld-tree-caption", "Баннеры отчёта"));
      bannerItems.forEach(item => {
        const node = treeNode({
          zone: "cols", key: `banner:${item.id}`, label: item.name, children: item.children,
          checked: bannerId === item.id, title: `${item.name} — колонки как в книге`,
        });
        node.querySelector(".bld-check").dataset.banner = item.id;
        cols.append(node);
      });
    }
    if (!cols.childNodes.length && projectId) cols.append(treeNote("bld-tree-empty", "Ничего не найдено."));
    trees.cols.root.replaceChildren(cols);
    renderTreeCounts();
    syncTuneButtons();
  }

  // После галочки дерево не пересобирается: поправить отметки и счётчики
  // дешевле, и раскрытые пункты со скроллом остаются на месте.
  function syncTrees() {
    if (treesStale || !trees.rows.root.offsetParent) {
      renderTrees();
      return;
    }
    trees.rows.root.querySelectorAll(".bld-check").forEach(input => {
      input.checked = layout.rows.includes(input.dataset.code);
    });
    syncTuneButtons();
    trees.cols.root.querySelectorAll(".bld-check").forEach(input => {
      input.checked = input.dataset.banner
        ? bannerId === input.dataset.banner
        : !bannerId && colCodes().includes(input.dataset.code);
    });
    renderTreeCounts();
  }

  function renderTreeCounts() {
    const rowsCount = layout.rows.length;
    const colsCount = bannerId ? 1 : layout.cols.length;
    trees.rows.count.textContent = rowsCount ? String(rowsCount) : "";
    trees.cols.count.textContent = bannerId ? "баннер" : (colsCount ? String(colsCount) : "только Total");
    trees.rows.clear.hidden = !rowsCount;
    trees.cols.clear.hidden = !colsCount;
  }

  Object.entries(trees).forEach(([zone, tree]) => {
    tree.root.addEventListener("change", event => {
      const input = event.target.closest(".bld-check");
      if (!input) return;
      if (input.dataset.banner) {
        notice = "";
        bannerId = input.checked ? input.dataset.banner : null;
        if (bannerId) layout.cols = [];
        render();
        return;
      }
      if (input.checked) addToZone(input.dataset.code, zone);
      else removeFromZone(input.dataset.code, zone);
    });
    tree.root.addEventListener("click", event => {
      const twist = event.target.closest(".bld-twist");
      if (!twist) return;
      const node = twist.closest(".bld-node");
      const open = !expanded[zone].has(node.dataset.key);
      if (open) {
        expanded[zone].add(node.dataset.key);
        fillKids(node);
      } else {
        expanded[zone].delete(node.dataset.key);
        node.querySelector(".bld-kids")?.remove();
      }
      node.setAttribute("aria-expanded", String(open));
    });
    tree.search.addEventListener("input", renderTrees);
    tree.clear.addEventListener("click", () => {
      notice = "";
      layout[zone] = [];
      if (zone === "cols") bannerId = null;
      render();
    });
  });

  /* ---- Блоки колонок ----
     Над деревом «Колонки» — сам разрез: блок за блоком, как он встанет в
     шапку таблицы. Вложение — явное действие над блоком: «Вложить…» или
     перетаскивание одного блока на другой. Внешняя переменная — первая,
     «⇄» меняет уровни местами, «Разделить» возвращает два блока. */
  function blockOf(outer) {
    return layout.cols.find(block => block[0] === outer);
  }

  function categoryCount(code) {
    return byCode.get(code)?.children?.length || 0;
  }

  function renderBlocks() {
    const show = !bannerId && layout.cols.length > 0;
    blockList.hidden = !show;
    if (!show) {
      blockList.replaceChildren();
      return;
    }
    blockList.innerHTML = layout.cols.map(block => {
      const [outer, inner] = block.map(code => byCode.get(code));
      const counts = block.map(categoryCount);
      const total = counts.reduce((left, right) => left * right, 1);
      const size = counts.every(Boolean)
        ? `${inner ? `${counts[0]} × ${counts[1]} = ` : ""}${total} ${plural(total, "колонка", "колонки", "колонок")}`
        : "";
      const name = inner
        ? `${escapeHtml(outer.label)}<span class="bld-block-in" aria-label="внутри"> › </span>${escapeHtml(inner.label)}`
        : escapeHtml(outer.label);
      const title = inner ? `${outer.label}, внутри — ${inner.label}` : outer.label;
      const actions = inner
        ? '<button type="button" data-block-action="swap" title="Поменять внешний и внутренний уровни">⇄</button>' +
          '<button type="button" data-block-action="split" title="Разложить на два блока рядом">Разделить</button>'
        : '<button type="button" data-block-action="nest" aria-haspopup="dialog" title="Вложить другую переменную внутрь этого блока">Вложить…</button>';
      return `<li class="bld-block${inner ? " nested" : ""}" data-block="${escapeHtml(outer.code)}" draggable="${inner ? "false" : "true"}">` +
        `<span class="bld-block-name" title="${escapeHtml(title)}">${name}</span>` +
        `<span class="bld-block-meta">${size ? `<span class="bld-block-size">${size}</span>` : ""}${actions}` +
        '<button type="button" class="bld-block-remove" data-block-action="remove" aria-label="Убрать блок" title="Убрать блок">×</button></span>' +
        "</li>";
    }).join("");
  }

  // Вложить `code` внутрь блока `outer`. Переменная, стоявшая отдельным
  // блоком, переезжает внутрь: одна переменная — одно место в разрезе.
  function nestInto(outer, code) {
    const target = blockOf(outer);
    if (!target || target.length > 1 || code === outer || !usable(code, "cols")) return;
    notice = "";
    layout.cols = layout.cols.filter(block => !(block.length === 1 && block[0] === code));
    target.push(code);
    render();
  }

  const BLOCK_ACTIONS = {
    swap(block) {
      block.reverse();
      sortBlocks();
    },
    split(block) {
      layout.cols.splice(layout.cols.indexOf(block), 1, [block[0]], [block[1]]);
      sortBlocks();
    },
    remove(block) {
      layout.cols.splice(layout.cols.indexOf(block), 1);
    },
  };

  blockList.addEventListener("click", event => {
    const button = event.target.closest("[data-block-action]");
    if (!button) return;
    const outer = button.closest(".bld-block").dataset.block;
    const action = button.dataset.blockAction;
    if (action === "nest") {
      event.stopPropagation();
      openNestPicker(outer, button);
      return;
    }
    const block = blockOf(outer);
    if (!block) return;
    notice = "";
    BLOCK_ACTIONS[action](block);
    render();
  });

  // Перетаскивание одиночного блока на другой одиночный вкладывает его внутрь.
  let draggedBlock = null;
  blockList.addEventListener("dragstart", event => {
    const item = event.target.closest(".bld-block");
    if (!item || item.classList.contains("nested")) return;
    draggedBlock = item.dataset.block;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", draggedBlock);
    item.classList.add("dragging");
  });
  blockList.addEventListener("dragend", () => {
    draggedBlock = null;
    blockList.querySelectorAll(".dragging, .drop").forEach(item => item.classList.remove("dragging", "drop"));
  });
  blockList.addEventListener("dragover", event => {
    const item = event.target.closest(".bld-block");
    const droppable = draggedBlock && item && !item.classList.contains("nested") && item.dataset.block !== draggedBlock;
    blockList.querySelectorAll(".drop").forEach(other => { if (other !== item) other.classList.remove("drop"); });
    if (!droppable) return;
    event.preventDefault();
    item.classList.add("drop");
  });
  blockList.addEventListener("drop", event => {
    const item = event.target.closest(".bld-block");
    if (!draggedBlock || !item) return;
    event.preventDefault();
    const code = draggedBlock;
    draggedBlock = null;
    nestInto(item.dataset.block, code);
  });

  /* Выбор вложенной переменной — тот же поповер, что у фильтра, с поиском:
     переменных с категориями в массиве бывают сотни. Сверху — отдельные
     блоки этой таблицы, их вкладывают чаще всего. Переменная из чужой пары
     не предлагается: забрать её значило бы молча разобрать ту пару. */

  function openNestPicker(outer, anchor) {
    closeViewMenu();
    closeReportMenu();
    pickerMode = "nest";
    pickerZone = null;
    nestOuter = outer;
    delete picker.dataset.mode;
    pickerTitle.textContent = `Вложить в «${byCode.get(outer)?.label || outer}»`;
    search.value = "";
    picker.hidden = false;
    renderPalette();
    placePopover(picker, anchor);
    params.forEach(item => item.setAttribute("aria-expanded", "false"));
    search.focus();
  }

  function renderNestPalette(query) {
    const paired = new Set(layout.cols.filter(block => block.length > 1).flat());
    const single = new Set(layout.cols.filter(block => block.length === 1).map(block => block[0]));
    const candidates = variables.filter(item => item.canCol && item.code !== nestOuter
      && !paired.has(item.code) && itemMatches(item, query));
    const groups = [
      ["Колонки этой таблицы", candidates.filter(item => single.has(item.code))],
      ["Другие переменные", candidates.filter(item => !single.has(item.code))],
    ];
    count.textContent = String(candidates.length);
    groups.forEach(([caption, items]) => {
      if (!items.length) return;
      list.append(treeNote("bld-tree-caption", caption));
      items.forEach(item => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "bld-var";
        chip.dataset.nest = item.code;
        chip.title = kindTitle(item);
        chip.innerHTML = '<span class="bld-var-name"></span>';
        chip.querySelector(".bld-var-name").textContent = `${item.display || item.code} · ${item.label}`;
        list.append(chip);
      });
    });
    if (!candidates.length) list.append(treeNote("bld-tree-empty", "Ничего не найдено."));
  }

  list.addEventListener("click", event => {
    if (pickerMode !== "nest") return;
    const chip = event.target.closest("[data-nest]");
    if (!chip) return;
    event.stopPropagation();
    const outer = nestOuter;
    closePicker();
    nestInto(outer, chip.dataset.nest);
  });

  // Фильтр таблицы — сохранённое правило проекта: условие собирается в
  // редакторе фильтра, и текст правила там же, одной строкой для всех мест.
  function renderFilterPalette(query) {
    const matched = filters.filter(item => !query || item.name.toLowerCase().includes(query));
    count.textContent = filters.length ? `${matched.length} из ${filters.length}` : "";
    const options = [{ id: null, name: "Вся выборка" }, ...matched];
    options.forEach(item => {
      const chosen = item.id ? layout.filter.includes(item.id) : !layout.filter.length;
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `bld-var${chosen ? " chosen" : ""}`;
      chip.dataset.filter = item.id || "";
      chip.setAttribute("aria-pressed", String(chosen));
      chip.innerHTML = `<span class="bld-var-name"></span><span class="bld-var-mark" aria-hidden="true"></span>`;
      chip.querySelector(".bld-var-name").textContent = item.name;
      chip.addEventListener("click", event => {
        event.stopPropagation();
        closePicker();
        if (item.id) addToZone(item.id, "filter");
        else { layout.filter = []; render(); }
      });
      list.append(chip);
    });
    if (!filters.length) {
      const empty = document.createElement("p");
      empty.className = "bld-placeholder bld-list-empty";
      empty.textContent = "Сохранённых фильтров нет. Правило собирается в разделе «Отчёты», строка «База».";
      list.append(empty);
    }
  }

  /* Полоса параметров: пилюля несёт имя свойства и его значение —
     ровно как строки свойств книги в разделе «Отчёты». Значение
     короткое: подписи вопросов длинные, и пилюля со всеми сразу
     растягивалась на пол-полосы и ломала её на два яруса. Полный
     список остаётся подсказкой пилюли. */
  function shorten(labels) {
    if (!labels.length) return "";
    // Обрезает сам код, а не многоточие CSS: иначе «+2» уезжает
    // за край пилюли вместе с хвостом длинной подписи.
    const clip = text => (text.length > 24 ? `${text.slice(0, 23)}…` : text);
    if (labels.length === 1) return clip(labels[0]);
    return `${clip(labels[0])} +${labels.length - 1}`;
  }

  /* Переключатели вместо выпадающих списков: вариантов два-четыре, и
     все видны сразу. Значение по-прежнему живёт в скрытом <select> —
     на его value и change опираются расчёт и сценарии ассистента, — а
     кнопки только отражают его и меняют. */
  function buildSegments() {
    document.querySelectorAll("#section-tables select.bld-seg-source").forEach(select => {
      const group = document.createElement("div");
      group.className = "bld-seg";
      group.setAttribute("role", "radiogroup");
      group.setAttribute("aria-labelledby", select.getAttribute("aria-labelledby"));
      group.dataset.for = select.id;
      [...select.options].forEach(option => {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "radio");
        button.dataset.value = option.value;
        button.textContent = option.textContent;
        if (option.title) button.title = option.title;
        group.append(button);
      });
      group.addEventListener("click", event => {
        const button = event.target.closest("button[data-value]");
        if (!button || select.value === button.dataset.value) return;
        select.value = button.dataset.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        syncSegments();
      });
      select.after(group);
    });
    syncSegments();
  }

  function syncSegments() {
    document.querySelectorAll("#section-tables .bld-seg").forEach(group => {
      const value = document.getElementById(group.dataset.for).value;
      group.querySelectorAll("button").forEach(button => {
        button.setAttribute("aria-checked", String(button.dataset.value === value));
      });
    });
  }

  // Пилюля «Вид» называет только отступления от отчёта: пока всё как в
  // книге, значения нет, и полоса не повторяет очевидное.
  function renderViewValue() {
    const slot = document.querySelector("#bld-view-value");
    const parts = [];
    if (measureSelect.value === "index") parts.push("индекс");
    if (sheetSelect.value === "filter") parts.push("от ответивших");
    if (boxSelect.value) parts.push(`Top/Bottom ${boxSelect.value}`);
    slot.textContent = parts.join(" · ");
    slot.hidden = !parts.length;
    syncSegments();
    scheduleSave();
  }

  function renderParams() {
    renderViewValue();
    const values = {
      rows: layout.rows.map(code => byCode.get(code)?.label).filter(Boolean),
      cols: bannerId
        ? [banners.find(item => item.id === bannerId)?.name].filter(Boolean)
        : layout.cols.map(block => block.map(code => byCode.get(code)?.label).filter(Boolean).join(" × ")),
      filter: layout.filter.map(id => filters.find(item => item.id === id)?.name).filter(Boolean),
    };
    saveCutButton.hidden = Boolean(bannerId) || !layout.cols.length;
    exportButton.hidden = !layout.rows.length;
    Object.entries(values).forEach(([zone, labels]) => {
      const slot = document.querySelector(`[data-slot="${zone}"]`);
      if (!slot) return;
      slot.textContent = shorten(labels) || ZONE_EMPTY[zone];
      const pill = slot.closest(".bld-param");
      pill.classList.toggle("off", !labels.length);
      pill.title = labels.length ? `${ZONE_TITLE[zone]}: ${labels.join(", ")}` : ZONE_TITLE[zone];
    });
  }

  // Смещения ярусов липкой шапки считаются из фактических высот: жёстко
  // прописанные пиксели разъезжаются на другом шрифте и масштабе.
  // Высота ярусов шапки меняется и после отрисовки: ширина окна, панели
  // слева, догрузка шрифта переносят подписи. Отступы, посчитанные один
  // раз, тогда расходятся, и ярусы наезжают друг на друга.
  const headObserver = new ResizeObserver(entries => {
    const table = entries[0]?.target.closest("table.bld-grid");
    if (table) stackStickyHeader(table);
  });

  function stackStickyHeader(table) {
    if (!table || !table.offsetParent) return;
    if (table.dataset.observed !== "1") {
      headObserver.disconnect();
      headObserver.observe(table.tHead);
      table.dataset.observed = "1";
    }
    let offset = 0;
    Array.from(table.tHead.rows).forEach(row => {
      Array.from(row.cells).forEach(cell => { cell.style.top = `${offset}px`; });
      offset += row.getBoundingClientRect().height;
    });
  }

  function renderEmpty(text) {
    wrap.innerHTML = `<div class="bld-empty"><p>${escapeHtml(text)}</p></div>`;
    note.textContent = "";
    lastTable = null;
  }

  async function renderGrid() {
    if (!wrap.offsetParent) {
      stale = true;
      return;
    }
    stale = false;
    closeProtocol();
    if (!projectId) {
      testsSlot.textContent = "—";
      renderEmpty("Откройте проект — таблица строится по его данным.");
      return;
    }
    if (!layout.rows.length) {
      renderEmpty("Отметьте вопросы в «Строках» слева — таблица соберётся сама.");
      return;
    }
    const token = ++requestToken;
    const request = tableRequest();
    wrap.classList.add("loading");
    if (!lastTable) wrap.innerHTML = '<div class="bld-empty"><p>Считаем…</p></div>';
    let table;
    try {
      const response = await fetch(`/api/projects/${projectId}/tables/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof payload.detail === "string" ? payload.detail : "Таблицу посчитать не удалось.");
      }
      table = payload;
    } catch (error) {
      if (token !== requestToken) return;
      wrap.classList.remove("loading");
      renderEmpty(error.message);
      return;
    }
    if (token !== requestToken) return;
    wrap.classList.remove("loading");
    renderTable(table);
  }

  // Раскладка для сервера: её же выгружает кнопка «Excel», поэтому книга
  // собирается по тому, что стоит на экране, а не по чему-то похожему.
  /* NET и Top/Bottom «на лету»: считаются сервером как настройки вопроса
     и сохраняются с таблицей, но не в вопросах проекта — книга отчёта их
     не видит. NET-группы и свой размер Top/Bottom хранятся по коду
     вопроса и правятся кнопкой на вопросе в дереве «Строки»; общий
     размер всей таблицы — в меню «Вид». */
  const liveNets = new Map();
  const liveBoxes = new Map();
  // Вопросы, у которых Top — младшие коды шкалы, а Bottom — старшие.
  const liveInverted = new Set();
  let netTarget = null;
  let questionTarget = null;
  // Top/Bottom сервер пишет под шкалой и под каждым элементом матрицы.
  const BOX_TYPES = new Set(["scale", "matrix"]);

  function overridesPayload() {
    const payload = {};
    // Настройки вопроса, убранного из строк, помнятся до его возвращения,
    // но в расчёт не идут.
    layout.rows.forEach(code => {
      const override = {};
      const nets = liveNets.get(code);
      if (nets?.length) override.nets = nets;
      if (BOX_TYPES.has(byCode.get(code)?.type)) {
        const size = liveBoxes.get(code) || Number(boxSelect.value);
        if (size) override.scale_box = size;
        if (liveInverted.has(code)) override.scale_inverted = true;
      }
      if (Object.keys(override).length) payload[code] = override;
    });
    return Object.keys(payload).length ? payload : undefined;
  }

  function netableQuestions() {
    if (!lastTable) return [];
    return lastTable.questions.filter(question =>
      question.rows.some(row => row.kind === "value" && !row.derived));
  }

  // Кнопка настроек есть у каждого вопроса строк, кроме числового: у него
  // нет ни ответов для NET, ни шкалы для Top/Bottom.
  function tunable(code) {
    const type = byCode.get(code)?.type;
    return Boolean(type) && type !== "numeric";
  }

  // Кнопка на вопросе, как пилюля «Вид», называет только отступления.
  function tuneSummary(code) {
    const parts = [];
    if (liveBoxes.get(code)) parts.push(`T${liveBoxes.get(code)}`);
    if (liveInverted.has(code)) parts.push("↕");
    const nets = liveNets.get(code)?.length || 0;
    if (nets) parts.push(`NET ${nets}`);
    return parts.join(" · ");
  }

  function syncTuneButtons() {
    trees.rows.root.querySelectorAll(".bld-node-tune").forEach(button => {
      const code = button.dataset.tune;
      const summary = tuneSummary(code);
      button.hidden = !layout.rows.includes(code) || !tunable(code);
      button.textContent = summary || "Σ";
      button.classList.toggle("on", Boolean(summary));
      button.setAttribute("aria-expanded", String(!groupsMenu.hidden && questionTarget === code));
    });
  }

  function tuneButton(code) {
    return trees.rows.root.querySelector(`.bld-node-tune[data-tune="${CSS.escape(code)}"]`);
  }

  function renderNetControl() {
    syncTuneButtons();
    renderViewValue();
    if (!groupsMenu.hidden) renderQuestionMenu();
  }

  // Окно одного вопроса: его размер Top/Bottom и его NET-группы.
  function renderQuestionMenu() {
    const code = questionTarget;
    const item = byCode.get(code);
    document.querySelector("#bld-q-code").textContent = item?.display || code;
    const name = document.querySelector("#bld-q-name");
    name.textContent = item?.label || "";
    name.title = item?.label || "";
    document.querySelector("#bld-q-box-row").hidden = !BOX_TYPES.has(item?.type);
    questionBoxSelect.value = liveBoxes.get(code) ? String(liveBoxes.get(code)) : "";
    invertButton.setAttribute("aria-pressed", String(liveInverted.has(code)));
    syncSegments();
    const question = netableQuestions().find(entry => entry.code === code);
    if (!question) {
      netList.innerHTML = `<p class="bld-net-empty">${lastTable
        ? "У вопроса нет вариантов ответа для группы."
        : "Таблица ещё считается — группы соберутся из её строк."}</p>`;
      return;
    }
    const chips = (liveNets.get(code) || []).map(net =>
      `<span class="bld-net-chip" title="${escapeHtml(net.values.join(", "))}">${escapeHtml(net.label)}` +
      `<button type="button" data-drop-net="${escapeHtml(net.label)}" aria-label="Убрать группу ${escapeHtml(net.label)}">×</button></span>`).join("");
    netList.innerHTML = `${chips ? `<div class="bld-net-chips">${chips}</div>` : ""}` +
      '<button type="button" class="bld-net-open" data-net-open>+ NET</button>';
  }

  function closeGroupsMenu() {
    groupsMenu.hidden = true;
    syncTuneButtons();
  }

  function openQuestionMenu(code) {
    const anchor = tuneButton(code);
    if (!anchor) return;
    closePicker();
    closeReportMenu();
    closeViewMenu();
    closeExportMenu();
    questionTarget = code;
    renderQuestionMenu();
    groupsMenu.hidden = false;
    syncTuneButtons();
    placePopover(groupsMenu, anchor);
  }

  trees.rows.root.addEventListener("click", event => {
    const button = event.target.closest(".bld-node-tune");
    if (!button) return;
    event.stopPropagation();
    if (!groupsMenu.hidden && questionTarget === button.dataset.tune) closeGroupsMenu();
    else openQuestionMenu(button.dataset.tune);
  });
  groupsMenu.addEventListener("click", event => {
    event.stopPropagation();
    if (event.target.closest("[data-net-open]")) {
      closeGroupsMenu();
      openNetPicker(questionTarget);
      return;
    }
    const drop = event.target.closest("[data-drop-net]");
    if (!drop) return;
    liveNets.set(questionTarget, (liveNets.get(questionTarget) || []).filter(net => net.label !== drop.dataset.dropNet));
    renderNetControl();
    void renderGrid();
  });
  invertButton.addEventListener("click", () => {
    if (liveInverted.has(questionTarget)) liveInverted.delete(questionTarget);
    else liveInverted.add(questionTarget);
    renderNetControl();
    void renderGrid();
  });
  questionBoxSelect.addEventListener("change", () => {
    if (questionBoxSelect.value) liveBoxes.set(questionTarget, Number(questionBoxSelect.value));
    else liveBoxes.delete(questionTarget);
    renderNetControl();
    void renderGrid();
  });

  // Сборка группы — в том же поповере, что фильтр: название и ответы.
  function openNetPicker(code) {
    const question = netableQuestions().find(item => item.code === code);
    const anchor = tuneButton(code);
    if (!question || !anchor) return;
    netTarget = question.code;
    const rows = question.rows.filter(row => row.kind === "value" && !row.derived);
    const taken = new Set((liveNets.get(question.code) || []).map(net => net.label));
    let name = "Топ";
    for (let index = 2; taken.has(name); index += 1) name = `Группа ${index}`;
    pickerTitle.textContent = `NET · ${byCode.get(question.code)?.display || question.code}`;
    count.textContent = "";
    list.innerHTML = `<div class="bld-net-panel">
      <p class="bld-net-question" title="${escapeHtml(question.label)}">${escapeHtml(question.label)}</p>
      <label class="bld-net-label">Название группы<input id="bld-net-label" value="${escapeHtml(name)}" maxlength="60" /></label>
      <div class="bld-net-values">${rows.map(row => `<label class="bld-net-value"><input type="checkbox" value="${escapeHtml(row.label)}" /><span>${escapeHtml(row.label)}</span></label>`).join("")}</div>
      <div class="bld-net-actions">
        <button type="button" class="bld-net-back">← Назад</button>
        <button type="button" id="bld-net-add" class="bld-net-add">Добавить группу</button>
      </div>
    </div>`;
    picker.hidden = false;
    picker.dataset.mode = "net";
    pickerMode = "net";
    pickerZone = null;
    params.forEach(item => item.setAttribute("aria-expanded", "false"));
    placePopover(picker, anchor);
    document.querySelector("#bld-net-label").select();
  }

  list.addEventListener("click", event => {
    if (picker.dataset.mode !== "net") return;
    if (event.target.closest(".bld-net-back")) {
      event.stopPropagation();
      closePicker();
      openQuestionMenu(netTarget);
      return;
    }
    if (!event.target.closest("#bld-net-add")) return;
    event.stopPropagation();
    const label = document.querySelector("#bld-net-label").value.trim();
    const values = [...document.querySelectorAll(".bld-net-values input:checked")].map(input => input.value);
    if (!label || !values.length) return;
    const nets = [...(liveNets.get(netTarget) || []).filter(net => net.label !== label), { label, values }];
    liveNets.set(netTarget, nets);
    closePicker();
    renderNetControl();
    void renderGrid();
    openQuestionMenu(netTarget);
  });

  boxSelect.addEventListener("change", () => { renderNetControl(); void renderGrid(); });

  function tableRequest() {
    const request = { questions: layout.rows, sheet: sheetSelect.value };
    if (bannerId) {
      request.banner_id = bannerId;
    } else if (layout.cols.length) {
      request.blocks = blocksOfLayout();
    }
    if (layout.filter.length) request.filter_id = layout.filter[0];
    const overrides = overridesPayload();
    if (overrides) request.overrides = overrides;
    return request;
  }

  /* Блоки разреза уходят в расчёт как есть: блок из двух переменных даёт
     полное пересечение категорий, как двухуровневый блок баннера книги. */
  function blocksOfLayout() {
    return layout.cols.map(block => {
      const chosen = block.map(code => byCode.get(code));
      return { label: chosen.map(item => item.label).join(" × "), sources: chosen.map(item => item.source) };
    });
  }

  function formatNumber(value, decimals) {
    return Number(value).toLocaleString("ru-RU", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  function cellHtml(cell, index, sheetRow) {
    const classes = ["bld-val"];
    if (index === 0) classes.push("bld-total");
    if (cell.value == null) return `<td class="${classes.join(" ")} bld-absent">–</td>`;
    if (cell.small) classes.push("bld-lowbase");
    if (cell.direction === "higher") classes.push("bld-up");
    if (cell.direction === "lower") classes.push("bld-down");
    const wave = cell.wave === "higher" ? '<span class="bld-wave" title="Выше волны сравнения">▴</span>'
      : cell.wave === "lower" ? '<span class="bld-wave" title="Ниже волны сравнения">▾</span>' : "";
    const letters = cell.higher_than?.length
      ? `<span class="bld-sig" title="Значимо выше колонок ${cell.higher_than.join(", ")}">${cell.higher_than.join("")}</span>`
      : "";
    // Индекс — та же ячейка, поделённая на Total: показатель переключается
    // без нового запроса, потому что оба числа уже пришли.
    const asIndex = measureSelect.value === "index" && cell.index != null;
    const weak = cell.higher_than_secondary?.length
      ? `<span class="bld-sig bld-sig-weak" title="Выше на втором уровне доверия: ${cell.higher_than_secondary.join(", ")}">${cell.higher_than_secondary.join("")}</span>`
      : "";
    const text = `${wave}${formatNumber(asIndex ? cell.index : cell.value, asIndex ? 0 : cell.decimals)}${letters}${weak}`;
    if (!cell.protocol) return `<td class="${classes.join(" ")}">${text}</td>`;
    return `<td class="${classes.join(" ")}"><button type="button" class="bld-cell-button" data-protocol="${sheetRow}:${index}" aria-label="Протокол теста для ячейки">${text}</button></td>`;
  }

  function renderTable(table) {
    const columns = table.columns;
    const width = columns.length + 1;
    const groups = columns.map(() => "");
    table.blocks.forEach(block => {
      for (let index = block.first; index <= block.last; index += 1) groups[index] = block.label;
    });
    let head = '<thead><tr><th class="bld-rowhead bld-grouphead"></th>';
    let cursor = 0;
    while (cursor < columns.length) {
      let span = 1;
      while (groups[cursor] && cursor + span < columns.length && groups[cursor + span] === groups[cursor]) span += 1;
      // Подпись блока — часто весь текст вопроса: в шапке две строки и
      // многоточие, полностью — в подсказке, иначе шапка съедает экран.
      const group = escapeHtml(groups[cursor]);
      head += `<th class="bld-grouphead" colspan="${span}"${group ? ` title="${group}"` : ""}>${group ? `<span class="bld-clamp">${group}</span>` : "&nbsp;"}</th>`;
      cursor += span;
    }
    head += '</tr><tr><th class="bld-rowhead bld-cathead" style="text-align:left">Показатель</th>';
    columns.forEach(column => {
      const label = escapeHtml(column.label);
      head += `<th class="bld-cathead" title="${label}"><span class="bld-letter">${column.letter}</span><span class="bld-clamp">${label}</span></th>`;
    });
    head += '</tr><tr class="bld-base"><th class="bld-rowhead">База, N</th>';
    columns.forEach(column => {
      head += `<th class="bld-basecell${column.small ? " bld-lowbase" : ""}">${formatNumber(column.base, 0)}</th>`;
    });
    head += "</tr>";
    if (columns.some(column => column.weighted_base != null)) {
      head += '<tr class="bld-base"><th class="bld-rowhead">База, взвеш.</th>';
      columns.forEach(column => { head += `<th class="bld-basecell">${formatNumber(column.weighted_base, 0)}</th>`; });
      head += "</tr>";
    }
    head += "</thead>";

    let body = "<tbody>";
    table.questions.forEach(question => {
      body += `<tr class="bld-qrow"><td class="bld-rowhead" colspan="${width}"><span class="bld-rowwrap">${escapeHtml(question.code)} · ${escapeHtml(question.label)}</span></td></tr>`;
      question.rows.forEach(row => {
        if (row.kind === "subquestion") {
          body += `<tr class="bld-subrow"><td class="bld-rowhead" colspan="${width}"><span class="bld-rowwrap">${escapeHtml(row.label)}</span></td></tr>`;
          return;
        }
        const rowClass = row.kind === "base" ? "bld-base" : row.derived ? "bld-derived" : "";
        // Длинная подпись обрезается многоточием, полная — в подсказке:
        // ячейка таблицы не держит max-width, и текст наезжал на числа.
        const label = escapeHtml(row.label);
        const head = `<span class="bld-rowlabel" title="${label}">${label}</span>`;
        body += `<tr class="${rowClass}"><td class="bld-rowhead">${head}</td>`;
        row.cells.forEach((cell, index) => { body += cellHtml(cell, index, row.sheet_row); });
        body += "</tr>";
      });
    });
    body += "</tbody>";

    wrap.innerHTML = `<table class="bld-grid bld-live">${head}${body}</table>`;
    lastTable = table;
    stackStickyHeader(wrap.querySelector("table.bld-grid"));
    renderNetControl();

    const settings = table.settings;
    const schemes = [];
    if (settings.compare_to_total) schemes.push(settings.compare_target === "total" ? "с Total" : "с остатком");
    if (settings.compare_pairwise) schemes.push("попарные");
    testsSlot.textContent = schemes.length
      ? `${schemes.join(" + ")}, ${Math.round(settings.confidence_level * 100)}%`
        + (settings.secondary_confidence_level ? ` / ${Math.round(settings.secondary_confidence_level * 100)}%` : "")
      : "не считаются";

    const parts = [`${columns.length} ${plural(columns.length, "колонка", "колонки", "колонок")}`];
    if (table.tests) parts.push(`${table.tests} ${plural(table.tests, "тест", "теста", "тестов")}`);
    if (settings.weight) parts.push(`вес: ${settings.weight}`);
    if (measureSelect.value === "index") parts.push("индекс: 100 — уровень всей выборки");
    if (columns.some(column => column.small)) parts.push(`серым — база меньше ${settings.minimum_base}`);
    if (table.empty_columns.length) parts.push(`без респондентов: ${table.empty_columns.join(", ")}`);
    if (notice) parts.push(notice);
    note.textContent = parts.join(" · ");
  }

  /* Мост в отчёт: разрез сохраняется баннером, таблица выгружается книгой.
     Имя баннера складывается из подписей переменных — переименовать его
     можно там же, где правят баннеры, в разделе «Отчёты». */
  // Тот же разрез — те же источники блоков в том же порядке. Баннер с
  // настроенными категориями уже не тот же: у него другие колонки.
  function sameCut(banner, blocks) {
    const key = items => JSON.stringify(items.map(block => block.sources.map(source =>
      source.categories && source.categories.length ? null : `${source.kind}:${source.ref}`)));
    return key(banner.blocks || []) === key(blocks);
  }

  async function saveCut() {
    const blocks = blocksOfLayout();
    if (!projectId || !blocks.length) return;
    // Повторное нажатие копило одинаковые баннеры: в демо-проекте их
    // набралось четырнадцать «Пол · Возраст». Такой разрез не сохраняем
    // второй раз, а называем уже сохранённый.
    const existing = (window.SavApp?.banners() || []).find(banner => sameCut(banner, blocks));
    if (existing) {
      notice = `Этот разрез уже сохранён баннером «${existing.name}»`;
      note.textContent = notice;
      return;
    }
    const name = blocks.map(block => block.label).join(" · ").slice(0, 500);
    saveCutButton.disabled = true;
    try {
      const response = await fetch(`/api/projects/${projectId}/banners`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, blocks }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof payload.detail === "string" ? payload.detail : "Баннер сохранить не удалось.");
      }
      // Конфигурацию проекта держит app.js — он же перечитает её и обновит
      // список баннеров в поповере.
      await window.SavApp?.refreshProject();
      notice = `Разрез сохранён баннером «${name}»`;
      note.textContent = notice;
    } catch (error) {
      note.textContent = error.message;
    } finally {
      saveCutButton.disabled = false;
    }
  }

  function closeExportMenu() {
    exportMenu.hidden = true;
    exportButton.setAttribute("aria-expanded", "false");
  }

  /* Меню «Вид» — то, что меняет вид уже посчитанного: доли и размер
     Top/Bottom всей таблицы. Свои Top/Bottom и NET вопроса — на кнопке
     вопроса в дереве «Строки». */
  function closeViewMenu() {
    viewMenu.hidden = true;
    viewButton.setAttribute("aria-expanded", "false");
  }

  viewButton.addEventListener("click", event => {
    event.stopPropagation();
    if (!viewMenu.hidden) {
      closeViewMenu();
      return;
    }
    closePicker();
    closeExportMenu();
    closeGroupsMenu();
    closeReportMenu();
    viewMenu.hidden = false;
    viewButton.setAttribute("aria-expanded", "true");
    placePopover(viewMenu, viewButton);
  });
  viewMenu.addEventListener("click", event => event.stopPropagation());

  // «Эта таблица» — вопросы строк; «Все вопросы отчёта» — полный отчёт с
  // разрезом и фильтром экрана, как выгрузка всех строк кросстаба у Qualtrics.
  async function exportTable(scope) {
    closeExportMenu();
    if (!projectId || !layout.rows.length) return;
    exportButton.disabled = true;
    try {
      const response = await fetch(`/api/projects/${projectId}/tables/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...tableRequest(), scope }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload.detail === "string" ? payload.detail : "Выгрузить не удалось.");
      }
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      const current = reports.find(item => item.id === currentReportId)?.name;
      link.download = scope === "report" ? "report_by_cut.xlsx" : `${current || "table"}.xlsx`;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } catch (error) {
      note.textContent = error.message;
    } finally {
      exportButton.disabled = false;
    }
  }

  saveCutButton.addEventListener("click", () => { void saveCut(); });
  exportButton.addEventListener("click", event => {
    event.stopPropagation();
    const open = exportMenu.hidden;
    exportMenu.hidden = !open;
    exportButton.setAttribute("aria-expanded", String(open));
  });
  exportMenu.addEventListener("click", event => {
    const item = event.target.closest("[data-export-scope]");
    if (!item) return;
    event.stopPropagation();
    void exportTable(item.dataset.exportScope);
  });
  document.addEventListener("click", event => {
    if (!event.target.closest(".bld-export-wrap")) closeExportMenu();
    if (!event.target.closest("#bld-view-menu") && !event.target.closest("#bld-view")) closeViewMenu();
    if (!event.target.closest("#bld-groups-menu") && !event.target.closest(".bld-node-tune")) closeGroupsMenu();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    closeExportMenu();
    closeViewMenu();
    closeGroupsMenu();
  });

  /* Протокол теста по щелчку на ячейке — тот же текст, что примечание
     ячейки в книге и запись в statistics.txt (PQ.3, «лучше Qualtrics»). */
  const protocolBox = document.createElement("div");
  protocolBox.className = "bld-protocol";
  protocolBox.hidden = true;
  protocolBox.setAttribute("role", "dialog");
  protocolBox.setAttribute("aria-label", "Протокол теста");
  document.querySelector("#section-tables").append(protocolBox);

  function closeProtocol() {
    protocolBox.hidden = true;
  }

  wrap.addEventListener("click", event => {
    const button = event.target.closest("[data-protocol]");
    if (!button || !lastTable) return;
    event.stopPropagation();
    const [sheetRow, index] = button.dataset.protocol.split(":").map(Number);
    const row = lastTable.questions.flatMap(question => question.rows).find(item => item.sheet_row === sheetRow);
    const column = lastTable.columns[index];
    protocolBox.innerHTML =
      `<div class="bld-protocol-head"><strong></strong><button type="button" class="bld-protocol-close" aria-label="Закрыть протокол">×</button></div>` +
      `<pre></pre><p class="bld-dim">Тот же текст — в примечании ячейки книги и в statistics.txt.</p>`;
    protocolBox.querySelector("strong").textContent = `${row.label} · ${column.letter} — ${column.label}`;
    protocolBox.querySelector("pre").textContent = row.cells[index].protocol;
    protocolBox.querySelector(".bld-protocol-close").addEventListener("click", closeProtocol);
    protocolBox.hidden = false;
    const box = button.getBoundingClientRect();
    const boxWidth = protocolBox.offsetWidth;
    const boxHeight = protocolBox.offsetHeight;
    protocolBox.style.left = `${Math.round(Math.max(12, Math.min(box.left, window.innerWidth - boxWidth - 12)))}px`;
    protocolBox.style.top = `${Math.round(Math.max(12, Math.min(box.bottom + 6, window.innerHeight - boxHeight - 12)))}px`;
    protocolBox.querySelector(".bld-protocol-close").focus();
  });

  // Своя копия склонения: shell.js и app.js — разные бандлы без общего
  // модуля, а «1 колонок» в подписи под таблицей видно сразу.
  function plural(count, one, few, many) {
    const tens = Math.abs(count) % 100;
    const units = count % 10;
    if (tens > 10 && tens < 20) return many;
    if (units === 1) return one;
    if (units >= 2 && units <= 4) return few;
    return many;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]
    ));
  }

  function render() {
    renderParams();
    renderBlocks();
    // Список нужен только в открытом поповере: при открытии он и так
    // собирается заново. Пересборка скрытого списка на массиве в 3 000
    // переменных стоила до секунды на каждую правку проекта.
    if (!picker.hidden) renderPalette();
    syncTrees();
    renderGrid();
    scheduleSave();
  }

  // Экран показан — только теперь у шапки таблицы есть настоящие высоты.
  function activate() {
    if (treesStale) renderTrees();
    if (stale) {
      void renderGrid();
      return;
    }
    stackStickyHeader(wrap.querySelector("table.bld-grid"));
  }

  // Пилюля «Фильтр» открывает список сохранённых правил проекта.
  params.forEach(zone => {
    zone.addEventListener("click", () => openPicker(zone.dataset.zone, zone));
  });

  document.addEventListener("click", event => {
    if (!event.target.closest(".bld-protocol")) closeProtocol();
    if (event.target.closest("#bld-picker") || event.target.closest(".bld-param[data-zone]")) return;
    closePicker();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    closePicker();
    closeProtocol();
  });

  search.addEventListener("input", renderPalette);
  sheetSelect.addEventListener("change", () => { renderViewValue(); void renderGrid(); });
  // Показатель меняет только вид уже посчитанного.
  measureSelect.addEventListener("change", () => { renderViewValue(); if (lastTable) renderTable(lastTable); });
  window.addEventListener("resize", activate);

  /* Ассистент. Заранее записанные сценарии: каждый меняет раскладку,
     потому что смысл экрана именно в этом, а не в тексте ответа. */
  function pushMessage(role, html, did) {
    const message = document.createElement("div");
    message.className = `bld-msg ${role}`;
    message.innerHTML =
      `<span class="bld-who">${role === "user" ? "Вы" : "Ассистент"}</span>` +
      `<div class="bld-bubble">${html}</div>` +
      (did ? `<div class="bld-did">Изменено: ${escapeHtml(did)}</div>` : "");
    log.append(message);
    log.classList.add("has-messages");
    log.scrollTop = log.scrollHeight;
  }

  function firstOfType(...types) {
    return variables.find(item => types.includes(item.type) && item.canRow);
  }

  const SCENARIOS = [
    {
      match: /индекс/i,
      run: () => {
        measureSelect.value = "index";
        renderViewValue();
        if (lastTable) renderTable(lastTable);
        return { reply: "Переключил показатель на индекс к Total: 100 — уровень всей выборки.", did: "Показатель: индекс" };
      },
    },
    {
      match: /ответивш/i,
      run: () => {
        sheetSelect.value = "filter";
        renderViewValue();
        void renderGrid();
        return { reply: "Доли теперь считаются от ответивших на вопрос, как на листе topline_filter.", did: "Доли: от ответивших" };
      },
    },
    {
      match: /разрез|колонк|разбей/i,
      run: () => {
        const target = variables.find(item => item.canCol && !colCodes().includes(item.code)
          && item.code !== layout.rows[0]);
        if (!target) return { reply: "Не нашёл подходящую переменную с категориями.", did: null };
        addToZone(target.code, "cols");
        return { reply: `Добавил <code>${escapeHtml(target.code)}</code> в колонки.`, did: `Колонки: ${target.label}` };
      },
    },
    {
      match: /строк|покажи|посчитай/i,
      run: () => {
        const target = firstOfType("single_choice", "multiple_choice_dichotomy", "scale");
        if (!target) return { reply: "В проекте нет вопроса с категориями.", did: null };
        layout.rows = [target.code];
        render();
        return { reply: `Собрал <code>${escapeHtml(target.code)}</code> по строкам.`, did: `Строки: ${target.label}` };
      },
    },
    {
      match: /очист|сброс|заново/i,
      run: () => {
        layout.rows = []; layout.cols = []; layout.filter = [];
        bannerId = null;
        render();
        return { reply: "Очистил стол.", did: "Раскладка сброшена" };
      },
    },
  ];

  /* Ассистент — круглая кнопка в углу и окно над ней: пока это заглушка,
     даже свёрнутая полоса под таблицей отнимала у неё строку. */
  function setAssistantOpen(open) {
    assistantBody.hidden = !open;
    assistantToggle.setAttribute("aria-expanded", String(open));
  }

  assistantToggle.addEventListener("click", () => {
    setAssistantOpen(assistantBody.hidden);
    if (assistantBody.hidden) return;
    input.focus();
    if (assistantEnabled === null || assistantProject !== projectId) void loadAssistant();
  });
  document.querySelector("#bld-assistant-close").addEventListener("click", () => {
    setAssistantOpen(false);
    assistantToggle.focus();
  });
  assistantBody.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    setAssistantOpen(false);
    assistantToggle.focus();
  });

  /* Настоящий ассистент (docs/assistant.md). Модель только предлагает план,
     карточка показывает описание шагов, собранное сервером, а применяет,
     отклоняет и откатывает план пользователь — модель в этом не участвует.
     Пока провайдер не настроен, работает прежняя заглушка. */
  const assistantMark = document.querySelector(".bld-assistant-head em");
  let assistantEnabled = null;
  let assistantProject = null;
  let assistantBusy = false;
  const planCards = new Map();

  function assistantBase() {
    return `/api/projects/${projectId}/assistant`;
  }

  function textHtml(text) {
    return escapeHtml(text || "").replace(/\n/g, "<br>");
  }

  async function loadAssistant() {
    if (!projectId) {
      assistantEnabled = false;
      return;
    }
    const requested = projectId;
    let state;
    try {
      state = await api(assistantBase());
    } catch {
      assistantEnabled = false;
      return;
    }
    if (requested !== projectId) return;
    assistantProject = requested;
    assistantEnabled = state.enabled;
    if (!state.enabled) return;
    assistantMark.textContent = "бета";
    assistantMark.title = "Подписи вопросов отправляются провайдеру модели, ответы " +
      "респондентов — нет. Изменения вносятся только кнопкой «Применить».";
    log.replaceChildren();
    log.classList.remove("has-messages");
    planCards.clear();
    const plans = new Map(state.plans.map(plan => [plan.id, plan]));
    if (!state.messages.length) {
      pushMessage("ai", "Опишите, какую таблицу собрать: например, «сделай кросс Q5 по возрасту». " +
        "Я предложу план, а изменения внесёте вы кнопкой «Применить». Подписи вопросов " +
        "уходят провайдеру модели, ответы респондентов — нет.", null);
    }
    state.messages.forEach(message => {
      if (message.role === "event") {
        pushNote(message.text);
        return;
      }
      pushMessage(message.role === "user" ? "user" : "ai", textHtml(message.text), null);
      if (message.plan_id && plans.has(message.plan_id)) showPlan(plans.get(message.plan_id));
    });
  }

  function pushNote(text) {
    const note = document.createElement("div");
    note.className = "bld-note";
    note.textContent = text;
    log.append(note);
    log.classList.add("has-messages");
    log.scrollTop = log.scrollHeight;
  }

  const PLAN_STATUS = {
    pending: "Ждёт решения",
    applied: "Применено",
    declined: "Отклонено",
    reverted: "Откачено",
    superseded: "Заменён новым планом",
    failed: "Не применился",
  };

  function showPlan(plan) {
    let card = planCards.get(plan.id);
    if (!card) {
      card = document.createElement("div");
      card.className = "bld-plan";
      planCards.set(plan.id, card);
      log.append(card);
      log.classList.add("has-messages");
    }
    const warnings = (plan.warnings || [])
      .map(text => `<li class="warn">${escapeHtml(text)}</li>`).join("");
    const steps = (plan.description || []).map(text => `<li>${escapeHtml(text)}</li>`).join("");
    const buttons = plan.status === "pending"
      ? '<button type="button" data-plan-action="apply">Применить</button>' +
        '<button type="button" class="secondary" data-plan-action="decline">Не надо</button>'
      : plan.status === "applied"
        ? '<button type="button" class="secondary" data-plan-action="revert">Откатить</button>'
        : "";
    card.dataset.status = plan.status;
    card.innerHTML =
      `<div class="bld-plan-head"><span>План</span><em>${PLAN_STATUS[plan.status] || plan.status}</em></div>` +
      `<ol>${steps}</ol>` +
      (warnings ? `<ul>${warnings}</ul>` : "") +
      (plan.error ? `<p class="bld-plan-error">${escapeHtml(plan.error)}</p>` : "") +
      (buttons ? `<div class="bld-plan-actions">${buttons}</div>` : "");
    card.querySelectorAll("[data-plan-action]").forEach(button => {
      button.addEventListener("click", () => { void planAction(plan, button.dataset.planAction); });
    });
    log.scrollTop = log.scrollHeight;
  }

  async function planAction(plan, action) {
    const card = planCards.get(plan.id);
    card?.querySelectorAll("button").forEach(button => { button.disabled = true; });
    const url = `${assistantBase()}/plans/${plan.id}/${action}`;
    try {
      // Раскладка, ждущая записи, должна лечь раньше плана: иначе ревизия устареет.
      await flushSave();
      let result;
      try {
        result = await api(url, { method: "POST" });
      } catch (error) {
        if (action !== "revert" || error.code !== "ASSISTANT_REVERT_CONFLICT" || !confirm(error.message)) {
          throw error;
        }
        result = await api(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cascade: true }),
        });
      }
      showPlan(result.plan);
      if (result.project) await window.SavApp.refreshProject();
    } catch (error) {
      pushNote(error.message);
      showPlan(plan);
    }
  }

  async function askModel(text) {
    if (assistantBusy) return;
    assistantBusy = true;
    const waiting = document.createElement("div");
    waiting.className = "bld-note";
    waiting.textContent = "Ассистент думает…";
    log.append(waiting);
    log.scrollTop = log.scrollHeight;
    try {
      await flushSave();
      const result = await api(`${assistantBase()}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, table_id: currentReportId }),
      });
      waiting.remove();
      pushMessage("ai", textHtml(result.reply), null);
      if (result.plan) showPlan(result.plan);
    } catch (error) {
      waiting.remove();
      pushNote(error.message);
    } finally {
      assistantBusy = false;
    }
  }

  async function ask(text) {
    if (!text.trim()) return;
    setAssistantOpen(true);
    if (assistantEnabled === null || assistantProject !== projectId) await loadAssistant();
    pushMessage("user", escapeHtml(text), null);
    if (assistantEnabled) await askModel(text.trim());
    else askStub(text);
  }

  function askStub(text) {
    const scenario = SCENARIOS.find(item => item.match.test(text));
    window.setTimeout(() => {
      if (!scenario) {
        pushMessage("ai", "Ассистент ещё не подключён. В заглушке работают: «разбей по…», " +
          "«покажи…», «доли от ответивших», «переключи на индекс», «очисти стол».", null);
        return;
      }
      const result = scenario.run();
      pushMessage("ai", result.reply, result.did);
    }, 320);
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    void ask(input.value);
    input.value = "";
  });
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  const suggestBox = document.querySelector("#bld-suggest");
  ["Разбей по другой переменной", "Покажи первый вопрос", "Переключи на индекс", "Очисти стол"].forEach(text => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", () => { void ask(text); });
    suggestBox.append(button);
  });

  pushMessage("ai", "Здесь можно будет попросить любой расчёт словами. " +
    "Пока ассистент — заглушка: он только меняет раскладку, а таблицу считает то же ядро, что книгу Excel.", null);

  buildSegments();
  render();
  return { setVariables, activate };
})();
