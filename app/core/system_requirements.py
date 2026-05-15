from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_NET_SCRIPTS = REPO_ROOT / "scripts" / "net"
DEPLOY_NET_SCRIPTS = Path(os.getenv("NETCONTROL_BIN_DIR", "/opt/deviceportal/bin"))


class SystemRequirementActionError(Exception):
    def __init__(self, message: str, *, code: str = "system_requirement_action_failed", detail: str = ""):
        super().__init__(message)
        self.code = code
        self.detail = detail


def _command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _run_ok(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)
    except Exception:
        return False
    return proc.returncode == 0


def _dpkg_installed(package: str) -> bool:
    return _run_ok(["dpkg-query", "-W", "-f=${Status}", package])


def _dpkg_version(package: str) -> str:
    try:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", package],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _command_version(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    out = (proc.stdout or proc.stderr or "").strip()
    if not out:
        return ""
    first_line = out.splitlines()[0].strip()
    return first_line[:140]


def _service_state(service_name: str) -> str:
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:
        return "unknown"
    state = (proc.stdout or proc.stderr or "").strip().lower()
    if not state:
        return "unknown"
    return state


def _sudoers_local_auth_allowed(script_path: Path) -> bool:
    if not script_path.exists():
        return False
    try:
        proc = subprocess.run(
            ["sudo", "-n", str(script_path), "__probe__"],
            input="\n",
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
    except Exception:
        return False
    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    if "password is required" in combined:
        return False
    if "not allowed to execute" in combined:
        return False
    if "missing password" in combined:
        return True
    if proc.returncode in {0, 2, 3, 10}:
        return True
    return False


def _candidate_script_paths(script_name: str) -> list[Path]:
    return [
        DEPLOY_NET_SCRIPTS / script_name,
        REPO_NET_SCRIPTS / script_name,
    ]


def _resolve_script(script_name: str) -> str:
    for path in _candidate_script_paths(script_name):
        if path.exists() and path.is_file():
            return str(path.resolve())
    raise SystemRequirementActionError(
        "Systemaktions-Script nicht gefunden.",
        code="script_missing",
        detail=f"searched in {DEPLOY_NET_SCRIPTS} and {REPO_NET_SCRIPTS}",
    )


def _parse_kv_output(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in (raw or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        parsed[key] = value.strip()
    return parsed


def run_system_requirement_action(*, action: str, key: str) -> dict:
    action_name = (action or "").strip().lower()
    item_key = (key or "").strip()
    if action_name not in {"install", "start", "uninstall"}:
        raise SystemRequirementActionError("Ungültige Aktion.", code="invalid_action")
    if not item_key:
        raise SystemRequirementActionError("Komponente fehlt.", code="missing_key")

    script_path = _resolve_script("system_requirements.sh")
    service_user = getpass.getuser()
    timeout = 240 if action_name in {"install", "uninstall"} else 40
    try:
        proc = subprocess.run(
            ["sudo", "-n", script_path, action_name, item_key, str(REPO_ROOT), service_user],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemRequirementActionError("Aktion hat zu lange gedauert.", code="timeout") from exc
    except FileNotFoundError as exc:
        raise SystemRequirementActionError("Benötigter Befehl fehlt.", code="command_not_found", detail=str(exc)) from exc
    except Exception as exc:
        raise SystemRequirementActionError("Aktion konnte nicht ausgeführt werden.", code="execution_failed", detail=str(exc)) from exc

    parsed = _parse_kv_output(proc.stdout or "")
    detail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise SystemRequirementActionError(
            parsed.get("message", "Systemaktion fehlgeschlagen."),
            code=parsed.get("code", "action_failed"),
            detail=detail,
        )

    return {
        "ok": True,
        "action": action_name,
        "key": item_key,
        "code": parsed.get("code", "ok"),
        "message": parsed.get("message", "Aktion ausgeführt."),
        "detail": detail,
        "payload": parsed,
    }


def _item(
    *,
    key: str,
    label: str,
    installed: bool,
    detail: str,
    version: str = "",
    runtime: str = "n/a",
    package_name: str = "",
    service_name: str = "",
) -> dict:
    normalized_runtime = (runtime or "").strip() or "unknown"
    installable = bool(package_name)
    startable = bool(service_name) and normalized_runtime not in {"running", "activating", "n/a", "not_installed"}
    uninstallable = installable and key not in {"netcontrol_scripts", "local_auth_script", "local_auth_sudoers"}
    return {
        "key": key,
        "label": label,
        "installed": bool(installed),
        "detail": detail,
        "version": (version or "").strip() or "unbekannt",
        "runtime": normalized_runtime,
        "package": (package_name or "").strip(),
        "service": (service_name or "").strip(),
        "installable": installable,
        "startable": startable,
        "uninstallable": uninstallable,
    }


def collect_system_requirements() -> list[dict]:
    has_python = _command_exists("python3")
    has_pip3 = _command_exists("pip3")
    has_nginx_cmd = _command_exists("nginx")
    has_nginx_pkg = _dpkg_installed("nginx")
    has_avahi_cmd = _command_exists("avahi-daemon")
    has_avahi_pkg = _dpkg_installed("avahi-daemon")
    has_nm_cmd = _command_exists("nmcli")
    has_nm_pkg = _dpkg_installed("network-manager")
    has_bluetoothctl = _command_exists("bluetoothctl")
    has_bluez_pkg = _dpkg_installed("bluez")
    has_tailscale_cmd = _command_exists("tailscale")
    has_tailscale_pkg = _dpkg_installed("tailscale")
    has_pamtester_cmd = _command_exists("pamtester")
    has_pamtester_pkg = _dpkg_installed("pamtester")
    local_auth_script = DEPLOY_NET_SCRIPTS / "local_auth.sh"
    local_auth_script_ok = local_auth_script.exists()
    local_auth_sudoers_ok = _sudoers_local_auth_allowed(local_auth_script)

    net_scripts_repo = sorted([p.name for p in REPO_NET_SCRIPTS.glob("*.sh") if p.is_file()])
    net_scripts_deploy = sorted([p.name for p in DEPLOY_NET_SCRIPTS.glob("*.sh") if p.is_file()]) if DEPLOY_NET_SCRIPTS.exists() else []
    net_scripts_total = len(net_scripts_repo)
    net_scripts_installed = sum(1 for name in net_scripts_repo if (DEPLOY_NET_SCRIPTS / name).exists())
    net_scripts_ok = net_scripts_total > 0 and net_scripts_installed == net_scripts_total

    items = [
        _item(
            key="git",
            label="Git",
            installed=_command_exists("git"),
            detail="Befehl: git",
            version=_command_version(["git", "--version"]),
            package_name="git",
        ),
        _item(
            key="python3",
            label="Python 3",
            installed=has_python,
            detail="Befehl: python3",
            version=_command_version(["python3", "--version"]),
            package_name="python3",
        ),
        _item(
            key="python3_venv",
            label="Python venv",
            installed=_run_ok(["python3", "-m", "venv", "--help"]) if has_python else False,
            detail="Modul: python3 -m venv",
            version=_dpkg_version("python3-venv"),
            package_name="python3-venv",
        ),
        _item(
            key="pip",
            label="pip",
            installed=has_pip3 or (has_python and _run_ok(["python3", "-m", "pip", "--version"])),
            detail="Befehl: pip3 oder python3 -m pip",
            version=_command_version(["pip3", "--version"]) if has_pip3 else _command_version(["python3", "-m", "pip", "--version"]),
            package_name="python3-pip",
        ),
        _item(
            key="nginx",
            label="Nginx",
            installed=has_nginx_cmd or has_nginx_pkg,
            detail="Befehl/Paket: nginx",
            version=_command_version(["nginx", "-v"]) if has_nginx_cmd else _dpkg_version("nginx"),
            runtime=_service_state("nginx") if (has_nginx_cmd or has_nginx_pkg) else "not_installed",
            package_name="nginx",
            service_name="nginx",
        ),
        _item(
            key="curl",
            label="curl",
            installed=_command_exists("curl"),
            detail="Befehl: curl",
            version=_command_version(["curl", "--version"]),
            package_name="curl",
        ),
        _item(
            key="ca_certificates",
            label="CA-Zertifikate",
            installed=_dpkg_installed("ca-certificates"),
            detail="Paket: ca-certificates",
            version=_dpkg_version("ca-certificates"),
            package_name="ca-certificates",
        ),
        _item(
            key="avahi",
            label="Avahi",
            installed=has_avahi_cmd or has_avahi_pkg,
            detail="Paket/Befehl: avahi-daemon",
            version=_command_version(["avahi-daemon", "--version"]) if has_avahi_cmd else _dpkg_version("avahi-daemon"),
            runtime=_service_state("avahi-daemon") if (has_avahi_cmd or has_avahi_pkg) else "not_installed",
            package_name="avahi-daemon",
            service_name="avahi-daemon",
        ),
        _item(
            key="networkmanager",
            label="NetworkManager",
            installed=has_nm_cmd or has_nm_pkg,
            detail="Befehl/Paket: nmcli / network-manager",
            version=_command_version(["nmcli", "--version"]) if has_nm_cmd else _dpkg_version("network-manager"),
            runtime=_service_state("NetworkManager") if (has_nm_cmd or has_nm_pkg) else "not_installed",
            package_name="network-manager",
            service_name="NetworkManager",
        ),
        _item(
            key="bluez",
            label="BlueZ / Bluetooth",
            installed=has_bluetoothctl or has_bluez_pkg,
            detail="Befehl/Paket: bluetoothctl / bluez",
            version=_command_version(["bluetoothctl", "--version"]) if has_bluetoothctl else _dpkg_version("bluez"),
            runtime=_service_state("bluetooth") if (has_bluetoothctl or has_bluez_pkg) else "not_installed",
            package_name="bluez",
            service_name="bluetooth",
        ),
        _item(
            key="tailscale",
            label="Tailscale",
            installed=has_tailscale_cmd or has_tailscale_pkg,
            detail="Befehl/Paket: tailscale / tailscale",
            version=_command_version(["tailscale", "version"]) if has_tailscale_cmd else _dpkg_version("tailscale"),
            runtime=_service_state("tailscaled") if (has_tailscale_cmd or has_tailscale_pkg) else "not_installed",
            package_name="tailscale",
            service_name="tailscaled",
        ),
        _item(
            key="pamtester",
            label="PAM Tester (Local Auth)",
            installed=has_pamtester_cmd or has_pamtester_pkg,
            detail="Befehl/Paket: pamtester",
            version=_command_version(["pamtester", "--version"]) if has_pamtester_cmd else _dpkg_version("pamtester"),
            package_name="pamtester",
        ),
        _item(
            key="local_auth_script",
            label="Local Auth Script",
            installed=local_auth_script_ok,
            detail=f"Script: {local_auth_script}",
            version="present" if local_auth_script_ok else "missing",
            runtime="n/a",
            package_name="netcontrol_scripts",
        ),
        _item(
            key="local_auth_sudoers",
            label="Local Auth Sudoers",
            installed=local_auth_sudoers_ok,
            detail="/etc/sudoers.d/deviceportal-net (sudo -n local_auth probe)",
            version="ok" if local_auth_sudoers_ok else "missing",
            runtime="n/a",
            package_name="netcontrol_scripts",
        ),
        _item(
            key="netcontrol_scripts",
            label="NetControl Scripts",
            installed=net_scripts_ok,
            detail=f"Deploy: {DEPLOY_NET_SCRIPTS}",
            version=f"{net_scripts_installed}/{net_scripts_total} scripts",
            runtime="n/a",
            package_name="netcontrol_scripts",
        ),
    ]
    return items
