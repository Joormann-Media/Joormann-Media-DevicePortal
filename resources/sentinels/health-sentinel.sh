#!/bin/bash
source "/opt/sentinels/config/sentinel.conf"

host=$(hostname)
ip=$(hostname -I | awk '{print $1}')
time=$(date '+%Y-%m-%d %H:%M:%S')

# Kennzahlen sammeln
uptime_info=$(uptime -p)
load_avg=$(cat /proc/loadavg | awk '{print $1" "$2" "$3}')
mem_usage=$(free -h | awk '/Mem:/ {print $3 "/" $2}')
disk_usage=$(df -h / | awk 'NR==2 {print $3 " / " $2 " (" $5 ")"}')

curl -s -H "Content-Type: application/json" -X POST -d @- "$DISCORD_WEBHOOK" <<JSON
{
  "username": "📡 Health-Sentinel",
  "embeds": [{
    "title": "✅ Server-Status: $host",
    "color": 3447003,
    "fields": [
      { "name": "IP", "value": "$ip", "inline": true },
      { "name": "Zeit", "value": "$time", "inline": true },
      { "name": "Uptime", "value": "$uptime_info", "inline": false },
      { "name": "Load Average", "value": "$load_avg", "inline": true },
      { "name": "RAM", "value": "$mem_usage", "inline": true },
      { "name": "Disk /", "value": "$disk_usage", "inline": true }
    ]
  }]
}
JSON
