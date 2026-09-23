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
    // Объединять имеет смысл только непересекающиеся категории; у multiple
    // колонки пересекаются, и группа из них — уже другой вопрос.
    const mergeable = !options.overlapping;
    const rows = ordered.map(item => `
      <div class="banner-category" data-key="${escapeAttribute(item.key)}" data-base="${item.base}">
        <input type="checkbox" class="banner-category-shown" ${item.setting?.hidden ? "" : "checked"} aria-label="Показывать «${escapeAttribute(item.label)}»" />
        <input class="banner-category-label" value="${escapeAttribute(item.setting?.label || "")}" placeholder="${escapeAttribute(item.label)}" aria-label="Подпись колонки" />
        ${mergeable ? `<input class="banner-category-group" value="${escapeAttribute(item.setting?.group || "")}" placeholder="Группа" aria-label="Объединить в колонку" title="Категории с одной группой выводятся одной колонкой" />` : ""}
        <em>${item.base.toLocaleString("ru-RU")}</em>
        <button type="button" class="icon-button" data-move-category="-1" aria-label="Выше">↑</button>
        <button type="button" class="icon-button" data-move-category="1" aria-label="Ниже">↓</button>
      </div>`).join("");
    const minimumBase = configuredReportSettings().minimum_base;
    const rare = ordered.filter(item => item.base < minimumBase).length;
    const merge = mergeable && rare > 1
      ? `<button type="button" class="secondary compact-button" data-merge-rare="${minimumBase}">Объединить редкие (база меньше ${minimumBase}) в «Другое»</button>`
      : "";
    list.classList.toggle("mergeable", mergeable);
    list.innerHTML = rows ? rows + merge : '<p class="muted">Категорий нет.</p>';
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
    group: row.querySelector(".banner-category-group")?.value.trim() || null,
  }));
  // Список, не отличающийся от данных, не сохраняем: так в баннер не попадает
  // замороженный порядок, и новые категории встают на свои места.
  const untouched = settings.every(item => !item.label && !item.hidden && !item.group)
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
  const merge = event.target.closest("[data-merge-rare]");
  if (merge) {
    const threshold = Number(merge.dataset.mergeRare);
    merge.closest(".banner-category-list").querySelectorAll(".banner-category").forEach(row => {
      const group = row.querySelector(".banner-category-group");
      if (Number(row.dataset.base) < threshold && group && !group.value.trim()) group.value = "Другое";
    });
    setBannerFormDirty(true);
    return;
  }
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
      || ["multiple_choice_dichotomy", "multiple_choice_categorical"].includes(item.question_type))
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

/* ---------------- Кнопки редактора ---------------- */

document.querySelector("#close-banner-editor").addEventListener("click", () => closeBanner());
document.querySelector("#add-banner-block").addEventListener("click", () => {
  addBannerBlock();
  setBannerFormDirty(true);
});
document.querySelector("#delete-banner").addEventListener("click", (...args) => deleteBanner(...args));
document.querySelector("#refresh-banner-preview").addEventListener("click", (...args) => loadBannerPreview(...args));

/* ---------------- Блоки и отметка изменений ---------------- */

document.querySelector("#banner-block-list").addEventListener("click", event => {
  const button = event.target.closest("button[data-remove-banner-block]");
  if (button) {
    button.closest(".banner-block").remove();
    setBannerFormDirty(true);
  }
});

const bannerForm = document.querySelector("#banner-form");
bannerForm.addEventListener("input", () => setBannerFormDirty(true));
bannerForm.addEventListener("change", () => setBannerFormDirty(true));

/* ---------------- Сохранение ---------------- */

document.querySelector("#banner-form").addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-banner");
  const bannerError = document.querySelector("#banner-error");
  bannerError.hidden = true;
  let blocks;
  try {
    blocks = collectBannerBlocks();
  } catch (error) {
    showError(bannerError, error);
    return;
  }
  const payload = {
    name: document.querySelector("#banner-name").value.trim(),
    blocks,
  };
  setBusy(saveButton, true, "Сохраняем…");
  try {
    const url = currentBannerId
      ? `/api/projects/${currentProject.id}/banners/${currentBannerId}`
      : `/api/projects/${currentProject.id}/banners`;
    currentProject = await api(url, {
      method: currentBannerId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!currentBannerId) {
      currentBannerId = configuredBanners().at(-1)?.id;
    }
    markInspectorClean(bannerEditor);
    renderProject();
    openBanner(currentBannerId);
    await loadBannerPreview();
    showToast("Баннер сохранён");
  } catch (error) {
    showError(bannerError, error);
  } finally {
    setBusy(saveButton, false, "Сохранить");
  }
});

