#!/usr/bin/env bash
# KR-bot Console - terminal dashboard for the krbot user service (the desktop uses the
# KR-bot Monitor window instead; this is for ssh sessions).
# Launched by the "KR-bot Console" desktop icon; also works over `ssh -t`.
#
# Shows the service state, the latest robot status line (mode, outputs, gamepad, motors, loop
# rate, facts, goal) and recent events, refreshed every second. Single-key commands:
#   SPACE/e E-STOP     s start     x stop     r restart     d start DRY RUN
#   b enable/disable at boot      u rebuild krbot      l full log      q quit (krbot keeps running)
#   krbot-console.sh --once   -> draw a single frame and exit
set -u
UNIT=krbot.service
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SC="systemctl --user"

if [ -t 1 ]; then
  B=$(tput bold); R=$(tput sgr0); RED=$(tput setaf 1); GRN=$(tput setaf 2); YEL=$(tput setaf 3)
  BLU=$(tput setaf 4); CYN=$(tput setaf 6); DIM=$(tput dim 2>/dev/null || true); REV=$(tput rev)
else
  B= R= RED= GRN= YEL= BLU= CYN= DIM= REV=
fi
MSG=""

prop() { $SC show "$UNIT" -p "$1" --value 2>/dev/null; }
log_lines() { journalctl --user -u "$UNIT" -o cat -n "${1:-300}" --no-pager 2>/dev/null; }

draw() {
  local cols rows active sub pid since restarts enabled args status mode
  cols=$(tput cols 2>/dev/null || echo 100)
  rows=$(tput lines 2>/dev/null || echo 35)
  active=$(prop ActiveState); sub=$(prop SubState); pid=$(prop MainPID)
  since=$(prop ActiveEnterTimestamp); restarts=$(prop NRestarts); enabled=$($SC is-enabled "$UNIT" 2>/dev/null)
  args=$($SC show-environment 2>/dev/null | sed -n 's/^KRBOT_ARGS=//p')

  printf "%s KR-bot Console %s  %s\n" "$REV$B" "$R" "$(date '+%H:%M:%S')  -  SPACE e-stop | s start | x stop | r restart | d dry-run | b boot | u rebuild | l log | q quit"
  printf '%*s\n' "$cols" '' | tr ' ' '-'

  case "$active" in
    active) col=$GRN ;; activating|reloading) col=$YEL ;; *) col=$RED ;;
  esac
  printf " Service   %s%s%-10s%s %-9s pid %-7s restarts %-3s boot: %-9s %s\n" "$B" "$col" "$active" "$R" "($sub)" \
    "${pid:-0}" "${restarts:-0}" "${enabled:-?}" "${args:+${YEL}[$args]$R}"
  [ "$active" = active ] && printf " Since     %s\n" "$since"
  # match the python process itself, not shells/editors that merely mention the file name
  if pgrep -f '^(/usr/bin/)?python3 [^ ]*(krc_robot_gui|tools/teleop)\.py' >/dev/null; then
    printf " %s%sWARNING%s the Python KR-Robot Control app / teleop.py is running - it competes for the motor port and gamepad\n" "$B" "$YEL" "$R"
  fi
  [ -n "$MSG" ] && printf " %s%s%s\n" "$CYN" "$MSG" "$R"
  echo

  # latest status line from krbot's core: "MODE L=.. R=.. | pad X | motors Y | loop N Hz | facts N | goal G | reason"
  status=$(log_lines 400 | grep -F '[core]' | grep -F '| pad ' | tail -1 | sed 's/^.*\[core\] //')
  if [ "$active" != active ]; then
    printf " %s%s ROBOT  NOT RUNNING %s\n" "$REV$B" "$DIM" "$R"
  elif [ -z "$status" ]; then
    printf " %sROBOT  starting...%s\n" "$YEL" "$R"
  else
    mode=$(echo "$status" | awk '{print $1}')
    case "$mode" in ARMED) col=$GRN ;; ESTOP) col=$RED ;; *) col=$YEL ;; esac
    IFS='|' read -r f_out f_pad f_mot f_loop f_facts f_goal f_reason <<<"$status"
    printf " %s%s  %-8s %s   %s\n" "$REV$B" "$col" "$mode" "$R" "${f_reason# }"
    printf "   Outputs  %s\n" "$(echo "$f_out" | awk '{$1=""; print}' | sed 's/^ //')"
    printf "   Gamepad  %s\n" "${f_pad# pad }"
    m="${f_mot# motors }"; m="${m% }"
    [ "$m" = ok ] && m="$GRN$m$R" || m="$RED$B$m$R"
    printf "   Motors   %s\n" "$m"
    printf "   Loop     %s    Facts %s    Goal %s\n" "${f_loop# loop }" "${f_facts# facts }" "${f_goal# goal }"
  fi
  echo
  printf " %sRecent events%s\n" "$B" "$R"
  local room=$(( rows - 16 )); [ "$room" -lt 3 ] && room=3
  log_lines 400 | grep -vF '| pad ' | tail -n "$room" | cut -c1-"$cols" | while IFS= read -r l; do
    case "$l" in
      *ERROR*) printf " %s%s%s\n" "$RED" "$l" "$R" ;;
      *WARN*|*E-STOP*|*ESTOP*) printf " %s%s%s\n" "$YEL" "$l" "$R" ;;
      *) printf " %s\n" "$l" ;;
    esac
  done
}

