#!/bin/sh
# Install / update Omnify in the current user's environment (no root).
#
# What it does (idempotent, safe to re-run after code changes):
#   1. Links the `omnify` package into the user site-packages, so
#      `python3 -m omnify` resolves everywhere (the symlink means the
#      installed launcher always runs the latest project code).
#   2. Installs the `omnify` launcher script into ~/.local/bin.
#   3. Installs the application icon into the hicolor icon theme.
#   4. Installs the .desktop entry (app menu, KRunner, rofi, ...).
#   5. Refreshes the desktop database.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HOME_DIR=${HOME:-$(eval echo "~")}
USER_SITE=$(python3 -c "import site; print(site.getusersitepackages())")

# 1. package (symlink -> always the latest code)
mkdir -p "$USER_SITE"
ln -sfn "$ROOT/omnify" "$USER_SITE/omnify"

# 2. launcher
BIN_DIR="$HOME_DIR/.local/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/omnify" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 -m omnify "$@"
EOF
chmod +x "$BIN_DIR/omnify"

# 2b. standalone private browser launcher
cat > "$BIN_DIR/omnify-browser" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 -m omnify.browser_launcher "$@"
EOF
chmod +x "$BIN_DIR/omnify-browser"

# 3. icon
ICON_SRC="$ROOT/omnify_logo.png"
if [ -f "$ICON_SRC" ]; then
    ICON_DIR="$HOME_DIR/.local/share/icons/hicolor/256x256/apps"
    mkdir -p "$ICON_DIR"
    cp "$ICON_SRC" "$ICON_DIR/omnify.png"
    ICON_NAME="omnify"
else
    ICON_NAME="applications-utilities"
fi

# 4. autostart (timers/stopwatch must resume after a reboot)
AUTOSTART_DIR="$HOME_DIR/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/omnify.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Omnify
Comment=Resume running timers and the stopwatch
Exec=$BIN_DIR/omnify
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

# 5. desktop entry
APPS_DIR="$HOME_DIR/.local/share/applications"
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/omnify.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Omnify
GenericName=Desktop Toolbox
Comment=Calculator, calendar, timer, stopwatch and task manager in one app
Exec=$BIN_DIR/omnify
Icon=$ICON_NAME
Terminal=false
Categories=Utility;
StartupNotify=true
EOF

# 5b. standalone browser application entry
cat > "$APPS_DIR/omnify-browser.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Omnify Browser
GenericName=Private Web Browser
Comment=LibreWolf/Firefox with Brave-style protections, managed by Omnify
Exec=$BIN_DIR/omnify-browser %U
TryExec=$BIN_DIR/omnify-browser
Icon=$ICON_NAME
Terminal=false
Categories=Network;WebBrowser;Utility;
MimeType=text/html;text/xml;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;x-scheme-handler/about;
StartupNotify=true
StartupWMClass=LibreWolf
EOF

# 5c. standalone Tor Browser entry (maximum privacy)
cat > "$APPS_DIR/omnify-tor-browser.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Omnify Tor Browser
GenericName=Maximum Privacy Web Browser
Comment=Tor Browser managed by Omnify; uses the Tor network and Tor Browser hardening
Exec=$BIN_DIR/omnify-browser --tor %U
TryExec=$BIN_DIR/omnify-browser
Icon=$ICON_NAME
Terminal=false
Categories=Network;WebBrowser;Utility;
MimeType=text/html;text/xml;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;
StartupNotify=true
StartupWMClass=Tor Browser
EOF

# 5d. one application entry per tool.  Every entry asks the single running
#     Omnify instance to switch to that module, so alarms/timers stay owned by
#     one process and never ring twice.
add_module_entry() {
    module_id=$1
    module_name=$2
    module_icon=$3
    module_comment=$4
    cat > "$APPS_DIR/omnify-$module_id.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Omnify $module_name
GenericName=Omnify module
Comment=$module_comment
Exec=$BIN_DIR/omnify --module $module_id
TryExec=$BIN_DIR/omnify
Icon=$module_icon
Terminal=false
Categories=Utility;
StartupNotify=true
EOF
}

add_module_entry dashboard "Home" "go-home" "Omnify dashboard"
add_module_entry calculator "Calculator" "accessories-calculator" "Scientific and graphing calculator"
add_module_entry browser "Browser" "web-browser" "Private browser cockpit"
add_module_entry calendar "Calendar" "x-office-calendar" "Local calendar, events and reminders"
add_module_entry timer "Timer" "alarm-symbolic" "Countdown timers that keep running"
add_module_entry stopwatch "Stopwatch" "chronometer" "Stopwatch with laps"
add_module_entry alarm "Alarm clock" "alarm-clock" "Persistent alarm clock"
add_module_entry taskmanager "Task Manager" "utilities-system-monitor" "Processes and performance"
add_module_entry notes "Notes" "accessories-text-editor" "Local notes and quick capture"
add_module_entry settings "Settings" "preferences-system" "Themes, language and privacy settings"

# 6. refresh databases (best effort)
update-desktop-database "$APPS_DIR" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 2>/dev/null || true

echo "Omnify installed:"
echo "  launcher : $BIN_DIR/omnify"
echo "  browser  : $BIN_DIR/omnify-browser"
echo "  package  : $USER_SITE/omnify -> $ROOT/omnify"
echo "  desktop  : $APPS_DIR/omnify.desktop"
echo "  autostart: $AUTOSTART_DIR/omnify.desktop (timers survive reboots)"
echo "Run 'omnify' to start it; it now also appears in the app menu."
echo "Uninstall: $ROOT/uninstall.sh (backups are preserved)."
