/* «Отменить» и «Вернуть» в проекте (P2, GAP-006). Классический скрипт после
   app.js (см. решение 016).

   Шаги хранит сервер (`project_history.py`): отмена записывает прежнюю
   конфигурацию новой ревизией, поэтому переживает перезагрузку и проходит те
   же проверки, что обычная правка. Здесь — кнопки, подсказка о том, что
   именно отменится, и Ctrl+Z вне полей ввода: внутри поля Ctrl+Z остаётся
   отменой набранного текста. */

let projectHistoryRequest = 0;

async function refreshProjectHistory() {
  const undo = document.querySelector("#undo-change");
  const redo = document.querySelector("#redo-change");
  if (!currentProject) {
    undo.disabled = true;
    redo.disabled = true;
    return;
  }
  const request = ++projectHistoryRequest;
  try {
    const response = await fetch(`/api/projects/${currentProject.id}/history`);
    if (!response.ok || request !== projectHistoryRequest) return;
    const history = await response.json();
    undo.disabled = !history.undo;
    redo.disabled = !history.redo;
    undo.title = history.undo
      ? `Отменить: ${history.undo_sections.join(", ")} (Ctrl+Z)`
      : "Отменять нечего";
    redo.title = history.redo
      ? `Вернуть: ${history.redo_sections.join(", ")} (Ctrl+Shift+Z)`
      : "Возвращать нечего";
  } catch {
    // История — удобство: без неё проект работает как прежде.
  }
}

async function stepProjectHistory(direction) {
  if (!currentProject) return;
  const button = document.querySelector(direction === "undo" ? "#undo-change" : "#redo-change");
  if (button.disabled) return;
  // Открытый редактор показывает прежнее состояние объекта: после отмены
  // он стал бы правкой поверх другой версии. Закрываем, спросив о несохранённом.
  if (!confirmDiscard(openInspectorPanel())) return;
  showInspector(null);
  button.disabled = true;
  try {
    currentProject = await api(`/api/projects/${currentProject.id}/${direction}`, { method: "POST" });
    renderProject();
    showToast(direction === "undo" ? "Изменение отменено" : "Изменение возвращено");
  } catch (error) {
    alert(error.message);
  } finally {
    void refreshProjectHistory();
  }
}

document.querySelector("#undo-change").addEventListener("click", () => { void stepProjectHistory("undo"); });
document.querySelector("#redo-change").addEventListener("click", () => { void stepProjectHistory("redo"); });

document.addEventListener("keydown", event => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
  const key = event.key.toLowerCase();
  const undo = key === "z" && !event.shiftKey;
  const redo = (key === "z" && event.shiftKey) || key === "y";
  if (!undo && !redo) return;
  const target = event.target;
  if (target.closest?.("input, textarea, select, [contenteditable='true']")) return;
  if (document.querySelector("#workspace")?.hidden || document.querySelector("dialog[open]")) return;
  event.preventDefault();
  void stepProjectHistory(undo ? "undo" : "redo");
});
