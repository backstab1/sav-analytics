/* Вход и защита запросов (P4).

   Подключается первым: оборачивает window.fetch, поэтому остальные модули
   ничего не знают об авторизации.
   - Запрос, меняющий данные, получает заголовок X-CSRF-Token своей сессии.
     Если токен ещё не получен, запрос ждёт ответа /api/auth/state.
   - Ответ 401 уводит на страницу входа, а после входа человек вернётся
     на тот же адрес.
   - Кнопка аккаунта в шапке открывает меню: кто вошёл, личный кабинет
     (account.html), пользователи администратору и выход.
   С выключенной авторизацией (локальный прототип) модуль ничего не меняет. */

(() => {
  const nativeFetch = window.fetch.bind(window);
  const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);
  let csrfToken = null;
  let redirecting = false;

  const state = nativeFetch("/api/auth/state", { credentials: "same-origin" })
    .then(response => (response.ok ? response.json() : null))
    .catch(() => null)
    .then(payload => {
      if (payload?.auth_enabled && !payload.user) toLogin();
      csrfToken = payload?.csrf_token || null;
      window.SavAuth.state = payload;
      return payload;
    });

  function toLogin() {
    if (redirecting) return;
    redirecting = true;
    const next = encodeURIComponent(location.pathname + location.search + location.hash);
    location.assign(`/login.html?next=${next}`);
  }

  function sameOrigin(url) {
    try {
      return new URL(url, location.href).origin === location.origin;
    } catch {
      return false;
    }
  }

  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const method = (init.method || (typeof input === "string" ? "GET" : input.method) || "GET")
      .toUpperCase();
    if (!sameOrigin(url)) return nativeFetch(input, init);
    if (!SAFE.has(method)) {
      await state;
      if (csrfToken) {
        const headers = new Headers(init.headers || (typeof input === "string" ? {} : input.headers));
        headers.set("X-CSRF-Token", csrfToken);
        init = { ...init, headers };
      }
    }
    const response = await nativeFetch(input, { credentials: "same-origin", ...init });
    if (response.status === 401 && new URL(url, location.href).pathname.startsWith("/api/")) {
      toLogin();
    }
    return response;
  };

  async function post(url, body) {
    const response = await window.fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Не получилось.");
    return payload;
  }

  function initials(user) {
    const words = (user.display_name || user.username).split(/[\s._@-]+/).filter(Boolean);
    return words.slice(0, 2).map(word => word[0]).join("").toUpperCase() || "?";
  }

  const ICONS = {
    account: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="7" r="3.2" stroke="currentColor" stroke-width="1.5"/><path d="M3.8 16.5c1.2-2.7 3.4-4 6.2-4s5 1.3 6.2 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    security: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="4" y="8.5" width="12" height="8.5" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M7 8.5V6.3a3 3 0 0 1 6 0v2.2" stroke="currentColor" stroke-width="1.5"/></svg>',
    users: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="7.5" cy="7.5" r="2.7" stroke="currentColor" stroke-width="1.5"/><path d="M2.5 16c.9-2.3 2.7-3.5 5-3.5s4.1 1.2 5 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M13 5a2.5 2.5 0 0 1 0 5M14.5 12.6c1.5.4 2.5 1.5 3 3.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    logout: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M8 4H5.5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1H8M12 6.5 15.5 10 12 13.5M15.5 10H8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  /* Кнопка аккаунта в шапке: круг с инициалами. Меню — кто вошёл, личный
     кабинет, безопасность, пользователи (администратору) и выход. */
  function mountMenu(user) {
    const avatar = document.querySelector(".bar .avatar");
    if (!avatar) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "account-button";
    button.setAttribute("aria-label", `${user.display_name || user.username} — меню учётной записи`);
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    const face = document.createElement("span");
    face.className = "account-initials";
    face.textContent = initials(user);
    button.append(face);
    avatar.replaceWith(button);

    const menu = document.createElement("div");
    menu.className = "account-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Учётная запись");
    menu.hidden = true;
    const who = document.createElement("div");
    who.className = "account-who";
    const whoFace = document.createElement("span");
    whoFace.className = "account-initials large";
    whoFace.textContent = face.textContent;
    const whoText = document.createElement("span");
    whoText.className = "account-who-text";
    const whoName = document.createElement("strong");
    whoName.textContent = user.display_name || user.username;
    const whoLogin = document.createElement("small");
    whoLogin.textContent = user.role === "admin" ? `${user.username} · администратор` : user.username;
    whoText.append(whoName, whoLogin);
    who.append(whoFace, whoText);
    menu.append(who);

    const item = (label, icon, action, className = "") => {
      const entry = document.createElement(typeof action === "string" ? "a" : "button");
      if (typeof action === "string") entry.href = action;
      else {
        entry.type = "button";
        entry.addEventListener("click", action);
      }
      entry.setAttribute("role", "menuitem");
      entry.tabIndex = -1;
      entry.className = className;
      entry.innerHTML = ICONS[icon];
      entry.append(label);
      menu.append(entry);
    };
    item("Личный кабинет", "account", "/account.html#profile");
    item("Пароль и сеансы", "security", "/account.html#security");
    if (user.role === "admin") item("Пользователи", "users", "/account.html#users");
    menu.append(Object.assign(document.createElement("hr"), { role: "separator" }));
    item("Выйти", "logout", async () => {
      await post("/api/auth/logout").catch(() => {});
      location.assign("/login.html");
    }, "danger");
    document.body.append(menu);

    const entries = () => [...menu.querySelectorAll('[role="menuitem"]')];
    const open = () => {
      const rect = button.getBoundingClientRect();
      menu.style.top = `${rect.bottom + 8}px`;
      menu.style.right = `${Math.max(8, innerWidth - rect.right)}px`;
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
      entries()[0]?.focus();
    };
    const close = (restoreFocus = false) => {
      if (menu.hidden) return;
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (restoreFocus) button.focus();
    };
    button.addEventListener("click", event => {
      event.stopPropagation();
      if (menu.hidden) open();
      else close();
    });
    button.addEventListener("keydown", event => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        open();
      }
    });
    menu.addEventListener("keydown", event => {
      const list = entries();
      const index = list.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        list[(index + step + list.length) % list.length].focus();
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        list[event.key === "Home" ? 0 : list.length - 1].focus();
      } else if (event.key === "Tab") {
        close();
      }
    });
    document.addEventListener("click", event => {
      if (!menu.contains(event.target)) close();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") close(true);
    });
    window.addEventListener("resize", () => close());
  }

  window.SavAuth = {
    state: null,
    ready: state,
    post,
    // Кабинет меняет пароль (новая сессия — новый CSRF) и имя.
    setCsrf(token) {
      if (token) csrfToken = token;
    },
    updateUser(change) {
      if (window.SavAuth.state?.user) Object.assign(window.SavAuth.state.user, change);
    },
  };
  document.addEventListener("DOMContentLoaded", async () => {
    const payload = await state;
    if (payload?.auth_enabled && payload.user) mountMenu(payload.user);
  });
})();
