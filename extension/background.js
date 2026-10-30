// Native Messaging доступен только фоновому скрипту: кнопка на странице просит его поговорить с программой.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const request = msg.type === "folders" ? { type: "folders" } : { url: msg.url, folder: msg.folder, start: msg.start, end: msg.end };
  chrome.runtime.sendNativeMessage("com.ytfast.host", request, (res) => {
    const err = chrome.runtime.lastError;
    reply(err ? { ok: false, error: "Нет связи с YT Fast: запусти install.cmd" } : res);
  });
  return true; // ответ придёт асинхронно
});
