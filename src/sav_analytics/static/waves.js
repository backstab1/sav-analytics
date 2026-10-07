/* Волны (PQ.19, решения 034 и 039).

   Селектор в шапке выбирает волну для работы: одну, все вместе или
   сравнение колонками. Выбор хранится в проекте, и по нему считается всё —
   таблицы, анализ, открытые ответы, книга. Волны — значения одной
   переменной с ролью «Волна»: размеченные в данных и подгруженные файлами.

   Новая волна: файл → предложение сопоставления (по имени и подписи) →
   человек правит пары, может попросить ИИ подобрать несопоставленные,
   отмечает новые переменные → «Добавить волну». */

let wavePreview = null;
let waveMapping = {};
let waveMatchJob = null;

const WAVE_HOW = { code: "по имени", label: "по подписи", ai: "ИИ", manual: "вручную" };

function projectWaves() {
  return currentProject?.waves || [];
}

let waveOverview = null;
let waveOverviewKey = "";

// Волны из самого проекта: роль «Волна» и подписи её значений. Число анкет
// приходит с сервера отдельно, селектор рисуется и без него.
function projectWaveValues() {
  const question = configuredQuestions().find(item => item.role === "wave" && item.source_variables?.length === 1);
  if (!question) return [];
  const variable = currentProject.inspection.variables.find(item => item.name === question.source_variables[0]);
  return [...(variable?.value_labels || [])].sort((left, right) => Number(left.value) - Number(right.value));
}

function currentWaveView() {
  const values = projectWaveValues();
  if (values.length < 2) return { mode: "all", value: null, label: null };
  const stored = currentProject.configuration.wave_view || {};
  const mode = ["wave", "all", "compare"].includes(stored.mode) ? stored.mode : "wave";
  if (mode !== "wave") return { mode, value: null, label: null };
  const chosen = values.find(item => String(Number(item.value)) === String(Number(stored.value))) || values[values.length - 1];
  return { mode, value: chosen.value, label: chosen.label };
}

// Сколько анкет в работе: в выбранной волне или во всех.
function activeRowCount() {
  const view = currentWaveView();
  if (view.mode === "wave" && waveOverview) {
    const item = waveOverview.values.find(entry => String(Number(entry.value)) === String(Number(view.value)));
    if (item) return item.count;
  }
  return waveOverview?.total || currentProject.inspection.row_count;
}

function waveViewTitle(view) {
  if (view.mode === "all") return "Все волны";
  if (view.mode === "compare") return "Сравнение волн";
  return view.label;
}

function renderWaveChrome() {
  if (!currentProject) return;
  const values = projectWaveValues();
  const many = values.length >= 2;
  const view = currentWaveView();
  document.querySelector("#wave-switch").hidden = !many;
  document.querySelector("#wave-crumb").hidden = !many;
  document.querySelector("#wave-add-quick").hidden = many;
  document.querySelector("#wave-name").textContent = many ? waveViewTitle(view) : "";
  document.querySelector("#wave-switch").classList.toggle("is-mode", many && view.mode !== "wave");
  // Числа анкет — один запрос на ревизию проекта.
  const key = `${currentProject.id}:${currentProject.configuration.revision}`;
  if (many && waveOverviewKey !== key) {
    waveOverviewKey = key;
    const projectId = currentProject.id;
    api(`/api/projects/${projectId}/waves`).then(result => {
      if (currentProject?.id !== projectId) return;
      waveOverview = result;
      if (!document.querySelector("#wave-menu").hidden) renderWaveMenu();
    }).catch(() => {});
  }
  if (!many) waveOverview = null;
}