ensure_unit() {
  if ! $SC cat "$UNIT" &>/dev/null; then
    MSG="krbot.service not installed - run deploy.ps1 (remote-setup.sh installs it)"
    return 1
  fi
  if [ ! -x "$APP_DIR/krbot/build/krbot" ]; then
    MSG="krbot not built yet - press u to build"
    return 1
  fi
}

if [ "${1:-}" = "--once" ]; then   # one frame, no input (for checks over plain ssh)
  draw
  exit 0
fi

# Render without flicker: build the whole frame first, then home the cursor and overwrite in
# place (clearing to end-of-line on each line and to end-of-screen after the last one).
EL=$(tput el 2>/dev/null); HOME_=$(tput cup 0 0 2>/dev/null); ED=$(tput ed 2>/dev/null)
render() {
  local frame
  frame=$(draw)
  printf '%s' "$HOME_"
  while IFS= read -r l; do printf '%s%s
' "$l" "$EL"; done <<<"$frame"
  printf '%s' "$ED"
}

trap 'tput cnorm 2>/dev/null; echo; exit 0' INT TERM
tput civis 2>/dev/null
tput clear 2>/dev/null
while true; do
  render
  key=""
  # rc > 128 = timeout (normal refresh); anything else = no usable stdin -> don't spin
  IFS= read -rsn1 -t 1 key || { [ $? -le 128 ] && sleep 1; }
  case "$key" in
    " "|e|E)
      if [ "$(prop ActiveState)" = active ]; then
        $SC kill -s USR1 "$UNIT" && MSG="E-STOP sent - re-arm from the gamepad (START)"
      else
        MSG="krbot not running - motors are not being driven"
      fi ;;
    s) ensure_unit && { $SC unset-environment KRBOT_ARGS; $SC start "$UNIT" && MSG="started (real motor board)"; } ;;
    d) ensure_unit && { $SC set-environment KRBOT_ARGS=--dry-run; $SC restart "$UNIT" && MSG="started DRY RUN (simulated motors)"; } ;;
    x) $SC stop "$UNIT" && MSG="stopped - motors zeroed" ;;
    r) ensure_unit && { $SC restart "$UNIT" && MSG="restarted (comes back DISARMED)"; } ;;
    b) if [ "$($SC is-enabled "$UNIT" 2>/dev/null)" = enabled ]; then
         $SC disable "$UNIT" 2>/dev/null && MSG="boot autostart DISABLED"
       else
         $SC enable "$UNIT" 2>/dev/null && MSG="boot autostart ENABLED (starts at login; robot comes up DISARMED)"
       fi ;;
    u) tput cnorm; tput clear
       NO_TESTS=1 bash "$APP_DIR/scripts/build-krbot.sh"; rc=$?
       [ $rc -eq 0 ] && MSG="rebuilt - press r to restart onto the new binary" || MSG="BUILD FAILED (exit $rc)"
       read -rsn1 -t 3 _ ; tput civis; tput clear ;;
    l) tput cnorm; journalctl --user -u "$UNIT" -e --no-pager | less -R +G; tput civis; tput clear ;;
    q|Q) tput cnorm; tput clear; echo "KR-bot Console closed - krbot is $(prop ActiveState)"; exit 0 ;;
  esac
done
