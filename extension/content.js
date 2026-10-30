// Кнопка «Скачать» рядом с «Поделиться». YouTube — одностраничное приложение и сам перестраивает
// разметку, поэтому кнопка возвращается на место наблюдателем, а ссылка берётся в момент нажатия.
(() => {
  const ARROW = ["M12 4v11", "M7 11l5 5 5-5", "M5 20h14"];
  const CHECK = ["M5 13l4 4 10-10"];
  const CROSS = ["M6 6l12 12", "M18 6L6 18"];
  const LABEL = "Скачать";
  const HINT = "Скачать через YT Fast";

  function videoLink() {
    const id = new URLSearchParams(location.search).get("v");
    return location.pathname === "/watch" && id ? `https://www.youtube.com/watch?v=${encodeURIComponent(id)}` : null;
  }

  function makeButton() {
    const btn = document.createElement("button");
    btn.className = "ytfast-btn";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    const text = document.createElement("span");
    btn.append(svg, text);
    const show = (paths, label, title) => {
      svg.replaceChildren(...paths.map((d) => {
        const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p.setAttribute("d", d);
        return p;
      }));
      text.textContent = label;
      btn.title = title;
    };
    show(ARROW, LABEL, HINT);
    btn.addEventListener("click", () => {
      const url = videoLink();
      if (!url || btn.disabled) return;
      btn.disabled = true; // двойной клик не должен запускать две загрузки
      chrome.runtime.sendMessage({ type: "download", url }, (res) => {
        const ok = !chrome.runtime.lastError && res && res.ok;
        const error = (res && res.error) || "Расширение обновилось: перезагрузите страницу";
        show(ok ? CHECK : CROSS, ok ? "Качается" : "Ошибка", ok ? "Загрузка идёт в YT Fast" : error);
        setTimeout(() => {
          btn.disabled = false;
          show(ARROW, LABEL, HINT);
        }, ok ? 2500 : 5000);
      });
    });
    return btn;
  }

  const button = makeButton();

  function place() {
    if (!videoLink()) return;
    const bar = document.querySelector("ytd-watch-metadata #top-level-buttons-computed");
    if (bar && button.parentElement !== bar) bar.append(button);
  }

  new MutationObserver(place).observe(document.documentElement, { childList: true, subtree: true });
  place();
})();
