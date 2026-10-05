#!/usr/bin/env bash
# Log this machine in to the BITS Goa Sophos captive portal from a terminal.
#
#   campnet_login.sh               log in (prompts for username + password)
#   campnet_login.sh --keepalive   log in, then keep the session alive in the background
#   campnet_login.sh --logout
#
# The password is read silently and sent to curl on stdin, so it never appears in
# `ps` output (these are shared machines) or in shell history.
set -euo pipefail

PORTAL="https://campnet.bits-goa.ac.in:8090"
STATE_DIR="$HOME/.campnet"
mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"

ts() { date +%s%3N; }
msg() { sed -n 's:.*<message><!\[CDATA\[\(.*\)\]\]></message>.*:\1:p'; }
urlenc() { python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.stdin.read().rstrip("\n"),safe=""))'; }

if [[ "${1:-}" == "--logout" ]]; then
  user=$(cat "$STATE_DIR/user" 2>/dev/null || { read -rp "Username: " u; echo "$u"; })
  pkill -u "$USER" -f "campnet-keepalive" 2>/dev/null || true
  printf 'mode=193&username=%s&a=%s&producttype=0' "$(printf %s "$user" | urlenc)" "$(ts)" \
    | curl -sS -m 20 --data @- "$PORTAL/logout.xml" | msg
  exit 0
fi

read -rp "Campnet username: " user
read -rsp "Campnet password: " pass; echo
resp=$(printf 'mode=191&username=%s&password=%s&a=%s&producttype=0' \
         "$(printf %s "$user" | urlenc)" "$(printf %s "$pass" | urlenc)" "$(ts)" \
       | curl -sS -m 30 --data @- "$PORTAL/login.xml")
unset pass
echo "Portal: $(echo "$resp" | msg)"
echo "$resp" | grep -q "<status><!\[CDATA\[LIVE\]\]></status>" || { echo "Login failed."; exit 1; }
printf %s "$user" > "$STATE_DIR/user"

if [[ "${1:-}" == "--keepalive" ]]; then
  pkill -u "$USER" -f "campnet-keepalive" 2>/dev/null || true
  # The portal page pings /live every 180 s; do the same so long downloads don't get cut off.
  nohup setsid bash -c '
    exec -a campnet-keepalive bash -c "
      u=\$(cat \"$1/user\")
      while sleep 170; do
        r=\$(curl -sS -m 20 \"$2/live?mode=192&username=\$u&a=\$(date +%s%3N)&producttype=0\")
        echo \"\$(date +%T) \$r\" | tail -c 300 >> \"$1/keepalive.log\"
        echo \"\$r\" | grep -q login_again && { echo \"\$(date +%T) session ended\" >> \"$1/keepalive.log\"; exit 1; }
      done"' _ "$STATE_DIR" "$PORTAL" > /dev/null 2>&1 < /dev/null &
  echo "Keep-alive running in background (log: $STATE_DIR/keepalive.log). Stop with --logout."
fi
