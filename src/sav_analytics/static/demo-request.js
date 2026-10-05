/* Заявка на демо с лендинга.

   Кнопки .lp-demo открывают родной <dialog id="demo-dialog">, форма уходит
   в POST /api/demo-requests. Сервер пишет заявку в журнал и, если настроен
   SMTP, отправляет письмо; здесь только проверка полей и два состояния
   окна — форма и «Заявка отправлена». */
(function () {
  "use strict";

  const dialog = document.querySelector("#demo-dialog");
  if (!dialog) return;
  const form = dialog.querySelector("#demo-form");
  const done = dialog.querySelector("#demo-done");
  const error = dialog.querySelector("#demo-error");
  const submit = form.querySelector(".lp-demo-submit");

  function showError(message, field) {
    error.textContent = message;
    error.hidden = !message;
    form.querySelectorAll("[aria-invalid]").forEach(el => el.removeAttribute("aria-invalid"));
    if (field) {
      field.setAttribute("aria-invalid", "true");
      field.focus();
    }
  }

  function open() {
    form.hidden = false;
    done.hidden = true;
    showError("");
    dialog.showModal();
    fields().name.focus();
  }

  document.querySelectorAll(".lp-demo").forEach(button => button.addEventListener("click", open));
  dialog.querySelectorAll("[data-demo-close]").forEach(button => {
    button.addEventListener("click", () => dialog.close());
  });
  // Щелчок по затемнению вокруг окна закрывает его, как Esc.
  dialog.addEventListener("click", event => {
    if (event.target === dialog) dialog.close();
  });

  // У полей формы префикс demo_: лендинг лежит в одном документе с формой
  // загрузки, и голое name="name" перехватывало бы её селекторы.
  const FIELDS = ["name", "email", "phone", "company", "role", "message", "consent", "website"];
  function fields() {
    return Object.fromEntries(FIELDS.map(key => [key, form.elements[`demo_${key}`]]));
  }

  // Сервер отвечает списком ошибок pydantic; показываем первую по-русски.
  const FIELD_MESSAGES = {
    name: "Укажите имя.",
    email: "Проверьте адрес почты.",
    phone: "Проверьте номер телефона.",
    consent: "Нужно согласие на обработку данных.",
  };

  function validate() {
    const f = fields();
    if (!f.name.value.trim()) return ["name", f.name];
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.value.trim())) return ["email", f.email];
    const phone = f.phone.value.trim();
    if (phone && !/^[0-9+()\-\s.]{5,32}$/.test(phone)) return ["phone", f.phone];
    if (!f.consent.checked) return ["consent", f.consent];
    return null;
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      showError(FIELD_MESSAGES[problem[0]], problem[1]);
      return;
    }
    showError("");
    const f = fields();
    const payload = {
      name: f.name.value, email: f.email.value, phone: f.phone.value,
      company: f.company.value, role: f.role.value, message: f.message.value,
      consent: f.consent.checked, website: f.website.value,
    };
    const label = submit.textContent;
    submit.disabled = true;
    submit.textContent = "Отправляем…";
    try {
      const response = await fetch("/api/demo-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const field = Array.isArray(body.detail) ? body.detail[0]?.loc?.at(-1) : null;
        showError(FIELD_MESSAGES[field] || "Не удалось отправить заявку. Попробуйте ещё раз.",
          field && f[field]);
        return;
      }
      form.reset();
      form.hidden = true;
      done.hidden = false;
      done.querySelector("button").focus();
    } catch {
      showError("Нет связи с сервером. Проверьте подключение и попробуйте ещё раз.");
    } finally {
      submit.disabled = false;
      submit.textContent = label;
    }
  });
})();
