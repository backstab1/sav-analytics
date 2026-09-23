/* Массовые операции над вопросами. Классический скрипт после app.js:
   пользуется его состоянием (currentProject, api, renderProject) и объявляет
   функции, которые app.js вызывает только после запуска из boot.js. */

/* Массовые операции над вопросами. Выбор живёт на экране; правка уходит
   одним запросом и одной ревизией, всё или ничего. */
const selectedQuestionCodes = new Set();

// Последняя массовая правка: прежние значения полей по вопросам, чтобы
// «Отменить» вернул каждому своё. Подтверждение распознавания не отменяется.
let lastBulkUndo = null;
const BULK_FIELDS = ["role", "question_type", "base_filter_id", "included_in_report"];

function updateBulkBar() {
  const known = new Set(configuredQuestions().map(question => question.code));
  [...selectedQuestionCodes].forEach(code => { if (!known.has(code)) selectedQuestionCodes.delete(code); });
  const bar = document.querySelector("#bulk-bar");
  // Полоса живёт, пока что-то выбрано, и на это время заменяет собой ряд
  // панели: заголовок, поиск и переключатель видов прячет .is-selecting.
  // Снятый выбор уносит и «Отменить» — общая история (Ctrl+Z) остаётся.
  bar.hidden = selectedQuestionCodes.size === 0;
  if (bar.hidden) lastBulkUndo = null;
  document.querySelector("#structure-toolbar").classList.toggle("is-selecting", !bar.hidden);
  document.querySelector("#bulk-count").textContent = `Выбрано: ${selectedQuestionCodes.size}`;
  document.querySelector("#bulk-undo").hidden = !lastBulkUndo;
  const base = document.querySelector("#bulk-base");
  base.innerHTML = '<option value="">База</option><option value="standard">Стандартная база</option>'
    + configuredFilters().map(filter => `<option value="${escapeAttribute(filter.id)}">${escapeHtml(filter.name)}</option>`).join("");
  // Источник копирования — вопрос того же типа, что все выбранные.
  const selectedTypes = new Set(configuredQuestions()
    .filter(question => selectedQuestionCodes.has(question.code))
    .map(question => question.question_type));
  const copySources = selectedTypes.size === 1
    ? configuredQuestions().filter(question => selectedTypes.has(question.question_type))
    : [];
  document.querySelector("#bulk-copy").innerHTML = '<option value="">Копировать из</option>'
    + copySources.map(question => `<option value="${escapeAttribute(question.code)}">${escapeHtml(question.code)} — ${escapeHtml(question.label)}</option>`).join("");
  document.querySelectorAll("#bulk-bar button:not([data-bulk=undo]):not([data-bulk=clear]), #bulk-bar select")
    .forEach(control => { control.disabled = selectedQuestionCodes.size === 0; });
}

async function patchQuestions(body) {
  return api(`/api/projects/${currentProject.id}/questions`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function bulkSnapshot(codes, changed) {
  // Роль волны или веса снимает вопрос с отчёта, поэтому включение
  // запоминается при любой правке.
  const fields = BULK_FIELDS.filter(field => changed.includes(field) || field === "included_in_report");
  const byCode = new Map(configuredQuestions().map(question => [question.code, question]));
  return codes.map(code => {
    const question = byCode.get(code);
    return Object.fromEntries([["code", code], ...fields.map(field => [field, question[field] ?? null])]);
  });
}

/* Групповая перестановка: выбранные вопросы двигаются блоком, сохраняя свой
   взаимный порядок. Функция чистая — возвращает новый порядок кодов. */
function shiftedQuestionOrder(codes, selected, action) {
  const order = [...codes];
  if (action === "move-up") {
    for (let index = 1; index < order.length; index += 1) {
      if (selected.has(order[index]) && !selected.has(order[index - 1])) {
        [order[index - 1], order[index]] = [order[index], order[index - 1]];
      }
    }
  } else if (action === "move-down") {
    for (let index = order.length - 2; index >= 0; index -= 1) {
      if (selected.has(order[index]) && !selected.has(order[index + 1])) {
        [order[index], order[index + 1]] = [order[index + 1], order[index]];
      }
    }
  } else if (action === "gather") {
    const first = order.findIndex(code => selected.has(code));
    const picked = order.filter(code => selected.has(code));
    const rest = order.filter(code => !selected.has(code));
    const before = order.slice(0, first).filter(code => !selected.has(code)).length;
    rest.splice(before, 0, ...picked);
    return rest;
  }
  return order;
}

async function saveQuestionOrder(codes) {
  currentProject = await api(`/api/projects/${currentProject.id}/questions/order`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ codes }),
  });
}

