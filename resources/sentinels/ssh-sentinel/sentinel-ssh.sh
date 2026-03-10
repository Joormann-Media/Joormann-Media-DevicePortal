#!/bin/bash
source "/opt/sentinels/config/sentinel.conf"

user=$PAM_USER
ruser=$PAM_RUSER
rhost=$PAM_RHOST
tty=$PAM_TTY
service=$PAM_SERVICE
pamtype=$PAM_TYPE

time=$(date '+%Y-%m-%d %H:%M:%S')
host=$(hostname)
local_ip=$(hostname -I | awk '{print $1}')
public_ip=$(curl -s ifconfig.me || echo "n/a")

# Farbe nach Event
color=3447003  # blau default
title="🔑 SSH-Ereignis"

if [ "$pamtype" = "open_session" ]; then
    color=5763719   # grün
    title="✅ SSH-Login erfolgreich"
elif [ "$pamtype" = "close_session" ]; then
    color=15158332  # rot
    title="🚪 SSH-Logout"
elif [ "$pamtype" = "auth" ]; then
    color=15105570  # gelb
    title="⚠️ SSH-Auth-Versuch"
fi

# Discord Push
curl -s -H "Content-Type: application/json" -X POST -d @- "$DISCORD_WEBHOOK" <<JSON
{
  "username": "🔑 SSH-Sentinel",
  "embeds": [{
    "title": "$title",
    "color": $color,
    "fields": [
      { "name": "Benutzer", "value": "$user", "inline": true },
      { "name": "Remote-User", "value": "${ruser:-none}", "inline": true },
      { "name": "Remote Host", "value": "$rhost", "inline": true },
      { "name": "Service", "value": "$service", "inline": true },
      { "name": "TTY", "value": "$tty", "inline": true },
      { "name": "Hostname", "value": "$host", "inline": true },
      { "name": "Lokale IP", "value": "$local_ip", "inline": true },
      { "name": "Öffentliche IP", "value": "$public_ip", "inline": true },
      { "name": "Zeitpunkt", "value": "$time", "inline": false }
    ]
  }]
}
JSON

# Logfile
echo "[$time] PAM($pamtype): $title – User=$user Remote=$rhost TTY=$tty" \
  >> /opt/sentinels/logs/ssh_sentinel.log

exit 0