function renderWaveMenu() {
  const view = currentWaveView();
  const counts = new Map((waveOverview?.values || []).map(item => [String(Number(item.value)), item.count]));
  const count = value => counts.has(value) ? counts.get(value).toLocaleString("ru-RU") : "";
  const item = (attributes, label, note, checked) => `<button type="button" class="wave-option${checked ? " is-on" : ""}" role="menuitemradio" aria-checked="${checked}" ${attributes}>
      <span class="wave-check" aria-hidden="true">${checked ? "✓" : ""}</span><span class="wave-label">${escapeHtml(label)}</span><span class="wave-count">${note}</span></button>`;
  const values = projectWaveValues();
  document.querySelector("#wave-menu").innerHTML = `
    <p class="wave-menu-cap">Работать с волной</p>
    ${[...values].reverse().map(entry => item(
      `data-wave-mode="wave" data-wave-value="${escapeAttribute(String(entry.value))}"`,
      entry.label, count(String(Number(entry.value))),
      view.mode === "wave" && String(Number(view.value)) === String(Number(entry.value)),
    )).join("")}
    <div class="wave-menu-sep"></div>
    ${item('data-wave-mode="all"', "Все волны вместе", waveOverview ? waveOverview.total.toLocaleString("ru-RU") : "", view.mode === "all")}
    ${item('data-wave-mode="compare"', "Сравнение волн", "колонками", view.mode === "compare")}
    <div class="wave-menu-sep"></div>
    <button type="button" class="wave-action" role="menuitem" data-wave-action="add">+ Добавить волну</button>
    <button type="button" class="wave-action" role="menuitem" data-wave-action="manage">Файлы волн…</button>`;
}

function toggleWaveMenu(open) {
  const menu = document.querySelector("#wave-menu");
  if (!currentProject) {
    menu.hidden = true;
    return;
  }
  const show = open ?? menu.hidden;
  if (show) renderWaveMenu();
  menu.hidden = !show;
  document.querySelector("#wave-switch").setAttribute("aria-expanded", String(show));
}

async function setWaveView(mode, value = null) {
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/waves/view`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, value: value == null ? null : Number(value) }),
    });
    // Числа, посчитанные по прежней волне, больше не верны.
    [recodePreviewCache, filterPreviewCache, readyWeightCache, recodeSourceValuesCache].forEach(cache => cache.clear());
    renderProject();
    void loadReportPreflight();
    showToast(mode === "wave" ? `Волна «${currentWaveView().label}»` : mode === "all" ? "Все волны вместе" : "Сравнение волн");
  } catch (error) {
    alert(error.message);
  }
}

document.querySelector("#wave-switch").addEventListener("click", event => {
  event.stopPropagation();
  toggleWaveMenu();
});

document.querySelector("#wave-menu").addEventListener("click", event => {
  const option = event.target.closest("[data-wave-mode]");
  const action = event.target.closest("[data-wave-action]");
  toggleWaveMenu(false);
  if (option) void setWaveView(option.dataset.waveMode, option.dataset.waveValue ?? null);
  else if (action?.dataset.waveAction === "add") document.querySelector("#add-wave-file").click();
  else if (action?.dataset.waveAction === "manage") openWavesSheet();
});

document.addEventListener("click", event => {
  if (!event.target.closest(".wave-switch-wrap")) toggleWaveMenu(false);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.querySelector("#wave-menu").hidden) toggleWaveMenu(false);
});
document.querySelector("#wave-add-quick").addEventListener("click", () => document.querySelector("#add-wave-file").click());

/* ---------------- Список волн ---------------- */

function openWavesSheet() {
  const waves = projectWaves();
  document.querySelector("#waves-body").innerHTML = waves.length
    ? `<ol class="waves-list">${waves.map((wave, index) => `
        <li data-wave-id="${escapeAttribute(wave.id)}">
          <input value="${escapeAttribute(wave.label)}" aria-label="Название волны" data-wave-label />
          <small class="muted">${escapeHtml(wave.filename || "")}${index === 0 ? " · задаёт структуру" : ""}</small>
          ${index === 0 ? "" : '<button type="button" class="text-button danger-text" data-wave-remove>Удалить</button>'}
        </li>`).join("")}</ol>
      <p class="muted">Файлы сложены в общий массив; каждая волна — значение переменной «${escapeHtml(currentProject.waves_meta?.variable || "WAVE")}». Какую волну смотреть, выбирается в шапке рядом с названием проекта.</p>`
    : '<p class="muted">Волны пока не подгружались файлами. Если волна размечена в данных, назначьте её переменной роль «Волна» в «Данных» — она появится в шапке.</p>';
  openSheet(document.querySelector("#waves-sheet"));
}

document.querySelector("#waves-body").addEventListener("change", async event => {
  const input = event.target.closest("[data-wave-label]");
  if (!input) return;
  const id = input.closest("[data-wave-id]").dataset.waveId;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/waves/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: input.value.trim() }),
    });
    renderProject();
    showToast("Волна переименована");
  } catch (error) {
    alert(error.message);
  }
});

document.querySelector("#waves-body").addEventListener("click", async event => {
  const button = event.target.closest("[data-wave-remove]");
  if (!button) return;
  const item = button.closest("[data-wave-id]");
  if (!confirm(`Удалить волну «${item.querySelector("input").value}»? Её строки уйдут из массива, история отмены начнётся заново.`)) return;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/waves/${item.dataset.waveId}`, { method: "DELETE" });
    renderProject();
    openWavesSheet();
    showToast("Волна удалена");
  } catch (error) {
    alert(error.message);
  }
});