async function undoBulk(snapshot) {
  if (snapshot.order) {
    await saveQuestionOrder(snapshot.order);
    return;
  }
  // Возвращаем поле за полем, группируя вопросы с одинаковым прежним значением:
  // сначала роль и тип, потом база и включение, которое роль могла сбросить.
  for (const field of BULK_FIELDS) {
    const groups = new Map();
    snapshot.filter(item => field in item).forEach(item => {
      const key = JSON.stringify(item[field]);
      groups.set(key, [...(groups.get(key) || []), item.code]);
    });
    for (const [key, codes] of groups) {
      currentProject = await patchQuestions({ codes, [field]: JSON.parse(key) });
    }
  }
}

// Выбор вопросов — отдельный режим: полоса массовых действий заменяет ряд
// панели, и редактор справа ему только мешает. Он закрывается, как только
// что-то отмечено; несохранённое в нём спрашивают, как при любом закрытии.
function closeInspectorForSelection() {
  if (!selectedQuestionCodes.size) return;
  const panel = openInspectorPanel();
  if (!panel || !confirmDiscard(panel)) return;
  closeAllInspectors();
  renderTable();
}

// Флажок строки не должен открывать карточку вопроса: щелчок останавливается
// на фазе перехвата, до обработчиков строки.
document.querySelector("#table-body").addEventListener("click", event => {
  if (event.target.closest(".select-cell")) event.stopPropagation();
}, true);
document.querySelector("#table-body").addEventListener("change", event => {
  const box = event.target.closest(".select-question");
  if (!box) return;
  if (box.checked) selectedQuestionCodes.add(box.dataset.selectCode);
  else selectedQuestionCodes.delete(box.dataset.selectCode);
  const all = document.querySelector("#select-all-questions");
  if (all) {
    const boxes = [...document.querySelectorAll("#table-body .select-question")];
    all.checked = boxes.length > 0 && boxes.every(item => item.checked);
  }
  updateBulkBar();
  closeInspectorForSelection();
});
document.querySelector("#table-head").addEventListener("change", event => {
  if (event.target.id !== "select-all-questions") return;
  document.querySelectorAll("#table-body .select-question").forEach(box => {
    box.checked = event.target.checked;
    if (box.checked) selectedQuestionCodes.add(box.dataset.selectCode);
    else selectedQuestionCodes.delete(box.dataset.selectCode);
  });
  updateBulkBar();
  closeInspectorForSelection();
});
document.querySelector("#bulk-bar").addEventListener("click", async event => {
  const button = event.target.closest("[data-bulk]");
  if (!button || !currentProject) return;
  const action = button.dataset.bulk;
  if (action === "clear") {
    selectedQuestionCodes.clear();
    renderTable();
    updateBulkBar();
    return;
  }
  if (action === "undo") {
    const snapshot = lastBulkUndo;
    lastBulkUndo = null;
    button.disabled = true;
    try {
      await undoBulk(snapshot);
      showToast("Массовая правка отменена");
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
      renderProject();
      updateBulkBar();
    }
    return;
  }
  if (["move-up", "move-down", "gather"].includes(action)) {
    const before = configuredQuestions().map(question => question.code);
    const after = shiftedQuestionOrder(before, selectedQuestionCodes, action);
    if (after.join("\n") === before.join("\n")) return;
    button.disabled = true;
    try {
      await saveQuestionOrder(after);
      lastBulkUndo = { order: before };
      renderProject();
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
      updateBulkBar();
    }
    return;
  }
  const body = { codes: [...selectedQuestionCodes] };
  if (action === "include") body.included_in_report = true;
  if (action === "exclude") body.included_in_report = false;
  if (action === "confirm") body.confirm_review = true;
  button.disabled = true;
  await runBulk(body, {
    include: "В отчёт",
    exclude: "Исключено",
    confirm: "Распознавание подтверждено",
  }[action]);
  button.disabled = false;
});

