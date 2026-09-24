/* Табло героя на «Главной».

   Крупное число сложено из мелких цифр: каждый пиксель шрифта 5×7 —
   квадрат K×K мелких цифр. Вокруг числа — облако полупрозрачных цифр
   с неровным, медленно дышащим краем: к краю облака цифры гаснут и
   сливаются с фоном, рамки у табло нет. Цифры сами перебираются, от
   курсора разлетаются и разогреваются, по клику табло переключается на
   следующее число.

   Табло наклонено на --lp-tilt — единственное наклонное на странице;
   угол берётся из styles.css. Подписей у табло нет — только число и
   облако. Место под табло отводит .lp-slot в сетке героя, а холст лежит
   под всем героем, чтобы разлетевшиеся цифры не обрезались краем
   колонки. */
(function () {
  "use strict";

  const hero = document.querySelector(".lp-hero");
  if (!hero) return;
  const canvas = hero.querySelector(".lp-board");
  const slot = hero.querySelector(".lp-slot");
  const ctx = canvas.getContext("2d");

  const K = 3;             // мелких цифр на сторону пикселя шрифта
  const MX = 18, MY = 14;  // запас под облако вокруг числа, в мелких цифрах
  const INTERVAL = 5200;   // мс на одно число

  // Числа — те же, что в демо ниже по странице: база, доля, интервал, p.
  const VALUES = ["2000", "64%", "95%", ".012"];

  // Шрифт 5×7. Точка в две колонки, чтобы «.012» не раздувало табло.
  const GLYPHS = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    ".": ["00", "00", "00", "00", "00", "11", "11"],
  };

  function bitmap(text) {
    const glyphs = Array.from(text, ch => GLYPHS[ch]);
    const width = glyphs.reduce((sum, g) => sum + g[0].length + 1, -1);
    const lit = [];
    let x = 0;
    glyphs.forEach(g => {
      g.forEach((row, y) => {
        for (let i = 0; i < row.length; i++) if (row[i] === "1") lit.push([x + i, y]);
      });
      x += g[0].length + 1;
    });
    return { width, lit };
  }

  const maps = VALUES.map(bitmap);
  const CORE_COLS = Math.max(...maps.map(m => m.width)) * K;
  const CORE_ROWS = 7 * K;
  const COLS = CORE_COLS + MX * 2;
  const ROWS = CORE_ROWS + MY * 2;

  // Для каждого числа — какие клетки табло горят. Число центруется.
  const grids = maps.map(m => {
    const grid = new Uint8Array(COLS * ROWS);
    const offset = MX + Math.floor((CORE_COLS - m.width * K) / 2 / K) * K;
    m.lit.forEach(([bx, by]) => {
      for (let dy = 0; dy < K; dy++) {
        for (let dx = 0; dx < K; dx++) {
          grid[(MY + by * K + dy) * COLS + offset + bx * K + dx] = 1;
        }
      }
    });
    return grid;
  });

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const cells = [];
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      // Положение в облаке: чуть «квадратный» эллипс, чтобы облако
      // накрывало и углы числа. Радиус 1 — край решётки по осям.
      const nx = (c + 0.5 - COLS / 2) / (COLS / 2);
      const ny = (r + 0.5 - ROWS / 2) / (ROWS / 2);
      cells.push({
        c, r,
        radius: (Math.abs(nx) ** 2.4 + Math.abs(ny) ** 2.4) ** (1 / 2.4),
        angle: Math.atan2(ny, nx),
        grain: 0.55 + Math.random() * 0.45, x: 0, y: 0, hx: 0, hy: 0, vx: 0, vy: 0,
        digit: Math.floor(Math.random() * 10),
        flipAt: 0, target: 0, level: 0, pending: -1, switchAt: 0, heat: 0,
        spark: Math.random() < 0.09,
      });
    }
  }

  let tilt = 0, sin = 0, cos = 1;
  let W = 0, H = 0, dpr = 1;
  let step = 10, cx = 0, cy = 0, lw = 0, lh = 0;
  let atlas = null, atlasCell = 0;
  let colors = {};
  let current = 0, nextSwitch = 0;
  let running = false, raf = 0, placed = false;
  const pointer = { x: 0, y: 0, active: false };

  function readTheme() {
    const cs = getComputedStyle(hero);
    tilt = (parseFloat(cs.getPropertyValue("--lp-tilt")) || 0) * Math.PI / 180;
    sin = Math.sin(tilt);
    cos = Math.cos(tilt);
    colors = {
      signal: cs.getPropertyValue("--lp-signal").trim() || "#5df2a2",
      bright: cs.getPropertyValue("--lp-bright").trim() || "#e6fff0",
      hot: cs.getPropertyValue("--lp-hot").trim() || "#ff7358",
      mono: cs.getPropertyValue("--lp-mono").trim() || "monospace",
    };
  }

  // Десять цифр в трёх цветах заранее: тысячи fillText за кадр не влезают,
  // а drawImage из одного атласа — влезают.
  function buildAtlas() {
    const size = step * 1.22;
    atlasCell = Math.ceil(size * dpr * 1.1);
    atlas = document.createElement("canvas");
    atlas.width = atlasCell * 10;
    atlas.height = atlasCell * 3;
    const a = atlas.getContext("2d");
    a.font = `700 ${size * dpr}px ${colors.mono}`;
    a.textAlign = "center";
    a.textBaseline = "middle";
    [colors.signal, colors.bright, colors.hot].forEach((color, row) => {
      a.fillStyle = color;
      for (let d = 0; d < 10; d++) {
        a.fillText(String(d), d * atlasCell + atlasCell / 2, row * atlasCell + atlasCell / 2 + dpr);
      }
    });
  }

  function layout() {
    const box = hero.getBoundingClientRect();
    const s = slot.getBoundingClientRect();
    if (!box.width || !s.width || !s.height) return false;

    readTheme();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = box.width;
    H = box.height;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);

    // В слот вписывается само число, с вылетом в поля героя; облако вокруг
    // может уходить дальше — к краю оно всё равно гаснет. Число стоит
    // чуть выше середины слота.
    const ac = Math.abs(cos), as = Math.abs(sin);
    cx = s.left - box.left + s.width / 2;
    cy = s.top - box.top + s.height * 0.42;
    const reach = Math.min(s.width * 1.18, 2 * (Math.min(cx, W - cx) - 16));
    step = Math.min(reach / (CORE_COLS * ac + CORE_ROWS * as),
      s.height * 0.8 / (CORE_COLS * as + CORE_ROWS * ac), 20);
    lw = COLS * step;
    lh = ROWS * step;

    const scatter = !placed && !reduceMotion.matches;
    cells.forEach(cell => {
      cell.hx = (cell.c + 0.5) * step;
      cell.hy = (cell.r + 0.5) * step;
      if (!placed) {
        // Первый показ: табло собирается из облака цифр.
        cell.x = cell.hx + (scatter ? (Math.random() - 0.5) * lw * 0.9 : 0);
        cell.y = cell.hy + (scatter ? (Math.random() - 0.5) * lh * 2.4 : 0);
      } else {
        cell.x = cell.hx;
        cell.y = cell.hy;
      }
    });
    if (!placed) show(0, performance.now(), true);
    placed = true;

    buildAtlas();
    return true;
  }

  function show(index, now, instant) {
    current = (index + VALUES.length) % VALUES.length;
    const grid = grids[current];
    cells.forEach((cell, i) => {
      const target = grid[i];
      if (instant || reduceMotion.matches) {
        cell.target = target;
        cell.level = target;
        cell.pending = -1;
      } else if (target !== cell.target) {
        // Смена бежит диагональной волной снизу-слева вверх-вправо.
        cell.pending = target;
        cell.switchAt = now + (cell.c + (ROWS - cell.r)) * 8 + Math.random() * 160;
      }
    });
    nextSwitch = now + INTERVAL;
  }

  // Координаты героя → координаты табло (снимаем наклон).
  function toLocal(x, y) {
    const dx = x - cx, dy = y - cy;
    return { x: dx * cos + dy * sin + lw / 2, y: -dx * sin + dy * cos + lh / 2 };
  }

  function burst(x, y) {
    const p = toLocal(x, y);
    const radius = step * 22;
    cells.forEach(cell => {
      const dx = cell.x - p.x, dy = cell.y - p.y;
      const d = Math.hypot(dx, dy) || 1;
      if (d > radius) return;
      const f = 1 - d / radius;
      cell.vx += (dx / d) * f * step * 5;
      cell.vy += (dy / d) * f * step * 5;
      cell.heat = Math.min(1, cell.heat + f * 1.2);
    });
  }

  function tick(now) {
    if (!reduceMotion.matches && now >= nextSwitch) show(current + 1, now);

    const p = pointer.active ? toLocal(pointer.x, pointer.y) : null;
    const radius = Math.max(64, step * 10);
    const r2 = radius * radius;

    for (let i = 0; i < cells.length; i++) {
      const cell = cells[i];

      if (cell.pending >= 0 && now >= cell.switchAt) {
        cell.target = cell.pending;
        cell.pending = -1;
        cell.heat = Math.max(cell.heat, 0.45);
        cell.flipAt = now;
        cell.vx += (Math.random() - 0.5) * step * 0.5;
        cell.vy += (Math.random() - 0.5) * step * 0.5;
      }

      if (p) {
        const dx = cell.x - p.x, dy = cell.y - p.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < r2 && d2 > 0.01) {
          const d = Math.sqrt(d2);
          const f = 1 - d / radius;
          const push = f * f * step * 0.55;
          cell.vx += (dx / d) * push;
          cell.vy += (dy / d) * push;
          cell.heat = Math.min(1, cell.heat + f * 0.16);
        }
      }

      cell.vx = (cell.vx + (cell.hx - cell.x) * 0.045) * 0.85;
      cell.vy = (cell.vy + (cell.hy - cell.y) * 0.045) * 0.85;
      cell.x += cell.vx;
      cell.y += cell.vy;
      cell.heat *= 0.955;
      cell.level += (cell.target - cell.level) * 0.16;

      if (now >= cell.flipAt) {
        cell.digit = Math.floor(Math.random() * 10);
        cell.flipAt = now + (cell.heat > 0.12
          ? 30 + Math.random() * 70
          : cell.target ? 160 + Math.random() * 900 : 900 + Math.random() * 5000);
      }
    }
  }

  function drawFrame(now) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.translate(cx, cy);
    ctx.rotate(tilt);
    ctx.translate(-lw / 2, -lh / 2);

    const t = reduceMotion.matches ? 0 : now / 1000;
    const size = atlasCell / dpr;
    const half = size / 2;
    for (let i = 0; i < cells.length; i++) {
      const cell = cells[i];
      // Край облака — сумма синусоид по углу, медленно плывущих во времени:
      // так он неровный и живой. Яркость гаснет от центра к этому краю.
      const a = cell.angle;
      const edge = 0.78 + 0.11 * Math.sin(3 * a + t * 0.45) + 0.07 * Math.sin(5 * a - t * 0.7 + 1)
        + 0.04 * Math.sin(11 * a + t * 1.1 + 2);
      const k = Math.min(1, Math.max(0, (cell.radius / edge - 0.3) / 0.7));
      const fade = 1 - k * k * (3 - 2 * k);
      const cloud = fade * 0.3 * cell.grain;
      const alpha = Math.min(1, cloud * (1 - cell.level) + cell.level * 0.93 + cell.heat * fade * 0.6);
      if (alpha < 0.02) continue;
      const row = cell.heat > 0.5 ? 2 : (cell.heat > 0.18 || (cell.spark && cell.level > 0.5)) ? 1 : 0;
      ctx.globalAlpha = alpha;
      ctx.drawImage(atlas, cell.digit * atlasCell, row * atlasCell, atlasCell, atlasCell,
        cell.x - half, cell.y - half, size, size);
    }
    ctx.globalAlpha = 1;
  }

  function frame(now) {
    if (!running) return;
    if (!reduceMotion.matches) tick(now);
    drawFrame(now);
    raf = requestAnimationFrame(frame);
  }

  function start() {
    if (running || !layout()) return;
    running = true;
    nextSwitch = performance.now() + INTERVAL;
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    running = false;
    cancelAnimationFrame(raf);
  }

  // Считаем, только пока герой виден: экран «Главная» скрыт почти всё время.
  new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) start();
    else stop();
  }).observe(hero);

  new ResizeObserver(() => { if (running) layout(); }).observe(hero);
  if (document.fonts) document.fonts.ready.then(() => { if (running) layout(); });

  hero.addEventListener("pointermove", event => {
    if (event.pointerType === "touch") return;
    const box = hero.getBoundingClientRect();
    pointer.x = event.clientX - box.left;
    pointer.y = event.clientY - box.top;
    pointer.active = true;
  });
  hero.addEventListener("pointerleave", () => { pointer.active = false; });

  // Клик по табло (не по кнопкам) — взрыв и следующее число.
  hero.addEventListener("pointerdown", event => {
    if (event.target.closest("button, a, .lp-hero-copy")) return;
    const box = hero.getBoundingClientRect();
    if (!reduceMotion.matches) burst(event.clientX - box.left, event.clientY - box.top);
    show(current + 1, performance.now());
  });
})();
