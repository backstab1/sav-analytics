/* Общие утилиты интерфейса: запросы к API с разбором конфликта ревизий,
   уведомления, форматирование и экранирование. Грузится первым: функции
   отсюда зовут все разделы, а сам файл ни от кого не зависит при загрузке. */

/* Свои записи вкладки. Две записи, ушедшие разом (отложенное сохранение
   «Таблиц» и «Сохранить» вопроса), несут одну ревизию, и вторая получает 409,
   хотя никакого другого окна нет. Здесь помнятся ревизии, которые создали
   записи этой вкладки, и записи ещё в пути: конфликт, целиком вызванный
   своими же записями, разбирается без диалога. */
const ownWrites = { projectId: null, revisions: new Set(), pending: new Map() };

async function api(url, options = {}, retried = false) {
  const method = (options.method || "GET").toUpperCase();
  const projectPrefix = currentProject ? `/api/projects/${currentProject.id}` : null;
  if (!projectPrefix || !url.startsWith(projectPrefix) || method === "GET" || retried) {
    return sendApi(url, options, retried);
  }
  const projectId = currentProject.id;
  if (ownWrites.projectId !== projectId) {
    ownWrites.projectId = projectId;
    ownWrites.revisions.clear();
  }
  const token = {};
  const write = sendApi(url, options, false, token).then(payload => {
    const revision = payload?.configuration?.revision;
    if (payload?.id === projectId && revision && ownWrites.projectId === projectId) {
      ownWrites.revisions.add(revision);
    }
    return payload;
  });
  const settled = write.catch(() => {});
  ownWrites.pending.set(token, settled);
  settled.then(() => ownWrites.pending.delete(token));
  return write;
}

async function sendApi(url, options = {}, retried = false, token = null) {
  const method = (options.method || "GET").toUpperCase();
  const projectPrefix = currentProject ? `/api/projects/${currentProject.id}` : null;
  const revision = currentProject?.configuration?.revision;
  if (projectPrefix && url.startsWith(projectPrefix) && method !== "GET" && revision) {
    const headers = new Headers(options.headers || {});
    headers.set("If-Match", String(revision));
    options = { ...options, headers };
  }
  const response = await fetch(url, options);
  const responseText = await response.text();
  let payload;
  try {
    payload = responseText ? JSON.parse(responseText) : {};
  } catch {
    payload = { detail: responseText || `Ошибка сервера ${response.status}` };
  }
  if (response.status === 409 && payload.error_code === "CONFIGURATION_CONFLICT" && currentProject && !retried) {
    return resolveRevisionConflict(url, options, payload, token);
  }
  if (!response.ok) {
    // Код ошибки нужен тем, кто предлагает следующий шаг по её виду.
    const error = new Error(payload.detail || "Запрос не выполнен.");
    error.code = payload.error_code;
    throw error;
  }
  // Любая правка проекта — новый шаг истории: кнопки отмены узнают об этом сразу.
  if (projectPrefix && url.startsWith(projectPrefix) && method !== "GET") {
    window.setTimeout(() => refreshProjectHistory(), 0);
  }
  return payload;
}

/* Конфликт ревизий (P2, GAP-024). Сервер отвечает 409, когда проект успели
   изменить в другой вкладке. Разбор идёт здесь, а не в каждом редакторе:
   приложение перечитывает проект, называет изменившиеся разделы и даёт
   выбрать. «Повторить» отправляет тот же запрос на новой ревизии, остальные
   варианты заканчиваются ошибкой вызывающему — и его редактор с вводом
   остаётся открытым, как при любой другой ошибке сохранения. */
const CONFLICT_SECTIONS = {
  questions: "структура вопросов",
  recodings: "перекодировки",
  banners: "баннеры",
  filters: "фильтры и базы",
  calculated_weights: "рассчитанные веса",
  report_settings: "настройки отчёта",
  report_banner_id: "баннер отчёта",
  report_filter_id: "общий фильтр",
  formulas: "формулы",
  codeframes: "кодификаторы открытых ответов",
  analysis_cards: "карточки анализа",
  analysis_models: "модели анализа",
  table_reports: "таблицы",
};

function changedConfigurationSections(before, after) {
  const skip = new Set(["revision", "updated_at"]);
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  const changed = [];
  keys.forEach(key => {
    if (skip.has(key)) return;
    if (JSON.stringify(before?.[key]) === JSON.stringify(after?.[key])) return;
    const name = CONFLICT_SECTIONS[key] || "прочие настройки";
    if (!changed.includes(name)) changed.push(name);
  });
  return changed;
}

