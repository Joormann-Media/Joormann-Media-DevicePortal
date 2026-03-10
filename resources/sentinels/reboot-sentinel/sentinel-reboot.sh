#!/bin/bash
source "/opt/sentinels/config/sentinel.conf"
# Warten bis Netzwerk online ist
timeout=60
count=0
until ping -c1 discord.com >/dev/null 2>&1; do
  sleep 3
  count=$((count+3))
  [ $count -ge $timeout ] && break
done

host=$(hostname)
ip=$(hostname -I | awk '{print $1}')
time=$(date '+%Y-%m-%d %H:%M:%S')

curl -s -H "Content-Type: application/json" -X POST -d @- "$DISCORD_WEBHOOK" <<JSON
{
  "username": "🚀 Reboot-Watch",
  "embeds": [{
    "title": "🔄 System wurde neugestartet",
    "color": 65280,
    "fields": [
      { "name": "Hostname", "value": "$host", "inline": true },
      { "name": "IP-Adresse", "value": "$ip", "inline": true },
      { "name": "Zeitpunkt", "value": "$time", "inline": false }
    ]
  }]
}
JSON

echo "[$time] Reboot-Meldung gesendet." >> /opt/sentinels/logs/reboot_sentinel.log
