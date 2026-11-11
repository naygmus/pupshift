#!/bin/sh
# Установка pupshift в Puppy Linux (запускать от root из каталога с pupshift.py)
# Копирует программу в /root/pupshift, ставит зависимости и автозапуск.
set -e

if [ "$(id -u)" != "0" ]; then
    echo "Запустите от root: sudo sh install.sh" >&2
    exit 1
fi

cd "$(dirname "$0")"

if [ ! -f pupshift.py ] || [ ! -d pupshift ]; then
    echo "Не найден pupshift.py или каталог pupshift/ — запустите из каталога проекта" >&2
    exit 1
fi

DEST=/root/pupshift
mkdir -p "$DEST/pupshift"
cp pupshift.py "$DEST/"
cp pupshift/*.py "$DEST/pupshift/"
chmod +x "$DEST/pupshift.py"

# --- зависимости: проверяем, что реально не хватает ---
check_missing() {
    MISSING=""
    python3 -c "import Xlib" 2>/dev/null || MISSING="$MISSING python3-xlib"
    python3 -c "import cairo" 2>/dev/null || MISSING="$MISSING python3-cairo"
    python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null \
        || MISSING="$MISSING python3-gi gir1.2-gtk-3.0"
    # python3-gi-cairo нужен для Gdk.pixbuf_get_from_surface
    python3 -c "from gi.repository import Gdk; Gdk.pixbuf_get_from_surface" 2>/dev/null \
        || MISSING="$MISSING python3-gi-cairo"
}

check_missing
if [ -n "$MISSING" ]; then
    echo "Устанавливаю:$MISSING"
    apt-get update || true
    # shellcheck disable=SC2086
    apt-get install -y $MISSING || true
    check_missing
fi

if [ -n "$MISSING" ]; then
    echo "Не хватает пакетов, установите вручную: apt-get install -y$MISSING" >&2
    exit 1
fi

# --- автозапуск при старте X (Puppy: /root/Startup) ---
STARTUP="$HOME/Startup/pupshift.sh"
mkdir -p "$HOME/Startup"
cat > "$STARTUP" <<EOF
#!/bin/sh
# автозапуск pupshift при старте X (Puppy Linux)
[ -n "\$DISPLAY" ] || DISPLAY=:0
exec python3 "$DEST/pupshift.py" >/dev/null 2>&1 &
EOF
chmod 755 "$STARTUP"

echo "pupshift установлен: $DEST"
echo "Автозапуск: $STARTUP"
echo "Запуск сейчас: DISPLAY=:0 python3 $DEST/pupshift.py &"