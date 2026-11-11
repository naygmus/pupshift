# pupshift

Переключатель раскладки последнего слова / выделения для Puppy Linux (X11, JWM, без systemd).
Мини-аналог Punto Switcher.

## Что делает

- **Горячая клавиша** (по умолчанию `Pause/Break`, настраивается) — меняет раскладку
  последнего набранного слова: `ghbdtn ` → `привет `, `привет ` → `ghbdtn `.
- Если в активном окне **есть выделение** — меняет раскладку выделенного участка.
- Направление определяется автоматически: есть кириллица → латиница, иначе → кириллица.
- Левый клик по значку в трее — переключить раскладку (us ↔ ru).
- Значок показывает текущую раскладку: **RU / EN** (или `off`, если стоп).

Работает в Geany, Leafpad, Chrome (веб-приложениях), терминалах (с ограничениями).
Поддерживает смешанный текст (`.kf` ↔ `юла`, `,kf` ↔ `бла`, `[ey` ↔ `хун`, `них` ↔ `yb[`),
фразы с пробелами (`ghbdtn ghbdtn` ↔ `привет привет`), знаки `!`, `?`.

## Требования

- **X11** (не Wayland). Puppy использует X11 — не актуально.
- `python3`, `python3-xlib`, `python3-gi`, `python3-gi-cairo`, `python3-cairo`,
  `gir1.2-gtk-3.0`.
- **`setxkbmap`** с `grp:alt_shift_toggle`. Проверить:
  ```sh
  setxkbmap -query | grep options
  # должно быть: options: ...grp:alt_shift_toggle...
  ```
  Если нет — настроить (см. «Настройка XKB» ниже).

## Установка

```sh
cd pupshift
sudo sh install.sh    # от root; копирует в /root/pupshift, ставит зависимости и автозапуск
```

`install.sh`:

- проверяет, что запущен от root и из каталога проекта;
- ставит только те пакеты, которых реально не хватает (проверка через `import`);
- копирует `pupshift.py` и каталог `pupshift/` в `/root/pupshift`;
- создаёт `/root/Startup/pupshift.sh` для автозапуска при старте X.

Запуск вручную:

```sh
DISPLAY=:0 python3 /root/pupshift/pupshift.py &
```

## Настройка XKB

pupshift эмулирует `Ctrl+Shift+Left` для выделения последнего слова. Если у вас
включён `grp:ctrl_shift_toggle`, то `Ctrl+Shift` **переключает раскладку** раньше,
чем приходит `Left` — pupshift не сможет выделить слово. Нужно `grp:alt_shift_toggle`
(или любая другая пара, но не `Ctrl+Shift`).

Проверка и настройка:

```sh
# Очистить все options и поставить только alt_shift_toggle
setxkbmap -layout us,ru -option
setxkbmap -layout us,ru -option grp:alt_shift_toggle

# Проверить
setxkbmap -query
# ожидаемое: options: grp:alt_shift_toggle
```

**Важно**: в Puppy `/etc/xdg/autostart/xorg-mousekeys.desktop` при каждом старте X
делает `setxkbmap -option keypad:pointerkeys` — это **стирает** все options,
включая `grp:alt_shift_toggle`. Чтобы настройки сохранялись, создайте скрипт
в `~/Startup`:

```sh
cat > ~/Startup/xkb.sh <<'XX'
#!/bin/sh
[ -n "$DISPLAY" ] || DISPLAY=:0
sleep 1
setxkbmap -layout us,ru -option keypad:pointerkeys,grp:alt_shift_toggle
XX
chmod +x ~/Startup/xkb.sh
```

`keypad:pointerkeys` сохраняем (иначе теряем управление мышью с numpad),
`grp:alt_shift_toggle` — наше переключение.

## Автозапуск

Puppy запускает скрипты из `/root/Startup` при старте X. `install.sh` создаёт
`/root/Startup/pupshift.sh`; в меню значка есть пункт «Автозапуск» (вкл/выкл),
который создаёт/удаляет этот файл.

## Настройка

Конфиг: `~/.config/pupshift/config.json` (клавиша и модификаторы, автозапуск).

Меню значка (правый клик):

- **Клавиша переключения…** — диалог выбора сочетания. `Esc` — отмена,
  `Enter`/`Применить` — применить. Чистые модификаторы игнорируются, нужна
  основная клавиша.
- **Автозапуск** — вкл/выкл скрипт в `~/Startup`.
- **Переключение активно** — стоп/пуск перехвата клавиши (значок → `off`).
- **Выход**.

Занятые сочетания отклоняются: `XGrabKey` возвращает `BadAccess`, если клавишу
уже держит другая программа. Диалог остаётся открытым с сообщением об ошибке —
выберите другое сочетание.

## Как это работает

- **Захват горячей клавиши** — `XGrabKey` на корневом окне. Захватываются все
  комбинации `Lock` / `Mod2` / `Mod5` (CapsLock/NumLock/ScrollLock), чтобы
  срабатывало при любом их состоянии.
