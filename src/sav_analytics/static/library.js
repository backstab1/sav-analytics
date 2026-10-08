/* Библиотека проектов. Классический скрипт после app.js: пользуется его
   состоянием и функциями (api, showProject); сам список грузит boot.js. */

/* Библиотека проектов: поиск и порядок — на стороне экрана, над уже
   загруженным списком; переименование, копия и корзина — на сервере. */
let libraryProjects = [];
let libraryTrash = [];
let libraryShowTrash = false;

async function loadProjects() {
  const list = document.querySelector("#project-list");
  try {
    [libraryProjects, libraryTrash] = await Promise.all([
      api("/api/projects"),
      api("/api/projects/trash"),
    ]);
    renderLibrary();
  } catch (error) {
    list.innerHTML = `<p class="error library-empty">${escapeHtml(error.message)}</p>`;
  }
}

function renderLibrary() {
  const list = document.querySelector("#project-list");
  const toggle = document.querySelector("#project-trash-toggle");
  toggle.textContent = libraryTrash.length ? `Корзина · ${libraryTrash.length}` : "Корзина";
  toggle.setAttribute("aria-pressed", String(libraryShowTrash));
  document.querySelector("#project-count").textContent = libraryProjects.length || "";
  const query = document.querySelector("#project-search").value.trim().toLowerCase();
  const order = document.querySelector("#project-sort").value;
  const shown = (libraryShowTrash ? libraryTrash : libraryProjects)
    .filter(project => !query || `${project.name} ${project.original_filename}`.toLowerCase().includes(query))
    .sort((left, right) => {
      if (order === "name") return left.name.localeCompare(right.name, "ru");
      if (order === "old") return left.created_at.localeCompare(right.created_at);
      return right.created_at.localeCompare(left.created_at);
    });
  if (!shown.length) {
    const [title, hint] = libraryShowTrash
      ? ["Корзина пуста", "Сюда попадают проекты, убранные из списка. Их можно вернуть."]
      : query
        ? ["Ничего не нашлось", "Поиск идёт по названию проекта и имени файла."]
        : ["Проектов пока нет", "Загрузите первый массив слева — проект появится здесь."];
    list.innerHTML = `<div class="library-empty"><strong>${title}</strong><span>${hint}</span></div>`;
    return;
  }
  list.innerHTML = shown.map(project => (libraryShowTrash ? trashCard(project) : projectCard(project))).join("");
}

/* Значок формата берётся из расширения исходного файла: по нему в
   длинном списке быстрее находится нужный массив, чем по дате. */
function projectBadge(project) {
  const extension = (project.original_filename.split(".").pop() || "").toLowerCase();
  const known = ["sav", "csv", "tsv", "xlsx"].includes(extension) ? extension : "sav";
  return `<span class="project-badge" data-ext="${known}" aria-hidden="true">${known.toUpperCase()}</span>`;
}

const PROJECT_ICONS = {
  rename: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m12.5 4.5 3 3L7 16H4v-3l8.5-8.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
  copy: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M13 4.5V4a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  trash: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 6h12M8 6V4.5h4V6M5.5 6l.7 9.2a1 1 0 0 0 1 .8h5.6a1 1 0 0 0 1-.8L14.5 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function shortDate(value) {
  return new Date(value).toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
}

function projectCard(project) {
  const id = escapeAttribute(project.id);
  return `<div class="project-card" data-card-id="${id}">
    <button class="project-open" type="button" data-project-id="${id}">
      ${projectBadge(project)}
      <span class="project-title"><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.original_filename)}</small></span>
      <time datetime="${escapeAttribute(project.created_at)}" title="${escapeAttribute(formatDate(project.created_at))}">${shortDate(project.created_at)}</time>
    </button>
    <span class="project-actions">
      <button type="button" class="project-action" data-project-rename="${id}" title="Переименовать" aria-label="Переименовать">${PROJECT_ICONS.rename}</button>
      <button type="button" class="project-action" data-project-copy="${id}" title="Создать копию" aria-label="Создать копию">${PROJECT_ICONS.copy}</button>
      <button type="button" class="project-action danger" data-project-trash="${id}" title="В корзину" aria-label="В корзину">${PROJECT_ICONS.trash}</button>
    </span>
  </div>`;
}

function trashCard(project) {
  return `<div class="project-card trashed">
    <span class="project-open">
      ${projectBadge(project)}
      <span class="project-title"><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.original_filename)}</small></span>
      <time datetime="${escapeAttribute(project.trashed_at)}" title="В корзине с ${escapeAttribute(formatDate(project.trashed_at))}">${shortDate(project.trashed_at)}</time>
    </span>
    <span class="project-actions">
      <button type="button" class="project-restore" data-project-restore="${escapeAttribute(project.id)}">Восстановить</button>
    </span>
  </div>`;
}

function startProjectRename(id) {
  const card = document.querySelector(`[data-card-id="${CSS.escape(id)}"]`);
  const project = libraryProjects.find(item => item.id === id);
  if (!card || !project) return;
  const form = document.createElement("form");
  form.className = "project-rename-form";
  form.innerHTML = `${projectBadge(project)}<input maxlength="200" aria-label="Новое название проекта" value="${escapeAttribute(project.name)}" />
    <button type="submit">Сохранить</button>
    <button type="button" class="secondary" data-rename-cancel>Отмена</button>`;
  card.classList.add("renaming");
  card.querySelector(".project-open").replaceWith(form);
  card.querySelector(".project-actions").hidden = true;
  const input = form.querySelector("input");
  input.focus();
  input.select();
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") renderLibrary();
  });
  form.querySelector("[data-rename-cancel]").addEventListener("click", renderLibrary);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    try {
      await api(`/api/projects/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: input.value }),
      });
      showToast("Проект переименован");
      await loadProjects();
    } catch (error) {
      showError(errorBox, error);
    }
  });
}

document.querySelector("#project-list").addEventListener("click", async event => {
  const open = event.target.closest("[data-project-id]");
  const rename = event.target.closest("[data-project-rename]");
  const copy = event.target.closest("[data-project-copy]");
  const trash = event.target.closest("[data-project-trash]");
  const restore = event.target.closest("[data-project-restore]");
  if (rename) {
    startProjectRename(rename.dataset.projectRename);
    return;
  }
  const button = open || copy || trash || restore;
  if (!button) return;
  button.disabled = true;
  try {
    if (open) {
      showProject(await api(`/api/projects/${open.dataset.projectId}`));
      return;
    }
    if (copy) {
      await api(`/api/projects/${copy.dataset.projectCopy}/duplicate`, { method: "POST" });
      showToast("Копия проекта создана");
    } else if (trash) {
      await api(`/api/projects/${trash.dataset.projectTrash}`, { method: "DELETE" });
      showToast("Проект перемещён в корзину");
    } else {
      await api(`/api/projects/trash/${restore.dataset.projectRestore}/restore`, { method: "POST" });
      showToast("Проект восстановлен");
    }
    await loadProjects();
  } catch (error) {
    showError(errorBox, error);
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#project-search").addEventListener("input", renderLibrary);
document.querySelector("#project-sort").addEventListener("change", renderLibrary);
document.querySelector("#project-trash-toggle").addEventListener("click", () => {
  libraryShowTrash = !libraryShowTrash;
  renderLibrary();
});
