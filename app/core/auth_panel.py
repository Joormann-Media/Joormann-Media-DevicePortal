from __future__ import annotations

import re
from urllib.parse import urlencode
from urllib.parse import urljoin

import requests
from requests.utils import cookiejar_from_dict


class PanelAuthError(Exception):
    def __init__(self, message: str, *, code: str = "panel_auth_failed"):
        super().__init__(message)
        self.code = code


def _safe_base_url(url: str) -> str:
    value = (url or "").strip()
    return value.rstrip("/")


def _extract_csrf_token(html: str) -> str:
    if not html:
        return ""
    match = re.search(r'name=["\']_csrf_token["\']\s+value=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    return (match.group(1).strip() if match else "")


def _extract_form_action(html: str) -> str:
    if not html:
        return ""
    match = re.search(r"<form[^>]+action=[\"']([^\"']+)[\"'][^>]*>", html, flags=re.IGNORECASE)
    return (match.group(1).strip() if match else "")


def _extract_2fa_code_name(html: str) -> str:
    if not html:
        return ""

    patterns = [
        r'<input[^>]*id=["\']_auth_code["\'][^>]*name=["\']([^"\']+)["\']',
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*id=["\']_auth_code["\']',
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*autocomplete=["\']one-time-code["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return "_auth_code"


def _extract_2fa_csrf(html: str) -> tuple[str, str]:
    if not html:
        return ("", "")
    hidden_inputs = re.findall(
        r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
        html,
        flags=re.IGNORECASE,
    )
    for name, value in hidden_inputs:
        key = str(name or "").strip()
        if "csrf" in key.lower():
            return (key, str(value or "").strip())
    return ("", "")


def _normalize_identifier(value: str) -> str:
    return (value or "").strip().lower()


def _extract_items(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []


def _lookup_user_candidate(base_url: str, device_uuid: str, auth_key: str, identifier: str) -> dict | None:
    query = {
        "q": identifier,
        "deviceUuid": device_uuid,
        "authKey": auth_key,
        "limit": "25",
    }
    url = f"{base_url}/api/device/link/search/users?{urlencode(query)}"
    try:
        response = requests.get(url, timeout=8)
    except Exception as exc:
        raise PanelAuthError("Adminpanel nicht erreichbar.", code="panel_unreachable") from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise PanelAuthError("User-Suche am Adminpanel fehlgeschlagen.", code="panel_user_lookup_failed")

    try:
        payload = response.json()
    except Exception:
        payload = {}

    items = _extract_items(payload)
    ident = _normalize_identifier(identifier)
    exact = []
    for item in items:
        username = _normalize_identifier(str(item.get("username") or ""))
        email = _normalize_identifier(str(item.get("email") or ""))
        if ident and ident in (username, email):
            exact.append(item)

    if len(exact) == 1:
        return exact[0]

    if len(exact) > 1:
        for item in exact:
            if _normalize_identifier(str(item.get("username") or "")) == ident:
                return item

    return None


def _prepare_two_factor_payload(*, session: requests.Session, base_url: str, location: str, user_candidate: dict) -> dict:
    challenge_url = urljoin(f"{base_url}/", location or "/2fa")
    try:
        challenge = session.get(challenge_url, timeout=10)
    except Exception as exc:
        raise PanelAuthError("2FA-Seite am Adminpanel nicht erreichbar.", code="panel_2fa_unreachable") from exc

    if challenge.status_code < 200 or challenge.status_code >= 300:
        raise PanelAuthError("2FA-Seite am Adminpanel liefert Fehler.", code="panel_2fa_unreachable")

    html = challenge.text or ""
    code_param = _extract_2fa_code_name(html)
    form_action = _extract_form_action(html) or "/2fa_check"
    csrf_name, csrf_value = _extract_2fa_csrf(html)
    check_url = urljoin(challenge_url, form_action)

    return {
        "requires_2fa": True,
        "username": str(user_candidate.get("username") or ""),
        "display_name": str(user_candidate.get("displayName") or user_candidate.get("username") or ""),
        "user_id": int(user_candidate.get("id") or 0) or None,
        "pending_2fa": {
            "base_url": base_url,
            "challenge_url": challenge_url,
            "check_url": check_url,
            "code_param": code_param or "_auth_code",
            "csrf_param": csrf_name,
            "csrf_token": csrf_value,
            "cookies": requests.utils.dict_from_cookiejar(session.cookies),
        },
    }


def authenticate_via_panel(
    *,
    base_url: str,
    device_uuid: str,
    auth_key: str,
    username: str,
    password: str,
    allowed_user_ids: list[int],
) -> dict:
    base = _safe_base_url(base_url)
    user_identifier = (username or "").strip()
    if not base:
        raise PanelAuthError("Panel URL fehlt.", code="panel_url_missing")
    if not user_identifier or not password:
        raise PanelAuthError("Benutzername und Passwort erforderlich.", code="invalid_credentials")

    user_candidate = _lookup_user_candidate(base, device_uuid, auth_key, user_identifier)
    if not user_candidate:
        raise PanelAuthError("Benutzer ist nicht für dieses Gerät freigegeben.", code="login_not_allowed")

    user_id = int(user_candidate.get("id") or 0)
    if user_id <= 0 or user_id not in set(int(x) for x in allowed_user_ids if int(x) > 0):
        raise PanelAuthError("Benutzer ist nicht mit diesem Gerät verknüpft.", code="login_not_linked")

    login_url = f"{base}/login"
    session = requests.Session()
    try:
        login_page = session.get(login_url, timeout=8)
    except Exception as exc:
        raise PanelAuthError("Adminpanel Login-Seite nicht erreichbar.", code="panel_unreachable") from exc

    if login_page.status_code < 200 or login_page.status_code >= 300:
        raise PanelAuthError("Adminpanel Login-Seite liefert Fehler.", code="panel_login_unreachable")

    csrf = _extract_csrf_token(login_page.text or "")
    if not csrf:
        raise PanelAuthError("CSRF-Token im Adminpanel nicht gefunden.", code="panel_login_csrf_missing")

    form = {
        "_username": user_identifier,
        "_password": password,
        "_csrf_token": csrf,
    }

    try:
        result = session.post(login_url, data=form, timeout=10, allow_redirects=False)
    except Exception as exc:
        raise PanelAuthError("Adminpanel Login fehlgeschlagen.", code="panel_unreachable") from exc

    location = str(result.headers.get("Location") or "")
    if result.status_code in (302, 303):
        lower_location = location.lower()
        if "/2fa" in lower_location:
            return _prepare_two_factor_payload(
                session=session,
                base_url=base,
                location=location,
                user_candidate=user_candidate,
            )
        if "/login" not in lower_location:
            return {
                "ok": True,
                "username": str(user_candidate.get("username") or user_identifier),
                "display_name": str(user_candidate.get("displayName") or user_candidate.get("username") or user_identifier),
                "user_id": user_id,
                "requires_2fa": False,
            }

    raise PanelAuthError("Benutzername oder Passwort ungültig.", code="invalid_credentials")


def complete_panel_two_factor(*, pending_2fa: dict, code: str) -> dict:
    if not isinstance(pending_2fa, dict):
        raise PanelAuthError("2FA-Status fehlt. Bitte erneut einloggen.", code="panel_2fa_missing")

    otp = (code or "").strip()
    if not otp:
        raise PanelAuthError("Bitte 2FA-Code eingeben.", code="panel_2fa_code_missing")

    check_url = str(pending_2fa.get("check_url") or "").strip()
    base_url = str(pending_2fa.get("base_url") or "").strip()
    code_param = str(pending_2fa.get("code_param") or "_auth_code").strip() or "_auth_code"
    csrf_param = str(pending_2fa.get("csrf_param") or "").strip()
    csrf_token = str(pending_2fa.get("csrf_token") or "").strip()
    cookies = pending_2fa.get("cookies") if isinstance(pending_2fa.get("cookies"), dict) else {}

    if not check_url or not base_url:
        raise PanelAuthError("2FA-Status unvollständig. Bitte erneut einloggen.", code="panel_2fa_missing")

    form = {code_param: otp}
    if csrf_param and csrf_token:
        form[csrf_param] = csrf_token

    session = requests.Session()
    session.cookies = cookiejar_from_dict(cookies)
    try:
        response = session.post(check_url, data=form, timeout=10, allow_redirects=False)
    except Exception as exc:
        raise PanelAuthError("2FA-Prüfung am Adminpanel fehlgeschlagen.", code="panel_2fa_unreachable") from exc

    location = str(response.headers.get("Location") or "").lower()
    if response.status_code in (302, 303):
        if "/2fa" not in location and "/login" not in location:
            return {"ok": True}
        if "/2fa" in location:
            raise PanelAuthError("2FA-Code ungültig.", code="invalid_2fa_code")

    raise PanelAuthError("2FA-Code ungültig.", code="invalid_2fa_code")