- **Чтение выделения** — `PRIMARY` selection. Проверяется, что владелец PRIMARY
  связан с фокусным окном (`_related` / `WM_CLASS`) или текст похож на
  пользовательский ввод (fallback для Chrome, где PRIMARY держит служебное
  окно с другим WM_CLASS). Отсеиваются устаревшие PRIMARY и shell-команды.
- **Последнее слово** — синтетические `Ctrl+Shift+Left` (`XTest`), затем чтение
  `PRIMARY`; результат принимается только если `PRIMARY` изменился.
  `_extend_selection_left` дотягивает пунктуацию (`.`, `,`, `;`, `[`, `]`, `'`),
  которую word-select не включает.
- **Замена** — `Delete` (снимает выделение) + посимвольная печать через `XTest`.
  Для каждого символа выбирается нужная XKB-группа:
  - Кириллица → RU-группа (`to_latin('ю')` → `.` → keycode 60, в RU-группе = `ю`).
  - US-символы → US-группа.
  - Пунктуация и цифры → по контексту (preferred).
  - Keycodes берутся из hardcoded-таблицы `_US_KEYCODES` (PC104): это надёжнее
    `keysym_to_keycode`, который на нестандартных XKB-сборках возвращает keycode
    «чужой» группы (`.` → 61 вместо 60).
- **Переключение XKB-группы** — не через `XkbLockGroup` (он меняет только master,
  оставляя slave XTest в старой группе — рассинхрон для XInput2-приложений
  вроде Chrome), а через **симуляцию `Alt+Shift`** (XTest). Это единственный
  надёжный путь: XKB обновляет группу на всех slave-устройствах, Chrome
  получает смену раскладки как пользовательскую. Между keydown/keyup выдерживаем
  паузы (~0.35 с), иначе XKB не успевает распознать toggle.
- **После конвертации** раскладка остаётся по последнему напечатанному символу:
  `ghbdtn → привет` → RU, `привет → ghbdtn` → US. Удобно продолжать печатать.

## Ограничения

- **Chrome/веб**: автоконвертация по Pause без выделения **не работает** —
  синтетический `Ctrl+Shift+Left` в contenteditable не создаёт визуального
  выделения (PRIMARY не обновляется). В Chrome работает **только выделение
  мышью + Pause**. Это архитектурная особенность Chromium, не баг pupshift.
- **Смешанный текст с точками**: `привет. ghbdtn.` → `ghbdtn/ ghbdtn/`
  (русская `.` и US-точка живут на разных keycodes — `.` в `_RU_TO_US` = `/`).
  Конвертация по физическому маппингу (Punto Switcher работает так же).
  Опция `preserve_punctuation` — в планах.
- **Терминалы** (xterm, urxvt): word-select может не сработать — нет выделения,
  ничего не происходит.
- **Wayland**: не поддерживается (Puppy — X11).
- **Раскладки** определяются из `setxkbmap -query` (нужны `us` и `ru` в `layout`).
- Символы вне US-клавиатуры (`€`, emoji) пропускаются.

## Отладка

```sh
PUPSHIFT_DEBUG=1 DISPLAY=:0 python3 /root/pupshift/pupshift.py
```

Логи с префиксом `[pupshift]`:

- `km check:` — keysyms для keycodes 59/60/61 в US/RU (проверка раскладки).
- `force_toggle: before=X after=Y` — переключилась ли XKB-группа (`X != Y` — ок).
- `type_char: 'ю' latin='.' kc=60 shift=False group=1` — как печатается символ.
- `last_word: post_text='...'` — что прочитано после combo.
- `active_selection:` — что прочитано из выделения.
- `do_switch: text=... last_seen=...` — отсеивание stale PRIMARY.

Ключевое при проблемах:

- Если `force_toggle: before=X after=X` — `Alt+Shift` не сработал (нет
  `grp:alt_shift_toggle` в `setxkbmap -query`).
- Если `type_char: ... kc=61` для `.` — `_US_KEYCODES` неверный (не должно быть,
  PC104 стабилен).
- Если `active_selection: skip (ctx mismatch, looks like command)` — fallback
  отклонил текст как shell-команду; пришлите текст, ослабим проверку.

## Структура

```
pupshift.py             — точка входа
pupshift/transform.py   — таблицы ЙЦУКЕН↔QWERTY (чистая логика, есть тесты)
pupshift/xkb.py         — XkbGetState/XkbLockGroup через ctypes + XSync
pupshift/xswitch.py     — X11-движок: захват, PRIMARY, XTest, замена
pupshift/app.py         — трей-значок и меню (GTK3)
pupshift/keydialog.py   — диалог выбора клавиши
pupshift/config.py      — чтение/запись config.json
tests/test_transform.py — тесты трансформации (pytest)
install.sh              — установка в /root/pupshift + автозапуск
```

## Тесты

```sh
python3 -m pytest tests/ -v
```

## Лицензия

MIT (см. LICENSE).