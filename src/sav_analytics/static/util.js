/* Общие утилиты интерфейса: запросы к API с разбором конфликта ревизий,
   уведомления, форматирование и экранирование. Грузится первым: функции
   отсюда зовут все разделы, а сам файл ни от кого не зависит при загрузке. */

async function api(url, options = {}, retried = false) {
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
    return resolveRevisionConflict(url, options, payload);
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

async function resolveRevisionConflict(url, options, payload) {
  const response = await fetch(`/api/projects/${currentProject.id}`);
  if (!response.ok) throw new Error(payload.detail || "Проект изменён в другом окне.");
  const fresh = await response.json();
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

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
