// Две кнопки: «Скачать» рядом с «Поделиться» и круглая на миниатюре под курсором.
// YouTube — одностраничное приложение и сам перестраивает разметку, поэтому первая кнопка
// возвращается на место наблюдателем, а ссылка берётся в момент нажатия. Вторая живёт в body:
// внутри ссылки-миниатюры клик открывал бы ролик, а предпросмотр при наведении её перекрывал бы.
(() => {
  const ICONS = {
    idle: ["M12 4v11", "M7 11l5 5 5-5", "M5 20h14"],
    ok: ["M5 13l4 4 10-10"],
    err: ["M6 6l12 12", "M18 6L6 18"],
  };
  const THUMB_LINK = 'a[href*="/watch?v="], a[href^="/shorts/"]';
  const THUMB_MIN_WIDTH = 100; // меньше — это уже не превью, а значок
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
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const url = getLink();
      if (!url || btn.disabled) return;
      btn.disabled = true; // двойной клик не должен запускать две загрузки
      chrome.runtime.sendMessage({ type: "download", url }, (res) => {
        const ok = !chrome.runtime.lastError && res && res.ok;
        const error = (res && res.error) || "Расширение обновилось: перезагрузите страницу";
        show(ok ? "ok" : "err", ok ? "Качается" : "Ошибка", ok ? "Загрузка идёт в YT Fast" : error);
        timer = setTimeout(reset, ok ? 2500 : 5000);
      });
    });
    reset();
    return { el: btn, reset };
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
  let current = null; // ссылка-миниатюра под курсором
  let currentLink = null; // запомнена при наведении: уходя на кнопку, курсор закрывает предпросмотр, и YouTube очищает его href
  const thumb = makeButton("ytfast-btn ytfast-thumb", "", () => currentLink);
  document.body.append(thumb.el);

  function hideThumb() {
    current = null;
    thumb.el.classList.remove("ytfast-visible");
  }

  function showThumb(link, rect) {
    current = link;
    currentLink = linkFor(link.href);
    thumb.reset();
    thumb.el.style.top = `${rect.top + 8}px`;
    thumb.el.style.left = `${rect.right - 8 - 36}px`;
    thumb.el.classList.add("ytfast-visible");
  }

  document.addEventListener("mouseover", (e) => {
    if (!(e.target instanceof Element) || thumb.el.contains(e.target)) return;
    const link = e.target.closest(THUMB_LINK);
    if (!link || link === current || !link.querySelector("img") || !linkFor(link.href)) return;
    const rect = link.getBoundingClientRect();
    if (rect.width >= THUMB_MIN_WIDTH) showThumb(link, rect);
  }, true);

  // По геометрии, а не по mouseout: предпросмотр YouTube ложится поверх миниатюры и забирает события
  document.addEventListener("mousemove", (e) => {
    if (!current || thumb.el.contains(e.target)) return;
    const r = current.getBoundingClientRect();
    const inside = e.clientX >= r.left && e.clientX < r.right && e.clientY >= r.top && e.clientY < r.bottom;
    if (!inside || !current.isConnected) hideThumb();
  }, true);

  document.addEventListener("scroll", hideThumb, { capture: true, passive: true });
  document.documentElement.addEventListener("mouseleave", hideThumb);
  document.addEventListener("yt-navigate-start", hideThumb);
})();
