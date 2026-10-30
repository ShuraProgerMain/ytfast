// Native Messaging доступен только фоновому скрипту: кнопка на странице просит его передать ссылку программе.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type !== "download") return;
  chrome.runtime.sendNativeMessage("com.ytfast.host", { url: msg.url }, (res) => {
    const err = chrome.runtime.lastError;
    reply(err ? { ok: false, error: "Нет связи с YT Fast: запусти install.cmd" } : res);
  });
  return true; // ответ придёт асинхронно
});
