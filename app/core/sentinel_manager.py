from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class SentinelManagerError(Exception):
    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass(frozen=True)
class SentinelDef:
    slug: str
    name: str
    description: str
    script_rel: str
    install_mode: str  # service | timer | pam
    service_name: str = ""
    timer_name: str = ""


SENTINELS: tuple[SentinelDef, ...] = (
    SentinelDef(
        slug="reboot-sentinel",
        name="Reboot Sentinel",
        description="Sendet Meldung nach jedem Reboot.",
        script_rel="reboot-sentinel/sentinel-reboot.sh",
        install_mode="service",
        service_name="jm-sentinel-reboot.service",
    ),
    SentinelDef(
        slug="ssh-sentinel",
        name="SSH Sentinel",
        description="Sendet SSH Login/Logout Events via PAM Hook.",
        script_rel="ssh-sentinel/sentinel-ssh.sh",
        install_mode="pam",
    ),
    SentinelDef(
        slug="folder-sentinel",
        name="Folder Sentinel",
        description="Überwacht Ordner per inotify und sendet Events.",
        script_rel="folder-sentinel/folderwatcher.sh",
        install_mode="service",
        service_name="jm-sentinel-folder.service",
    ),
    SentinelDef(
        slug="health-sentinel",
        name="Health Sentinel",
        description="Periodischer Host-Health-Report.",
        script_rel="health-sentinel.sh",
        install_mode="timer",
        service_name="jm-sentinel-health.service",
        timer_name="jm-sentinel-health.timer",
    ),
    SentinelDef(
        slug="osm-healthcheck",
        name="OSM Healthcheck",
        description="Periodischer Check lokaler OSM-Dienste.",
        script_rel="osm-healthcheck.sh",
        install_mode="timer",
        service_name="jm-sentinel-osm-health.service",
        timer_name="jm-sentinel-osm-health.timer",
    ),
)

TARGET_BASE = Path("/opt/sentinels")
TARGET_BIN = TARGET_BASE / "bin"
TARGET_LOGS = TARGET_BASE / "logs"
TARGET_CONFIG = TARGET_BASE / "config"
TARGET_SENTINEL_CONF = TARGET_CONFIG / "sentinel.conf"
TARGET_FOLDER_CONF = TARGET_CONFIG / "folder.conf"
TARGET_DISPATCH_HELPER = TARGET_BIN / "webhook-dispatch.sh"
SYSTEMD_DIR = Path("/etc/systemd/system")
PAM_SSHD_PATH = Path("/etc/pam.d/sshd")
PAM_MARKER = "# DevicePortal Sentinel Hook"
PAM_HOOK_LINE = "session optional pam_exec.so seteuid /opt/sentinels/bin/ssh-sentinel.sh"
NETCONTROL_BIN_DIR = Path(os.getenv("NETCONTROL_BIN_DIR", "/opt/deviceportal/bin"))
SENTINEL_PRIV_SCRIPT = "sentinel_priv.sh"


def _sentinel_index() -> dict[str, SentinelDef]:
    return {item.slug: item for item in SENTINELS}


def _run(cmd: list[str], timeout: int = 45) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SentinelManagerError("command_not_found", "Required command missing", str(exc))
    except subprocess.TimeoutExpired:
        raise SentinelManagerError("timeout", f"Command timed out after {timeout}s")
    except Exception as exc:
        raise SentinelManagerError("execution_failed", "Command execution failed", str(exc))
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _run_checked(cmd: list[str], timeout: int = 45, code: str = "command_failed", message: str = "Command failed") -> str:
    rc, out, err = _run(cmd, timeout=timeout)
    if rc != 0:
        raise SentinelManagerError(code, message, err or out)
    return out


def _sudo_prefix() -> list[str]:
    if os.geteuid() == 0:
        return []
    wrapper = NETCONTROL_BIN_DIR / SENTINEL_PRIV_SCRIPT
    if wrapper.exists() and wrapper.is_file():
        return ["sudo", "-n", str(wrapper)]
    return ["sudo", "-n"]


