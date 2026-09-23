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
  const groupsButton = document.querySelector("#bld-groups");
  const groupsMenu = document.querySelector("#bld-groups-menu");
  const netList = document.querySelector("#bld-net-list");
  const nestToggle = document.querySelector("#bld-nest");
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
  // Разрез задаётся либо сохранённым баннером, либо переменными в колонках.
  let banners = [];
  let bannerId = null;
  // Вложенный разрез — полное пересечение пар, как блок баннера в книге.
  let nested = false;
  const layout = { rows: [], cols: [], filter: [] };

  function setVariables(next, context = {}) {
    variables = next;
    byCode = new Map(next.map(item => [item.code, item]));
    projectId = context.projectId || null;
    filters = context.filters || [];
    banners = context.banners || [];
    if (bannerId && !banners.some(item => item.id === bannerId)) bannerId = null;
    layout.rows = layout.rows.filter(code => byCode.get(code)?.canRow);
    layout.cols = layout.cols.filter(code => byCode.get(code)?.canCol);
    layout.filter = layout.filter.filter(id => filters.some(item => item.id === id));
    treesStale = true;
    render();
  }

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
    if (zone === "cols") bannerId = null;
    const order = new Map(variables.map((item, index) => [item.code, index]));
    layout[zone] = [...layout[zone].filter(item => item !== code), code]
      .sort((left, right) => order.get(left) - order.get(right));
    render();
  }

  function removeFromZone(code, zone) {
    notice = "";
    layout[zone] = layout[zone].filter(item => item !== code);
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
    picker.hidden = true;
    delete picker.dataset.mode;
    params.forEach(item => item.setAttribute("aria-expanded", "false"));
  }

  function renderPalette() {
    // Пока в поповере собирают NET, список фильтров его не затирает.
    if (pickerMode === "net") return;
    list.innerHTML = "";
    renderFilterPalette(search.value.trim().toLowerCase());
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
      "</div>";
    const input = node.querySelector(".bld-check");
    input.checked = checked;
    if (code) input.dataset.code = code;
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
        checked: !bannerId && layout.cols.includes(item.code), title: kindTitle(item),
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
    trees.cols.root.querySelectorAll(".bld-check").forEach(input => {
      input.checked = input.dataset.banner
        ? bannerId === input.dataset.banner
        : !bannerId && layout.cols.includes(input.dataset.code);
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
  function shorten(labels, joiner) {
    if (!labels.length) return "";
    // Обрезает сам код, а не многоточие CSS: иначе «+2» уезжает
    // за край пилюли вместе с хвостом длинной подписи.
    const clip = text => (text.length > 24 ? `${text.slice(0, 23)}…` : text);
    if (joiner === " × ") return labels.map(clip).join(" × ");
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
    slot.textContent = parts.join(" · ");
    slot.hidden = !parts.length;
    syncSegments();
  }

  function renderParams() {
    renderViewValue();
    const values = {
      rows: layout.rows.map(code => byCode.get(code)?.label).filter(Boolean),
      cols: bannerId
        ? [banners.find(item => item.id === bannerId)?.name].filter(Boolean)
        : layout.cols.map(code => byCode.get(code)?.label).filter(Boolean),
      filter: layout.filter.map(id => filters.find(item => item.id === id)?.name).filter(Boolean),
    };
    saveCutButton.hidden = Boolean(bannerId) || !layout.cols.length;
    exportButton.hidden = !layout.rows.length;
    nestToggle.hidden = Boolean(bannerId) || layout.cols.length < 2;
    nestToggle.setAttribute("aria-pressed", String(nested));
    Object.entries(values).forEach(([zone, labels]) => {
      const slot = document.querySelector(`[data-slot="${zone}"]`);
      if (!slot) return;
      const joiner = zone === "cols" && nested ? " × " : ", ";
      slot.textContent = shorten(labels, joiner) || ZONE_EMPTY[zone];
      const pill = slot.closest(".bld-param");
      pill.classList.toggle("off", !labels.length);
      pill.title = labels.length ? `${ZONE_TITLE[zone]}: ${labels.join(joiner)}` : ZONE_TITLE[zone];
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
  /* NET и Top/Bottom «на лету»: считаются сервером как настройки вопроса,
     но в проект не сохраняются — экран показывает «что было бы».
     Набор NET хранится по коду вопроса, пока открыт раздел. */
  const liveNets = new Map();
  let netTarget = null;

  function overridesPayload() {
    const payload = {};
    // Группы вопроса, убранного из строк, помнятся до его возвращения,
    // но в расчёт не идут.
    liveNets.forEach((nets, code) => {
      if (nets.length && layout.rows.includes(code)) payload[code] = { nets };
    });
    // Размер Top/Bottom — настройка отчёта: сервер применяет его ко всем
    // шкалам таблицы, поэтому передаётся с любым вопросом строк.
    if (boxSelect.value && layout.rows.length) {
      const code = layout.rows[0];
      payload[code] = { ...(payload[code] || {}), scale_box: Number(boxSelect.value) };
    }
    return Object.keys(payload).length ? payload : undefined;
  }

  function netableQuestions() {
    if (!lastTable) return [];
    return lastTable.questions.filter(question =>
      question.rows.some(row => row.kind === "value" && !row.derived));
  }

  function netCount() {
    return layout.rows.reduce((sum, code) => sum + (liveNets.get(code)?.length || 0), 0);
  }

  /* Пилюля «Группировки», как «Вид», называет только отступления от
     отчёта: число NET-групп и размер Top/Bottom. */
  function renderNetControl() {
    const parts = [];
    const total = netCount();
    if (total) parts.push(`NET ${total}`);
    if (boxSelect.value) parts.push(`Top/Bottom ${boxSelect.value}`);
    const slot = document.querySelector("#bld-groups-value");
    slot.textContent = parts.join(" · ");
    slot.hidden = !parts.length;
    syncSegments();
    if (!groupsMenu.hidden) renderNetList();
  }

  // Список вопросов таблицы: у каждого свои группы и своя кнопка «+ NET».
  function renderNetList() {
    const questions = netableQuestions();
    if (!questions.length) {
      netList.innerHTML = `<p class="bld-net-empty">${lastTable
        ? "В таблице нет вопросов с вариантами ответа."
        : "Отметьте вопросы в «Строках» — группы собираются из их ответов."}</p>`;
      return;
    }
    netList.innerHTML = questions.map(question => {
      const nets = liveNets.get(question.code) || [];
      const chips = nets.map(net =>
        `<span class="bld-net-chip" title="${escapeHtml(net.values.join(", "))}">${escapeHtml(net.label)}` +
        `<button type="button" data-drop-net="${escapeHtml(net.label)}" data-net-question="${escapeHtml(question.code)}" aria-label="Убрать группу ${escapeHtml(net.label)}">×</button></span>`).join("");
      return `<div class="bld-net-q">
        <div class="bld-net-q-head">
          <span class="bld-net-q-code">${escapeHtml(question.code)}</span>
          <span class="bld-net-q-name" title="${escapeHtml(question.label)}">${escapeHtml(question.label)}</span>
          <button type="button" class="bld-net-open" data-net-open="${escapeHtml(question.code)}">+ NET</button>
        </div>
        ${chips ? `<div class="bld-net-chips">${chips}</div>` : ""}
      </div>`;
    }).join("");
  }

  function closeGroupsMenu() {
    groupsMenu.hidden = true;
    groupsButton.setAttribute("aria-expanded", "false");
  }

  function openGroupsMenu() {
    closePicker();
    closeViewMenu();
    closeExportMenu();
    renderNetList();
    groupsMenu.hidden = false;
    groupsButton.setAttribute("aria-expanded", "true");
    placePopover(groupsMenu, groupsButton);
  }

  groupsButton.addEventListener("click", event => {
    event.stopPropagation();
    if (!groupsMenu.hidden) closeGroupsMenu();
    else openGroupsMenu();
  });
  groupsMenu.addEventListener("click", event => {
    event.stopPropagation();
    const open = event.target.closest("[data-net-open]");
    if (open) {
      closeGroupsMenu();
      openNetPicker(open.dataset.netOpen);
      return;
    }
    const drop = event.target.closest("[data-drop-net]");
    if (!drop) return;
    const code = drop.dataset.netQuestion;
    liveNets.set(code, (liveNets.get(code) || []).filter(net => net.label !== drop.dataset.dropNet));
    renderNetControl();
    void renderGrid();
  });

  // Сборка группы — в том же поповере, что фильтр: название и ответы.
  function openNetPicker(code) {
    const question = netableQuestions().find(item => item.code === code);
    if (!question) return;
    netTarget = question.code;
    const rows = question.rows.filter(row => row.kind === "value" && !row.derived);
    const taken = new Set((liveNets.get(question.code) || []).map(net => net.label));
    let name = "Топ";
    for (let index = 2; taken.has(name); index += 1) name = `Группа ${index}`;
    pickerTitle.textContent = `NET · ${question.code}`;
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
    placePopover(picker, groupsButton);
    document.querySelector("#bld-net-label").select();
  }

  list.addEventListener("click", event => {
    if (picker.dataset.mode !== "net") return;
    if (event.target.closest(".bld-net-back")) {
      event.stopPropagation();
      closePicker();
      openGroupsMenu();
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
    openGroupsMenu();
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

  /* Блоки разреза: рядом — по блоку на переменную, вложенно — парами,
     и пара даёт полное пересечение категорий, как в баннере книги. */
  function blocksOfLayout() {
    const chosen = layout.cols.map(code => byCode.get(code));
    if (!nested) {
      return chosen.map(item => ({ label: item.label, sources: [item.source] }));
    }
    const blocks = [];
    for (let index = 0; index < chosen.length; index += 2) {
      const pair = chosen.slice(index, index + 2);
      blocks.push({ label: pair.map(item => item.label).join(" × "), sources: pair.map(item => item.source) });
    }
    return blocks;
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

  /* График строки: столбцы по колонкам разреза. Рисуется по уже пришедшим
     числам таблицы — своего расчёта у графика нет, поэтому он не может
     разойтись с ней и с книгой. */
  const chartBox = document.querySelector("#bld-chart");
  const chartBody = document.querySelector("#bld-chart-body");
  const chartTitle = document.querySelector("#bld-chart-title");
  let chartRow = null;

  function renderChart() {
    if (chartRow == null || !lastTable) {
      chartBox.hidden = true;
      return;
    }
    const row = lastTable.questions.flatMap(question => question.rows)
      .find(item => item.sheet_row === chartRow);
    if (!row) {
      chartBox.hidden = true;
      return;
    }
    const asIndex = measureSelect.value === "index";
    const values = row.cells.map(cell => (asIndex ? cell.index : cell.value));
    const highest = Math.max(1, ...values.filter(value => value != null).map(Math.abs));
    const step = 26;
    const height = row.cells.length * step + 10;
    const labelWidth = 190;
    const chartWidth = 760;
    const bars = row.cells.map((cell, index) => {
      const column = lastTable.columns[index];
      const value = values[index];
      const width = value == null ? 0 : Math.abs(value) / highest * (chartWidth - labelWidth - 70);
      const y = index * step + 6;
      const tone = cell.direction === "higher" ? "bld-chart-up"
        : cell.direction === "lower" ? "bld-chart-down"
        : cell.small ? "bld-chart-faint" : "bld-chart-bar";
      const text = value == null ? "–" : formatNumber(value, asIndex ? 0 : cell.decimals);
      const label = `${column.letter}. ${column.label}`;
      return `<g>
        <title>${escapeHtml(label)}: ${escapeHtml(text)}${cell.small ? " · малая база" : ""}</title>
        <text x="0" y="${y + 13}" class="bld-chart-label">${escapeHtml(label.length > 28 ? `${label.slice(0, 27)}…` : label)}</text>
        <rect x="${labelWidth}" y="${y}" width="${width.toFixed(1)}" height="16" rx="3" class="${tone}"></rect>
        <text x="${labelWidth + width + 6}" y="${y + 13}" class="bld-chart-value">${escapeHtml(text)}</text>
      </g>`;
    }).join("");
    chartTitle.textContent = `${row.label}${asIndex ? " · индекс к Total" : ""}`;
    chartBody.innerHTML = `<svg viewBox="0 0 ${chartWidth} ${height}" role="img" aria-label="График строки ${escapeHtml(row.label)}">${bars}</svg>`;
    chartBox.hidden = false;
  }

  wrap.addEventListener("click", event => {
    const button = event.target.closest("[data-chart-row]");
    if (!button) return;
    const sheetRow = Number(button.dataset.chartRow);
    chartRow = chartRow === sheetRow ? null : sheetRow;
    renderChart();
  });
  document.querySelector("#bld-chart-close").addEventListener("click", () => {
    chartRow = null;
    renderChart();
  });

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
        // Название строки — кнопка графика: те же числа, что в строке.
        // Длинная подпись обрезается многоточием, полная — в подсказке:
        // ячейка таблицы не держит max-width, и текст наезжал на числа.
        const label = escapeHtml(row.label);
        const head = row.kind === "value"
          ? `<button type="button" class="bld-rowchart bld-rowlabel" data-chart-row="${row.sheet_row}" title="${label} — показать график строки">${label}</button>`
          : `<span class="bld-rowlabel" title="${label}">${label}</span>`;
        body += `<tr class="${rowClass}"><td class="bld-rowhead">${head}</td>`;
        row.cells.forEach((cell, index) => { body += cellHtml(cell, index, row.sheet_row); });
        body += "</tr>";
      });
    });
    body += "</tbody>";

    wrap.innerHTML = `<table class="bld-grid bld-live">${head}${body}</table>`;
    lastTable = table;
    stackStickyHeader(wrap.querySelector("table.bld-grid"));
    renderChart();
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

  /* Меню «Вид» — то, что меняет вид уже посчитанного: доли, размер
     Top/Bottom и NET. В полосе они стояли отдельными пилюлями, и
     полоса из-за них не держалась в одну строку. */
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
      link.download = scope === "report" ? "report_by_cut.xlsx" : "table.xlsx";
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
    if (!event.target.closest("#bld-groups-menu") && !event.target.closest("#bld-groups")) closeGroupsMenu();
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
    // Список нужен только в открытом поповере: при открытии он и так
    // собирается заново. Пересборка скрытого списка на массиве в 3 000
    // переменных стоила до секунды на каждую правку проекта.
    if (!picker.hidden) renderPalette();
    syncTrees();
    renderGrid();
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
  nestToggle.addEventListener("click", () => {
    nested = !nested;
    notice = "";
    render();
  });
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
        const target = variables.find(item => item.canCol && !layout.cols.includes(item.code)
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
        nested = false;
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
    if (!assistantBody.hidden) input.focus();
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

  function ask(text) {
    if (!text.trim()) return;
    setAssistantOpen(true);
    pushMessage("user", escapeHtml(text), null);
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
    ask(input.value);
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
    button.addEventListener("click", () => ask(text));
    suggestBox.append(button);
  });

  pushMessage("ai", "Здесь можно будет попросить любой расчёт словами. " +
    "Пока ассистент — заглушка: он только меняет раскладку, а таблицу считает то же ядро, что книгу Excel.", null);

  buildSegments();
  render();
  return { setVariables, activate };
})();
