<#
  YT Fast — установка одной командой.

  Двойной клик по install.cmd или в PowerShell:
      irm https://raw.githubusercontent.com/ShuraProgerMain/ytfast/main/install.ps1 | iex

  Что делает: ставит Python (если нет), качает yt-dlp, ffmpeg и deno в
  %LOCALAPPDATA%\YTFast\bin, добавляет команду ytfast в PATH, создаёт ярлык в «Пуск»,
  автозапуск фонового процесса и запускает его — дальше Ctrl+Alt+Y.
  Повторный запуск обновляет программу и yt-dlp. Удаление: install.cmd -Uninstall
  или «Установка и удаление программ».
#>
param(
    [switch]$Uninstall,
    [string]$Source  # папка с программой; install.cmd передаёт свою
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = 'ShuraProgerMain/ytfast'
$Data = Join-Path $env:LOCALAPPDATA 'YTFast'  # инструменты и команда ytfast
$Bin = Join-Path $Data 'bin'
$Programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$MenuLnk = Join-Path $Programs 'YT Fast.lnk'
$AutoLnk = Join-Path $Programs 'Startup\YT Fast.lnk'
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\YTFast'
$Downloaded = Join-Path $env:LOCALAPPDATA 'Programs\YTFast'  # куда кладём программу при установке по ссылке

function Step($text) { Write-Host "`n$text" -ForegroundColor Cyan }
function Done($text) { Write-Host "  + $text" -ForegroundColor Green }
function Note($text) { Write-Host "  $text" -ForegroundColor DarkGray }

function Invoke-Native {
    # Запуск внешней программы: её stderr не должен превращаться в ошибку PowerShell
    param([string]$Exe, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments 2>$null } finally { $ErrorActionPreference = $old }
}

function Wait-Process-Or-Fail($Process, [int]$Seconds, [string]$Log) {
    # чужой установщик не должен вешать нашу установку навсегда
    $null = $Process.Handle  # без этого PowerShell теряет код завершения
    if (-not $Process.WaitForExit($Seconds * 1000)) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        throw "установщик не закончил работу за $Seconds с — смотри $Log"
    }
    if ($Process.ExitCode -notin 0, 3010) { throw "установщик завершился с кодом $($Process.ExitCode) — смотри $Log" }
}

