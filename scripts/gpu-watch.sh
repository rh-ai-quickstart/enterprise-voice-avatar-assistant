#!/bin/bash
# The GPU watch: a LaunchAgent on this Mac runs scripts/setup.sh --adapt every 10 minutes, which
# redeploys when the booked GPUs no longer match the deployment (a new booking, whole GPUs instead
# of MIG slices, more or fewer of them) and does nothing otherwise. It uses this Mac's oc login:
# when the token expires the watch pauses, with one notification, until the next oc login.
#
#   scripts/gpu-watch.sh install     start it (again) for this checkout; INTERVAL=<seconds> sets the
#                                    period (default 600)
#   scripts/gpu-watch.sh uninstall   stop it and remove the LaunchAgent
#   scripts/gpu-watch.sh status      whether it runs, and the last lines of its log
#   scripts/gpu-watch.sh now         check now, as the timer would, in the background
#
# Its log is ~/.assistant-setup/logs/adapt.log, one line per check; a redeploy writes step 6's
# output there too. A redeploy waits while a voice session is live (ADAPT_MAX_WAIT minutes at most,
# default 60). Notifications: ADAPT_NOTIFY=0 at install turns them off.
# On Linux it prints a crontab line instead. Written for the bash 3.2 that macOS ships.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=io.github.rh-ai-quickstart.assistant-gpu-watch
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
STATE_DIR="${STATE_DIR:-$HOME/.assistant-setup}"
LOG="$STATE_DIR/logs/adapt.log"
INTERVAL="${INTERVAL:-600}"
TARGET="gui/$(id -u)"

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
loaded() { launchctl print "$TARGET/$LABEL" >/dev/null 2>&1; }

install_agent() {
  if [ "$(uname -s)" != Darwin ]; then
    echo "Not a Mac: add this line with crontab -e to check every 10 minutes:"
    echo "*/10 * * * * $ROOT/scripts/setup.sh --adapt >/dev/null 2>&1"
    return 0
  fi
  case "$INTERVAL" in ''|*[!0-9]*) echo "INTERVAL=$INTERVAL: give seconds, for example 600"; return 1;; esac
  grep -q '^CONFIRMED_TARGET=' "$STATE_DIR/state.env" 2>/dev/null \
    || echo "Note: setup has not deployed from this Mac yet; the watch does nothing until scripts/setup.sh has run once."
  # a linked worktree is usually someone's branch: the watch deploys the chart of the checkout it runs from
  if [ "$(git -C "$ROOT" rev-parse --git-dir 2>/dev/null)" != "$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null)" ]; then
    echo "Note: $ROOT is a linked git worktree; the watch deploys the chart from here. Install it from the main checkout instead, unless that is what you want."
  fi
  mkdir -p "$HOME/Library/LaunchAgents" "$STATE_DIR/logs"
  local env_extra=""
  [ -n "${KUBECONFIG:-}" ] && env_extra+="    <key>KUBECONFIG</key><string>$(xml "$KUBECONFIG")</string>"$'\n'
  [ -n "${ADAPT_NOTIFY:-}" ] && env_extra+="    <key>ADAPT_NOTIFY</key><string>$(xml "$ADAPT_NOTIFY")</string>"$'\n'
  [ -n "${ADAPT_MAX_WAIT:-}" ] && env_extra+="    <key>ADAPT_MAX_WAIT</key><string>$(xml "$ADAPT_MAX_WAIT")</string>"$'\n'
  [ "$STATE_DIR" != "$HOME/.assistant-setup" ] && env_extra+="    <key>STATE_DIR</key><string>$(xml "$STATE_DIR")</string>"$'\n'
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(xml "$ROOT/scripts/setup.sh")</string>
    <string>--adapt</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
$env_extra  </dict>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$(xml "$STATE_DIR/logs/gpu-watch-launchd.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$STATE_DIR/logs/gpu-watch-launchd.log")</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null || { echo "the LaunchAgent file did not validate: $PLIST"; return 1; }
  loaded && launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$TARGET" "$PLIST"
  echo "GPU watch on: scripts/setup.sh --adapt every $((INTERVAL / 60)) minutes from $ROOT, and now."
  echo "Log: $LOG (scripts/gpu-watch.sh status shows its last lines)."
  oc whoami >/dev/null 2>&1 || echo "Note: oc is not logged in now; the watch waits for the next oc login."
}

uninstall_agent() {
  [ "$(uname -s)" = Darwin ] || { echo "Not a Mac: remove the setup.sh --adapt line with crontab -e"; return 0; }
  loaded && launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "GPU watch off (LaunchAgent removed). Its log stays in $LOG."
}

status_agent() {
  if [ "$(uname -s)" = Darwin ] && loaded; then
    echo "GPU watch on:"
    launchctl print "$TARGET/$LABEL" | grep -E '^\s*(state|run interval|last exit code|runs) =' | sed 's/^[[:space:]]*/  /'
    grep -o '<string>[^<]*setup.sh</string>' "$PLIST" 2>/dev/null | sed 's/<[^>]*>//g; s/^/  runs /'
  else
    echo "GPU watch off (scripts/gpu-watch.sh install starts it)."
  fi
  if [ -f "$LOG" ]; then echo "Last checks ($LOG):"; tail -n "${LINES_SHOWN:-5}" "$LOG" | sed 's/^/  /'; fi
}

case "${1:-status}" in
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  now) loaded && launchctl kickstart "$TARGET/$LABEL" && echo "Checking now; the result appears in $LOG within a minute." || echo "GPU watch off; scripts/gpu-watch.sh install starts it." ;;
  -h|--help|help) sed -n 2,16p "$0" ;;
  *) echo "usage: scripts/gpu-watch.sh install|uninstall|status|now"; exit 1 ;;
esac
