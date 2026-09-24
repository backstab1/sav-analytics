/* Источник данных проекта: выгрузка SAV с производными переменными
   и замена массива новой волной. */

/* SAV с производными скачивается через fetch: если длинные тексты ломают
   запись, сервер отказывает с объяснением, и экран предлагает выгрузку без них
   вместо страницы с JSON ошибки. */
document.querySelector("#download-derived-sav").addEventListener("click", async event => {
  event.preventDefault();
  if (!currentProject) return;
  const url = `/api/projects/${currentProject.id}/export.sav`;
  const stem = (currentProject.original_filename || "project").replace(/\.[^.]+$/, "");
  showToast("Готовим SAV…");
  try {
    let response = await fetch(url);
    if (response.status === 422 && response.headers.get("X-Long-Text")) {
      const { detail } = await response.json();
      if (!confirm(`${detail}\n\nВыгрузить без этих переменных?`)) return;
      response = await fetch(`${url}?long_text=omit`);
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `Ошибка сервера ${response.status}`);
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = `${stem}_производные.sav`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 60_000);
  } catch (error) {
    alert(error.message);
  }
});

/* Новая волна: разбор расхождений структуры до замены данных. Файл уходит
   на сервер дважды — сначала разбором, потом заменой: держать загруженное
   между запросами значило бы хранить чужие данные дольше нужного. */
let waveFile = null;

document.querySelector("#wave-file").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || !currentProject) return;
  waveFile = file;
  const body = document.querySelector("#wave-body");
  const apply = document.querySelector("#wave-apply");
  apply.disabled = true;
  body.innerHTML = '<p class="muted">Разбираем структуру нового файла…</p>';
  openSheet(document.querySelector("#wave-sheet"));
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const diff = await api(`/api/projects/${currentProject.id}/source/diff`, {
      method: "POST",
      body: form,
    });
    body.innerHTML = renderWaveDiff(diff, file);
    apply.disabled = !diff.can_replace;
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
});

function renderWaveDiff(diff, file) {
  const group = (title, items, empty) => items.length
    ? `<section class="wave-group"><h4>${title} · ${items.length}</h4><ul>${items.slice(0, 40).map(item => `<li><code>${escapeHtml(item.name)}</code> ${escapeHtml(item.label)}${item.changes.length ? ` — ${escapeHtml(item.changes.join("; "))}` : ""}</li>`).join("")}${items.length > 40 ? `<li>…ещё ${items.length - 40}</li>` : ""}</ul></section>`
    : `<section class="wave-group"><h4>${title}</h4><p class="wave-rows">${empty}</p></section>`;
  const blocking = diff.blocking.length
    ? `<div class="wave-blocking"><b>Замена невозможна.</b><ul>${diff.blocking.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>Снимите связи в структуре, баннерах или фильтрах и попробуйте снова.</div>`
    : "";
  return `${blocking}
    <p class="wave-rows">Файл <b>${escapeHtml(file.name)}</b>: респондентов было ${diff.rows_before.toLocaleString("ru-RU")}, станет ${diff.rows_after.toLocaleString("ru-RU")}. Настройки вопросов, перекодировки, фильтры, баннеры, формулы и кодификаторы сохранятся.</p>
    <div class="wave-groups">
      ${group("Новые переменные", diff.added, "новых переменных нет")}
      ${group("Исчезли", diff.removed, "ничего не исчезло")}
      ${group("Изменились", diff.changed, "подписи и коды прежние")}
    </div>`;
}

document.querySelector("#wave-apply").addEventListener("click", async () => {
  if (!waveFile || !currentProject) return;
  const apply = document.querySelector("#wave-apply");
  const body = document.querySelector("#wave-body");
  const form = new FormData();
  form.append("file", waveFile, waveFile.name);
  setBusy(apply, true, "Заменяем…");
  try {
    const result = await api(`/api/projects/${currentProject.id}/source`, {
      method: "PUT",
      body: form,
    });
    currentProject = result.project;
    waveFile = null;
    closeSheet();
    renderProject();
    void loadReportPreflight();
    showToast("Данные проекта заменены новой волной");
  } catch (error) {
    body.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>` + body.innerHTML;
  } finally {
    setBusy(apply, false, "Заменить данные");
  }
});
