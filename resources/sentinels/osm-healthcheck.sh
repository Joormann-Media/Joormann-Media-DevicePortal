#!/bin/bash
source "/opt/sentinels/config/sentinel.conf"

host=$(hostname)
ip=$(hostname -I | awk '{print $1}')
time=$(date '+%Y-%m-%d %H:%M:%S')

declare -A services=(
    ["Nominatim"]="http://localhost:7071/search?q=Essen&format=json"
    ["TileServer"]="http://localhost:8083/styles/osm-bright/style.json"
    ["OpenRouteService"]="http://localhost:8082/ors/v2/health"
    # Weitere Dienste hier eintragen:
    # ["VROOM"]="http://localhost:3000"
    # ["XYZ"]="http://localhost:1234/health"
)

all_ok=true
fields_json=""

for service in "${!services[@]}"; do
    url="${services[$service]}"
    status=$(curl -s -o /dev/null -w "%{http_code}" "$url")
    if [ "$status" -eq 200 ]; then
        fields_json+="
      { \"name\": \"$service\", \"value\": \"🟢 OK\", \"inline\": true },"
    else
        fields_json+="
      { \"name\": \"$service\", \"value\": \"❌ Fehler (HTTP $status)\", \"inline\": true },"
        all_ok=false
    fi
done

# ⏪ Letztes Komma im JSON-Feld entfernen
fields_json=$(echo "$fields_json" | sed '$s/},$/}/')

# Discord JSON generieren
if [ "$all_ok" = true ]; then
    title="✅ Alle Systeme laufen"
    color=3066993
    description="Alle definierten Dienste des OSM-Stacks sind erreichbar."
else
    title="⚠️ Fehler im OSM-Stack"
    color=15158332
    description="Mindestens ein Dienst ist nicht erreichbar."
fi

# Absenden an Discord
curl -s -H "Content-Type: application/json" -X POST -d @- "$DISCORD_WEBHOOK" <<JSON
{
  "username": "📡 OSM Sentinel",
  "embeds": [{
    "title": "$title",
    "color": $color,
    "description": "$description",
    "fields": [ $fields_json ],
    "footer": { "text": "$host • $ip • $time" }
  }]
}
JSON
