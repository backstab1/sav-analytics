/* Оболочка экранов.
 *
 * Отвечает за две вещи и больше ни за что: какой экран показан и что в
 * этот момент лежит во втором ярусе шапки. Всё, что относится к работе с
 * проектом, остаётся в app.js — оболочка только вызывается оттуда через
 * window.Shell.
 *
 * Добавить четвёртый экран — значит дописать запись в SCREENS и секцию
 * с таким же id в index.html. Править шапку при этом не нужно.
 */
(() => {
  "use strict";

  // Конструктор перестал быть экраном: это раздел «Таблицы» рабочей области,
  // его показывает app.js, а оболочка только активирует по вызову.
  const SCREENS = { home: {}, manual: {} };

  const nav = document.querySelector("#screen-nav");
  // Кнопка «Новый проект» стоит в том же ряду, но экраном не является,
  // поэтому выбирается только по data-screen.
  const navButtons = Array.from(nav.querySelectorAll("button[data-screen]"));

  function showScreen(name) {
    if (!SCREENS[name]) return;
    navButtons.forEach(button => {
      if (button.dataset.screen === name) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    Object.keys(SCREENS).forEach(key => {
      document.querySelector(`#screen-${key}`).hidden = key !== name;
    });
    window.scrollTo(0, 0);
    // Адрес экрана пишет app.js: у проекта и раздела он свой.
    document.dispatchEvent(new CustomEvent("shell:screen", { detail: name }));
  }

  navButtons.forEach(button => {
    button.addEventListener("click", () => showScreen(button.dataset.screen));
  });

  /* «Новый проект» стоит в том же ряду и сбрасывает проект силами app.js, но
     сам по себе экрана не меняет: нажатие с лендинга или из конструктора
     выглядело бы как «ничего не произошло». Переводим на ручной режим. */
  document.querySelector("#new-project").addEventListener("click", () => showScreen("manual"));

  /* Техническое меню в углу шапки панели: выгрузки и перераспознавание.
     Держим здесь, а не в app.js, потому что это поведение оболочки, а не
     работы с проектом; app.js по-прежнему слушает сами пункты по их id. */
  const exportToggle = document.querySelector("#export-toggle");
  const exportList = document.querySelector("#export-list");

  function closeExportMenu() {
    if (!exportList || exportList.hidden) return;
    exportList.hidden = true;
    exportToggle.setAttribute("aria-expanded", "false");
  }

  function toggleExportMenu() {
    const open = exportList.hidden;
    exportList.hidden = !open;
    exportToggle.setAttribute("aria-expanded", String(open));
    if (open) exportList.querySelector("[role=menuitem]").focus();
  }

  if (exportToggle) {
    exportToggle.addEventListener("click", event => {
      event.stopPropagation();
      toggleExportMenu();
    });
    // Пункты закрывают меню сами: скачивание уже началось, держать его открытым
    // незачем, а «Перераспознать» показывает confirm поверх.
    exportList.addEventListener("click", event => {
      if (event.target.closest("[role=menuitem]")) closeExportMenu();
    });
    document.addEventListener("click", event => {
      if (!event.target.closest(".export-menu")) closeExportMenu();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") closeExportMenu();
    });
  }

  document.querySelectorAll(".lp-demo").forEach(button => {
    button.addEventListener("click", () => {
      window.alert("Форма заявки ещё не подключена.");
    });
  });

  /* ================= Раздел «Таблицы» =================
   * Раскладка — строки, разрез, фильтр — уходит на сервер, а таблица
   * приходит посчитанной тем же кодом, что пишет книгу Excel
   * (`core/reporting/live.py`). Своих формул у экрана нет: число здесь
   * равно числу в выгрузке, и цвет значимости, стрелка волны и число
   * знаков тоже берутся из книги.
   */
  const builder = (() => {
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
    const netButton = document.querySelector("#bld-net");
    const nestToggle = document.querySelector("#bld-nest");
    const saveCutButton = document.querySelector("#bld-save-cut");
    const exportButton = document.querySelector("#bld-export");
    const exportMenu = document.querySelector("#bld-export-menu");
    const testsSlot = document.querySelector("#bld-tests");
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
      layout[zone] = layout[zone].filter(item => item !== code);
      layout[zone].push(code);
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
    let pickerZone = null;

    function openPicker(zone, anchor) {
      if (pickerZone === zone) {
        closePicker();
        return;
      }
      pickerZone = zone;
      pickerTitle.textContent = ZONE_TITLE[zone];
      search.value = "";
      picker.hidden = false;
      renderPalette();
      // Поповер раскрывается вверх: полоса параметров стоит внизу экрана.
      const box = anchor.getBoundingClientRect();
      const width = picker.offsetWidth;
      picker.style.left = `${Math.round(Math.max(12, Math.min(box.left, window.innerWidth - width - 12)))}px`;
      picker.style.bottom = `${Math.round(window.innerHeight - box.top + 8)}px`;
      params.forEach(item => item.setAttribute("aria-expanded", String(item.dataset.zone === zone)));
      search.focus();
    }

    function closePicker() {
      if (!pickerZone) return;
      pickerZone = null;
      picker.hidden = true;
      params.forEach(item => item.setAttribute("aria-expanded", "false"));
    }

    function renderPalette() {
      const zone = pickerZone || "rows";
      const query = search.value.trim().toLowerCase();
      list.innerHTML = "";
      if (zone === "filter") {
        renderFilterPalette(query);
        return;
      }
      if (zone === "cols") renderBannerOptions(query);
      const matched = variables.filter(item =>
        !query || (item.display || item.code).toLowerCase().includes(query) || item.label.toLowerCase().includes(query));
      count.textContent = variables.length ? `${matched.length} из ${variables.length}` : "";

      if (!variables.length) {
        const empty = document.createElement("p");
        empty.className = "bld-placeholder bld-list-empty";
        empty.textContent = "Откройте проект — переменные появятся здесь.";
        list.append(empty);
        return;
      }

      matched.forEach(item => {
        const flat = !usable(item.code, zone);
        const chosen = layout[zone].includes(item.code);
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `bld-var${flat ? " bld-var-flat" : ""}${chosen ? " chosen" : ""}`;
        chip.disabled = flat;
        chip.draggable = !flat;
        chip.dataset.code = item.code;
        chip.title = flat
          ? `${item.label} — ${zone === "rows" ? "такой вопрос лист книги не раскладывает" : "нет категорий для колонок"}`
          : item.label;
        chip.innerHTML =
          `<span class="bld-var-mark" aria-hidden="true"></span>` +
          `<span class="bld-var-code"></span><span class="bld-var-name"></span>` +
          `<span class="bld-var-kind">${KIND_LABEL[item.type] || ""}</span>`;
        chip.querySelector(".bld-var-code").textContent = item.display || item.code;
        chip.querySelector(".bld-var-name").textContent = item.label;
        chip.setAttribute("aria-pressed", String(chosen));
        chip.addEventListener("click", event => {
          // Список тут же пересобирается, и щелчок всплыл бы уже от
          // оторванного узла — обработчик «щёлкнули мимо» принял бы это
          // за клик вне поповера и закрыл его после каждого выбора.
          event.stopPropagation();
          if (chosen) removeFromZone(item.code, zone);
          else addToZone(item.code, zone);
          renderPalette();
        });
        chip.addEventListener("dragstart", event => {
          event.dataTransfer.setData("text/plain", item.code);
          event.dataTransfer.effectAllowed = "copy";
          chip.classList.add("dragging");
        });
        chip.addEventListener("dragend", () => chip.classList.remove("dragging"));
        list.append(chip);
      });
    }

    // Сохранённый баннер отчёта годится и таблице: колонки те же, что в книге.
    function renderBannerOptions(query) {
      const matched = banners.filter(item => !query || item.name.toLowerCase().includes(query));
      if (!matched.length) return;
      const caption = document.createElement("p");
      caption.className = "bld-list-caption";
      caption.textContent = "Баннеры отчёта";
      list.append(caption);
      matched.forEach(item => {
        const chosen = bannerId === item.id;
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `bld-var${chosen ? " chosen" : ""}`;
        chip.dataset.banner = item.id;
        chip.setAttribute("aria-pressed", String(chosen));
        chip.innerHTML = '<span class="bld-var-mark" aria-hidden="true"></span><span class="bld-var-name"></span>';
        chip.querySelector(".bld-var-name").textContent = item.name;
        chip.addEventListener("click", event => {
          event.stopPropagation();
          bannerId = chosen ? null : item.id;
          if (bannerId) layout.cols = [];
          render();
          renderPalette();
        });
        list.append(chip);
      });
      const caption2 = document.createElement("p");
      caption2.className = "bld-list-caption";
      caption2.textContent = "Переменные";
      list.append(caption2);
    }

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
        chip.innerHTML = `<span class="bld-var-mark" aria-hidden="true"></span><span class="bld-var-name"></span>`;
        chip.querySelector(".bld-var-name").textContent = item.name;
        chip.addEventListener("click", event => {
          event.stopPropagation();
          if (item.id) addToZone(item.id, "filter");
          else { layout.filter = []; render(); }
          renderPalette();
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

    // Полоса параметров: пилюля несёт имя свойства и его значение —
    // ровно как строки свойств книги в разделе «Отчёты».
    function renderParams() {
      const slots = {
        rows: layout.rows.map(code => byCode.get(code)?.label).filter(Boolean).join(", "),
        cols: bannerId
          ? banners.find(item => item.id === bannerId)?.name || ""
          : layout.cols.map(code => byCode.get(code)?.label).filter(Boolean).join(nested ? " × " : ", "),
        filter: layout.filter.map(id => filters.find(item => item.id === id)?.name).filter(Boolean).join(", "),
      };
      saveCutButton.hidden = Boolean(bannerId) || !layout.cols.length;
      exportButton.hidden = !layout.rows.length;
      nestToggle.hidden = Boolean(bannerId) || layout.cols.length < 2;
      nestToggle.setAttribute("aria-pressed", String(nested));
      Object.entries(slots).forEach(([zone, text]) => {
        const slot = document.querySelector(`[data-slot="${zone}"]`);
        if (!slot) return;
        slot.textContent = text || ZONE_EMPTY[zone];
        slot.closest(".bld-param").classList.toggle("off", !text);
      });
    }

    // Смещения ярусов липкой шапки считаются из фактических высот: жёстко
    // прописанные пиксели разъезжаются на другом шрифте и масштабе.
    function stackStickyHeader(table) {
      if (!table || !table.offsetParent) return;
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
        renderEmpty("Выберите вопросы в «Строках» — таблица соберётся сама.");
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
      liveNets.forEach((nets, code) => {
        if (nets.length) payload[code] = { nets };
      });
      if (boxSelect.value) {
        const code = layout.rows[0];
        if (code) payload[code] = { ...(payload[code] || {}), scale_box: Number(boxSelect.value) };
      }
      return Object.keys(payload).length ? payload : undefined;
    }

    function netableQuestions() {
      if (!lastTable) return [];
      return lastTable.questions.filter(question =>
        question.rows.some(row => row.kind === "value" && !row.derived));
    }

    function renderNetControl() {
      netButton.hidden = !lastTable || !netableQuestions().length;
      const total = [...liveNets.values()].reduce((sum, nets) => sum + nets.length, 0);
      netButton.textContent = total ? `NET · ${total}` : "NET";
      netButton.setAttribute("aria-pressed", total ? "true" : "false");
    }

    function openNetPicker() {
      const questions = netableQuestions();
      if (!questions.length) return;
      const question = questions[0];
      netTarget = question.code;
      const rows = question.rows.filter(row => row.kind === "value" && !row.derived);
      const existing = liveNets.get(question.code) || [];
      const chosen = existing.map(net => `<div class="bld-net-row"><span>NET: ${escapeHtml(net.label)}</span><button type="button" data-drop-net="${escapeHtml(net.label)}">×</button></div>`).join("");
      pickerTitle.textContent = `NET для «${question.code}» — только на этом экране`;
      list.innerHTML = `<div class="bld-net-panel">
        ${chosen}
        <label class="bld-net-label">Название<input id="bld-net-label" value="Топ" maxlength="60" /></label>
        <div class="bld-net-values">${rows.map(row => `<label><input type="checkbox" value="${escapeHtml(row.label)}" /> ${escapeHtml(row.label)}</label>`).join("")}</div>
        <button type="button" id="bld-net-add" class="bld-net-add">Добавить группу</button>
      </div>`;
      picker.hidden = false;
      picker.dataset.mode = "net";
    }

    list.addEventListener("click", event => {
      if (picker.dataset.mode !== "net") return;
      const drop = event.target.closest("[data-drop-net]");
      if (drop) {
        const nets = (liveNets.get(netTarget) || []).filter(net => net.label !== drop.dataset.dropNet);
        liveNets.set(netTarget, nets);
        openNetPicker();
        renderNetControl();
        void renderGrid();
        return;
      }
      if (!event.target.closest("#bld-net-add")) return;
      const label = document.querySelector("#bld-net-label").value.trim();
      const values = [...document.querySelectorAll(".bld-net-values input:checked")].map(input => input.value);
      if (!label || !values.length) return;
      const question = lastTable.questions.find(item => item.code === netTarget);
      const codes = new Map(question.rows.filter(row => row.kind === "value" && !row.derived)
        .map(row => [row.label, row.label]));
      const nets = [...(liveNets.get(netTarget) || []).filter(net => net.label !== label),
        { label, values: values.map(value => codes.get(value) ?? value) }];
      liveNets.set(netTarget, nets);
      picker.hidden = true;
      delete picker.dataset.mode;
      renderNetControl();
      void renderGrid();
    });

    netButton.addEventListener("click", openNetPicker);
    boxSelect.addEventListener("change", () => { void renderGrid(); });

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
        head += `<th class="bld-grouphead" colspan="${span}">${escapeHtml(groups[cursor]) || "&nbsp;"}</th>`;
        cursor += span;
      }
      head += '</tr><tr><th class="bld-rowhead bld-cathead" style="text-align:left">Показатель</th>';
      columns.forEach(column => {
        head += `<th class="bld-cathead"><span class="bld-letter">${column.letter}</span>${escapeHtml(column.label)}</th>`;
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
        body += `<tr class="bld-qrow"><td class="bld-rowhead" colspan="${width}">${escapeHtml(question.code)} · ${escapeHtml(question.label)}</td></tr>`;
        question.rows.forEach(row => {
          if (row.kind === "subquestion") {
            body += `<tr class="bld-subrow"><td class="bld-rowhead" colspan="${width}">${escapeHtml(row.label)}</td></tr>`;
            return;
          }
          const rowClass = row.kind === "base" ? "bld-base" : row.derived ? "bld-derived" : "";
          // Название строки — кнопка графика: те же числа, что в строке.
          const head = row.kind === "value"
            ? `<button type="button" class="bld-rowchart" data-chart-row="${row.sheet_row}" title="Показать график строки">${escapeHtml(row.label)}</button>`
            : escapeHtml(row.label);
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
    async function saveCut() {
      const blocks = blocksOfLayout();
      if (!projectId || !blocks.length) return;
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
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") closeExportMenu();
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
      // Список пересобирается всегда, даже пока поповер закрыт: он —
      // отражение переменных проекта, а не состояния поповера.
      renderPalette();
      renderGrid();
    }

    // Экран показан — только теперь у шапки таблицы есть настоящие высоты.
    function activate() {
      if (stale) {
        void renderGrid();
        return;
      }
      stackStickyHeader(wrap.querySelector("table.bld-grid"));
    }

    // Пилюля — и кнопка выбора, и зона приёма: перетаскивание из
    // открытого поповера работает так же, как раньше из палитры.
    params.forEach(zone => {
      zone.addEventListener("click", () => openPicker(zone.dataset.zone, zone));
      zone.addEventListener("dragover", event => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        zone.classList.add("over");
      });
      zone.addEventListener("dragleave", () => zone.classList.remove("over"));
      zone.addEventListener("drop", event => {
        event.preventDefault();
        zone.classList.remove("over");
        const code = event.dataTransfer.getData("text/plain");
        if (byCode.has(code)) addToZone(code, zone.dataset.zone);
      });
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
    sheetSelect.addEventListener("change", () => { void renderGrid(); });
    // Показатель меняет только вид уже посчитанного.
    measureSelect.addEventListener("change", () => { if (lastTable) renderTable(lastTable); });
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
          if (lastTable) renderTable(lastTable);
          return { reply: "Переключил показатель на индекс к Total: 100 — уровень всей выборки.", did: "Показатель: индекс" };
        },
      },
      {
        match: /ответивш/i,
        run: () => {
          sheetSelect.value = "filter";
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

    function ask(text) {
      if (!text.trim()) return;
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

    render();
    return { setVariables, activate };
  })();

  /* Проектное в шапке: название открытого проекта и размер массива. Ярус
     хрома один, поэтому блок не «пустеет», а скрывается целиком — до
     открытия проекта показывать в нём нечего. Выгрузка живёт не здесь,
     а в ряду действий над окном списка, внутри самой рабочей области. */
  const projectChrome = document.querySelector("#project-chrome");

  window.Shell = {
    showScreen,
    /* Вызывается из app.js при входе в раздел «Таблицы». */
    activateTables() {
      builder.activate();
    },
    /* Вызывается из app.js: проект открыли или закрыли. */
    setProjectOpen(open) {
      projectChrome.hidden = !open;
      if (!open) {
        builder.setVariables([], {});
        closeExportMenu();
      }
    },
    /* Вызывается из app.js после разбора проекта. */
    setProjectVariables(items, context) {
      builder.setVariables(items, context);
    },
  };

  showScreen("manual");
})();
