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

  const SCREENS = { home: {}, manual: {}, builder: {} };

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
    if (name === "builder") builder.activate();
    window.scrollTo(0, 0);
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

  /* ================= Экран конструктора =================
   * Раскладка настоящая, числа — нет. Пока нет эндпоинта, который считает
   * произвольный кросс, таблица заполняется детерминированной заглушкой:
   * одна и та же раскладка всегда даёт одну и ту же картинку, поэтому её
   * можно обсуждать, но нельзя принять за результат расчёта.
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
    };
    const ZONE_PLACEHOLDER = {
      rows: "Перетащите вопрос — он станет строками таблицы",
      cols: "Перетащите переменную — она станет разрезом",
      filter: "Необязательно: ограничить выборку",
    };
    const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

    const list = document.querySelector("#bld-list");
    const search = document.querySelector("#bld-search");
    const count = document.querySelector("#bld-count");
    const wrap = document.querySelector("#bld-grid-wrap");
    const note = document.querySelector("#bld-stage-note");
    const measure = document.querySelector("#bld-measure");
    const sig = document.querySelector("#bld-sig");
    const log = document.querySelector("#bld-log");
    const form = document.querySelector("#bld-form");
    const input = document.querySelector("#bld-input");

    let variables = [];
    let byCode = new Map();
    let rowCount = 0;
    const layout = { rows: [], cols: [], filter: [] };

    function setVariables(next, respondents) {
      variables = next;
      byCode = new Map(next.map(item => [item.code, item]));
      rowCount = respondents || 0;
      Object.keys(layout).forEach(key => {
        layout[key] = layout[key].filter(code => byCode.has(code));
      });
      render();
    }

    // В строки и колонки годится только то, у чего есть категории: у ID,
    // открытых текстов и голых чисел раскладывать нечего.
    function usable(code) {
      return Boolean(byCode.get(code)?.categories.length);
    }

    function addToZone(code, zone) {
      if (zone !== "filter" && !usable(code)) return;
      Object.keys(layout).forEach(key => {
        layout[key] = layout[key].filter(item => item !== code);
      });
      layout[zone].push(code);
      render();
    }

    function removeFromZone(code, zone) {
      layout[zone] = layout[zone].filter(item => item !== code);
      render();
    }

    function renderPalette() {
      const query = search.value.trim().toLowerCase();
      const matched = variables.filter(item =>
        !query || item.code.toLowerCase().includes(query) || item.label.toLowerCase().includes(query));
      count.textContent = variables.length ? `${matched.length} из ${variables.length}` : "";
      list.innerHTML = "";

      if (!variables.length) {
        const empty = document.createElement("p");
        empty.className = "bld-placeholder bld-list-empty";
        empty.textContent = "Откройте проект в ручном режиме — переменные появятся здесь.";
        list.append(empty);
        return;
      }

      matched.forEach(item => {
        const flat = !item.categories.length;
        const chip = document.createElement("div");
        chip.className = flat ? "bld-var bld-var-flat" : "bld-var";
        chip.draggable = !flat;
        chip.dataset.code = item.code;
        chip.title = flat
          ? `${item.label} — нет категорий, разложить по строкам или колонкам нечего`
          : item.label;
        chip.innerHTML =
          `<span class="bld-var-code"></span><span class="bld-var-name"></span>` +
          `<span class="bld-var-kind">${KIND_LABEL[item.type] || ""}</span>` +
          (flat ? "" : `<span class="bld-quick"><button type="button" data-zone="rows">СТР</button>` +
          `<button type="button" data-zone="cols">КОЛ</button></span>`);
        chip.querySelector(".bld-var-code").textContent = item.code;
        chip.querySelector(".bld-var-name").textContent = item.label;
        chip.addEventListener("dragstart", event => {
          event.dataTransfer.setData("text/plain", item.code);
          event.dataTransfer.effectAllowed = "copy";
          chip.classList.add("dragging");
        });
        chip.addEventListener("dragend", () => chip.classList.remove("dragging"));
        chip.querySelectorAll(".bld-quick button").forEach(button => {
          button.addEventListener("click", () => addToZone(item.code, button.dataset.zone));
        });
        list.append(chip);
      });
    }

    function renderShelves() {
      document.querySelectorAll(".bld-zone").forEach(zone => {
        const key = zone.dataset.zone;
        zone.innerHTML = "";
        if (!layout[key].length) {
          const hint = document.createElement("span");
          hint.className = "bld-placeholder";
          hint.textContent = ZONE_PLACEHOLDER[key];
          zone.append(hint);
          return;
        }
        layout[key].forEach(code => {
          const pill = document.createElement("span");
          pill.className = "bld-pill";
          pill.draggable = true;
          pill.innerHTML = `<strong></strong><span></span><button type="button" aria-label="Убрать">×</button>`;
          pill.querySelector("strong").textContent = code;
          pill.querySelector("span").textContent = byCode.get(code).label;
          pill.querySelector("button").addEventListener("click", () => removeFromZone(code, key));
          pill.addEventListener("dragstart", event => {
            event.dataTransfer.setData("text/plain", code);
            event.dataTransfer.effectAllowed = "copy";
          });
          zone.append(pill);
        });
      });
    }

    function hash(text) {
      let value = 2166136261;
      for (let index = 0; index < text.length; index += 1) {
        value ^= text.charCodeAt(index);
        value = Math.imul(value, 16777619);
      }
      return (value >>> 0) / 4294967295;
    }

    function sheetLetter(index) {
      let label = "";
      let value = index;
      do {
        label = LETTERS[value % 26] + label;
        value = Math.floor(value / 26) - 1;
      } while (value >= 0);
      return label;
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

    function combine(codes) {
      return codes.reduce((accumulator, code) => {
        const variable = byCode.get(code);
        const next = [];
        accumulator.forEach(prefix => {
          variable.categories.forEach(value => { next.push(prefix.concat([{ code, value }])); });
        });
        return next;
      }, [[]]);
    }

    function renderGrid() {
      if (!layout.rows.length || !byCode.get(layout.rows[0])?.categories.length) {
        wrap.innerHTML = `<div class="bld-empty"><p>${
          layout.rows.length
            ? "У выбранного вопроса нет категорий для строк."
            : "Перетащите вопрос в «Строки» — таблица соберётся сама."
        }</p></div>`;
        note.textContent = "";
        return;
      }

      const usableCols = layout.cols.filter(code => byCode.get(code)?.categories.length);
      const colCombos = usableCols.length ? combine(usableCols) : [];
      const columns = [{ key: "total", label: "Total", group: "" }].concat(colCombos.map(combo => ({
        key: combo.map(part => `${part.code}=${part.value}`).join("|"),
        label: combo[combo.length - 1].value,
        group: combo.length > 1
          ? combo.slice(0, -1).map(part => part.value).join(" · ")
          : byCode.get(combo[0].code).label,
      })));

      const totalBase = rowCount || 1000;
      const bases = columns.map((column, index) =>
        index === 0 ? totalBase : Math.round(totalBase * (0.06 + hash(column.key) * 0.26)));

      let head = `<thead><tr><th class="bld-corner bld-ref"></th><th class="bld-ref">A</th>`;
      columns.forEach((column, index) => { head += `<th class="bld-ref">${sheetLetter(index + 1)}</th>`; });
      head += `</tr><tr><th class="bld-corner"></th><th class="bld-rowhead bld-grouphead" style="left:34px"></th>`;
      let cursor = 0;
      while (cursor < columns.length) {
        let span = 1;
        while (cursor + span < columns.length && columns[cursor + span].group === columns[cursor].group) span += 1;
        head += `<th class="bld-grouphead" colspan="${span}">${escapeHtml(columns[cursor].group) || "&nbsp;"}</th>`;
        cursor += span;
      }
      head += `</tr><tr><th class="bld-corner bld-ref">1</th>` +
              `<th class="bld-rowhead bld-cathead" style="left:34px;text-align:left">Показатель</th>`;
      columns.forEach((column, index) => {
        head += `<th class="bld-cathead"><span class="bld-letter">${sheetLetter(index + 1)}</span>${escapeHtml(column.label)}</th>`;
      });
      head += `</tr></thead>`;

      const rowVariable = byCode.get(layout.rows[0]);
      const nested = layout.rows.slice(1).filter(code => byCode.get(code)?.categories.length);
      const nestedCombos = nested.length ? combine(nested) : [[]];
      const showSig = sig.value === "on";
      const mode = measure.value;

      let rowNumber = 2;
      let body = `<tbody><tr class="bld-qrow"><td class="bld-rownum">${rowNumber}</td>` +
                 `<td class="bld-rowhead" colspan="${columns.length + 1}">${escapeHtml(rowVariable.code)} · ${escapeHtml(rowVariable.label)}</td></tr>`;
      rowNumber += 1;
      body += `<tr class="bld-base"><td class="bld-rownum">${rowNumber}</td><td class="bld-rowhead">База, чел.</td>`;
      bases.forEach((base, index) => {
        body += `<td class="bld-val${index === 0 ? " bld-total" : ""}">${base}</td>`;
      });
      body += `</tr>`;

      nestedCombos.forEach(nestedCombo => {
        if (nestedCombo.length) {
          rowNumber += 1;
          body += `<tr class="bld-qrow"><td class="bld-rownum">${rowNumber}</td>` +
                  `<td class="bld-rowhead" colspan="${columns.length + 1}">${escapeHtml(nestedCombo.map(part => part.value).join(" · "))}</td></tr>`;
        }
        rowVariable.categories.forEach(category => {
          const seed = `${category}|${nestedCombo.map(part => part.value).join("|")}`;
          rowNumber += 1;
          const values = columns.map(column => 0.04 + hash(`${seed}#${column.key}`) * 0.62);
          body += `<tr><td class="bld-rownum">${rowNumber}</td><td class="bld-rowhead">${escapeHtml(category)}</td>`;
          values.forEach((share, index) => {
            const lowBase = bases[index] < 100;
            let text;
            if (mode === "n") text = Math.round(share * bases[index]);
            else if (mode === "index") text = Math.round((share / values[0]) * 100);
            else text = `${(share * 100).toFixed(1).replace(".", ",")}%`;

            let marks = "";
            if (showSig && index > 0 && mode !== "index" && !lowBase) {
              const beaten = values
                .map((other, position) => ({ other, position }))
                .filter(item => item.position > 0 && item.position !== index
                  && columns[item.position].group === columns[index].group
                  && share - item.other > 0.1)
                .map(item => sheetLetter(item.position + 1));
              if (beaten.length) marks = `<span class="bld-sig">${beaten.join("")}</span>`;
            }
            body += `<td class="bld-val${index === 0 ? " bld-total" : ""}${lowBase ? " bld-lowbase" : ""}">${text}${marks}</td>`;
          });
          body += `</tr>`;
        });
      });
      body += `</tbody>`;

      wrap.innerHTML = `<table class="bld-grid">${head}${body}</table>`;
      stackStickyHeader(wrap.querySelector("table.bld-grid"));

      const parts = [`${columns.length} колонок`];
      if (usableCols.length) parts.push(`разрез: ${usableCols.map(code => byCode.get(code).label).join(" × ")}`);
      if (layout.filter.length) parts.push(`фильтр: ${layout.filter.map(code => byCode.get(code).label).join(", ")}`);
      if (bases.some(base => base < 100)) parts.push("серым — база меньше 100");
      note.textContent = parts.join(" · ");
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, character => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]
      ));
    }

    function render() {
      renderPalette();
      renderShelves();
      renderGrid();
    }

    // Экран показан — только теперь у шапки таблицы есть настоящие высоты.
    function activate() {
      stackStickyHeader(wrap.querySelector("table.bld-grid"));
    }

    document.querySelectorAll(".bld-zone").forEach(zone => {
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

    search.addEventListener("input", renderPalette);
    [measure, sig].forEach(control => control.addEventListener("change", renderGrid));
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
      log.scrollTop = log.scrollHeight;
    }

    function firstOfType(...types) {
      return variables.find(item => types.includes(item.type) && item.categories.length);
    }

    const SCENARIOS = [
      {
        match: /разрез|колонк|разбей/i,
        run: () => {
          const target = variables.find(item => item.categories.length && !layout.cols.includes(item.code)
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
        match: /индекс/i,
        run: () => {
          measure.value = "index";
          renderGrid();
          return { reply: "Переключил показатель на индекс к Total: 100 — уровень всей выборки.", did: "Показатель: индекс" };
        },
      },
      {
        match: /очист|сброс|заново/i,
        run: () => {
          layout.rows = []; layout.cols = []; layout.filter = [];
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
            "«покажи…», «переключи на индекс», «очисти стол».", null);
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
      "Пока это заглушка: раскладка меняется, числа не считаются.", null);

    render();
    return { setVariables, activate };
  })();

  window.Shell = {
    showScreen,
    /* Вызывается из app.js: проект открыли или закрыли. */
    setProjectOpen(open) {
      if (!open) {
        builder.setVariables([], 0);
        closeExportMenu();
      }
    },
    /* Вызывается из app.js после разбора проекта. */
    setProjectVariables(items, respondents) {
      builder.setVariables(items, respondents);
    },
  };

  showScreen("manual");
})();