document.querySelector("#waves-add").addEventListener("click", () => document.querySelector("#add-wave-file").click());

/* ---------------- Новая волна ---------------- */

document.querySelector("#add-wave-file").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || !currentProject) return;
  const body = document.querySelector("#wave-map-body");
  document.querySelector("#wave-map-apply").disabled = true;
  body.innerHTML = '<p class="muted">Читаем файл и сопоставляем переменные…</p>';
  openSheet(document.querySelector("#wave-map-sheet"));
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    wavePreview = await api(`/api/projects/${currentProject.id}/waves/stage`, { method: "POST", body: form });
    waveMapping = Object.fromEntries(wavePreview.mapping.map(row => [row.target, row.source]));
    renderWaveMapping();
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
});

function renderWaveMapping() {
  const preview = wavePreview;
  const check = preview.convergence;
  const used = new Set(Object.values(waveMapping).filter(Boolean));
  const variables = preview.wave_variables;
  const option = (row, variable) => `<option value="${escapeAttribute(variable.name)}" ${waveMapping[row.target] === variable.name ? "selected" : ""} ${used.has(variable.name) && waveMapping[row.target] !== variable.name ? "disabled" : ""}>${escapeHtml(variable.name)}${variable.label ? ` — ${escapeHtml(variable.label.slice(0, 50))}` : ""}</option>`;
  const unmatched = preview.mapping.filter(row => !waveMapping[row.target]).length;
  const rows = preview.mapping.map(row => {
    const changed = check.changed.find(item => item.name === row.target);
    return `<tr class="${waveMapping[row.target] ? "" : "is-missing"}">
      <td><code>${escapeHtml(row.target)}</code><small>${escapeHtml(row.label)}</small></td>
      <td><select data-wave-map="${escapeAttribute(row.target)}" aria-label="Переменная волны для ${escapeAttribute(row.target)}">
        <option value="">— нет в волне —</option>${variables.map(variable => option(row, variable)).join("")}
      </select>${changed ? `<small class="wave-note">${escapeHtml(changed.notes.join("; "))}</small>` : ""}</td>
      <td>${waveMapping[row.target] ? `<span class="answer-source">${WAVE_HOW[row.how] || "вручную"}</span>` : '<span class="muted">не задавался</span>'}</td>
    </tr>`;
  }).join("");
  const extra = preview.unmatched.filter(item => !used.has(item.name));
  document.querySelector("#wave-map-body").innerHTML = `
    <div class="wave-map-head">
      <label class="f">Название волны<input id="wave-label" value="${escapeAttribute(document.querySelector("#wave-label")?.value || `Волна ${Math.max(2, projectWaves().length + 1)}`)}" /></label>
      <p class="muted">Файл <b>${escapeHtml(preview.filename)}</b>: ${check.rows.toLocaleString("ru-RU")} респондентов. Сопоставлено ${preview.mapping.length - unmatched} из ${preview.mapping.length} переменных.</p>
    </div>
    ${check.blocking.length ? `<div class="wave-blocking"><b>Так добавить нельзя.</b><ul>${check.blocking.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
    ${check.weights.length ? `<div class="qn-stale">${check.weights.map(escapeHtml).join("<br>")}</div>` : ""}
    <div class="wave-map-tools">
      <button type="button" class="secondary" id="wave-ai-match" ${unmatched && extra.length ? "" : "disabled"}>Подобрать пары с ИИ</button>
      <span class="muted" id="wave-ai-status"></span>
    </div>
    <div class="wave-map-wrap"><table class="wave-map">
      <thead><tr><th>Переменная проекта</th><th>Переменная волны</th><th>Как найдена</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    ${extra.length ? `<section class="wave-extra"><h4>Новые переменные волны · ${extra.length}</h4>
      <p class="muted">Их нет в проекте. Отмеченные добавятся новыми, в прошлых волнах у них не будет значений.</p>
      ${extra.map(item => `<label class="checkbox"><input type="checkbox" data-wave-add="${escapeAttribute(item.name)}" ${item.can_add ? "checked" : "disabled"} /> <code>${escapeHtml(item.name)}</code> ${escapeHtml(item.label)}${item.can_add ? "" : " — имя занято переменной проекта"}</label>`).join("")}
    </section>` : ""}`;
  document.querySelector("#wave-map-apply").disabled = check.blocking.length > 0;
}

document.querySelector("#wave-map-body").addEventListener("change", async event => {
  const select = event.target.closest("[data-wave-map]");
  if (!select) return;
  waveMapping[select.dataset.waveMap] = select.value || null;
  try {
    wavePreview = await api(`/api/projects/${currentProject.id}/waves/stage/${wavePreview.staging_id}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mapping: waveMapping }),
    });
    renderWaveMapping();
  } catch (error) {
    alert(error.message);
  }
});

