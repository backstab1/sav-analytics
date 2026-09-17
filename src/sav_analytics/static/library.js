/* Библиотека проектов. Классический скрипт после app.js: пользуется его
   состоянием и функциями (api, showProject); сам список грузит boot.js. */

/* Библиотека проектов: поиск и порядок — на стороне экрана, над уже
   загруженным списком; переименование, копия и корзина — на сервере. */
let libraryProjects = [];
let libraryTrash = [];
let libraryShowTrash = false;

async function loadProjects() {
  const library = document.querySelector("#project-library");
  const list = document.querySelector("#project-list");
  try {
    [libraryProjects, libraryTrash] = await Promise.all([
      api("/api/projects"),
      api("/api/projects/trash"),
    ]);
    library.hidden = !libraryProjects.length && !libraryTrash.length;
    renderLibrary();
  } catch (error) {
    library.hidden = false;
    list.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

function renderLibrary() {
  const list = document.querySelector("#project-list");
  const toggle = document.querySelector("#project-trash-toggle");
  toggle.textContent = libraryTrash.length ? `Корзина · ${libraryTrash.length}` : "Корзина";
  toggle.setAttribute("aria-pressed", String(libraryShowTrash));
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
    const empty = libraryShowTrash ? "Корзина пуста." : query ? "Ничего не нашлось." : "Проектов пока нет.";
    list.innerHTML = `<p class="muted library-empty">${empty}</p>`;
    return;
  }
  list.innerHTML = shown.map(project => (libraryShowTrash ? trashCard(project) : projectCard(project))).join("");
}

function projectCard(project) {
  return `<div class="project-card" data-card-id="${escapeAttribute(project.id)}">
    <button class="project-open" type="button" data-project-id="${escapeAttribute(project.id)}">
      <span><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.original_filename)}</small></span>
      <time>${formatDate(project.created_at)}</time>
    </button>
    <span class="project-actions">
      <button type="button" class="text-button" data-project-rename="${escapeAttribute(project.id)}">Переименовать</button>
      <button type="button" class="text-button" data-project-copy="${escapeAttribute(project.id)}">Копия</button>
      <button type="button" class="text-button danger-text" data-project-trash="${escapeAttribute(project.id)}">В корзину</button>
    </span>
  </div>`;
}

function trashCard(project) {
  return `<div class="project-card trashed">
    <span class="project-open">
      <span><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.original_filename)} · в корзине с ${formatDate(project.trashed_at)}</small></span>
    </span>
    <span class="project-actions">
      <button type="button" class="text-button" data-project-restore="${escapeAttribute(project.id)}">Восстановить</button>
    </span>
  </div>`;
}

function startProjectRename(id) {
  const card = document.querySelector(`[data-card-id="${CSS.escape(id)}"]`);
  const project = libraryProjects.find(item => item.id === id);
  if (!card || !project) return;
  const form = document.createElement("form");
  form.className = "project-rename-form";
  form.innerHTML = `<input maxlength="200" aria-label="Новое название проекта" value="${escapeAttribute(project.name)}" />
    <button type="submit">Сохранить</button>
    <button type="button" class="secondary" data-rename-cancel>Отмена</button>`;
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
