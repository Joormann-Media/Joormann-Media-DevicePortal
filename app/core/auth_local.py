from __future__ import annotations

import pwd
import subprocess
from pathlib import Path


class LocalAuthError(Exception):
    def __init__(self, message: str, *, code: str = "local_auth_failed"):
        super().__init__(message)
        self.code = code


def _is_interactive_shell(shell: str) -> bool:
    raw = (shell or "").strip()
    if raw in ("", "/usr/sbin/nologin", "/sbin/nologin", "/bin/false"):
        return False
    return True


def list_interactive_users() -> list[dict]:
    users: list[dict] = []
    for entry in pwd.getpwall():
        if entry.pw_uid < 1000:
            continue
        if not _is_interactive_shell(entry.pw_shell):
            continue
        users.append(
            {
                "username": entry.pw_name,
                "uid": entry.pw_uid,
                "home": entry.pw_dir,
                "shell": entry.pw_shell,
                "display_name": entry.pw_gecos.split(",")[0].strip() or entry.pw_name,
            }
        )
    users.sort(key=lambda item: item["username"])
    return users


def _find_interactive_user(username: str) -> dict | None:
    target = (username or "").strip()
    if not target:
        return None
    for user in list_interactive_users():
        if user["username"] == target:
            return user
    return None


def authenticate_local_user(username: str, password: str, auth_script: str = "/opt/deviceportal/bin/local_auth.sh") -> dict:
    user = _find_interactive_user(username)
    if not user:
        raise LocalAuthError("Lokaler Benutzer nicht erlaubt oder nicht vorhanden.", code="local_user_not_allowed")

    if not isinstance(password, str) or password == "":
        raise LocalAuthError("Passwort fehlt.", code="password_missing")

    script_path = Path(auth_script)
    if not script_path.exists():
        raise LocalAuthError("Lokale Authentifizierung nicht verfügbar (Auth-Script fehlt).", code="local_auth_unavailable")

    cmd = ["sudo", "-n", str(script_path), user["username"]]
    try:
        proc = subprocess.run(
            cmd,
            input=f"{password}\n",
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalAuthError("Lokale Authentifizierung Zeitüberschreitung.", code="local_auth_timeout") from exc
    except Exception as exc:
        raise LocalAuthError("Lokale Authentifizierung fehlgeschlagen.", code="local_auth_exec_failed") from exc

    if proc.returncode != 0:
        raise LocalAuthError("Benutzername oder Passwort ungültig.", code="invalid_credentials")

    return {
        "ok": True,
        "username": user["username"],
        "display_name": user["display_name"],
        "uid": user["uid"],
    }