function askConflictChoice(sections, mine, theirs) {
  const dialog = document.querySelector("#conflict-dialog");
  document.querySelector("#conflict-summary").textContent =
    `Вы редактировали версию ${mine}, в проекте уже версия ${theirs}. В другом окне изменились:`;
  document.querySelector("#conflict-sections").innerHTML = (sections.length ? sections : ["данные проекта"])
    .map(name => `<li>${escapeHtml(name)}</li>`).join("");
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue || "cancel"), { once: true });
    dialog.returnValue = "";
    dialog.showModal();
    document.querySelector("#conflict-retry").focus();
  });
}

async function resolveRevisionConflict(url, options, payload, token) {
  // Сначала дождаться своих записей в пути: ревизию, которую создала уже
  // применённая сервером своя запись, вкладка узнает только из её ответа.
  const sent = Number(new Headers(options.headers).get("If-Match"));
  const others = [...ownWrites.pending].filter(([key]) => key !== token).map(([, settled]) => settled);
  await Promise.all(others.map(settled => Promise.race([settled, delay(5000)])));
  const response = await fetch(`/api/projects/${currentProject.id}`);
  if (!response.ok) throw new Error(payload.detail || "Проект изменён в другом окне.");
  const fresh = await response.json();
  if (ownChangesOnly(fresh, sent)) {
    // Проект менялся только этой вкладкой: повтор на новой ревизии ничего
    // чужого не перезапишет.
    currentProject = fresh;
    return sendApi(url, options, false, token);
  }
  const choice = await askConflictChoice(
    changedConfigurationSections(currentProject.configuration, fresh.configuration),
    currentProject.configuration.revision,
    fresh.configuration.revision,
  );
  if (choice === "retry") {
    // Новая ревизия подставится в If-Match при повторе; проект целиком
    // вызывающий получит ответом, как при обычном сохранении.
    currentProject = fresh;
    return api(url, options, true);
  }
  if (choice === "reload") {
    currentProject = fresh;
    renderProject();
    throw new Error("Проект перезагружен с изменениями из другого окна. Ваш ввод остался в редакторе — проверьте и сохраните снова.");
  }
  throw new Error(payload.detail || "Сохранение отменено: проект изменён в другом окне.");
}

function ownChangesOnly(fresh, sent) {
  const latest = fresh.configuration?.revision;
  if (!sent || !latest || latest <= sent || ownWrites.projectId !== fresh.id) return false;
  for (let revision = sent + 1; revision <= latest; revision += 1) {
    if (!ownWrites.revisions.has(revision)) return false;
  }
  return true;
}

function delay(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function setBusy(button, busy, text) {
  button.disabled = busy;
  button.textContent = text;
}

function showError(element, error) {
  element.textContent = error.message;
  element.hidden = false;
}

function showToast(message) {
  const container = document.querySelector("#toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.innerHTML = '<svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden="true"><circle cx="9" cy="9" r="8" stroke="#2eae68" stroke-width="1.6"/><path d="M5.5 9.5L7.5 11.5L12.5 6.5" stroke="#2eae68" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  toast.append(document.createTextNode(message));
  container.append(toast);
  window.setTimeout(() => {
    toast.classList.add("toast-out");
    toast.addEventListener("animationend", () => toast.remove(), { once: true });
  }, 2100);
}

function formatDate(value) {
  return new Date(value).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

function formatPercent(value) {
  return value == null ? "—" : `${(value * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
}

function formatRange(category) {
  const lower = category.lower == null ? "−∞" : Number(category.lower).toLocaleString("ru-RU");
  const upper = category.upper == null ? "+∞" : Number(category.upper).toLocaleString("ru-RU");
  return `${lower}…${upper}`;
}

function formatRecodeCategory(recoding, category) {
  if ((recoding.mode || "ranges") === "categories") {
    return `${category.values.length} исходных знач.`;
  }
  return formatRange(category);
}

// Подписи пунктов блока без общего начала: «Что вы обычно заказываете:
// эспрессо» → «эспрессо». Режется только начало, которое кончается
// разделителем (двоеточие, тире, косая черта): у «Очень доволен» и «Очень
// недоволен» общее «Очень» — часть ответа, а не вопрос.
function stripCommonPrefix(labels) {
  if (labels.length < 2) return labels;
  const words = labels.map(label => String(label).split(/\s+/));
  let common = 0;
  while (words.every(parts => parts.length > common + 1 && parts[common] === words[0][common])) common += 1;
  while (common > 0 && !/[:—–\-/]$/.test(words[0][common - 1])) common -= 1;
  if (!common) return labels;
  return words.map(parts => parts.slice(common).join(" "));
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
