/* Страница входа. Пока пользователей нет, та же форма заводит первого
   администратора — только на самом сервере (сервер это проверяет). */

(async () => {
  const form = document.querySelector("#login-form");
  const errorBox = document.querySelector("#login-error");
  const submit = document.querySelector("#login-submit");
  const next = new URLSearchParams(location.search).get("next");
  // Только свой путь: иначе ссылку на вход можно было бы направить на чужой сайт.
  const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";

  const state = await fetch("/api/auth/state").then(response => response.json()).catch(() => null);
  if (!state || !state.auth_enabled || state.user) {
    location.replace(target);
    return;
  }
  const setup = state.setup_required;
  if (setup) {
    document.querySelector("#login-title").textContent = "Первый администратор";
    document.querySelector("#login-lead").textContent = state.setup_allowed
      ? "Пользователей ещё нет. Заведите учётную запись администратора — дальше остальных добавите вы."
      : "Пользователей ещё нет. Первого администратора заводят на сервере: sav-analytics create-user --admin.";
    document.querySelector("#display-name-field").hidden = false;
    form.password.setAttribute("autocomplete", "new-password");
    submit.textContent = "Создать и войти";
    submit.disabled = !state.setup_allowed;
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    errorBox.hidden = true;
    submit.disabled = true;
    const body = { username: form.username.value, password: form.password.value };
    if (setup) body.display_name = form.display_name.value;
    try {
      const response = await fetch(setup ? "/api/auth/setup" : "/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail;
        throw new Error(
          typeof detail === "string" ? detail : "Проверьте имя и пароль (пароль — от 10 символов)."
        );
      }
      location.replace(target);
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      submit.disabled = false;
      form.password.select();
    }
  });
})();