def _sudo_run_script(
    script_path: Path,
    timeout: int = 30,
    code: str = "test_failed",
    message: str = "Could not run script",
    args: list[str] | None = None,
) -> str:
    extra_args = list(args or [])
    wrapper = NETCONTROL_BIN_DIR / SENTINEL_PRIV_SCRIPT
    if os.geteuid() != 0 and wrapper.exists() and wrapper.is_file():
        return _sudo_checked(["run-script", str(script_path)] + extra_args, timeout=timeout, code=code, message=message)
    return _sudo_checked([str(script_path)] + extra_args, timeout=timeout, code=code, message=message)


def _sudo_checked(cmd: list[str], timeout: int = 45, code: str = "sudo_command_failed", message: str = "Privileged command failed") -> str:
    return _run_checked(_sudo_prefix() + cmd, timeout=timeout, code=code, message=message)


def _source_dir_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_dir = str(os.getenv("SENTINELS_SOURCE_DIR") or "").strip()
    if env_dir:
        candidates.append(Path(env_dir))

    portal_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            # Preferred: bundled sentinel sources inside the DevicePortal repo.
            portal_root / "resources" / "sentinels",
            portal_root.parent / "Joormann-Media-DeviceSentinels" / "sentinels",
            portal_root.parent / "projects" / "Joormann-Media-DeviceSentinels" / "sentinels",
            Path("/opt/joormann-media/Joormann-Media-DeviceSentinels/sentinels"),
            Path("/opt/device-sentinels/sentinels"),
            Path("/opt/sentinels/src"),
        ]
    )

    uniq: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def resolve_source_dir() -> Path:
    for candidate in _source_dir_candidates():
        if candidate.exists() and candidate.is_dir():
            return candidate
    searched = ", ".join(str(p) for p in _source_dir_candidates())
    raise SentinelManagerError(
        "source_not_found",
        "Sentinel source directory not found",
        f"searched: {searched}",
    )


