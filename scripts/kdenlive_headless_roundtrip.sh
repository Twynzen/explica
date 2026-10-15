#!/usr/bin/env bash
# Real-GUI round-trip test for an exported .kdenlive, without touching the user's desktop:
#   private Weston (headless backend) + rootful Xwayland + private D-Bus session + isolated XDG dirs.
# Kdenlive is driven with real input events (XTEST via scripts/xtest.py) and its own dialogs.
# Evidence: screenshots (x11grab), progress.txt, and the file saved by Kdenlive.
#
# Requirements (all user-local, no sudo):
#   - a Weston build with headless backend (see docs/KDENLIVE.md "Prueba headless"), prefix in $WESTON_PREFIX
#   - Xwayland, ffmpeg (x11grab), dbus-run-session, python3 with libX11/libXtst available
# Usage: scripts/kdenlive_headless_roundtrip.sh <project.kdenlive> <workdir>
set -euo pipefail
SRC=${1:?project.kdenlive}
WORK=${2:?workdir}
WESTON_PREFIX=${WESTON_PREFIX:-$HOME/.cache/explica-weston-build/prefix}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$WORK"
cp "$SRC" "$WORK/test.kdenlive"
cat > "$WORK/weston.ini" <<'EOF'
[core]
idle-time=0
xwayland=false
[shell]
locking=false
panel-position=none
background-color=0xff202020
EOF
cat > "$WORK/session.sh" <<EOF
set -u
cd "$WORK"
export LD_LIBRARY_PATH=$WESTON_PREFIX/lib:$WESTON_PREFIX/lib/weston
rm -rf xdg rt; mkdir -p xdg/config xdg/data xdg/cache rt; chmod 700 rt
export XDG_RUNTIME_DIR=\$PWD/rt
unset DISPLAY WAYLAND_DISPLAY
$WESTON_PREFIX/bin/weston --backend=headless --width=1700 --height=1000 --socket=explica-wl --config=\$PWD/weston.ini --log=\$PWD/weston.log > weston.out 2>&1 &
WPID=\$!
for i in \$(seq 1 30); do [ -S rt/explica-wl ] && break; sleep 0.5; done
export WAYLAND_DISPLAY=explica-wl
Xwayland :2 -geometry 1700x1000 -retro > xwayland.log 2>&1 &
XPID=\$!
export DISPLAY=:2
for i in \$(seq 1 20); do ffmpeg -v error -y -f x11grab -i :2 -frames:v 1 shot-00-empty.png 2>/dev/null && break; sleep 1; done
export QT_QPA_PLATFORM=xcb XDG_CONFIG_HOME=\$PWD/xdg/config XDG_DATA_HOME=\$PWD/xdg/data XDG_CACHE_HOME=\$PWD/xdg/cache QT_LOGGING_RULES="*.debug=false" XDG_CURRENT_DESKTOP=weston
sha256sum test.kdenlive | cut -c1-16 > before.sha
kdenlive test.kdenlive > kd.log 2>&1 &
KPID=\$!
sleep 50
echo "T+50 kdenlive alive=\$(kill -0 \$KPID 2>/dev/null && echo yes || echo no)" > progress.txt
ffmpeg -v error -y -f x11grab -i :2 -frames:v 1 shot-01-loaded.png
NAME=\$(dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames 2>/dev/null | grep -o 'org.kde.kdenlive[^"]*' | head -1)
echo "dbus name: \$NAME" >> progress.txt
# edit 1: click the ruler (moves the playhead), G adds a guide, Return accepts the guide dialog
python3 $HERE/xtest.py :2 move 500 522 click 1 sleep 1 key g sleep 2 key Return sleep 2 >> progress.txt 2>&1
ffmpeg -v error -y -f x11grab -i :2 -frames:v 1 shot-02-guide-added.png
# edit 2: click in the timeline (focus + playhead), select all clips, cut at playhead (Shift+R)
python3 $HERE/xtest.py :2 move 400 590 click 1 sleep 1 key ctrl+a sleep 1 key shift+r sleep 3 >> progress.txt 2>&1
ffmpeg -v error -y -f x11grab -i :2 -frames:v 1 shot-03-after-cut.png
# quit → "save changes?" → Save → "backup created" notice → OK
(dbus-send --session --print-reply --dest=\$NAME /kdenlive/MainWindow_1/actions/file_quit org.qtproject.Qt.QAction.trigger >/dev/null 2>&1 &)
sleep 5
ffmpeg -v error -y -f x11grab -i :2 -frames:v 1 shot-04-quit-dialog.png
python3 $HERE/xtest.py :2 move 845 320 click 1 sleep 6 >> progress.txt 2>&1
ffmpeg -v error -y -f x11grab -i :2 -frames:v 1 shot-05-after-save-click.png
python3 $HERE/xtest.py :2 move 1080 511 click 1 sleep 10 >> progress.txt 2>&1
ffmpeg -v error -y -f x11grab -i :2 -frames:v 1 shot-06-after-ok.png
echo "sha before: \$(cat before.sha) after: \$(sha256sum test.kdenlive | cut -c1-16)" >> progress.txt
echo "kdenlive alive after save+quit=\$(kill -0 \$KPID 2>/dev/null && echo yes || echo no)" >> progress.txt
cp test.kdenlive saved-by-kdenlive.kdenlive
kill \$KPID 2>/dev/null; sleep 2; kill -9 \$KPID 2>/dev/null; kill \$XPID \$WPID 2>/dev/null; sleep 1
echo done >> progress.txt
EOF
timeout 300 dbus-run-session -- bash "$WORK/session.sh" >/dev/null 2>&1 || true
cat "$WORK/progress.txt"
echo "saved file: $WORK/saved-by-kdenlive.kdenlive"
