/* Личный кабинет: профиль, пароль, сеансы, история входов и настройки
   браузера; администратору — пользователи и журнал. Права проверяет
   сервер, страница — только интерфейс к /api/auth и /api/admin. */

(() => {
  const $ = selector => document.querySelector(selector);
  const SECTIONS = ["profile", "security", "settings", "users", "audit"];
  const ADMIN_SECTIONS = new Set(["users", "audit"]);
  const START_SCREEN_KEY = "sav-analytics:start-screen";
  const LOCAL_PREFIX = "sav-analytics:";
  const dateTime = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });
  const dateOnly = new Intl.DateTimeFormat("ru-RU", { dateStyle: "long" });
  const relative = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });

  let me = null;
  let minLength = 10;

  // Общее ------------------------------------------------------------------------

  const when = value => (value ? dateTime.format(new Date(value)) : "—");

  function ago(value) {
    if (!value) return "—";
    const seconds = (new Date(value) - Date.now()) / 1000;
    const abs = Math.abs(seconds);
    if (abs < 60) return "только что";
    if (abs < 3600) return relative.format(Math.round(seconds / 60), "minute");
    if (abs < 86400) return relative.format(Math.round(seconds / 3600), "hour");
    if (abs < 86400 * 7) return relative.format(Math.round(seconds / 86400), "day");
    return when(value);
  }

  function initials(name) {
    const words = (name || "").split(/[\s._@-]+/).filter(Boolean);
    return words.slice(0, 2).map(word => word[0]).join("").toUpperCase() || "?";
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof payload.detail === "string" ? payload.detail : "Не получилось. Повторите позже.");
    }
    return payload;
  }

  const json = (method, body) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

  function flash(target, text, tone = "ok") {
    target.textContent = text;
    target.dataset.tone = tone;
    clearTimeout(target.flashTimer);
    if (tone === "ok") target.flashTimer = setTimeout(() => (target.textContent = ""), 4000);
  }

  function showError(box, message) {
    box.textContent = message || "";
    box.hidden = !message;
  }

  function busy(button, on, label) {
    if (on) {
      button.dataset.label = button.textContent.trim();
      button.textContent = label;
    } else if (button.dataset.label) {
      button.textContent = button.dataset.label;
    }
    button.disabled = on;
    button.setAttribute("aria-busy", String(on));
  }

  // Показать пароль — у каждого поля с кнопкой-глазом.
  const EYE = '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M2 10s3-5.5 8-5.5S18 10 18 10s-3 5.5-8 5.5S2 10 2 10Z" stroke="currentColor" stroke-width="1.5"/><circle cx="10" cy="10" r="2.3" stroke="currentColor" stroke-width="1.5"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 3l14 14M8.3 4.8A8.6 8.6 0 0 1 10 4.5c5 0 8 5.5 8 5.5a14 14 0 0 1-2.4 3.1M5.1 6.3A13.5 13.5 0 0 0 2 10s3 5.5 8 5.5c1.3 0 2.5-.4 3.5-.9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
  document.querySelectorAll(".acc-eye").forEach(button => {
    button.innerHTML = EYE;
    button.addEventListener("click", () => {
      const input = button.parentElement.querySelector("input");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.innerHTML = show ? EYE_OFF : EYE;
      button.setAttribute("aria-pressed", String(show));
      button.setAttribute("aria-label", show ? "Скрыть пароль" : "Показать пароль");
      input.focus();
    });
  });

  // Разделы ----------------------------------------------------------------------

  function showSection() {
    let name = location.hash.slice(1);
    if (!SECTIONS.includes(name) || (ADMIN_SECTIONS.has(name) && me?.role !== "admin")) name = "profile";
    document.querySelectorAll(".acc-section").forEach(section => {
      section.hidden = section.dataset.section !== name;
    });
    document.querySelectorAll(".acc-nav a").forEach(link => {
      if (link.dataset.section === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    const title = document.querySelector(`#section-${name} h1`)?.textContent;
    document.title = `${title ? `${title} — ` : ""}Личный кабинет — sav-analytics`;
    if (name === "security") loadSecurity();
    if (name === "users") loadUsers();
    if (name === "audit") loadAudit();
  }

  window.addEventListener("hashchange", showSection);

  // Профиль ----------------------------------------------------------------------

  const profileForm = $("#profile-form");
  const nameInput = $("#display-name");
  const profileSave = $("#profile-save");

  function renderIdentity() {
    const name = me.display_name || me.username;
    $("#side-name").textContent = name;
    $("#side-login").textContent = me.username;
    $("#side-avatar").textContent = initials(name);
    $("#profile-avatar").textContent = initials(nameInput.value || name);
    $("#fact-login").textContent = me.username;
    $("#fact-access").textContent = me.role === "admin"
      ? "Администратор — все проекты, пользователи и журнал"
      : "Аналитик — все проекты";
    $("#fact-created").textContent = me.created_at ? dateOnly.format(new Date(me.created_at)) : "—";
    $("#fact-last-login").textContent = when(me.last_login_at);
    window.SavAuth.updateUser?.({ display_name: name });
  }

  nameInput.addEventListener("input", () => {
    const value = nameInput.value.trim();
    profileSave.disabled = !value || value === me.display_name;
    $("#profile-avatar").textContent = initials(value || me.username);
  });

  profileForm.addEventListener("submit", async event => {
    event.preventDefault();
    const status = $("#profile-status");
    const value = nameInput.value.trim();
    if (!value) {
      flash(status, "Имя не может быть пустым.", "error");
      nameInput.focus();
      return;
    }
    busy(profileSave, true, "Сохраняем…");
    try {
      const { user } = await request("/api/auth/account", json("PATCH", { display_name: value }));
      me = { ...me, ...user };
      nameInput.value = me.display_name;
      renderIdentity();
      flash(status, "Сохранено.");
    } catch (error) {
      flash(status, error.message, "error");
    } finally {
      busy(profileSave, false);
      profileSave.disabled = nameInput.value.trim() === me.display_name;
    }
  });

  // Пароль -----------------------------------------------------------------------

  const passwordForm = $("#password-form");
  const passwordSave = $("#password-save");

  function passwordRules() {
    const { current, new: fresh, repeat } = passwordForm.elements;
    const rules = {
      length: fresh.value.length >= minLength,
      differs: Boolean(fresh.value) && fresh.value !== current.value,
      match: Boolean(repeat.value) && fresh.value === repeat.value,
    };
    for (const [rule, ok] of Object.entries(rules)) {
      const item = passwordForm.querySelector(`[data-rule="${rule}"]`);
      item.classList.toggle("ok", ok);
      item.classList.toggle("bad", !ok && Boolean(rule === "match" ? repeat.value : fresh.value));
    }
    passwordSave.disabled = !current.value || !Object.values(rules).every(Boolean);
  }

  passwordForm.addEventListener("input", () => {
    showError($("#password-error"), "");
    passwordRules();
  });

  passwordForm.addEventListener("submit", async event => {
    event.preventDefault();
    passwordRules();
    if (passwordSave.disabled) return;
    const { current, new: fresh } = passwordForm.elements;
    busy(passwordSave, true, "Меняем…");
    try {
      const payload = await window.SavAuth.post("/api/auth/password", {
        current: current.value,
        new: fresh.value,
      });
      window.SavAuth.setCsrf?.(payload.csrf_token);
      passwordForm.reset();
      passwordForm.querySelectorAll(".acc-secret input").forEach(input => (input.type = "password"));
      flash($("#password-status"), "Пароль изменён. Другие сеансы завершены.");
      loadSecurity();
    } catch (error) {
      showError($("#password-error"), error.message);
      if (/текущий/i.test(error.message)) current.select();
    } finally {
      busy(passwordSave, false);
      passwordRules();
    }
  });

  // Сеансы и история -------------------------------------------------------------

  function describeAgent(agent) {
    const browser =
      /Edg\//.test(agent) ? "Edge"
      : /OPR\//.test(agent) ? "Opera"
      : /YaBrowser\//.test(agent) ? "Яндекс Браузер"
      : /Firefox\//.test(agent) ? "Firefox"
      : /Chrome\//.test(agent) ? "Chrome"
      : /Safari\//.test(agent) ? "Safari"
      : "";
    const system =
      /Windows/.test(agent) ? "Windows"
      : /Mac OS X|Macintosh/.test(agent) ? "macOS"
      : /Android/.test(agent) ? "Android"
      : /iPhone|iPad/.test(agent) ? "iOS"
      : /Linux/.test(agent) ? "Linux"
      : "";
    if (browser && system) return `${browser} · ${system}`;
    return browser || system || "Неизвестное устройство";
  }

  async function closeSession(id, item) {
    item.classList.add("closing");
    try {
      await request(`/api/auth/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    } catch (error) {
      item.classList.remove("closing");
      flash($("#password-status"), error.message, "error");
    }
    loadSessions();
  }

  async function loadSessions() {
    const list = $("#sessions");
    try {
      const { sessions } = await request("/api/auth/sessions");
      list.replaceChildren(
        ...sessions.map(session => {
          const item = element("li", session.current ? "current" : "");
          const icon = element("span", "acc-device");
          icon.innerHTML = /Android|iPhone|iPad|Mobile/.test(session.user_agent)
            ? '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="6" y="2.5" width="8" height="15" rx="1.8" stroke="currentColor" stroke-width="1.5"/><path d="M9 15h2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>'
            : '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="2.5" y="4" width="15" height="9.5" rx="1.5" stroke="currentColor" stroke-width="1.5"/><path d="M7 16.5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
          const text = element("span", "acc-session-text");
          const title = element("strong", "", describeAgent(session.user_agent));
          if (session.current) title.append(" ", element("em", "acc-badge ok", "этот сеанс"));
          text.append(
            title,
            element("small", "", [
              session.ip || "адрес неизвестен",
              session.current ? "активен сейчас" : `активность ${ago(session.last_seen_at)}`,
              `вход ${when(session.created_at)}`,
            ].join(" · ")),
          );
          item.append(icon, text);
          if (!session.current) {
            const close = element("button", "acc-btn small", "Завершить");
            close.type = "button";
            close.addEventListener("click", () => closeSession(session.id, item));
            item.append(close);
          }
          return item;
        }),
      );
      $("#close-others").hidden = sessions.filter(session => !session.current).length === 0;
    } catch (error) {
      list.replaceChildren(element("li", "acc-empty", error.message));
    }
  }

  const ACTIVITY = {
    "auth.login": ["Вход", ""],
    "auth.login_failed": ["Неудачная попытка входа", "bad"],
    "auth.login_blocked": ["Вход временно заблокирован после серии ошибок", "bad"],
    "auth.logout": ["Выход", ""],
    "auth.password": ["Смена пароля", "warn"],
    "auth.account": ["Изменён профиль", ""],
    "auth.setup": ["Создана учётная запись администратора", ""],
    "auth.sessions_closed": ["Завершены другие сеансы", "warn"],
  };

  async function loadActivity() {
    const list = $("#activity");
    try {
      const { entries } = await request("/api/auth/activity");
      if (!entries.length) {
        list.replaceChildren(element("li", "acc-empty", "Записей пока нет."));
        return;
      }
      list.replaceChildren(
        ...entries.map(entry => {
          let [label, tone] = ACTIVITY[entry.action] || [entry.action, ""];
          // Отклонённая смена пароля или профиля — тоже в истории, но как неудача.
          if (entry.status >= 400) [label, tone] = [`${label} — не удалось`, "bad"];
          const item = element("li", tone);
          item.append(
            element("span", "acc-dot"),
            element("span", "acc-activity-label", label),
            element("span", "acc-activity-ip", entry.ip || ""),
            element("time", "", when(entry.at)),
          );
          item.querySelector("time").dateTime = entry.at || "";
          return item;
        }),
      );
    } catch (error) {
      list.replaceChildren(element("li", "acc-empty", error.message));
    }
  }

  function loadSecurity() {
    loadSessions();
    loadActivity();
  }

  $("#close-others").addEventListener("click", async event => {
    const button = event.currentTarget;
    busy(button, true, "Завершаем…");
    try {
      const { closed } = await request("/api/auth/sessions/close-others", json("POST"));
      flash($("#password-status"), closed ? `Завершено сеансов: ${closed}.` : "Других сеансов нет.");
    } catch (error) {
      flash($("#password-status"), error.message, "error");
    } finally {
      busy(button, false);
      loadSecurity();
    }
  });

  // Настройки --------------------------------------------------------------------

  function readLocal(key, fallback) {
    try {
      return localStorage.getItem(key) ?? fallback;
    } catch {
      return fallback;
    }
  }

  const startInputs = document.querySelectorAll('input[name="start-screen"]');
  const startValue = readLocal(START_SCREEN_KEY, "projects");
  startInputs.forEach(input => {
    input.checked = input.value === startValue;
    input.addEventListener("change", () => {
      try {
        localStorage.setItem(START_SCREEN_KEY, input.value);
        flash($("#settings-status"), "Сохранено.");
      } catch {
        flash($("#settings-status"), "Браузер не даёт сохранить настройку.", "error");
      }
    });
  });

  $("#forget-local").addEventListener("click", () => {
    let removed = 0;
    try {
      for (const key of Object.keys(localStorage)) {
        if (key.startsWith(LOCAL_PREFIX) && key !== START_SCREEN_KEY) {
          localStorage.removeItem(key);
          removed += 1;
        }
      }
      flash($("#forget-status"), removed ? "Сброшено." : "Запомненного нет.");
    } catch {
      flash($("#forget-status"), "Браузер не даёт доступа к хранилищу.", "error");
    }
  });

  // Пользователи -----------------------------------------------------------------

  const addCard = $("#add-user-card");
  const userForm = $("#user-form");

  function toggleAdd(open) {
    addCard.hidden = !open;
    $("#add-user-open").hidden = open;
    showError($("#user-error"), "");
    if (open) userForm.username.focus();
    else userForm.reset();
  }

  $("#add-user-open").addEventListener("click", () => toggleAdd(true));
  $("#add-user-cancel").addEventListener("click", () => toggleAdd(false));

  userForm.addEventListener("submit", async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(userForm));
    if (!data.username.trim() || data.password.length < minLength) {
      showError($("#user-error"), !data.username.trim()
        ? "Укажите имя входа."
        : `Пароль — не короче ${minLength} символов.`);
      return;
    }
    const submit = userForm.querySelector('[type="submit"]');
    busy(submit, true, "Создаём…");
    try {
      const user = await request("/api/admin/users", json("POST", data));
      toggleAdd(false);
      flash($("#users-status"), `Пользователь ${user.username} создан.`);
      loadUsers();
    } catch (error) {
      showError($("#user-error"), error.message);
    } finally {
      busy(submit, false);
    }
  });

  async function updateUser(user, change, message) {
    try {
      await request(`/api/admin/users/${user.id}`, json("PATCH", change));
      flash($("#users-status"), message);
    } catch (error) {
      flash($("#users-status"), error.message, "error");
    }
    loadUsers();
  }

  // Отключение — в два нажатия: первое спрашивает подтверждение на самой кнопке.
  function confirmButton(button, question, action) {
    button.addEventListener("click", () => {
      if (button.dataset.armed) {
        action();
        return;
      }
      button.dataset.armed = "1";
      button.dataset.label = button.textContent;
      button.textContent = question;
      button.classList.add("danger");
      setTimeout(() => {
        delete button.dataset.armed;
        button.textContent = button.dataset.label;
        button.classList.remove("danger");
      }, 4000);
    });
  }

  async function loadUsers() {
    const body = $("#users");
    try {
      const { users } = await request("/api/admin/users");
      body.replaceChildren(
        ...users.map(user => {
          const row = element("tr", user.active ? "" : "inactive");
          const who = element("td");
          const person = element("span", "acc-person");
          const avatar = element("span", "acc-avatar", initials(user.display_name || user.username));
          avatar.setAttribute("aria-hidden", "true");
          const names = element("span", "acc-person-text");
          const title = element("strong", "", user.display_name || user.username);
          if (user.id === me.id) title.append(" ", element("em", "acc-badge", "вы"));
          if (user.role === "admin") title.append(" ", element("em", "acc-badge", "администратор"));
          names.append(title, element("small", "", user.username));
          person.append(avatar, names);
          who.append(person);

          const state = element("td");
          state.append(element("span", `acc-pill ${user.active ? "ok" : "off"}`, user.active ? "Активен" : "Отключён"));

          const seen = element("td", "muted",
            user.id === me.id ? "сейчас в системе"
            : user.last_login_at ? ago(user.last_login_at) : "ещё не входил");
          if (user.last_login_at) seen.title = when(user.last_login_at);

          const actions = element("td", "acc-row-actions");
          const reset = element("button", "acc-btn small", "Новый пароль");
          reset.type = "button";
          reset.addEventListener("click", () => openReset(user));
          actions.append(reset);
          if (user.id !== me.id) {
            const toggle = element("button", "acc-btn small", user.active ? "Отключить" : "Включить");
            toggle.type = "button";
            if (user.active) {
              confirmButton(toggle, "Точно отключить?", () =>
                updateUser(user, { active: false }, `${user.username}: вход отключён, открытые сеансы закрыты.`));
            } else {
              toggle.addEventListener("click", () =>
                updateUser(user, { active: true }, `${user.username}: вход снова разрешён.`));
            }
            actions.append(toggle);
          }
          row.append(who, state, seen, actions);
          return row;
        }),
      );
    } catch (error) {
      const row = element("tr");
      const cell = element("td", "acc-empty", error.message);
      cell.colSpan = 4;
      row.append(cell);
      body.replaceChildren(row);
    }
  }

  const resetDialog = $("#reset-dialog");
  const resetForm = $("#reset-form");
  let resetTarget = null;

  function openReset(user) {
    resetTarget = user;
    resetForm.reset();
    showError($("#reset-error"), "");
    $("#reset-lead").textContent =
      `Для ${user.display_name || user.username} (${user.username}). Открытые сеансы этой учётной записи закроются, пароль передайте лично.`;
    resetDialog.showModal();
    resetForm.password.focus();
  }

  $("#reset-cancel").addEventListener("click", () => resetDialog.close());
  resetForm.addEventListener("submit", async event => {
    event.preventDefault();
    const password = resetForm.password.value;
    if (password.length < minLength) {
      showError($("#reset-error"), `Пароль — не короче ${minLength} символов.`);
      return;
    }
    try {
      await request(`/api/admin/users/${resetTarget.id}`, json("PATCH", { password }));
      resetDialog.close();
      flash($("#users-status"), `Пароль для ${resetTarget.username} задан.`);
      loadUsers();
    } catch (error) {
      showError($("#reset-error"), error.message);
    }
  });

  // Журнал -----------------------------------------------------------------------

  async function loadAudit(filter = {}) {
    const body = $("#audit");
    const query = new URLSearchParams({ limit: "200" });
    for (const [key, value] of Object.entries(filter)) if (value) query.set(key, value);
    try {
      const { entries } = await request(`/api/admin/audit?${query}`);
      if (!entries.length) {
        const row = element("tr");
        const cell = element("td", "acc-empty", "Записей нет.");
        cell.colSpan = 6;
        row.append(cell);
        body.replaceChildren(row);
        return;
      }
      body.replaceChildren(
        ...entries.map(entry => {
          const row = element("tr");
          const [label] = ACTIVITY[entry.action] || [
            entry.action === "change" ? "Изменение" : entry.action === "download" ? "Скачивание" : entry.action,
          ];
          row.append(
            element("td", "nowrap", when(entry.at)),
            element("td", "", entry.username || "—"),
            element("td", "", label),
            element("td", "mono", [entry.method, entry.path].filter(Boolean).join(" ") || "—"),
            element("td", entry.status >= 400 ? "bad" : "", entry.status ?? "—"),
            element("td", "muted", entry.ip || "—"),
          );
          return row;
        }),
      );
    } catch (error) {
      const row = element("tr");
      const cell = element("td", "acc-empty", error.message);
      cell.colSpan = 6;
      row.append(cell);
      body.replaceChildren(row);
    }
  }

  $("#audit-filter").addEventListener("submit", event => {
    event.preventDefault();
    loadAudit(Object.fromEntries(new FormData(event.currentTarget)));
  });

  // Выход и запуск ---------------------------------------------------------------

  $("#logout").addEventListener("click", async () => {
    await window.SavAuth.post("/api/auth/logout").catch(() => {});
    location.assign("/login.html");
  });

  window.SavAuth.ready.then(async state => {
    if (!state?.auth_enabled) {
      document.body.classList.add("auth-off");
      $("#auth-off").hidden = false;
      return;
    }
    if (!state.user) return; // auth.js уже уводит на вход
    try {
      const account = await request("/api/auth/account");
      me = account.user;
      minLength = account.min_password_length || minLength;
      $("#min-length").textContent = minLength;
      $("#session-rules").textContent =
        `Сеанс закрывается после ${account.session_idle_minutes} мин бездействия и не позже чем через ${account.session_ttl_hours} ч после входа.`;
      nameInput.value = me.display_name || "";
      renderIdentity();
      $("#admin-nav").hidden = me.role !== "admin";
      showSection();
    } catch (error) {
      $("#auth-off").hidden = false;
      $("#auth-off h1").textContent = "Не удалось открыть кабинет";
      $("#auth-off p").textContent = error.message;
    }
  });
})();
