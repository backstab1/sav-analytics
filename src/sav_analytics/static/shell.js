/* Оболочка экранов.
 *
 * Отвечает за две вещи и больше ни за что: какой экран показан и что в
 * этот момент лежит во втором ярусе шапки. Всё, что относится к работе с
 * проектом, остаётся в app.js — оболочка только вызывается оттуда через
 * window.Shell.
 *
 * Добавить четвёртый экран — значит дописать запись в SCREENS и секцию
 * с таким же id в index.html. Править шапку при этом не нужно.
 */
(() => {
  "use strict";

  // Конструктор перестал быть экраном: это раздел «Таблицы» рабочей области,
  // его показывает app.js, а оболочка только активирует по вызову.
  const SCREENS = { home: {}, manual: {} };

  const nav = document.querySelector("#screen-nav");
  // Кнопка «Новый проект» стоит в том же ряду, но экраном не является,
  // поэтому выбирается только по data-screen.
  const navButtons = Array.from(nav.querySelectorAll("button[data-screen]"));

  function showScreen(name) {
    if (!SCREENS[name]) return;
    navButtons.forEach(button => {
      if (button.dataset.screen === name) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    Object.keys(SCREENS).forEach(key => {
      document.querySelector(`#screen-${key}`).hidden = key !== name;
    });
    // Лендинг живёт без шапки приложения: проекта и разделов на нём нет.
    document.body.classList.toggle("is-landing", name === "home");
    window.scrollTo(0, 0);
    // Адрес экрана пишет app.js: у проекта и раздела он свой.
    document.dispatchEvent(new CustomEvent("shell:screen", { detail: name }));
  }

  navButtons.forEach(button => {
    button.addEventListener("click", () => showScreen(button.dataset.screen));
  });

  /* «Новый проект» стоит в том же ряду и сбрасывает проект силами app.js, но
     сам по себе экрана не меняет: нажатие с лендинга или из конструктора
     выглядело бы как «ничего не произошло». Переводим на ручной режим. */
  document.querySelector("#new-project").addEventListener("click", () => showScreen("manual"));

  /* Техническое меню в углу шапки панели: выгрузки и перераспознавание.
     Держим здесь, а не в app.js, потому что это поведение оболочки, а не
     работы с проектом; app.js по-прежнему слушает сами пункты по их id. */
  /* Тем же выпадающим меню набраны «Инструменты» и фильтр по статусу.
     Слушаем делегированием: фильтр перерисовывается целиком (renderSummary). */
  function closeMenus(except) {
    document.querySelectorAll(".export-menu").forEach(menu => {
      if (menu === except) return;
      const list = menu.querySelector(".export-list");
      if (!list || list.hidden) return;
      list.hidden = true;
      menu.querySelector(".menu-toggle")?.setAttribute("aria-expanded", "false");
    });
  }

  document.addEventListener("click", event => {
    const toggle = event.target.closest(".menu-toggle");
    if (toggle) {
      const menu = toggle.closest(".export-menu");
      const list = menu.querySelector(".export-list");
      closeMenus(menu);
      const open = list.hidden;
      list.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      if (open) list.querySelector("[role=menuitem]:not([hidden])")?.focus();
      return;
    }
    // Пункты закрывают меню сами: скачивание уже началось, держать его открытым
    // незачем, а «Перераспознать» показывает confirm поверх.
    if (event.target.closest(".export-list [role=menuitem]") || !event.target.closest(".export-menu")) closeMenus();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeMenus();
  });

  // Основной CTA лендинга ведёт в существующий сценарий загрузки массива:
  // отдельной регистрации у приложения пока нет, и лендинг её не обещает.
  document.querySelectorAll(".lp-start").forEach(button => {
    button.addEventListener("click", () => showScreen("manual"));
  });

  document.querySelector(".lp-tour")?.addEventListener("click", () => {
    document.querySelector("#how-it-works")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  /* Проектное в шапке: название открытого проекта и размер массива. Ярус
     хрома один, поэтому блок не «пустеет», а скрывается целиком — до
     открытия проекта показывать в нём нечего. Выгрузка живёт не здесь,
     а в ряду действий над окном списка, внутри самой рабочей области. */
  const projectChrome = document.querySelector("#project-chrome");

  window.Shell = {
    showScreen,
    /* Вызывается из app.js при входе в раздел «Таблицы». */
    activateTables() {
      TablesSection.activate();
    },
    /* Вызывается из app.js: проект открыли или закрыли. */
    setProjectOpen(open) {
      projectChrome.hidden = !open;
      if (!open) {
        TablesSection.setVariables([], {});
        closeMenus();
      }
    },
    /* Вызывается из app.js после разбора проекта. */
    setProjectVariables(items, context) {
      TablesSection.setVariables(items, context);
    },
  };

  showScreen("manual");
})();