document.querySelector("#wave-map-body").addEventListener("click", async event => {
  if (!event.target.closest("#wave-ai-match")) return;
  try {
    if (!(await ensureAiAllowed())) return;
    openSheet(document.querySelector("#wave-map-sheet"));
    const job = await api(`/api/projects/${currentProject.id}/waves/stage/${wavePreview.staging_id}/ai-match`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mapping: waveMapping }),
    });
    waveMatchJob = job.job_id;
    document.querySelector("#wave-ai-status").textContent = "ИИ подбирает пары…";
    document.querySelector("#wave-ai-match").disabled = true;
    let done = job;
    while (done.status === "queued" || done.status === "running") {
      await new Promise(resolve => window.setTimeout(resolve, 1000));
      done = await api(`/api/projects/${currentProject.id}/jobs/${job.job_id}`);
    }
    if (waveMatchJob !== job.job_id) return;
    if (done.status === "failed") throw new Error(done.error || "Подбор не удался.");
    done.result.pairs.forEach(pair => { waveMapping[pair.target] = pair.source; });
    wavePreview = await api(`/api/projects/${currentProject.id}/waves/stage/${wavePreview.staging_id}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mapping: waveMapping }),
    });
    const found = new Set(done.result.pairs.map(pair => pair.target));
    wavePreview.mapping.forEach(row => { if (found.has(row.target)) row.how = "ai"; });
    renderWaveMapping();
    document.querySelector("#wave-ai-status").textContent = done.result.pairs.length
      ? `ИИ нашёл ${plural(done.result.pairs.length, "пару", "пары", "пар")} — проверьте их`
      : "ИИ не нашёл уверенных пар";
  } catch (error) {
    alert(error.message);
  }
});

document.querySelector("#wave-map-apply").addEventListener("click", async () => {
  if (!wavePreview) return;
  const apply = document.querySelector("#wave-map-apply");
  const label = document.querySelector("#wave-label").value.trim();
  if (!label) {
    document.querySelector("#wave-label").focus();
    return;
  }
  const added = [...document.querySelectorAll("#wave-map-body [data-wave-add]:checked")].map(input => input.dataset.waveAdd);
  setBusy(apply, true, "Складываем волны…");
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/waves`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ staging_id: wavePreview.staging_id, label, mapping: waveMapping, added }),
    });
    wavePreview = null;
    closeSheet();
    renderProject();
    void loadReportPreflight();
    showToast(`Волна «${label}» добавлена`);
  } catch (error) {
    document.querySelector("#wave-map-body").insertAdjacentHTML("afterbegin", `<p class="error">${escapeHtml(error.message)}</p>`);
  } finally {
    setBusy(apply, false, "Добавить волну");
  }
});

