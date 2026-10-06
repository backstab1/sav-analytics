/* Волны отдельными источниками (PQ.19, решение 034).

   Новая волна: файл → предложение сопоставления (по имени и подписи) →
   человек правит пары, может попросить ИИ подобрать несопоставленные,
   отмечает новые переменные → «Добавить волну». Сервер складывает волны в
   общий массив с переменной волны; таблицы, баннер и лист «Тренды» видят
   волны колонками. */

let wavePreview = null;
let waveMapping = {};
let waveMatchJob = null;

const WAVE_HOW = { code: "по имени", label: "по подписи", ai: "ИИ", manual: "вручную" };

function projectWaves() {
  return currentProject?.waves || [];
}

function renderWaveChrome() {
  const pill = document.querySelector("#waves-pill");
  if (!pill || !currentProject) return;
  const waves = projectWaves();
  pill.hidden = waves.length < 2;
  pill.textContent = `Волны: ${waves.length}`;
}

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
      <p class="muted">Волны сложены в общий массив с переменной «${escapeHtml(currentProject.waves_meta?.variable || "WAVE")}»: поставьте её колонкой в «Таблицах» или баннере. В книге Excel есть лист «Тренды» со сравнением с предыдущей волной.</p>`
    : '<p class="muted">В проекте одна волна — исходный файл. Добавьте следующую волну файлом, и она встанет колонкой рядом.</p>';
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

document.querySelector("#waves-pill").addEventListener("click", openWavesSheet);
document.querySelector("#open-waves").addEventListener("click", openWavesSheet);
