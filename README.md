# YT Fast

Быстрое скачивание роликов с YouTube на Windows: строка в стиле Spotlight по
**Ctrl+Alt+Y**, вставил ссылку, Enter — качается.

![Как пользоваться](docs/demo.gif)

## Зачем

Провайдер режет скорость YouTube **на каждое соединение** — около 100 КБ/с, и
обычный `yt-dlp` качает ролик минутами. YT Fast берёт у `yt-dlp` только прямые
ссылки на потоки, а сами потоки качает кусками в 32 соединения и склеивает
`ffmpeg` без перекодирования.

| Ролик | yt-dlp | YT Fast |
|---|---|---|
| 1080p60, 22 МБ | ~80 КБ/с, это ~5 мин | 14 с |
| 4K60, 133 МБ | — | 58 с |

## Как пользоваться

### Окно

| Действие | Что происходит |
|---|---|
| **Ctrl+Alt+Y** | открыть строку; ссылка из буфера обмена уже вставлена |
| **↓** или клик по `1080p ⌄` | выбрать качество — от «Лучшее» до «Только звук»; выбор запоминается |
| **Enter** | скачать |
| **Esc** или клик мимо окна | закрыть; во время загрузки окно сворачивается в полоску слева внизу |
| наведение на полоску | кнопки «развернуть» и «остановить» |
| **Ctrl+Alt+Y** ещё раз | убрать открытую строку |

Готовые файлы лежат в `Загрузки\YouTube`. Несколько загрузок можно запускать
параллельно — полоски встают стопкой.

### Консоль

```bash
ytfast "https://www.youtube.com/watch?v=..." -q 1080
```

| Ключ | Что делает |
|---|---|
| `-q 720` / `1080` / `2160` | максимальная высота кадра (по умолчанию — лучшее) |
| `--audio` | только звук, m4a |
| `--h264` | H.264 + AAC в mp4 — для плееров, не понимающих VP9 |
| `-c 64` | число соединений (по умолчанию 32) |
| `-o папка` | куда сохранять |

`ytfast` без аргументов открывает окно.

## Установка

Нужно:

- Windows 10 2004+ или Windows 11;
- Python с tkinter (проверено на 3.14);
- `yt-dlp` и `ffmpeg` — в `PATH`, в переменных `YTDLP` / `FFMPEG` или в папке
  [media-downloader](https://github.com/mhogomchungu/media-downloader);
- шрифт [Inter](https://rsms.me/inter/) — ближе всего к SF Pro; без него будет Segoe UI.

Добавить папку в `PATH`, чтобы работала команда `ytfast`, и включить фоновый
процесс с горячей клавишей (PowerShell, из папки репозитория):

```powershell
$dir = (Get-Location).Path
$pyw = & python -c "import os, sys; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"

# PATH пользователя
$k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
$k.SetValue('Path', $k.GetValue('Path', '', 'DoNotExpandEnvironmentNames').TrimEnd(';') + ";$dir", 'ExpandString')

# автозапуск фонового процесса и ярлык в «Пуск»
$sh = New-Object -ComObject WScript.Shell
foreach ($p in @('Startup\YT Fast.lnk', 'YT Fast.lnk')) {
    $s = $sh.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\$p")
    $s.TargetPath = $pyw
    $s.Arguments = "`"$dir\ytfast_gui.pyw`"" + $(if ($p -like 'Startup*') { ' --background' } else { '' })
    $s.WorkingDirectory = $dir
    $s.Save()
}
Start-Process $pyw "`"$dir\ytfast_gui.pyw`" --background"
```

Удалить: убрать `YT Fast` из папки `shell:startup` и из меню «Пуск».

## Как устроено

| Файл | Что внутри |
|---|---|
| `ytfast.py` | загрузчик: ссылки через `yt-dlp -J`, многопоточная загрузка с докачкой обрывов и перехватом хвоста у медленных соединений, отмена, склейка `ffmpeg` |
| `ytfast_gui.pyw` | окно и фоновый процесс: держит Tk и готовые иконки, сам ловит Ctrl+Alt+Y, повторный запуск передаёт ему команду через именованный канал — поэтому окно появляется за ~60 мс |
| `macui.py` | интерфейс в духе macOS Big Sur поверх tkinter: размытый «вибрантный» фон из снимка экрана под окном, мягкая тень отдельным layered-окном, сглаженные иконки в духе SF Symbols, поле ввода |
| `ytfast.bat` | команда `ytfast` для консоли |

Настройки — `%APPDATA%\ytfast\settings.json`, ошибки фонового процесса —
`%APPDATA%\ytfast\gui.log`.
