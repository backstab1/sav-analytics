/* Редактор баннера: блоки, категории и предпросмотр колонок. Классический скрипт после app.js (см. решение 016): пользуется
   его состоянием, а app.js вызывает эти функции только после запуска. */

/* Категории блока баннера: порядок, подпись и скрытие в этом баннере, без
   новой перекодировки. Список грузится при раскрытии — с базами. */
async function loadBannerCategories(details) {
  const block = details.closest(".banner-block");
  const level = Number(details.dataset.level);
  const select = block.querySelector(level ? ".banner-source-second" : ".banner-source-first");
  const list = details.querySelector(".banner-category-list");
  if (!select.value) {
    list.innerHTML = '<p class="muted">Сначала выберите переменную.</p>';
    return;
  }
  if (list.dataset.source === select.value) return;
  list.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const options = await api(`/api/projects/${currentProject.id}/banners/source-categories`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parseBannerSource(select.value)),
    });
    const settings = block.bannerCategories[level] || [];
    const byKey = new Map(options.categories.map(item => [item.key, item]));
    const ordered = [
      ...settings.filter(item => byKey.has(item.key)).map(item => ({ ...byKey.get(item.key), setting: item })),
      ...options.categories.filter(item => !settings.some(setting => setting.key === item.key)).map(item => ({ ...item, setting: null })),
    ];
    list.dataset.source = select.value;
    list.innerHTML = ordered.map(item => `
      <div class="banner-category" data-key="${escapeAttribute(item.key)}">
        <input type="checkbox" class="banner-category-shown" ${item.setting?.hidden ? "" : "checked"} aria-label="Показывать «${escapeAttribute(item.label)}»" />
        <input class="banner-category-label" value="${escapeAttribute(item.setting?.label || "")}" placeholder="${escapeAttribute(item.label)}" aria-label="Подпись колонки" />
        <em>${item.base.toLocaleString("ru-RU")}</em>
        <button type="button" class="icon-button" data-move-category="-1" aria-label="Выше">↑</button>
        <button type="button" class="icon-button" data-move-category="1" aria-label="Ниже">↓</button>
      </div>`).join("") || '<p class="muted">Категорий нет.</p>';
  } catch (error) {
    list.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

function collectBannerCategories(block, level) {
  const list = block.querySelectorAll(".banner-category-list")[level];
  const rows = [...list.querySelectorAll(".banner-category")];
  if (!list.dataset.source) return block.bannerCategories[level] || null;
  const settings = rows.map(row => ({
    key: row.dataset.key,
    label: row.querySelector(".banner-category-label").value.trim() || null,
    hidden: !row.querySelector(".banner-category-shown").checked,
  }));
  // Список, не отличающийся от данных, не сохраняем: так в баннер не попадает
  // замороженный порядок, и новые категории встают на свои места.
  const untouched = settings.every(item => !item.label && !item.hidden)
    && list.dataset.initialOrder === rows.map(row => row.dataset.key).join("\n")
    && !block.bannerCategories[level];
  return untouched ? null : settings;
}

document.querySelector("#banner-block-list").addEventListener("toggle", event => {
  const details = event.target;
  if (!details.matches?.(".banner-categories") || !details.open) return;
  void loadBannerCategories(details).then(() => {
    const list = details.querySelector(".banner-category-list");
    if (list.dataset.initialOrder === undefined) {
      list.dataset.initialOrder = [...list.querySelectorAll(".banner-category")].map(row => row.dataset.key).join("\n");
    }
  });
}, true);

document.querySelector("#banner-block-list").addEventListener("click", event => {
  const button = event.target.closest("[data-move-category]");
  if (!button) return;
  const row = button.closest(".banner-category");
  const step = Number(button.dataset.moveCategory);
  const sibling = step < 0 ? row.previousElementSibling : row.nextElementSibling;
  if (!sibling) return;
  if (step < 0) sibling.before(row);
  else sibling.after(row);
  setBannerFormDirty(true);
});

document.querySelector("#banner-block-list").addEventListener("change", event => {
  const select = event.target.closest(".banner-source-first, .banner-source-second");
  if (!select) return;
  const block = select.closest(".banner-block");
  const level = select.matches(".banner-source-second") ? 1 : 0;
  // Другая переменная — другие категории: прежние настройки к ней не относятся.
  block.bannerCategories[level] = null;
  const details = block.querySelectorAll(".banner-categories")[level];
  const list = details.querySelector(".banner-category-list");
  list.innerHTML = "";
  delete list.dataset.source;
  delete list.dataset.initialOrder;
  details.open = false;
  if (level === 1) details.hidden = !select.value;
});

function bannerSourceOptions(selected, allowEmpty) {
  const selectedValue = selected ? `${selected.kind}:${selected.ref}` : "";
  const options = [];
  if (allowEmpty) options.push('<option value="">Без вложения</option>');
  configuredQuestions()
    .filter(item => (item.question_type === "single_choice" && item.source_variables.length === 1)
      || item.question_type === "multiple_choice_dichotomy")
    .forEach(item => options.push(`<option value="question:${escapeAttribute(item.code)}" ${selectedValue === `question:${item.code}` ? "selected" : ""}>${escapeHtml(item.code)} — ${escapeHtml(item.label)}</option>`));
  configuredRecodings().forEach(item => options.push(`<option value="recoding:${item.id}" ${selectedValue === `recoding:${item.id}` ? "selected" : ""}>↳ ${escapeHtml(item.code)} — ${escapeHtml(item.name)}</option>`));
  return options.join("");
}

function collectBannerBlocks() {
  const elements = [...document.querySelectorAll("#banner-block-list .banner-block")];
  if (!elements.length) throw new Error("Добавьте хотя бы один блок баннера.");
  return elements.map(element => {
    const first = parseBannerSource(element.querySelector(".banner-source-first").value);
    const secondValue = element.querySelector(".banner-source-second").value;
    const firstCategories = collectBannerCategories(element, 0);
    if (firstCategories) first.categories = firstCategories;
    const sources = [first];
    if (secondValue) {
      const second = parseBannerSource(secondValue);
      const secondCategories = collectBannerCategories(element, 1);
      if (secondCategories) second.categories = secondCategories;
      sources.push(second);
    }
    return {
      label: element.querySelector(".banner-block-label").value.trim() || null,
      sources,
    };
  });
}

function parseBannerSource(value) {
  const separator = value.indexOf(":");
  if (separator < 1) throw new Error("Выберите источник баннера.");
  return { kind: value.slice(0, separator), ref: value.slice(separator + 1) };
}

function bannerSourceLabel(source) {
  if (source.kind === "question") return findQuestion(source.ref)?.label || source.ref;
  return configuredRecodings().find(item => item.id === source.ref)?.name || source.ref;
}

async function loadBannerPreview() {
  if (!currentProject || !currentBannerId) return;
  const container = document.querySelector("#banner-preview");
  container.innerHTML = '<p class="muted">Считаем…</p>';
  try {
    const preview = await api(`/api/projects/${currentProject.id}/banners/${currentBannerId}/preview`);
    container.innerHTML = renderBannerPreview(preview);
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

function renderBannerPreview(preview) {
  const minimumBase = configuredReportSettings().minimum_base;
  document.querySelector("#banner-preview-count").textContent = `${preview.columns.length} колонок`;
  // Перекрытие multiple в баннере видно числом до отчёта, а не по буквам в нём.
  const overlaps = (preview.overlaps || []).map(item => `
    <p class="banner-overlap">«${escapeHtml(item.block)}»: колонки пересекаются у ${item.respondents.toLocaleString("ru-RU")} респондентов — внутри блока сравнение только с остальными, без попарных букв.</p>`).join("");
  return `${overlaps}<div class="col-preview">${preview.columns.map((column, index) => {
    const label = index === 0 ? "Total" : `${column.block ? `${column.block} · ` : ""}${column.label}`;
    const smallBase = column.base > 0 && column.base < minimumBase;
    return `<div class="col-line ${index === 0 ? "total" : ""} ${smallBase ? "small-base" : ""}"><span title="${escapeAttribute(label)}">${escapeHtml(label)}</span><em>База ${column.base.toLocaleString("ru-RU")}</em></div>`;
  }).join("")}</div>`;
}

async function deleteBanner() {
  if (!currentBannerId || !confirm("Удалить этот баннер?")) return;
  const errorBox = document.querySelector("#banner-error");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/banners/${currentBannerId}`, { method: "DELETE" });
    setBannerFormDirty(false);
    closeBanner();
    renderProject();
    showToast("Баннер удалён");
  } catch (error) {
    showError(errorBox, error);
  }
}