reportSettingsForm.addEventListener("submit", async event => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-report-settings");
  const settingsError = document.querySelector("#report-settings-error");
  const weightSelection = document.querySelector("#report-weight").value;
  settingsError.hidden = true;
  setBusy(saveButton, true, "Сохраняем…");
  try {
    await patchReportSettings({
      weight_variable: weightSelection.startsWith("ready:") ? weightSelection.slice(6) : null,
      calculated_weight_id: weightSelection.startsWith("calculated:")
        ? weightSelection.slice(11)
        : null,
    });
    closeSheet();
    showToast("Настройки отчёта сохранены");
  } catch (error) {
    showError(settingsError, error);
  } finally {
    setBusy(saveButton, false, "Применить вес");
  }
});

/* ---------------- Открытие, закрытие и блоки ---------------- */

function openBanner(bannerId = null) {
  if (!confirmDiscard(openInspectorPanel())) return;
  currentBannerId = bannerId;
  currentQuestionCode = null;
  currentRecodingId = null;
  showInspector(bannerEditor);
  const banner = bannerId ? configuredBanners().find(item => item.id === bannerId) : null;
  setHeadingText(document.querySelector("#banner-editor-title"), banner?.name || "Новый баннер");
  document.querySelector("#banner-name").value = banner?.name || `Баннер ${configuredBanners().length + 1}`;
  const list = document.querySelector("#banner-block-list");
  list.innerHTML = "";
  if (banner) banner.blocks.forEach(block => addBannerBlock(block));
  else addBannerBlock();
  document.querySelector("#delete-banner").hidden = !banner;
  document.querySelector("#banner-error").hidden = true;
  document.querySelector("#banner-preview-count").textContent = "";
  document.querySelector("#banner-preview").innerHTML = banner
    ? '<p class="muted">Считаем…</p>'
    : '<p class="muted">Сохраните баннер для расчёта.</p>';
  setBannerFormDirty(false);
  renderTable();
  if (banner) loadBannerPreview();
}

function closeBanner() {
  if (bannerFormDirty && !confirm("Есть несохранённые изменения. Закрыть редактор без сохранения?")) return;
  setBannerFormDirty(false);
  bannerEditor.hidden = true;
  currentBannerId = null;
  renderTable();
}

function setBannerFormDirty(dirty) {
  bannerFormDirty = dirty;
  const warning = document.querySelector("#banner-unsaved-warning");
  warning.hidden = !dirty;
  document.querySelector("#save-banner").classList.toggle("has-unsaved-changes", dirty);
}

function addBannerBlock(block = {}) {
  const first = block.sources?.[0];
  const second = block.sources?.[1];
  const element = document.createElement("div");
  element.className = "banner-block block-row";
  element.innerHTML = `<div class="banner-block-head block-row-head"><input class="banner-block-label" placeholder="Название блока — необязательно" value="${escapeAttribute(block.label || "")}" /><button class="del" type="button" data-remove-banner-block title="Удалить блок" aria-label="Удалить блок">×</button></div><div class="block-lvls"><label>Первый уровень<select class="banner-source-first">${bannerSourceOptions(first, false)}</select></label><label>Второй уровень<select class="banner-source-second">${bannerSourceOptions(second, true)}</select></label></div>
    <details class="banner-categories" data-level="0"><summary>Категории первого уровня</summary><div class="banner-category-list"></div></details>
    <details class="banner-categories" data-level="1" ${second ? "" : "hidden"}><summary>Категории второго уровня</summary><div class="banner-category-list"></div></details>`;
  // Сохранённые настройки категорий живут на блоке, пока список не открыли:
  // неоткрытый список не должен их терять при сохранении.
  element.bannerCategories = [first?.categories || null, second?.categories || null];
  document.querySelector("#banner-block-list").append(element);
}
