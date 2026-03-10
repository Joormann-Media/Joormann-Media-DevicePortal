#!/bin/bash
source "/opt/sentinels/config/sentinel.conf"
CONFIG_FILE="/opt/sentinels/config/folder.conf"
LOGFILE="/opt/sentinels/logs/folder_sentinel.log"

HOST=$(hostname)

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Folder config file $CONFIG_FILE fehlt – kein Start. Tschüssikowski!" >> "$LOGFILE"
  exit 1
fi

if [ ! -s "$CONFIG_FILE" ]; then
  echo "Kein Ordner zu überwachen. Das Leben ist langweilig. Exit." >> "$LOGFILE"
  exit 0
fi

while IFS= read -r watchfolder; do
  if [ ! -d "$watchfolder" ]; then continue; fi
  inotifywait -m -r -e create,delete,modify,move "$watchfolder" --format '%T|%e|%w%f' --timefmt '%Y-%m-%d %H:%M:%S' | while IFS="|" read -r time event file; do
    curl -s -H "Content-Type: application/json" -X POST -d @- "$DISCORD_WEBHOOK" <<JSON
{
  "username": "🗂 Folder-Sentinel",
  "embeds": [{
    "title": "⚡ Ordner-Ereignis",
    "color": 3447003,
    "fields": [
      { "name": "Ereignis", "value": "$event", "inline": true },
      { "name": "Datei/Pfad", "value": "$file", "inline": false },
      { "name": "Host", "value": "$HOST", "inline": true },
      { "name": "Zeitpunkt", "value": "$time", "inline": true }
    ]
  }]
}
JSON
    echo "[$time] $event: $file" >> "$LOGFILE"
  done &
done < "$CONFIG_FILE"

wait