def _validate_webhook_url(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        raise SentinelManagerError("invalid_payload", "Webhook URL must not be empty")
    if not re.match(r"^https?://", clean, flags=re.IGNORECASE):
        raise SentinelManagerError("invalid_payload", "Webhook URL must start with http:// or https://")
    return clean


def _normalize_webhook_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in {"discord", "internal", "both"}:
        return normalized
    return "discord"


def _compose_internal_url_with_secret(url: str, secret: str) -> str:
    clean_url = str(url or "").strip()
    if not clean_url:
        return ""
    token = str(secret or "").strip()
    if not token:
        return clean_url
    sep = "&" if "?" in clean_url else "?"
    return f"{clean_url}{sep}secret={token}"


def _ensure_base_dirs() -> None:
    _sudo_checked(["install", "-d", "-m", "0755", str(TARGET_BASE)], code="mkdir_failed", message="Could not prepare /opt/sentinels")
    _sudo_checked(["install", "-d", "-m", "0755", str(TARGET_BIN)], code="mkdir_failed", message="Could not prepare sentinel bin dir")
    _sudo_checked(["install", "-d", "-m", "0755", str(TARGET_LOGS)], code="mkdir_failed", message="Could not prepare sentinel log dir")
    _sudo_checked(["install", "-d", "-m", "0755", str(TARGET_CONFIG)], code="mkdir_failed", message="Could not prepare sentinel config dir")


def _write_file_sudo(path: Path, content: str, mode: int = 0o644) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        _sudo_checked(
            ["install", "-m", f"{mode:o}", str(tmp_path), str(path)],
            code="write_failed",
            message=f"Could not write {path}",
        )
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _ensure_webhook_conf(
    webhook_url: str,
    internal_webhook_url: str = "",
    internal_webhook_secret: str = "",
    webhook_mode: str = "discord",
) -> None:
    mode = _normalize_webhook_mode(webhook_mode)
    safe_discord = _validate_webhook_url(webhook_url) if str(webhook_url or "").strip() else ""
    safe_internal = _validate_webhook_url(internal_webhook_url) if str(internal_webhook_url or "").strip() else ""

    if mode == "discord" and not safe_discord:
        raise SentinelManagerError("invalid_payload", "Discord webhook URL is required for mode 'discord'")
    if mode == "internal" and not safe_internal:
        raise SentinelManagerError("invalid_payload", "Internal webhook URL is required for mode 'internal'")
    if mode == "both" and (not safe_discord or not safe_internal):
        raise SentinelManagerError("invalid_payload", "Both webhook URLs are required for mode 'both'")

    # Backward-compatibility for scripts that still use DISCORD_WEBHOOK only.
    legacy_target = safe_discord
    if mode == "internal":
        legacy_target = _compose_internal_url_with_secret(safe_internal, internal_webhook_secret)
    elif mode == "both":
        legacy_target = safe_discord

    _ensure_base_dirs()
    content = (
        f"WEBHOOK_MODE='{mode}'\n"
        f"DISCORD_WEBHOOK='{legacy_target}'\n"
        f"DISCORD_WEBHOOK_PRIMARY='{safe_discord}'\n"
        f"INTERNAL_WEBHOOK_URL='{safe_internal}'\n"
        f"INTERNAL_WEBHOOK_SECRET='{str(internal_webhook_secret or '').strip()}'\n"
    )
    _write_file_sudo(TARGET_SENTINEL_CONF, content, mode=0o640)
    try:
        _sudo_checked(["chown", "root:root", str(TARGET_SENTINEL_CONF)], code="chown_failed", message="Could not secure sentinel.conf")
    except SentinelManagerError:
        # Keep installation functional even if owner change is restricted.
        pass
    if not TARGET_FOLDER_CONF.exists():
        _write_file_sudo(TARGET_FOLDER_CONF, "# One folder path per line\n", mode=0o644)


def _target_script_path(slug: str) -> Path:
    return TARGET_BIN / f"{slug}.sh"


def _copy_script(defn: SentinelDef, source_dir: Path) -> Path:
    src = source_dir / defn.script_rel
    if not src.exists() or not src.is_file():
        raise SentinelManagerError("script_missing", "Sentinel script missing", str(src))
    dst = _target_script_path(defn.slug)
    _sudo_checked(["install", "-m", "0755", str(src), str(dst)], code="install_failed", message=f"Could not install script for {defn.slug}")
    return dst


def _install_dispatch_helper(source_dir: Path) -> None:
    src = source_dir / "lib" / "webhook_dispatch.sh"
    if not src.exists() or not src.is_file():
        raise SentinelManagerError("script_missing", "Webhook dispatch helper missing", str(src))
    _sudo_checked(
        ["install", "-m", "0755", str(src), str(TARGET_DISPATCH_HELPER)],
        code="install_failed",
        message="Could not install webhook dispatch helper",
    )


def _service_unit_content(defn: SentinelDef, script_path: Path) -> str:
    if defn.slug == "folder-sentinel":
        return f"""[Unit]
Description=DevicePortal {defn.name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={script_path}
Restart=always
RestartSec=3
StandardOutput=append:{TARGET_LOGS}/folder_sentinel.service.log
StandardError=append:{TARGET_LOGS}/folder_sentinel.service.log

[Install]
WantedBy=multi-user.target
"""
    if defn.slug == "reboot-sentinel":
        return f"""[Unit]
Description=DevicePortal {defn.name}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={script_path}

[Install]
WantedBy=multi-user.target
"""
    if defn.slug == "health-sentinel":
        return f"""[Unit]
Description=DevicePortal {defn.name}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={script_path}
"""
    if defn.slug == "osm-healthcheck":
        return f"""[Unit]
Description=DevicePortal {defn.name}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={script_path}
"""
    raise SentinelManagerError("unsupported_mode", "Unsupported service definition")


def _timer_unit_content(defn: SentinelDef) -> str:
    on_boot = "120s"
    on_unit = "15min"
    if defn.slug == "osm-healthcheck":
        on_unit = "10min"
    return f"""[Unit]
Description=DevicePortal {defn.name} Timer

[Timer]
OnBootSec={on_boot}
OnUnitActiveSec={on_unit}
Unit={defn.service_name}
Persistent=true

[Install]
WantedBy=timers.target
"""


def _install_service(defn: SentinelDef, script_path: Path) -> None:
    unit_path = SYSTEMD_DIR / defn.service_name
    _write_file_sudo(unit_path, _service_unit_content(defn, script_path), mode=0o644)
    _sudo_checked(["systemctl", "daemon-reload"], code="daemon_reload_failed", message="Could not reload systemd")
    _sudo_checked(["systemctl", "enable", "--now", defn.service_name], code="service_enable_failed", message=f"Could not enable {defn.service_name}")


def _install_timer(defn: SentinelDef, script_path: Path) -> None:
    if not defn.timer_name:
        raise SentinelManagerError("unsupported_mode", "Timer definition missing")
    service_path = SYSTEMD_DIR / defn.service_name
    timer_path = SYSTEMD_DIR / defn.timer_name
    _write_file_sudo(service_path, _service_unit_content(defn, script_path), mode=0o644)
    _write_file_sudo(timer_path, _timer_unit_content(defn), mode=0o644)
    _sudo_checked(["systemctl", "daemon-reload"], code="daemon_reload_failed", message="Could not reload systemd")
    _sudo_checked(["systemctl", "enable", "--now", defn.timer_name], code="timer_enable_failed", message=f"Could not enable {defn.timer_name}")


def _install_pam(defn: SentinelDef, script_path: Path) -> None:
    _ensure_base_dirs()
    if not PAM_SSHD_PATH.exists():
        raise SentinelManagerError("pam_missing", "PAM sshd config missing", str(PAM_SSHD_PATH))
    marker = f"{PAM_MARKER}\n{PAM_HOOK_LINE}\n"
    current = ""
    try:
        current = PAM_SSHD_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    if PAM_HOOK_LINE in current:
        return
    new_content = current.rstrip() + "\n\n" + marker
    _write_file_sudo(PAM_SSHD_PATH, new_content, mode=0o644)


def _disable_unit(unit_name: str) -> None:
    if not unit_name:
        return
    rc, _out, _err = _run(_sudo_prefix() + ["systemctl", "disable", "--now", unit_name], timeout=45)
    if rc != 0:
        _run(_sudo_prefix() + ["systemctl", "stop", unit_name], timeout=30)


def _systemd_is_enabled(unit_name: str) -> bool:
    if not unit_name:
        return False
    rc, out, _err = _run(["systemctl", "is-enabled", unit_name], timeout=10)
    return rc == 0 and out.strip() in {"enabled", "static", "indirect", "generated"}


def _systemd_is_active(unit_name: str) -> bool:
    if not unit_name:
        return False
    rc, out, _err = _run(["systemctl", "is-active", unit_name], timeout=10)
    return rc == 0 and out.strip() == "active"


def _pam_hook_installed() -> bool:
    try:
        content = PAM_SSHD_PATH.read_text(encoding="utf-8")
    except Exception:
        return False
    return PAM_HOOK_LINE in content


def _remove_pam_hook() -> None:
    try:
        content = PAM_SSHD_PATH.read_text(encoding="utf-8")
    except Exception:
        return
    if PAM_HOOK_LINE not in content:
        return
    lines = [line for line in content.splitlines() if line.strip() not in {PAM_MARKER, PAM_HOOK_LINE}]
    new_content = "\n".join(lines).rstrip() + "\n"
    _write_file_sudo(PAM_SSHD_PATH, new_content, mode=0o644)


def _remove_file(path: Path) -> None:
    _run(_sudo_prefix() + ["rm", "-f", str(path)], timeout=20)


def get_status(
    webhook_url: str = "",
    internal_webhook_url: str = "",
    internal_webhook_secret: str = "",
    webhook_mode: str = "discord",
) -> dict:
    source_dir = None
    source_error = ""
    try:
        source_dir = resolve_source_dir()
    except SentinelManagerError as exc:
        source_error = exc.detail or exc.message

    items: list[dict] = []
    for item in SENTINELS:
        script_path = _target_script_path(item.slug)
        unit_path = SYSTEMD_DIR / item.service_name if item.service_name else None
        timer_path = SYSTEMD_DIR / item.timer_name if item.timer_name else None
        installed = script_path.exists()
        enabled = False
        active = False
        mode_state = "not_installed"
        if item.install_mode == "pam":
            installed = installed and _pam_hook_installed()
            enabled = installed
            active = installed
            mode_state = "pam_hooked" if installed else "not_installed"
        elif item.install_mode == "service":
            enabled = _systemd_is_enabled(item.service_name)
            active = _systemd_is_active(item.service_name)
            installed = installed and bool(unit_path and unit_path.exists())
            mode_state = "service_enabled" if enabled else ("installed" if installed else "not_installed")
        elif item.install_mode == "timer":
            enabled = _systemd_is_enabled(item.timer_name)
            active = _systemd_is_active(item.timer_name)
            installed = installed and bool(unit_path and unit_path.exists()) and bool(timer_path and timer_path.exists())
            mode_state = "timer_enabled" if enabled else ("installed" if installed else "not_installed")

        src_exists = False
        if source_dir:
            src_exists = (source_dir / item.script_rel).exists()

        items.append(
            {
                "slug": item.slug,
                "name": item.name,
                "description": item.description,
                "install_mode": item.install_mode,
                "service_name": item.service_name,
                "timer_name": item.timer_name,
                "installed": installed,
                "enabled": enabled,
                "active": active,
                "state": mode_state,
                "script_path": str(script_path),
                "source_exists": src_exists,
            }
        )

    return {
        "source_dir": str(source_dir) if source_dir else "",
        "source_error": source_error,
        "webhook_url": str(webhook_url or ""),
        "internal_webhook_url": str(internal_webhook_url or ""),
        "internal_webhook_secret": str(internal_webhook_secret or ""),
        "webhook_mode": _normalize_webhook_mode(webhook_mode),
        "config_path": str(TARGET_SENTINEL_CONF),
        "sentinels": items,
    }


def install_sentinel(
    slug: str,
    webhook_url: str,
    internal_webhook_url: str = "",
    internal_webhook_secret: str = "",
    webhook_mode: str = "discord",
) -> dict:
    mapping = _sentinel_index()
    defn = mapping.get(str(slug or "").strip())
    if not defn:
        raise SentinelManagerError("invalid_payload", "Unknown sentinel slug")

    source_dir = resolve_source_dir()
    _ensure_webhook_conf(
        webhook_url=webhook_url,
        internal_webhook_url=internal_webhook_url,
        internal_webhook_secret=internal_webhook_secret,
        webhook_mode=webhook_mode,
    )
    _install_dispatch_helper(source_dir)
    script_path = _copy_script(defn, source_dir)

    if defn.install_mode == "service":
        _install_service(defn, script_path)
    elif defn.install_mode == "timer":
        _install_timer(defn, script_path)
    elif defn.install_mode == "pam":
        _install_pam(defn, script_path)
    else:
        raise SentinelManagerError("unsupported_mode", "Unsupported install mode", defn.install_mode)

    status = get_status(
        webhook_url=webhook_url,
        internal_webhook_url=internal_webhook_url,
        internal_webhook_secret=internal_webhook_secret,
        webhook_mode=webhook_mode,
    )
    return {"slug": defn.slug, "name": defn.name, "status": status}


def uninstall_sentinel(
    slug: str,
    webhook_url: str = "",
    internal_webhook_url: str = "",
    internal_webhook_secret: str = "",
    webhook_mode: str = "discord",
) -> dict:
    mapping = _sentinel_index()
    defn = mapping.get(str(slug or "").strip())
    if not defn:
        raise SentinelManagerError("invalid_payload", "Unknown sentinel slug")

    if defn.install_mode == "pam":
        _remove_pam_hook()
    if defn.timer_name:
        _disable_unit(defn.timer_name)
        _remove_file(SYSTEMD_DIR / defn.timer_name)
    if defn.service_name:
        _disable_unit(defn.service_name)
        _remove_file(SYSTEMD_DIR / defn.service_name)

    _remove_file(_target_script_path(defn.slug))
    _sudo_checked(["systemctl", "daemon-reload"], code="daemon_reload_failed", message="Could not reload systemd")

    status = get_status(
        webhook_url=webhook_url,
        internal_webhook_url=internal_webhook_url,
        internal_webhook_secret=internal_webhook_secret,
        webhook_mode=webhook_mode,
    )
    return {"slug": defn.slug, "name": defn.name, "status": status}


def sentinel_action(
    slug: str,
    action: str,
    webhook_url: str = "",
    internal_webhook_url: str = "",
    internal_webhook_secret: str = "",
    webhook_mode: str = "discord",
) -> dict:
    mapping = _sentinel_index()
    defn = mapping.get(str(slug or "").strip())
    if not defn:
        raise SentinelManagerError("invalid_payload", "Unknown sentinel slug")

    act = str(action or "").strip().lower()
    if act not in {"start", "stop", "restart", "test"}:
        raise SentinelManagerError("invalid_payload", "Unsupported sentinel action")

    script_path = _target_script_path(defn.slug)
    if not script_path.exists():
        raise SentinelManagerError("not_installed", "Sentinel script not installed", str(script_path))

    # Opportunistic self-heal: refresh scripts/helper from bundled sources when available.
    try:
        source_dir = resolve_source_dir()
        _install_dispatch_helper(source_dir)
        _copy_script(defn, source_dir)
    except SentinelManagerError:
        pass

    _ensure_webhook_conf(
        webhook_url=webhook_url,
        internal_webhook_url=internal_webhook_url,
        internal_webhook_secret=internal_webhook_secret,
        webhook_mode=webhook_mode,
    )

    if defn.install_mode == "pam":
        if act == "start":
            _install_pam(defn, script_path)
        elif act == "stop":
            _remove_pam_hook()
        elif act == "restart":
            _remove_pam_hook()
            _install_pam(defn, script_path)
        else:  # test
            _sudo_run_script(script_path, timeout=30, code="test_failed", message=f"Could not test {defn.slug}")
    elif defn.install_mode == "service":
        unit = defn.service_name
        if not unit:
            raise SentinelManagerError("invalid_state", "Service unit missing", defn.slug)
        if act == "test":
            if defn.slug == "folder-sentinel":
                _sudo_run_script(script_path, timeout=30, code="test_failed", message=f"Could not test {defn.slug}", args=["--test"])
            else:
                _sudo_run_script(script_path, timeout=30, code="test_failed", message=f"Could not test {defn.slug}")
        else:
            _sudo_checked(["systemctl", act, unit], code="service_action_failed", message=f"Could not {act} {unit}")
    elif defn.install_mode == "timer":
        timer_unit = defn.timer_name
        service_unit = defn.service_name
        if not timer_unit or not service_unit:
            raise SentinelManagerError("invalid_state", "Timer definition missing", defn.slug)
        if act == "test":
            _sudo_run_script(script_path, timeout=30, code="test_failed", message=f"Could not test {defn.slug}")
        else:
            _sudo_checked(["systemctl", act, timer_unit], code="timer_action_failed", message=f"Could not {act} {timer_unit}")
    else:
        raise SentinelManagerError("unsupported_mode", "Unsupported install mode", defn.install_mode)

    status = get_status(
        webhook_url=webhook_url,
        internal_webhook_url=internal_webhook_url,
        internal_webhook_secret=internal_webhook_secret,
        webhook_mode=webhook_mode,
    )
    return {"slug": defn.slug, "name": defn.name, "action": act, "status": status}
