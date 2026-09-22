/* Поиск в длинных выпадающих списках переменных (P2, GAP-018).

   Список остаётся родным <select>: его значение, событие change и клавиатура
   те же, на них опираются редакторы и браузерные тесты. Над списком от
   SEARCH_MIN вариантов встаёт поле поиска по коду и подписи: несовпадающие
   варианты скрываются, группа без совпадений — тоже, Enter выбирает первый
   найденный, стрелка вниз переводит фокус в сам список. Списки собираются
   редакторами на лету, поэтому поле ставится наблюдателем, а не каждым
   редактором отдельно. Классический скрипт после app.js (см. решение 016). */

const SEARCH_MIN = 12;

function selectSearchLabel(select) {
  const label = select.closest("label")?.firstChild?.textContent?.trim()
    || select.getAttribute("aria-label")
    || "список";
  return `Поиск: ${label}`;
}

function filterSelectOptions(select, query) {
  const needle = query.trim().toLowerCase();
  [...select.options].forEach(option => {
    option.hidden = Boolean(needle)
      && !option.textContent.toLowerCase().includes(needle)
      && !option.value.toLowerCase().includes(needle);
  });
  select.querySelectorAll("optgroup").forEach(group => {
    group.hidden = [...group.children].every(option => option.hidden);
  });
}

function enhanceSelect(select) {
  select.classList.add("select-search-ready");
  const input = document.createElement("input");
  input.type = "search";
  input.className = "select-search";
  input.placeholder = "Поиск по коду и подписи";
  input.autocomplete = "off";
  input.setAttribute("aria-label", selectSearchLabel(select));
  input.hidden = select.hidden;
  select.before(input);
  input.addEventListener("input", () => filterSelectOptions(select, input.value));
  input.addEventListener("keydown", event => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      select.focus();
      return;
    }
    if (event.key === "Escape" && input.value) {
      event.preventDefault();
      event.stopPropagation();
      input.value = "";
      filterSelectOptions(select, "");
      return;
    }
    if (event.key !== "Enter") return;
    // Enter в поле поиска не должен отправлять форму редактора.
    event.preventDefault();
    const first = [...select.options].find(option => !option.hidden && !option.disabled && option.value);
    if (!first) return;
    select.value = first.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    input.value = "";
    filterSelectOptions(select, "");
    select.focus();
  });
}

function scanSelects() {
  document.querySelectorAll("#workspace select:not(.select-search-ready)").forEach(select => {
    if (select.options.length >= SEARCH_MIN && !select.multiple && !select.dataset.noSearch) {
      enhanceSelect(select);
    }
  });
  // Поле живёт рядом со своим списком: скрыт список — скрыто и поле.
  document.querySelectorAll("#workspace .select-search").forEach(input => {
    const select = input.nextElementSibling;
    // Присваиваем только при расхождении: запись того же значения — тоже
    // мутация, и наблюдатель крутился бы каждый кадр.
    if (select?.tagName === "SELECT" && input.hidden !== select.hidden) input.hidden = select.hidden;
  });
}

let selectScanPending = false;
new MutationObserver(() => {
  if (selectScanPending) return;
  selectScanPending = true;
  requestAnimationFrame(() => {
    selectScanPending = false;
    scanSelects();
  });
}).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
