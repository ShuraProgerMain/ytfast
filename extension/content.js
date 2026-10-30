// Кнопки: «Скачать» рядом с «Поделиться», круглая в панели Shorts и круглая на миниатюре под курсором.
// YouTube — одностраничное приложение и сам перестраивает разметку, поэтому первая кнопка
// возвращается на место наблюдателем, а ссылка берётся в момент нажатия. Вторая живёт в body:
// внутри ссылки-миниатюры клик открывал бы ролик, а предпросмотр при наведении её перекрывал бы.
// Клик качает в текущую папку, при наведении открывается список сохранённых папок,
// а там, где есть плеер, ещё и форма фрагмента: моменты можно брать из него.
(() => {
  const ICONS = {
    idle: ["M12 4v11", "M7 11l5 5 5-5", "M5 20h14"],
    ok: ["M5 13l4 4 10-10"],
    err: ["M6 6l12 12", "M18 6L6 18"],
  };
  // в кадре 16×16, та же геометрия, что у значков программы (macui.py)
  const SYMBOLS = {
    check: ["M3 8.5l3.5 3.5L13 4.2"],
    scissors: ["M5.6 10.2L11.5 2", "M10.4 10.2L4.5 2", "M6.4 12a2.2 2.2 0 1 1-4.4 0a2.2 2.2 0 1 1 4.4 0",
               "M14 12a2.2 2.2 0 1 1-4.4 0a2.2 2.2 0 1 1 4.4 0"],
    chevron: ["M4 6.2l4 4 4-4"],
    clock: ["M14 8A6 6 0 1 1 2 8a6 6 0 1 1 12 0", "M8 4.6V8l2.2 1.4"],
  };
  const TAGS = { // метки Finder, как в программе
    red: "#ff3b30", orange: "#ff9500", yellow: "#ffcc00", green: "#28cd41",
    blue: "#007aff", purple: "#af52de", gray: "#8e8e93",
  };
  const THUMB_LINK = 'a[href*="/watch?v="], a[href^="/shorts/"]';
  const THUMB_MIN_WIDTH = 100; // меньше — это уже не превью, а значок
  const MENU_OPEN_DELAY = 150;
  const MENU_CLOSE_DELAY = 300;
  const DEFAULT_CLIP = 30; // секунд в новом фрагменте
  const HANDLE_GRAB = 9; // пикселей вокруг ползунка, где он ещё берётся мышью
  const SEEK_INTERVAL = 150; // как часто плеер показывает кадр при перетаскивании, мс
  const PLAYHEAD_INTERVAL = 250;
  const HINT = "Скачать через YT Fast";

  function symbol(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("class", "ytfast-symbol");
    for (const d of SYMBOLS[name]) {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d);
      svg.append(p);
    }
    return svg;
  }

  // Шрифт программы: тот же Inter. Если YouTube его не пустит, останется системный
  const font = document.createElement("style");
  font.textContent = `@font-face { font-family: "YT Fast Inter"; font-weight: 100 900; font-display: swap;
    src: url("${chrome.runtime.getURL("Inter.ttf")}") format("truetype"); }`;
  document.head.append(font);

  function linkFor(href) {
    const u = new URL(href, location.href);
    const id = u.searchParams.get("v");
    if (u.pathname === "/watch" && id) return `https://www.youtube.com/watch?v=${encodeURIComponent(id)}`;
    const shorts = u.pathname.match(/^\/shorts\/([\w-]+)/);
    return shorts ? `https://www.youtube.com/shorts/${shorts[1]}` : null;
  }

  function makeButton(className, label, getLink) {
    const btn = document.createElement("button");
    btn.className = className;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    btn.append(svg);
    const text = label ? btn.appendChild(document.createElement("span")) : null;
    let timer;
    const show = (state, caption, title) => {
      svg.replaceChildren(...ICONS[state].map((d) => {
        const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p.setAttribute("d", d);
        return p;
      }));
      if (text) text.textContent = caption;
      btn.title = title;
      btn.dataset.state = state;
    };
    const reset = () => {
      clearTimeout(timer);
      btn.disabled = false;
      show("idle", label, HINT);
    };
    const start = (folder, section) => {
      const url = getLink();
      if (!url || btn.disabled) return;
      btn.disabled = true; // двойной клик не должен запускать две загрузки
      chrome.runtime.sendMessage({ type: "download", url, folder, ...section }, (res) => {
        const ok = !chrome.runtime.lastError && res && res.ok;
        const error = (res && res.error) || "Расширение обновилось: перезагрузите страницу";
        show(ok ? "ok" : "err", ok ? "Качается" : "Ошибка", ok ? "Загрузка идёт в YT Fast" : error);
        timer = setTimeout(reset, ok ? 2500 : 5000);
      });
    };
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeMenu();
      start();
    });
    reset();
    const owner = { el: btn, reset, start };
    btn.addEventListener("mouseenter", () => openMenuSoon(owner));
    btn.addEventListener("mouseleave", closeMenuSoon);
    return owner;
  }

  // ---------- список папок ----------
  const menu = document.createElement("div");
  menu.className = "ytfast-menu";
  menu.addEventListener("mouseenter", () => clearTimeout(menuTimer));
  menu.addEventListener("mouseleave", closeMenuSoon);
  for (const type of ["keydown", "keyup", "keypress"]) { // цифры в полях не должны перематывать ролик
    menu.addEventListener(type, (e) => e.stopPropagation());
  }
  let menuTimer;
  let menuOwner = null; // кнопка, для которой открыт или открывается список
  let formOpen = false; // форма фрагмента закрывается только сама: с ней работают и в плеере
  let playheadTimer;

  function closeMenu() {
    clearTimeout(menuTimer);
    clearInterval(playheadTimer);
    menu.classList.remove("ytfast-menu-wide");
    menuOwner = null;
    formOpen = false;
    menu.classList.remove("ytfast-visible");
  }

  function closeMenuSoon() {
    clearTimeout(menuTimer);
    if (!formOpen) menuTimer = setTimeout(closeMenu, MENU_CLOSE_DELAY);
  }

  function openMenuSoon(owner) {
    clearTimeout(menuTimer);
    if (owner.el.disabled || formOpen) return;
    if (menuOwner === owner && menu.classList.contains("ytfast-visible")) return;
    closeMenu();
    menuOwner = owner;
    menuTimer = setTimeout(() => {
      chrome.runtime.sendMessage({ type: "folders" }, (res) => {
        if (chrome.runtime.lastError || !res || !res.ok || menuOwner !== owner) return;
        if (res.folders.length > 1 || owner !== thumb) showMenu(owner, res.folders, res.current);
      });
    }, MENU_OPEN_DELAY);
  }

  function samePath(a, b) {
    const norm = (p) => p.replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
    return norm(a) === norm(b);
  }

  function folderName(path) {
    return path.split(/[\\/]+/).filter(Boolean).slice(-2).join("\\");
  }

  function makeDot(color) {
    const dot = document.createElement("span");
    dot.className = "ytfast-dot";
    const fill = TAGS[color] || (/^#[0-9a-f]{6}$/i.test(color || "") ? color : null);
    if (fill) dot.style.background = fill;
    else dot.classList.add("ytfast-dot-empty");
    return dot;
  }

  function makeRow(mark, lead, text, onClick) {
    const row = document.createElement("button");
    row.className = "ytfast-row";
    const check = document.createElement("span");
    check.className = "ytfast-mark";
    if (mark) check.append(symbol("check"));
    const name = document.createElement("span");
    name.className = "ytfast-name";
    name.textContent = text;
    row.append(check, lead, name);
    row.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      onClick();
    });
    return row;
  }

  function folderRow(folder, current, onPick) {
    const row = makeRow(samePath(folder.path, current), makeDot(folder.color), folderName(folder.path), () => onPick(folder));
    row.title = folder.path;
    return row;
  }

  function showMenu(owner, folders, current) {
    const rows = folders.length > 1 ? folders.map((f) => folderRow(f, current, (picked) => {
      closeMenu();
      owner.start(picked.path);
    })) : [];
    if (owner !== thumb) {
      const separator = document.createElement("div");
      separator.className = "ytfast-separator";
      if (rows.length) rows.unshift(separator);
      const cut = document.createElement("span");
      cut.className = "ytfast-lead";
      cut.append(symbol("scissors"));
      rows.unshift(makeRow(false, cut, "Скачать фрагмент…", () => showForm(owner, folders, current)));
    }
    menu.replaceChildren(...rows);
    placeMenu(owner);
  }

  // ---------- фрагмент ----------
  function parseTime(text) {
    const parts = text.trim().replace(",", ".").split(":");
    if (parts.length > 3 || !parts.every((x) => /^\d+(\.\d+)?$/.test(x))) return null;
    return parts.reduce((sum, x) => sum * 60 + Number(x), 0);
  }

  function formatTime(seconds) {
    const s = Math.floor(seconds);
    const pad = (n) => String(n).padStart(2, "0");
    const m = Math.floor(s / 60);
    return m >= 60 ? `${Math.floor(m / 60)}:${pad(m % 60)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
  }

  function showForm(owner, folders, current) {
    formOpen = true;
    clearTimeout(menuTimer);
    menu.onclick = null;
    const make = (tag, className, parent) => {
      const el = document.createElement(tag);
      if (className) el.className = className;
      if (parent) parent.append(el);
      return el;
    };
    const video = document.querySelector("video.html5-main-video");
    const ad = !!document.querySelector("#movie_player.ad-showing"); // в рекламе время и длительность — её собственные
    const now = video && !ad ? video.currentTime : 0;
    const duration = video ? video.duration : NaN;
    const known = !ad && Number.isFinite(duration) && duration > 0; // у прямого эфира длительности нет
    const begin = known ? Math.max(0, Math.min(now, duration - DEFAULT_CLIP)) : now;

    const header = make("div", "ytfast-form-header");
    make("div", "ytfast-form-title", header).textContent = "Фрагмент";
    const length = make("div", "ytfast-form-length", header);

    const times = make("div", "ytfast-times");
    const timeField = (label, value) => {
      const group = make("label", "ytfast-time", times);
      make("span", "ytfast-caption", group).textContent = label;
      const box = make("span", "ytfast-time-box", group);
      const input = make("input", "ytfast-input", box);
      input.value = value;
      const take = make("button", "ytfast-take", box);
      take.title = "Взять текущий момент из плеера";
      take.append(symbol("clock"));
      take.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!video) return;
        input.value = formatTime(video.currentTime);
        input.dispatchEvent(new Event("input"));
      });
      return input;
    };
    const from = timeField("От", formatTime(begin));
    const to = timeField("До", formatTime(known ? Math.min(begin + DEFAULT_CLIP, duration) : begin + DEFAULT_CLIP));
    const timeline = known ? [makeTimeline(video, duration, from, to, length)] : [];

    const place = make("label", "ytfast-place");
    make("span", "ytfast-caption", place).textContent = "Куда";
    const selectBox = make("span", "ytfast-select-box", place);
    // свой список вместо системного <select>: у того выпадающая часть рисуется браузером
    let chosen = folders.find((f) => samePath(f.path, current)) || folders[0];
    const popup = make("button", "ytfast-popup", selectBox);
    const popupList = make("div", "ytfast-popup-list", selectBox);
    const showChosen = () => {
      const name = document.createElement("span");
      name.className = "ytfast-name";
      name.textContent = folderName(chosen.path);
      popup.title = chosen.path;
      popup.replaceChildren(makeDot(chosen.color), name, symbol("chevron"));
    };
    const showList = (open) => {
      popupList.classList.toggle("ytfast-visible", open);
      if (open) {
        popupList.replaceChildren(...folders.map((f) => folderRow(f, chosen.path, (picked) => {
          chosen = picked;
          showChosen();
          showList(false);
        })));
      }
    };
    showChosen();
    popup.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      showList(!popupList.classList.contains("ytfast-visible"));
    });
    menu.onclick = () => showList(false);
    const error = make("div", "ytfast-error");
    const buttons = make("div", "ytfast-form-buttons");
    const cancel = make("button", "ytfast-push", buttons);
    cancel.textContent = "Отмена";
    const submit = make("button", "ytfast-push ytfast-primary", buttons);
    submit.textContent = "Скачать";
    cancel.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeMenu();
    });
    const send = () => {
      const start = parseTime(from.value);
      const end = parseTime(to.value);
      if (start === null || end === null) {
        error.textContent = "Время пишется так: 1:20";
      } else if (end <= start) {
        error.textContent = "Конец должен быть позже начала";
      } else {
        closeMenu();
        owner.start(chosen.path, { start, end });
      }
    };
    submit.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      send();
    });
    menu.onkeydown = (e) => {
      if (!formOpen) return;
      if (e.key === "Enter" && e.target.tagName === "INPUT") send();
      if (e.key === "Escape") {
        if (popupList.classList.contains("ytfast-visible")) showList(false);
        else closeMenu();
      }
    };
    menu.replaceChildren(header, ...timeline, times, place, error, buttons);
    menu.classList.add("ytfast-menu-wide");
    placeMenu(owner);
  }

  // Таймлайн: линия с двумя ползунками, связанными с полями времени. Перетаскивание выделения двигает
  // оба, а плеер показывает кадр под ползунком. Точность — секунда: точнее можно ввести в полях.
  function makeTimeline(video, duration, fromInput, toInput, length) {
    const part = (className, parent) => {
      const el = document.createElement("div");
      el.className = className;
      parent.append(el);
      return el;
    };
    const bar = document.createElement("div");
    bar.className = "ytfast-tl";
    const track = part("ytfast-tl-track", bar);
    const range = part("ytfast-tl-range", track);
    const play = part("ytfast-tl-play", track);
    const startHandle = part("ytfast-tl-handle", track);
    const endHandle = part("ytfast-tl-handle", track);

    const pct = (t) => `${Math.max(0, Math.min(t / duration, 1)) * 100}%`;
    const paint = () => {
      const start = parseTime(fromInput.value);
      const end = parseTime(toInput.value);
      if (start === null || end === null) return;
      startHandle.style.left = range.style.left = pct(start);
      endHandle.style.left = pct(end);
      range.style.width = `calc(${pct(end)} - ${pct(start)})`;
      const clip = end > start ? `Клип ${formatTime(end - start)} · ` : "";
      length.textContent = `${clip}весь ролик ${formatTime(duration)}`;
    };
    const paintPlayhead = () => {
      play.style.left = pct(video.currentTime);
    };
    fromInput.addEventListener("input", paint);
    toInput.addEventListener("input", paint);
    paint();
    paintPlayhead();
    clearInterval(playheadTimer);
    playheadTimer = setInterval(paintPlayhead, PLAYHEAD_INTERVAL);

    let drag = null;
    let lastSeek = 0;
    const seek = (t, force) => {
      if (!force && performance.now() - lastSeek < SEEK_INTERVAL) return;
      lastSeek = performance.now();
      video.currentTime = t;
      paintPlayhead();
    };
    const timeAt = (e) => {
      const r = track.getBoundingClientRect();
      return Math.max(0, Math.min((e.clientX - r.left) / r.width, 1)) * duration;
    };
    const apply = (e, force) => {
      const t = Math.round(timeAt(e));
      const start = parseTime(fromInput.value);
      const end = parseTime(toInput.value);
      let shown;
      if (drag.mode === "start") {
        shown = Math.max(0, Math.min(t, end - 1));
        fromInput.value = formatTime(shown);
      } else if (drag.mode === "end") {
        shown = Math.min(duration, Math.max(t, start + 1));
        toInput.value = formatTime(shown);
      } else {
        const clip = end - start;
        shown = Math.max(0, Math.min(t - drag.grab, duration - clip));
        fromInput.value = formatTime(shown);
        toInput.value = formatTime(shown + clip);
      }
      paint();
      seek(shown, force);
    };
    bar.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const start = parseTime(fromInput.value);
      const end = parseTime(toInput.value);
      if (start === null || end === null) return;
      const t = timeAt(e);
      const nearby = HANDLE_GRAB / track.getBoundingClientRect().width * duration;
      const toStart = Math.abs(t - start);
      const toEnd = Math.abs(t - end);
      let mode = toStart <= toEnd ? "start" : "end";
      if (t > start && t < end && Math.min(toStart, toEnd) > nearby) mode = "move";
      // на длинном ролике ползунки слипаются: какой из них взяли, решит направление движения
      if (toStart <= nearby && toEnd <= nearby) mode = "pick";
      drag = { mode, x: e.clientX, grab: Math.round(t) - start };
      bar.setPointerCapture(e.pointerId);
      if (mode !== "pick") apply(e);
    });
    bar.addEventListener("pointermove", (e) => {
      if (!drag) return;
      if (drag.mode === "pick") {
        if (Math.abs(e.clientX - drag.x) < 2) return;
        drag.mode = e.clientX < drag.x ? "start" : "end";
      }
      apply(e);
    });
    const drop = (e) => {
      if (!drag) return;
      if (drag.mode !== "pick") apply(e, true);
      drag = null;
    };
    bar.addEventListener("pointerup", drop);
    bar.addEventListener("pointercancel", drop);
    return bar;
  }

  function placeMenu(owner) {
    menu.classList.add("ytfast-visible");
    const r = owner.el.getBoundingClientRect();
    const { width, height } = menu.getBoundingClientRect();
    const below = r.bottom + 6 + height <= window.innerHeight - 8;
    menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - width - 8))}px`;
    menu.style.top = `${below ? r.bottom + 6 : Math.max(8, r.top - 6 - height)}px`;
  }

  // ---------- страница ролика ----------
  const main = makeButton("ytfast-btn", "Скачать", () => linkFor(location.href));

  function placeMain() {
    if (location.pathname !== "/watch") return;
    const bar = document.querySelector("ytd-watch-metadata #top-level-buttons-computed");
    if (bar && main.el.parentElement !== bar) bar.append(main.el);
  }

  // ---------- страница Shorts ----------
  const shorts = makeButton("ytfast-btn ytfast-shorts", "", () => linkFor(location.href));

  function placeShorts() {
    if (!location.pathname.startsWith("/shorts/")) return;
    // панелей может быть несколько, пока лента прокручивается: нужна та, что сейчас на экране
    const bar = [...document.querySelectorAll("reel-action-bar-view-model")].find((el) => {
      const r = el.getBoundingClientRect();
      return r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
    });
    if (bar && shorts.el.parentElement !== bar) bar.prepend(shorts.el);
  }

  new MutationObserver(() => {
    placeMain();
    placeShorts();
  }).observe(document.documentElement, { childList: true, subtree: true });
  placeMain();
  placeShorts();

  // ---------- миниатюры ----------
  // Предпросмотр YouTube ложится поверх миниатюры, забирает события мыши, а сам элемент может заменить
  // или очистить. Поэтому при наведении запоминаем ссылку и прямоугольник и дальше живём только по ним.
  let hovered = null; // { el, link, rect, scrollY } — миниатюра под курсором
  const thumb = makeButton("ytfast-btn ytfast-thumb", "", () => hovered && hovered.link);
  document.body.append(thumb.el, menu);

  function hideThumb() {
    hovered = null;
    thumb.el.classList.remove("ytfast-visible");
    if (menuOwner === thumb) closeMenu();
  }

  function showThumb(el, link, rect) {
    if (menuOwner === thumb) closeMenu();
    hovered = { el, link, rect, scrollY: window.scrollY };
    thumb.reset();
    thumb.el.style.top = `${rect.top + 8}px`;
    thumb.el.style.left = `${rect.left + 8}px`; // в правом углу YouTube показывает свои кнопки
    thumb.el.classList.add("ytfast-visible");
  }

  document.addEventListener("mouseover", (e) => {
    if (!(e.target instanceof Element) || thumb.el.contains(e.target) || menu.contains(e.target)) return;
    const el = e.target.closest(THUMB_LINK);
    if (!el || !el.querySelector("img")) return;
    const link = linkFor(el.href);
    if (!link || (hovered && hovered.link === link)) return; // предпросмотр того же ролика — кнопка остаётся
    const rect = el.getBoundingClientRect();
    if (rect.width >= THUMB_MIN_WIDTH) showThumb(el, link, rect);
  }, true);

  document.addEventListener("mousemove", (e) => {
    if (!hovered || thumb.el.contains(e.target) || menu.contains(e.target)) return;
    const r = hovered.rect;
    if (e.clientX >= r.left && e.clientX < r.right && e.clientY >= r.top && e.clientY < r.bottom) return;
    // список может выходить за миниатюру: пока он открыт, курсору дают до него дойти
    if (menuOwner === thumb && menu.classList.contains("ytfast-visible")) return;
    hideThumb();
  }, true);

  // Внутри предпросмотра своя прокрутка: прячемся, только когда сдвинулась сама миниатюра
  document.addEventListener("scroll", (e) => {
    if (menu.contains(e.target)) return;
    if (e.target === document && !formOpen) closeMenu();
    if (!hovered) return;
    const moved = e.target === document ? window.scrollY !== hovered.scrollY : e.target.contains(hovered.el);
    if (moved) hideThumb();
  }, { capture: true, passive: true });

  document.documentElement.addEventListener("mouseleave", () => {
    hideThumb();
    if (!formOpen) closeMenu();
  });
  document.addEventListener("yt-navigate-start", () => { hideThumb(); closeMenu(); });
})();
