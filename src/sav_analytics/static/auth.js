/* Вход и защита запросов (P4).

   Подключается первым: оборачивает window.fetch, поэтому остальные модули
   ничего не знают об авторизации.
   - Запрос, меняющий данные, получает заголовок X-CSRF-Token своей сессии.
     Если токен ещё не получен, запрос ждёт ответа /api/auth/state.
   - Ответ 401 уводит на страницу входа, а после входа человек вернётся
     на тот же адрес.
   - Аватар в шапке открывает меню: имя, пользователи (администратору),
     смена пароля и выход.
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
    const words = (user.display_name || user.username).split(/\s+/).filter(Boolean);
    return words.slice(0, 2).map(word => word[0]).join("").toUpperCase() || "?";
  }

  function mountMenu(user) {
    const avatar = document.querySelector(".bar .avatar");
    if (!avatar) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "avatar avatar-button";
    button.textContent = initials(user);
    button.title = `${user.display_name} — меню учётной записи`;
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    avatar.replaceWith(button);

    const menu = document.createElement("div");
    menu.className = "account-menu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    const who = document.createElement("div");
    who.className = "account-who";
    who.textContent = `${user.display_name} · ${user.role === "admin" ? "администратор" : "аналитик"}`;
    menu.append(who);
    const item = (label, action) => {
      const entry = document.createElement("button");
      entry.type = "button";
      entry.setAttribute("role", "menuitem");
      entry.textContent = label;
      entry.addEventListener("click", action);
      menu.append(entry);
    };
    if (user.role === "admin") item("Пользователи и журнал", () => location.assign("/admin.html"));
    item("Сменить пароль", async () => {
      const current = prompt("Текущий пароль");
      if (!current) return;
      const fresh = prompt("Новый пароль, не короче 10 символов");
      if (!fresh) return;
      try {
        const payload = await post("/api/auth/password", { current, new: fresh });
        csrfToken = payload.csrf_token;
        alert("Пароль изменён. Другие сессии завершены.");
      } catch (error) {
        alert(error.message);
      }
    });
    item("Выйти", async () => {
      await post("/api/auth/logout").catch(() => {});
      location.assign("/login.html");
    });
    document.body.append(menu);

    const close = () => {
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
    };
    button.addEventListener("click", event => {
      event.stopPropagation();
      const rect = button.getBoundingClientRect();
      menu.style.top = `${rect.bottom + 6}px`;
      menu.style.right = `${Math.max(8, innerWidth - rect.right)}px`;
      menu.hidden = !menu.hidden;
      button.setAttribute("aria-expanded", String(!menu.hidden));
    });
    document.addEventListener("click", event => {
      if (!menu.contains(event.target)) close();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") close();
    });
  }

  window.SavAuth = { state: null, ready: state, post };
  document.addEventListener("DOMContentLoaded", async () => {
    const payload = await state;
    if (payload?.auth_enabled && payload.user) mountMenu(payload.user);
  });
})();