function Stop-YTFast {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*ytfast_gui.pyw*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Edit-UserPath([string[]]$Remove, [string]$Add) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
    $norm = { param($p) $p.TrimEnd('\').ToLowerInvariant() }
    $drop = @($Remove + $Add | Where-Object { $_ } | ForEach-Object { & $norm $_ })
    $parts = @($key.GetValue('Path', '', 'DoNotExpandEnvironmentNames') -split ';' |
        Where-Object { $_ -and ($drop -notcontains (& $norm ([Environment]::ExpandEnvironmentVariables($_)))) })
    if ($Add) { $parts += $Add }
    $key.SetValue('Path', ($parts -join ';'), 'ExpandString')
    $key.Close()
    # оповестить Windows, чтобы новые окна терминала увидели PATH
    [Environment]::SetEnvironmentVariable('YTFAST_TMP', '1', 'User')
    [Environment]::SetEnvironmentVariable('YTFAST_TMP', $null, 'User')
}

# ---------- удаление ----------

if ($Uninstall) {
    Step 'Удаляю YT Fast'
    Stop-YTFast
    Remove-Item $MenuLnk, $AutoLnk -Force -ErrorAction SilentlyContinue
    Remove-Item $UninstallKey -Recurse -Force -ErrorAction SilentlyContinue
    Edit-UserPath -Remove $Data
    Remove-Item $Data, (Join-Path $env:APPDATA 'ytfast') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $Downloaded -Recurse -Force -ErrorAction SilentlyContinue
    Done 'ярлыки, автозапуск, PATH и скачанные инструменты удалены'
    Note 'Python и скачанные ролики (Загрузки\YouTube) не трогал.'
    return
}

# ---------- программа ----------

Step 'Программа'
if (-not $Source) { $Source = $PSScriptRoot }
if ($Source -and (Test-Path (Join-Path $Source 'ytfast_gui.pyw'))) {
    $App = (Resolve-Path $Source).Path.TrimEnd('\')
    Note "из папки $App"
} else {
    $App = $Downloaded
    $zip = Join-Path $env:TEMP 'ytfast-main.zip'
    $tmp = Join-Path $env:TEMP 'ytfast-main'
    Note "скачиваю github.com/$Repo"
    Invoke-Native curl.exe @('-L', '--fail', '-s', '-o', $zip, "https://github.com/$Repo/archive/refs/heads/main.zip")
    if ($LASTEXITCODE) { throw 'не удалось скачать программу с GitHub' }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip $tmp -Force
    Stop-YTFast
    Remove-Item $App -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force (Split-Path $App) | Out-Null
    Move-Item (Get-ChildItem $tmp | Select-Object -First 1).FullName $App
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
Done $App

# ---------- Python ----------

function Find-Python {
    $probe = 'import sys, tkinter; assert sys.version_info >= (3, 9); print(sys.executable)'
    foreach ($cmd in 'py -3', 'python') {
        $parts = $cmd -split ' '
        if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
        $out = Invoke-Native $parts[0] (@($parts | Select-Object -Skip 1) + '-c', $probe)
        $exe = "$($out | Select-Object -Last 1)".Trim()
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe }
    }
    # PATH в этой сессии мог ещё не обновиться — смотрим обычные места установки
    $known = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                           "$env:LOCALAPPDATA\Python\pythoncore-3*\python.exe" -ErrorAction SilentlyContinue
    foreach ($p in $known) {
        Invoke-Native $p.FullName @('-c', $probe) | Out-Null
        if ($LASTEXITCODE -eq 0) { return $p.FullName }
    }
}

Step 'Python'
$Python = Find-Python
if (-not $Python) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Note 'ставлю Python 3.14 через winget…'
        Invoke-Native winget @('install', '--id', 'Python.Python.3.14', '-e', '--scope', 'user', '--silent',
                              '--accept-source-agreements', '--accept-package-agreements') | Out-Null
        Update-SessionPath
        $Python = Find-Python
    }
    if (-not $Python) {
        # Официальный Python install manager (python.org рекомендует его с 3.14):
        # ставится без прав администратора, сам Python — командой py install
        $index = (Invoke-WebRequest 'https://www.python.org/ftp/python/pymanager/' -UseBasicParsing).Content
        $latest = [regex]::Matches($index, 'python-manager-(\d+\.\d+)\.msi"') |
            ForEach-Object { [version]$_.Groups[1].Value } | Sort-Object -Descending | Select-Object -First 1
        if (-not $latest) { throw 'не нашёл Python install manager на python.org' }
        $msi = Join-Path $env:TEMP "python-manager-$latest.msi"
        Note "ставлю Python install manager $latest…"
        Invoke-Native curl.exe @('-L', '--fail', '-s', '-o', $msi, "https://www.python.org/ftp/python/pymanager/python-manager-$latest.msi")
        if ($LASTEXITCODE) { throw 'не удалось скачать Python install manager' }
        $msiLog = Join-Path $env:TEMP 'ytfast-pymanager.log'
        Wait-Process-Or-Fail (Start-Process msiexec.exe -PassThru -ArgumentList '/i', "`"$msi`"", '/quiet', '/norestart', '/l*v', "`"$msiLog`"") 300 $msiLog
        Remove-Item $msi -Force -ErrorAction SilentlyContinue
        Update-SessionPath
        $pymanager = @((Get-Command pymanager -ErrorAction SilentlyContinue | Select-Object -First 1).Source,
                       (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WindowsApps\pymanager.exe",
                                      "$env:ProgramFiles\Python*Manager*\pymanager.exe",
                                      "$env:LOCALAPPDATA\Programs\Python*Manager*\pymanager.exe" -ErrorAction SilentlyContinue |
                           Select-Object -First 1).FullName) | Where-Object { $_ } | Select-Object -First 1
        if (-not $pymanager) { throw "Python install manager не установился — смотри $msiLog" }
        Note 'скачиваю и ставлю Python 3.14…'
        $pyLog = Join-Path $env:TEMP 'ytfast-py-install.log'
        Wait-Process-Or-Fail (Start-Process $pymanager -PassThru -NoNewWindow -ArgumentList 'install', '-y', '3.14' `
                                  -RedirectStandardOutput $pyLog -RedirectStandardError "$pyLog.err") 600 $pyLog
        Update-SessionPath
        $Python = Find-Python
    }
    if (-not $Python) { throw 'Python не установился. Поставь его с python.org и запусти установку ещё раз.' }
}
$Pythonw = Join-Path (Split-Path $Python) 'pythonw.exe'
Done $Python

# ---------- yt-dlp, ffmpeg, deno ----------

function Find-Tool([string]$Name, [string]$EnvName) {
    # тот же порядок, что в ytfast.find_tool
    $candidates = @(
        [Environment]::GetEnvironmentVariable($EnvName),
        (Join-Path $Bin "$Name.exe"),
        (Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1).Source,
        (Join-Path $env:APPDATA "media-downloader\bin\$Name.exe")
    )
    foreach ($p in $candidates) { if ($p -and (Test-Path $p)) { return $p } }
}

function Get-File([string]$Url, [string]$Out) {
    # своим загрузчиком в много соединений: GitHub отдаёт ~0.5 МБ/с на соединение.
    # Вызов напрямую, без перехвата вывода, — иначе прогресс появится только в конце.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Python -X utf8 (Join-Path $App 'ytfast.py') $Url --fetch $Out -c 16
    $ErrorActionPreference = $old
    if ($LASTEXITCODE) { throw "не удалось скачать $Url" }
}

function Expand-Files([string]$Zip, [string]$Pattern) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        foreach ($e in $archive.Entries) {
            if ($e.FullName -match $Pattern -and $e.Name) {
                [IO.Compression.ZipFileExtensions]::ExtractToFile($e, (Join-Path $Bin $e.Name), $true)
            }
        }
    } finally { $archive.Dispose() }
    Remove-Item $Zip -Force
}

New-Item -ItemType Directory -Force $Bin | Out-Null

# Python проверяет HTTPS по хранилищу сертификатов Windows, а на свежей системе корневые
# сертификаты Windows подгружает только при первом заходе на сайт своими средствами.
# Один запрос через curl.exe (он работает через Windows) — и Python тоже доверяет этим сайтам.
foreach ($site in 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com',
                  'www.youtube.com', 'www.python.org') {
    Invoke-Native curl.exe @('-s', '-o', 'NUL', '-I', '--max-time', '15', "https://$site") | Out-Null
}

Step 'yt-dlp'
$ytdlp = Find-Tool 'yt-dlp' 'YTDLP'
if ($ytdlp -eq (Join-Path $Bin 'yt-dlp.exe')) {
    Note 'обновляю…'
    Invoke-Native $ytdlp @('-U') | Out-Null
} elseif (-not $ytdlp) {
    $ytdlp = Join-Path $Bin 'yt-dlp.exe'
    Get-File 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' $ytdlp
}
Done "$ytdlp ($(Invoke-Native $ytdlp @('--version')))"

Step 'deno (нужен yt-dlp, чтобы расшифровывать ссылки YouTube)'
$deno = @((Get-Command deno -ErrorAction SilentlyContinue | Select-Object -First 1).Source,
          (Join-Path (Split-Path $ytdlp) 'deno.exe'), (Join-Path $Bin 'deno.exe')) |
    Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $deno) {
    $zip = Join-Path $Bin 'deno.zip'
    Get-File 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' $zip
    Expand-Files $zip '^deno\.exe$'
    $deno = Join-Path $Bin 'deno.exe'
}
Done $deno

Step 'ffmpeg (склейка видео и звука)'
$ffmpeg = Find-Tool 'ffmpeg' 'FFMPEG'
if (-not $ffmpeg) {
    $zip = Join-Path $Bin 'ffmpeg.zip'
    Get-File 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl-shared.zip' $zip
    Expand-Files $zip '/bin/(ffmpeg\.exe|[^/]+\.dll)$'
    $ffmpeg = Join-Path $Bin 'ffmpeg.exe'
}
Done $ffmpeg

# ---------- команда ytfast, ярлыки, автозапуск ----------

Step 'Команда ytfast, ярлык в «Пуск», автозапуск'
function Use-EnvVars([string]$Path) {
    # в .cmd пути через переменные — не ломаются, если в имени пользователя есть кириллица
    $Path.Replace($env:LOCALAPPDATA, '%LOCALAPPDATA%').Replace($env:APPDATA, '%APPDATA%').Replace($env:USERPROFILE, '%USERPROFILE%')
}
$launcher = @"
@echo off
rem Generated by install.ps1 (YT Fast). No arguments: window; with a link: console download.
if "%~1"=="" (
  start "" "$(Use-EnvVars $Pythonw)" "$(Use-EnvVars "$App\ytfast_gui.pyw")"
  exit /b
)
"$(Use-EnvVars $Python)" -X utf8 "$(Use-EnvVars "$App\ytfast.py")" %*
"@
$oem = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
[IO.File]::WriteAllText((Join-Path $Data 'ytfast.cmd'), $launcher, $oem)
Edit-UserPath -Remove $App -Add $Data  # $App в PATH — от старой ручной установки
Done "ytfast → $Data\ytfast.cmd"

$shell = New-Object -ComObject WScript.Shell
foreach ($item in @(@($MenuLnk, ''), @($AutoLnk, ' --background'))) {
    $lnk = $shell.CreateShortcut($item[0])
    $lnk.TargetPath = $Pythonw
    $lnk.Arguments = "`"$App\ytfast_gui.pyw`"$($item[1])"
    $lnk.WorkingDirectory = $App
    $lnk.IconLocation = "$env:windir\System32\imageres.dll,175"
    $lnk.Description = 'YT Fast — быстрое скачивание с YouTube (Ctrl+Alt+Y)'
    $lnk.Hotkey = ''  # горячую клавишу держит сам фоновый процесс
    $lnk.Save()
}
Done 'ярлык «YT Fast» в «Пуск» и в автозагрузке'

New-Item $UninstallKey -Force | Out-Null
$props = @{
    DisplayName = 'YT Fast'; Publisher = 'ShuraProgerMain'; InstallLocation = $App
    DisplayIcon = "$env:windir\System32\imageres.dll,175"
    UninstallString = "cmd.exe /c `"`"$App\install.cmd`" -Uninstall`""
    NoModify = 1; NoRepair = 1
}
foreach ($k in $props.Keys) { Set-ItemProperty $UninstallKey $k $props[$k] }
Done 'удаление — через «Установку и удаление программ»'

# ---------- запуск ----------

Step 'Запуск'
Stop-YTFast
Start-Process $Pythonw -ArgumentList "`"$App\ytfast_gui.pyw`"", '--background' -WorkingDirectory $App
Start-Sleep -Seconds 2
$running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*ytfast_gui.pyw*' }
if (-not $running) { throw "фоновый процесс не запустился — смотри $env:APPDATA\ytfast\gui.log" }
Done 'фоновый процесс работает'

Write-Host "`nГотово. Скопируй ссылку на ролик и нажми Ctrl+Alt+Y." -ForegroundColor Green
Write-Host 'Видео сохраняются в Загрузки\YouTube. Команда ytfast заработает в новых окнах терминала.' -ForegroundColor DarkGray