async function runBulk(body, caption) {
  const changed = Object.keys(body).filter(field => BULK_FIELDS.includes(field));
  const snapshot = changed.length ? bulkSnapshot(body.codes, changed) : null;
  try {
    currentProject = await patchQuestions(body);
    lastBulkUndo = snapshot;
    renderProject();
    showToast(`${caption}: ${plural(body.codes.length, "вопрос", "вопроса", "вопросов")}`);
  } catch (error) {
    alert(error.message);
  } finally {
    updateBulkBar();
  }
}

document.querySelector("#bulk-bar").addEventListener("change", async event => {
  const select = event.target.closest(".bulk-select");
  if (!select || !select.value || !currentProject) return;
  if (select.id === "bulk-copy") {
    const sourceCode = select.value;
    select.value = "";
    const codes = [...selectedQuestionCodes].filter(code => code !== sourceCode);
    if (!codes.length) return;
    if (!confirm(`Перенести из ${sourceCode} набор вывода, NET-группы, исключённые ответы, спецпоказатель и базу в ${plural(codes.length, "вопрос", "вопроса", "вопросов")}? Их нынешние настройки заменятся.`)) return;
    try {
      currentProject = await api(`/api/projects/${currentProject.id}/questions/copy-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: sourceCode, codes }),
      });
      // Отмена хранит только поля массовой полосы, настройки вопроса она не вернёт.
      lastBulkUndo = null;
      renderProject();
      showToast(`Настройки ${sourceCode} перенесены: ${plural(codes.length, "вопрос", "вопроса", "вопросов")}`);
    } catch (error) {
      alert(error.message);
    } finally {
      updateBulkBar();
    }
    return;
  }
  if (select.id === "bulk-group") {
    const [questionType, rankingEncoding] = select.value.split(":");
    select.value = "";
    const codes = configuredQuestions()
      .map(question => question.code)
      .filter(code => selectedQuestionCodes.has(code));
    try {
      const before = new Set(configuredQuestions().map(question => question.code));
      currentProject = await api(`/api/projects/${currentProject.id}/questions/group`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes, question_type: questionType, ranking_encoding: rankingEncoding }),
      });
      const created = configuredQuestions().find(question => !before.has(question.code));
      // Отмена массовой полосы хранит поля вопросов, а не состав структуры:
      // группа разбирается обратно кнопкой в её карточке.
      lastBulkUndo = null;
      selectedQuestionCodes.clear();
      renderProject();
      showToast(`${plural(codes.length, "вопрос", "вопроса", "вопросов")} собраны в ${created?.code || "группу"}`);
    } catch (error) {
      alert(error.message);
    } finally {
      updateBulkBar();
    }
    return;
  }
  const body = { codes: [...selectedQuestionCodes] };
  let caption;
  if (select.id === "bulk-type") {
    body.question_type = select.value;
    caption = `Тип «${typeLabels[select.value]}»`;
  } else if (select.id === "bulk-role") {
    body.role = select.value;
    caption = `Роль «${select.selectedOptions[0].textContent}»`;
  } else {
    body.base_filter_id = select.value === "standard" ? null : select.value;
    caption = `База «${select.selectedOptions[0].textContent}»`;
  }
  select.value = "";
  await runBulk(body, caption);
});
