/* Поповер выбора баннера, базы и веса в разделе «Отчёт». Классический скрипт после app.js (см. решение 016): пользуется
   его состоянием, а app.js вызывает эти функции только после запуска. */

/* ---------------- Поповер выбора ---------------- */

function pickerRows(kind) {
  if (kind === "banner") {
    const active = selectedReportBannerId();
    return [{ value: "", title: "Только Total", note: "без разбивки", checked: !active }].concat(
      configuredBanners().map(banner => ({
        value: banner.id,
        title: banner.name,
        note: `${plural(banner.blocks.length, "блок", "блока", "блоков")} · ${plural(reportBannerColumnCount(banner), "колонка", "колонки", "колонок")}`,
        checked: banner.id === active,
        edit: { kind: "banner", id: banner.id },
      }))
    );
  }
  if (kind === "filter") {
    const active = selectedReportFilterId();
    const total = currentProject.inspection.row_count;
    return [{
      value: "", title: "Все респонденты",
      note: `без общего фильтра · ${total.toLocaleString("ru-RU")}`,
      checked: !active,
    }].concat(
      configuredFilters().map(filter => {
        const preview = filterPreviewCache.get(filterPreviewKey(filter));
        const sample = preview ? preview.selected.toLocaleString("ru-RU") : "…";
        return {
          value: filter.id,
          title: filter.name,
          note: `${plural(countFilterConditions(filter.rule), "условие", "условия", "условий")} · выборка ${sample}`,
          checked: filter.id === active,
          edit: { kind: "filter", id: filter.id },
        };
      })
    );
  }
  const settings = configuredReportSettings();
  const selection = settings.calculated_weight_id
    ? `calculated:${settings.calculated_weight_id}`
    : settings.weight_variable ? `ready:${settings.weight_variable}` : "";
  return [{ value: "", title: "Без веса", note: "невзвешенные показатели", checked: !selection }]
    .concat(declaredWeightVariables().map(variable => ({
      value: `ready:${variable.name}`,
      title: variable.name,
      note: `готовый из массива — ${variable.label}`,
      checked: selection === `ready:${variable.name}`,
    })))
    .concat(configuredWeights().map(weight => ({
      value: `calculated:${weight.id}`,
      title: weight.name,
      note: calculatedWeightSummary(weight),
      checked: selection === `calculated:${weight.id}`,
      edit: { kind: "weight", id: weight.id },
    })));
}

const pickerHeads = {
  banner: { caption: "Баннеры проекта", create: "+ Новый баннер" },
  filter: { caption: "Базы и фильтры", create: "+ Новое правило" },
  weight: { caption: "Веса проекта", create: "+ Новый рассчитанный вес" },
};

function renderPicker(kind) {
  const head = pickerHeads[kind];
  const rows = pickerRows(kind);
  const items = rows.map(row => {
    return `<div class="picker-row ${row.checked ? "checked" : ""}">
      <button type="button" class="picker-pick" data-pick="${escapeAttribute(row.value)}" aria-checked="${row.checked}" role="radio">
        <span class="dot" aria-hidden="true"></span>
        <span class="picker-text"><strong>${escapeHtml(row.title)}</strong><small>${escapeHtml(row.note)}</small></span>
      </button>
      ${row.edit ? `<button type="button" class="picker-edit" data-edit="${row.edit.kind}" data-id="${escapeAttribute(row.edit.id)}">править</button>` : ""}
    </div>`;
  }).join("");
  return `<div class="picker-head"><strong>${escapeHtml(head.caption)}</strong><span>${rows.length - 1}</span></div>
    <div class="picker-list" role="radiogroup">${items}</div>
    <div class="picker-foot"><button type="button" class="btn ghost compact" data-new="${kind}">${escapeHtml(head.create)}</button></div>`;
}

function openPicker(kind, anchor) {
  if (pickerKind === kind && !pickerElement.hidden) {
    closePicker();
    return;
  }
  closePicker();
  pickerKind = kind;
  pickerElement.innerHTML = renderPicker(kind);
  pickerElement.hidden = false;
  const rect = anchor.getBoundingClientRect();
  const width = pickerElement.offsetWidth;
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
  pickerElement.style.top = `${Math.round(rect.bottom + 6)}px`;
  pickerElement.style.left = `${Math.round(left)}px`;
  anchor.setAttribute("aria-expanded", "true");
}

function closePicker() {
  if (!pickerElement || pickerElement.hidden) return;
  pickerElement.hidden = true;
  pickerElement.innerHTML = "";
  pickerKind = null;
  document.querySelectorAll("[data-picker]").forEach(button => button.setAttribute("aria-expanded", "false"));
}

function openEntityEditor(kind, id = null) {
  if (kind === "banner") openBanner(id);
  else if (kind === "filter") openFilter(id);
  else if (kind === "weight") openWeight(id);
  else if (kind === "recoding") openRecoding(id);
}

async function applyPick(kind, value) {
  const anchor = document.querySelector(`[data-picker="${kind}"]`);
  closePicker();
  if (kind === "banner") await assignReportBanner(value || null, anchor);
  else if (kind === "filter") await assignReportFilter(value || null, anchor);
  else await assignReportWeight(value);
}

// Вес живёт в report_settings целиком, поэтому переключение отправляет
// текущие настройки с заменённым весом: сервер заодно проверит пригодность.
async function assignReportWeight(value) {
  const payload = reportSettingsPayload({
    ...configuredReportSettings(),
    weight_variable: value.startsWith("ready:") ? value.slice(6) : null,
    calculated_weight_id: value.startsWith("calculated:") ? value.slice(11) : null,
  });
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/report-settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderProject();
    showToast(value ? "Вес применён к отчёту" : "Отчёт считается без веса");
  } catch (error) {
    alert(error.message);
  }
}

pickerElement.addEventListener("click", event => {
  const editButton = event.target.closest("[data-edit]");
  if (editButton) {
    closePicker();
    openEntityEditor(editButton.dataset.edit, editButton.dataset.id);
    return;
  }
  const newButton = event.target.closest("[data-new]");
  if (newButton) {
    const kind = newButton.dataset.new;
    closePicker();
    openEntityEditor(kind);
    return;
  }
  const pick = event.target.closest("[data-pick]");
  if (pick) void applyPick(pickerKind, pick.dataset.pick);
});

document.addEventListener("click", event => {
  if (pickerElement.hidden) return;
  if (event.target.closest("#picker") || event.target.closest("[data-picker]")) return;
  closePicker();
});
window.addEventListener("resize", closePicker);
