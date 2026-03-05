# Security Audit: Joormann-Media DevicePortal

## Abstract
Dieses Audit bewertet die aktuelle Sicherheitslage des DevicePortals anhand des Ist-Codes. Jede Feststellung enthält Priorität, Evidenz und konkrete Fix-Strategie.

## Executive Summary
- Das Portal ist funktional, aber aktuell als **vertrauenswürdiges LAN-Tool** implementiert, nicht als gehärtete Internet-API.
- Kritisch sind vor allem fehlende Authentisierung/Autorisierung auf mutierenden Endpoints sowie zu detaillierte Fehlerausgaben.

## Findings

### P0-1: Keine Authentisierung/Autorisierung auf sensitiven Endpoints
**Impact:** Jeder Client mit Netz-Zugriff kann Panel-Linking ändern, Fingerprint neu schreiben, Plan ziehen.

**Evidence**
- `/api/panel/register` ohne Auth: [app/api/routes_panel.py:150](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_panel.py:150)
- `/api/panel/unlink` ohne Auth: [app/api/routes_panel.py:228](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_panel.py:228)
- `/api/plan/pull` ohne Auth: [app/api/routes_plan.py:19](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_plan.py:19)
- `/api/fingerprint/refresh` ohne Auth: [app/api/routes_status.py:51](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_status.py:51)

**Recommendation (minimal-invasiv)**
1. Shared secret per header (`X-Device-Admin-Key`) einführen.
2. Optional nur Requests aus RFC1918-Netzen zulassen.
3. Für UI-POSTs CSRF-Token ergänzen, falls Session-Modell eingeführt wird.

---

### P0-2: Interne Exception-Details werden an Clients geleakt
**Impact:** Offenlegung interner Pfade/Bibliotheken/Fehlertexte, nützlich für Angreifer.

**Evidence**
- Globaler Handler gibt `detail=str(exc)` zurück: [app/__init__.py:28](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/__init__.py:28)

**Recommendation**
- Client-seitig nur generische Fehler-ID zurückgeben.
- Detaillierte Exception nur serverseitig loggen.

---

### P1-1: Keine Rate Limits auf kritischen Endpoints
**Impact:** Brute-force/Spam gegen Register/Ping/Test-URL/Plan-Pull möglich.

**Evidence**
- Keine Rate-Limit Middleware oder Checks im Repo gefunden.

**Recommendation**
- Flask-Limiter pro Endpoint-Gruppe (`/api/panel/*`, `/api/plan/*`) einführen.
- Zusätzlich Fail2ban/Proxy-Ratelimit auf Reverse Proxy Ebene.

---

### P1-2: Token/Secrets werden im UI angezeigt bzw. vorbefüllt
**Impact:** Shoulder-surfing und Browser-Leak-Risiko.

**Evidence**
- Registrierungstoken als Value im Input: [app/templates/index.html:55](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/templates/index.html:55)
- Auth-Key wird maskiert angezeigt (gut), aber Gerätedaten sind vollständig sichtbar: [app/templates/index.html:72](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/templates/index.html:72)

**Recommendation**
- `registration_token` nicht persistiert vorbefüllen oder per Toggle/Masking anzeigen.
- Optional “copy once” UX statt Plaintext-Field.

---

### P1-3: Inkonsistente Behandlung von Schreibfehlern bei JSON-Persistenz
**Impact:** API meldet Erfolg, obwohl Persistenz fehlschlägt (je nach Call-Site).

**Evidence**
- `write_json` liefert `(ok, err)`: [app/core/jsonio.py:18](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/core/jsonio.py:18)
- Mehrere Call-Sites ignorieren Rückgabe, z. B. [app/core/device.py:54](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/core/device.py:54), [app/core/config.py:67](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/core/config.py:67), [app/api/routes_panel.py:29](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_panel.py:29)

**Recommendation**
- Einheitlicher Persist-Wrapper mit hartem Fehlerpfad und konsistentem HTTP-500 bei kritischen Writes.

---

### P1-4: API läuft unverschlüsselt auf 0.0.0.0:5070
**Impact:** Ohne TLS-Termination sind Secrets im LAN abgreifbar.

**Evidence**
- Gunicorn bindet `0.0.0.0:5070`: [docs/systemd/device-portal.service:12](/home/djanebmb/projects/Joormann-Media-Deviceportal/docs/systemd/device-portal.service:12)
- Dev-Run ebenfalls unverschlüsselt: [scripts/dev_run.sh:5](/home/djanebmb/projects/Joormann-Media-Deviceportal/scripts/dev_run.sh:5)

**Recommendation**
- Reverse Proxy mit TLS erzwingen; Port 5070 nur localhost oder internes VLAN.

---

### P2-1: Keine dedizierte Logging-Strategie (Audit/PII-Richtlinie)
**Impact:** Incident-Analyse und Compliance erschwert.

**Evidence**
- Kein strukturiertes Logging-Setup in App-Factory; nur Default-Verhalten.

**Recommendation**
- Structured JSON Logs (request-id, endpoint, status, latency).
- PII-Redaction für Tokens/Auth-Keys.

---

### P2-2: Keine CORS-/Origin-Policy definiert
**Impact:** Browser-basiert aktuell eingeschränkt, aber keine explizite Schutzpolitik dokumentiert.

**Evidence**
- Kein CORS-Handling im Code.

**Recommendation**
- Explizit dokumentieren: “no CORS expected”; bei API-Exposition CORS restriktiv konfigurieren.

---

### P2-3: Keine Health/Readiness Trennung
**Impact:** Operativ eingeschränkte Aussagekraft von `/health`.

**Evidence**
- `/health` immer `ok=true`: [app/api/routes_status.py:20](/home/djanebmb/projects/Joormann-Media-Deviceportal/app/api/routes_status.py:20)

**Recommendation**
- `/health/live` (process alive) + `/health/ready` (Dateizugriff, Panel reachability optional).

## Security-Fix Roadmap

1. **Sofort (P0)**: Admin-Key Gate + Exception-Detail-Redaction.
2. **Kurzfristig (P1)**: Rate limiting + persistenter Fehlerpfad + TLS Reverse Proxy.
3. **Mittelfristig (P2)**: Structured Logging, CORS-Policy, Health split.
