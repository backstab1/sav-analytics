/* Книги отчёта (PQ.7): у каждой свои баннер, общий фильтр и настройки.
   Меню в шапке «Ручного отчёта» — тот же вид, что меню «Таблица» в
   «Таблицах». Классический скрипт после report.js (решение 016): пользуется
   currentProject, api и renderProject из app.js. Выбранная книга — та, что
   собирается; переключение записывает проект и попадает в отмену. */

const bookSwitch = document.querySelector("#book-switch");
const bookMenu = document.querySelector("#book-menu");
const bookList = document.querySelector("#book-list");
const bookRename = document.querySelector("#book-rename");
const bookRenameInput = document.querySelector("#book-rename-input");
const bookError = document.querySelector("#book-error");

function reportBooks() {
  return currentProject?.configuration?.reports || [];
}

function activeReportBook() {
  const id = currentProject?.configuration?.active_report_id;
  return reportBooks().find(book => book.id === id) || null;
}

function describeBook(book) {
  const configuration = currentProject.configuration;
  const active = book.id === configuration.active_report_id;
  const bannerId = active ? configuration.report_banner_id : book.banner_id;
  const filterId = active ? configuration.report_filter_id : book.filter_id;
  const settings = (active ? configuration.report_settings : book.settings) || {};
  const banner = configuredBanners().find(item => item.id === bannerId);
  const filter = configuredFilters().find(item => item.id === filterId);
  const parts = [banner ? banner.name : "только Total"];
  if (filter) parts.push(filter.name);
  if (settings.weight_variable || settings.calculated_weight_id) parts.push("вес");
  return parts.join(" · ");
}

function renderBookSwitch() {
  const book = activeReportBook();
  document.querySelector("#book-name").textContent = book?.name || "Отчёт";
  bookSwitch.title = book ? `Книга «${book.name}» — книги отчёта проекта` : "Книги отчёта проекта";
  if (bookMenu.hidden) return;
  const books = reportBooks();
  bookList.innerHTML = books.map(item =>
    `<button type="button" role="option" class="bld-report-item" data-book="${escapeAttribute(item.id)}" aria-selected="${item.id === book?.id}">` +
    `<span>${escapeHtml(item.name)}</span><small>${escapeHtml(describeBook(item))}</small></button>`).join("");
  bookMenu.querySelector('[data-book-action="delete"]').disabled = books.length < 2;
}

function openBookMenu() {
  bookRename.hidden = true;
  bookError.hidden = true;
  bookMenu.hidden = false;
  bookSwitch.setAttribute("aria-expanded", "true");
  renderBookSwitch();
  const box = bookSwitch.getBoundingClientRect();
  const width = bookMenu.offsetWidth;
  bookMenu.style.left = `${Math.round(Math.max(10, Math.min(box.left, window.innerWidth - width - 10)))}px`;
  bookMenu.style.top = `${Math.round(box.bottom + 6)}px`;
}

function closeBookMenu() {
  if (bookMenu.hidden) return;
  bookMenu.hidden = true;
  bookRename.hidden = true;
  bookSwitch.setAttribute("aria-expanded", "false");
}

async function bookRequest(path, method, body) {
  const options = { method };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  return api(`/api/projects/${currentProject.id}/report-books${path}`, options);
}

async function bookAction(run) {
  bookError.hidden = true;
  try {
    const project = await run();
    if (!project) return;
    currentProject = project;
    renderProject();
    renderBookSwitch();
  } catch (error) {
    showError(bookError, error);
  }
}

const BOOK_ACTIONS = {
  new: () => bookRequest("", "POST", {}),
  copy: () => bookRequest("", "POST", { source_id: activeReportBook().id }),
  // Очистка снимает баннер и фильтр и возвращает настройки по умолчанию;
  // вернуть прежнее можно кнопкой «Отменить».
  clear: () => bookRequest(`/${activeReportBook().id}/clear`, "POST"),
  delete: async () => {
    const book = activeReportBook();
    if (!book || !confirm(`Удалить книгу «${book.name}»? Вернуть её можно кнопкой «Отменить».`)) return null;
    return bookRequest(`/${book.id}`, "DELETE");
  },
};

bookSwitch.addEventListener("click", event => {
  event.stopPropagation();
  if (bookMenu.hidden) openBookMenu();
  else closeBookMenu();
});

bookMenu.addEventListener("click", event => {
  event.stopPropagation();
  const item = event.target.closest("[data-book]");
  if (item) {
    if (item.dataset.book === activeReportBook()?.id) closeBookMenu();
    else void bookAction(() => bookRequest(`/${item.dataset.book}/activate`, "POST"));
    return;
  }
  const action = event.target.closest("[data-book-action]")?.dataset.bookAction;
  if (!action) return;
  if (action === "rename") {
    bookRenameInput.value = activeReportBook()?.name || "";
    bookRename.hidden = false;
    bookRenameInput.select();
    return;
  }
  void bookAction(BOOK_ACTIONS[action]);
});

bookRename.addEventListener("submit", event => {
  event.preventDefault();
  const name = bookRenameInput.value.trim();
  const book = activeReportBook();
  if (!name || !book) return;
  void bookAction(() => bookRequest(`/${book.id}`, "PATCH", { name }));
});

bookRenameInput.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  event.stopPropagation();
  bookRename.hidden = true;
});

document.addEventListener("click", event => {
  if (!event.target.closest("#book-menu") && !event.target.closest("#book-switch")) closeBookMenu();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeBookMenu();
});
