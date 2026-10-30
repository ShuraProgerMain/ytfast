// Две кнопки: «Скачать» рядом с «Поделиться» и круглая на миниатюре под курсором.
// YouTube — одностраничное приложение и сам перестраивает разметку, поэтому первая кнопка
// возвращается на место наблюдателем, а ссылка берётся в момент нажатия. Вторая живёт в body:
// внутри ссылки-миниатюры клик открывал бы ролик, а предпросмотр при наведении её перекрывал бы.
// Клик качает в текущую папку, при наведении открывается список сохранённых папок.
(() => {
  const ICONS = {
    idle: ["M12 4v11", "M7 11l5 5 5-5", "M5 20h14"],
    ok: ["M5 13l4 4 10-10"],
    err: ["M6 6l12 12", "M18 6L6 18"],
  };
  const TAGS = { // метки Finder, как в программе
    red: "#ff3b30", orange: "#ff9500", yellow: "#ffcc00", green: "#28cd41",
    blue: "#007aff", purple: "#af52de", gray: "#8e8e93",
  };
  const THUMB_LINK = 'a[href*="/watch?v="], a[href^="/shorts/"]';
  const THUMB_MIN_WIDTH = 100; // меньше — это уже не превью, а значок
  const MENU_OPEN_DELAY = 150;
  const MENU_CLOSE_DELAY = 300;
  const HINT = "Скачать через YT Fast";

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
    const start = (folder) => {
      const url = getLink();
      if (!url || btn.disabled) return;
      btn.disabled = true; // двойной клик не должен запускать две загрузки
      chrome.runtime.sendMessage({ type: "download", url, folder }, (res) => {
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
  let menuTimer;
  let menuOwner = null; // кнопка, для которой открыт или открывается список

  function closeMenu() {
    clearTimeout(menuTimer);
    menuOwner = null;
    menu.classList.remove("ytfast-visible");
  }

  function closeMenuSoon() {
    clearTimeout(menuTimer);
    menuTimer = setTimeout(closeMenu, MENU_CLOSE_DELAY);
  }

  function openMenuSoon(owner) {
    clearTimeout(menuTimer);
    if (owner.el.disabled) return;
    if (menuOwner === owner && menu.classList.contains("ytfast-visible")) return;
    closeMenu();
    menuOwner = owner;
    menuTimer = setTimeout(() => {
      chrome.runtime.sendMessage({ type: "folders" }, (res) => {
        if (chrome.runtime.lastError || !res || !res.ok || menuOwner !== owner) return;
        if (res.folders.length > 1) showMenu(owner, res.folders, res.current);
      });
    }, MENU_OPEN_DELAY);
  }

  function samePath(a, b) {
    const norm = (p) => p.replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
    return norm(a) === norm(b);
  }

  function folderRow(owner, folder, current) {
    const row = document.createElement("button");
    row.className = "ytfast-row";
    row.title = folder.path;
    const check = document.createElement("span");
    check.className = "ytfast-check";
    check.textContent = samePath(folder.path, current) ? "✓" : "";
    const dot = document.createElement("span");
    dot.className = "ytfast-dot";
    const color = TAGS[folder.color] || (/^#[0-9a-f]{6}$/i.test(folder.color || "") ? folder.color : null);
    if (color) dot.style.background = color;
    else dot.classList.add("ytfast-dot-empty");
    const name = document.createElement("span");
    name.className = "ytfast-name";
    name.textContent = folder.path.split(/[\\/]+/).filter(Boolean).slice(-2).join("\\");
    row.append(check, dot, name);
    row.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeMenu();
      owner.start(folder.path);
    });
    return row;
  }

  function showMenu(owner, folders, current) {
    menu.replaceChildren(...folders.map((f) => folderRow(owner, f, current)));
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

  new MutationObserver(placeMain).observe(document.documentElement, { childList: true, subtree: true });
  placeMain();

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
    if (e.target === document) closeMenu();
    if (!hovered) return;
    const moved = e.target === document ? window.scrollY !== hovered.scrollY : e.target.contains(hovered.el);
    if (moved) hideThumb();
  }, { capture: true, passive: true });

  document.documentElement.addEventListener("mouseleave", () => { hideThumb(); closeMenu(); });
  document.addEventListener("yt-navigate-start", () => { hideThumb(); closeMenu(); });
})();
