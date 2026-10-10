/* Администрирование (P4): пользователи и журнал аудита. Права проверяет
   сервер; страница — только интерфейс к /api/admin. */

(() => {
  const usersBody = document.querySelector("#users");
  const auditBody = document.querySelector("#audit");
  const status = document.querySelector("#users-status");
  const dateTime = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

  const when = value => (value ? dateTime.format(new Date(value)) : "—");

  function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text ?? "—";
    if (className) td.className = className;
    return td;
  }

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof payload.detail === "string" ? payload.detail : "Не получилось.");
    }
    return payload;
  }

  const json = (method, body) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  async function update(user, change, message) {
    try {
      await request(`/api/admin/users/${user.id}`, json("PATCH", change));
      status.textContent = message;
      loadUsers();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  async function loadUsers() {
    const { users } = await request("/api/admin/users");
    usersBody.replaceChildren(
      ...users.map(user => {
        const row = document.createElement("tr");
        row.append(
          cell(user.username),
          cell(user.display_name),
          cell(user.role === "admin" ? "Администратор" : "Аналитик"),
          cell(user.active ? "Активен" : "Отключён", user.active ? "" : "muted"),
          cell(when(user.last_login_at), "muted"),
        );
        const actions = document.createElement("td");
        const button = (label, action) => {
          const element = document.createElement("button");
          element.type = "button";
          element.textContent = label;
          element.addEventListener("click", action);
          actions.append(element, " ");
        };
        button(user.active ? "Отключить" : "Включить", () =>
          update(user, { active: !user.active }, user.active ? "Отключён, сессии закрыты." : "Включён."),
        );
        button(user.role === "admin" ? "Сделать аналитиком" : "Сделать администратором", () =>
          update(user, { role: user.role === "admin" ? "user" : "admin" }, "Роль изменена."),
        );
        button("Новый пароль", () => {
          const password = prompt(`Новый пароль для ${user.username}, не короче 10 символов`);
          if (password) update(user, { password }, "Пароль задан, сессии закрыты.");
        });
        row.append(actions);
        return row;
      }),
    );
  }

  async function loadAudit(filter = {}) {
    const query = new URLSearchParams({ limit: "200" });
    for (const [key, value] of Object.entries(filter)) if (value) query.set(key, value);
    const { entries } = await request(`/api/admin/audit?${query}`);
    auditBody.replaceChildren(
      ...entries.map(entry => {
        const row = document.createElement("tr");
        row.append(
          cell(when(entry.at)),
          cell(entry.username),
          cell(entry.action),
          cell([entry.method, entry.path].filter(Boolean).join(" ")),
          cell(entry.status),
          cell(entry.ip, "muted"),
        );
        return row;
      }),
    );
  }

  document.querySelector("#user-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await request("/api/admin/users", json("POST", Object.fromEntries(new FormData(form))));
      status.textContent = `Пользователь ${form.username.value} добавлен.`;
      form.reset();
      loadUsers();
    } catch (error) {
      status.textContent = error.message;
    }
  });

  document.querySelector("#audit-filter").addEventListener("submit", event => {
    event.preventDefault();
    loadAudit(Object.fromEntries(new FormData(event.currentTarget)));
  });

  window.SavAuth.ready.then(() => {
    loadUsers().catch(error => (status.textContent = error.message));
    loadAudit();
  });
})();
