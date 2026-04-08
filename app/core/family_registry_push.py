from __future__ import annotations

import hashlib
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import current_app

from app.core.jsonio import write_json
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now


def _to_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_base_url(value: object) -> str:
    out = str(value or "").strip()
    if not out:
        return ""
    if not out.startswith("http://") and not out.startswith("https://"):
        out = f"https://{out}"
    return out.rstrip("/")


def _safe_path(value: object, default: str) -> str:
    out = str(value or "").strip() or default
    if not out.startswith("/"):
        out = f"/{out}"
    return out


def _normalize_repo_link(value: object) -> str:
    out = str(value or "").strip().lower()
    if out.endswith("/"):
        out = out.rstrip("/")
    if out.endswith(".git"):
        out = out[:-4]
    if out.startswith("git@github.com:"):
        out = out.replace("git@github.com:", "https://github.com/", 1)
    return out


def _default_repo_name(repo_link: str) -> str:
    value = repo_link.strip().rstrip("/")
    if not value:
        return "Repo"
    tail = value.split("/")[-1].strip() or value
    if tail.endswith(".git"):
        tail = tail[:-4].strip() or tail
    return tail


def _default_service_name(repo_link: str) -> str:
    base = _default_repo_name(repo_link).strip().lower()
    safe = []
    last_dash = False
    for ch in base:
        if ch.isalnum():
            safe.append(ch)
            last_dash = False
            continue
        if ch in {" ", "-", "_", "."}:
            if not last_dash:
                safe.append("-")
                last_dash = True
    slug = "".join(safe).strip("-") or "service"
    return f"{slug}.service"


def _default_service_id(repo_link: str, repo_name: str = "", service_name: str = "") -> str:
    seed = f"{repo_link.strip().lower()}|{repo_name.strip().lower()}|{service_name.strip().lower()}"
    return f"jsvc_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def _clean_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        row = str(item or "").strip()
        if row:
            out.append(row)
    return out


def _clean_endpoints(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        k = str(key or "").strip()
        v = str(raw or "").strip()
        if k and v:
            out[k] = v
    return out


def _normalize_entry(row: dict, source_name: str, fallback_node_name: str) -> dict:
    repo_link = str(row.get("repo_link") or row.get("repo_url") or row.get("repo_dir") or "").strip()
    repo_name = str(row.get("repo_name") or row.get("name") or "").strip() or _default_repo_name(repo_link)
    service_name = str(row.get("service_name") or "").strip() or _default_service_name(repo_link)
    service_id = str(row.get("service_id") or row.get("serviceId") or "").strip() or _default_service_id(repo_link, repo_name, service_name)
    node_name = str(row.get("node_name") or row.get("hostname") or "").strip() or fallback_node_name
    source = str(row.get("source") or "").strip() or source_name

    try:
        service_port = int(row.get("service_port")) if row.get("service_port") not in (None, "") else None
    except Exception:
        service_port = None

    return {
        "service_id": service_id,
        "service_name": service_name,
        "repo_name": repo_name,
        "repo_link": repo_link,
        "install_dir": str(row.get("install_dir") or "").strip(),
        "service_user": str(row.get("service_user") or "").strip(),
        "node_name": node_name,
        "hostname": str(row.get("hostname") or "").strip(),
        "api_base_url": str(row.get("api_base_url") or "").strip(),
        "health_url": str(row.get("health_url") or "").strip(),
        "ui_url": str(row.get("ui_url") or row.get("web_url") or "").strip(),
        "endpoints": _clean_endpoints(row.get("endpoints")),
        "service_port": service_port,
        "use_service": _to_bool(row.get("use_service"), True),
        "autostart": _to_bool(row.get("autostart"), True),
        "tags": _clean_list(row.get("tags")),
        "capabilities": _clean_list(row.get("capabilities")),
        "source": source,
        "updated_at": str(row.get("updated_at") or ""),
        "first_seen_at": str(row.get("first_seen_at") or ""),
        "last_seen_at": str(row.get("last_seen_at") or ""),
    }


def _guess_node_host(cfg: dict, fallback_node_name: str) -> str:
    candidates: list[str] = []
    managed = cfg.get("managed_install_repos")
    if isinstance(managed, list):
        for row in managed:
            if not isinstance(row, dict):
                continue
            for key in ("api_base_url", "health_url", "ui_url"):
                raw = str(row.get(key) or "").strip()
                if not raw:
                    continue
                try:
                    parsed = urlparse(raw)
                except Exception:
                    parsed = None
                if parsed and parsed.hostname:
                    candidates.append(parsed.hostname)
    if candidates:
        return candidates[0]
    return fallback_node_name


def _guess_node_name(cfg: dict, fallback_node_name: str) -> str:
    managed = cfg.get("managed_install_repos")
    if isinstance(managed, list):
        for row in managed:
            if not isinstance(row, dict):
                continue
            for key in ("node_name", "hostname"):
                value = str(row.get(key) or "").strip()
                if value:
                    return value
    return fallback_node_name


def _build_runtime_entries(cfg: dict, fallback_node_name: str) -> list[dict]:
    node_host = _guess_node_host(cfg, fallback_node_name)
    node_name = _guess_node_name(cfg, fallback_node_name) or node_host or fallback_node_name
    entries: list[dict] = []

    portal_base = f"http://{node_host}:5070" if node_host else "http://127.0.0.1:5070"
    entries.append(
        _normalize_entry(
            {
                "service_id": "deviceportal_core",
                "service_name": "device-portal.service",
                "repo_name": "Device-Portal",
                "repo_link": "internal://deviceportal/core",
                "api_base_url": portal_base,
                "health_url": f"{portal_base}/health",
                "ui_url": f"{portal_base}/",
                "node_name": node_name,
                "hostname": node_host,
                "service_port": 5070,
                "tags": ["jarvis", "portal", "system"],
                "capabilities": ["portal.ui", "portal.sync", "portal.registry"],
                "source": "runtime_system",
            },
            "runtime_system",
            fallback_node_name,
        )
    )

    llm = cfg.get("llm_manager") if isinstance(cfg.get("llm_manager"), dict) else {}
    llm_base = str(llm.get("api_base_url") or "").strip()
    if llm_base:
        llm_models = llm.get("models") if isinstance(llm.get("models"), list) else []
        entries.append(
            _normalize_entry(
                {
                    "service_id": "llm_manager_runtime",
                    "service_name": "llm-manager.runtime",
                    "repo_name": "LLM-Manager",
                    "repo_link": "internal://llm-manager/runtime",
                    "api_base_url": llm_base,
                    "health_url": str(llm.get("health_url") or "").strip(),
                    "ui_url": str(llm.get("ui_url") or "").strip(),
                    "node_name": node_name,
                    "hostname": node_host,
                    "service_port": urlparse(llm_base).port if urlparse(llm_base).port else None,
                    "tags": ["jarvis", "llm", "system"],
                    "capabilities": ["llm.runtime", "ollama.version", "llm.models"],
                    "default_model": str(llm.get("default_model") or "").strip(),
                    "models": llm_models,
                    "source": "runtime_system",
                },
                "runtime_system",
                fallback_node_name,
            )
        )

    return entries


def _dedupe_entries(rows: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for row in rows:
        service_id = str(row.get("service_id") or "").strip().lower()
        repo_link = _normalize_repo_link(row.get("repo_link"))
        install_dir = str(row.get("install_dir") or "").strip().lower()
        key = f"sid:{service_id}" if service_id else f"repo:{repo_link}|{install_dir}"
        deduped[key] = row
    return list(deduped.values())


def _prefer_modern_audio_entries(rows: list[dict]) -> list[dict]:
    def _bag(row: dict) -> str:
        return " ".join([
            str(row.get("repo_name") or "").strip().lower(),
            str(row.get("repo_link") or "").strip().lower(),
            str(row.get("service_name") or "").strip().lower(),
        ])

    has_modern_audio = False
    for row in rows:
        bag = _bag(row)
        if "jarvis-audioplayer" in bag or "jarvis-audio-player" in bag:
            has_modern_audio = True
            break

    if not has_modern_audio:
        return rows

    filtered: list[dict] = []
    for row in rows:
        bag = _bag(row)
        is_legacy_deviceplayer = (
            ("deviceplayer" in bag or "device-player" in bag)
            and "displayplayer" not in bag
            and "jarvis-audioplayer" not in bag
            and "jarvis-audio-player" not in bag
        )
        if is_legacy_deviceplayer:
            continue
        filtered.append(row)
    return filtered


def _parse_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _trigger_enabled(sync_cfg: dict, trigger: str) -> bool:
    key_map = {
        "login": "trigger_login",
        "manual_sync": "trigger_manual_sync",
        "auto_sync": "trigger_auto_sync",
        "warmup": "trigger_warmup",
    }
    key = key_map.get(trigger)
    if not key:
        return True
    return _to_bool(sync_cfg.get(key), True)


def maybe_push_family_registry(cfg: dict, trigger: str, *, force: bool = False) -> dict:
    sync_cfg = cfg.get("family_panel_registry_sync") if isinstance(cfg.get("family_panel_registry_sync"), dict) else {}
    enabled = _to_bool(sync_cfg.get("enabled"), True)
    if not enabled:
        return {"ok": False, "pushed": False, "reason": "disabled"}
    if not _trigger_enabled(sync_cfg, trigger):
        return {"ok": False, "pushed": False, "reason": f"trigger_{trigger}_disabled"}

    base_url = _safe_base_url(sync_cfg.get("base_url"))
    path = _safe_path(sync_cfg.get("sync_path"), "/api/public/portal/service-registry/sync")
    token = str(sync_cfg.get("sync_token") or "").strip()
    timeout_seconds = max(2, _to_int(sync_cfg.get("timeout_seconds"), 8))
    min_interval_seconds = max(0, _to_int(sync_cfg.get("min_interval_seconds"), 15))

    if not base_url:
        return {"ok": False, "pushed": False, "reason": "base_url_missing"}
    if not token:
        return {"ok": False, "pushed": False, "reason": "token_missing"}

    if not force and min_interval_seconds > 0:
        last_pushed_at = _parse_iso(sync_cfg.get("last_pushed_at"))
        if last_pushed_at is not None:
            now = datetime.now(timezone.utc)
            if last_pushed_at.tzinfo is None:
                last_pushed_at = last_pushed_at.replace(tzinfo=timezone.utc)
            age = (now - last_pushed_at).total_seconds()
            if age < min_interval_seconds:
                return {"ok": False, "pushed": False, "reason": "throttled", "retry_after_seconds": int(min_interval_seconds - age)}

    fallback_node_name = str(cfg.get("device_slug") or "").strip() or socket.gethostname().strip() or "deviceportal-node"
    entries: list[dict] = []

    if _to_bool(sync_cfg.get("include_managed_repos"), True):
        managed = cfg.get("managed_install_repos")
        if isinstance(managed, list):
            for row in managed:
                if not isinstance(row, dict):
                    continue
                entries.append(_normalize_entry(row, "managed_repo", fallback_node_name))

    if _to_bool(sync_cfg.get("include_autodiscover"), True):
        autodiscover = cfg.get("autodiscover_services")
        if isinstance(autodiscover, list):
            for row in autodiscover:
                if not isinstance(row, dict):
                    continue
                entries.append(_normalize_entry(row, "autodiscover", fallback_node_name))

    if _to_bool(sync_cfg.get("include_runtime_services"), True):
        entries.extend(_build_runtime_entries(cfg, fallback_node_name))

    entries = _dedupe_entries(entries)
    entries = _prefer_modern_audio_entries(entries)
    if not entries:
        return {"ok": False, "pushed": False, "reason": "entries_empty"}

    payload = {
        "trigger": trigger,
        "sent_at": utc_now(),
        "node_runtime_type": str(cfg.get("node_runtime_type") or ""),
        "device_slug": str(cfg.get("device_slug") or ""),
        "entries": entries,
    }
    url = f"{base_url}{path}"
    headers = {
        "Content-Type": "application/json",
        "X-Portal-Sync-Token": token,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        try:
            resp_json = response.json()
        except Exception:
            resp_json = {"raw": (response.text or "")[:2000]}
        ok = 200 <= response.status_code < 300 and bool((resp_json or {}).get("ok", True))

        sync_cfg["last_trigger"] = trigger
        sync_cfg["last_pushed_at"] = utc_now()
        sync_cfg["last_http_status"] = int(response.status_code)
        sync_cfg["last_status"] = "success" if ok else "error"
        sync_cfg["last_error"] = "" if ok else str((resp_json or {}).get("error") or f"http_{response.status_code}")
        cfg["family_panel_registry_sync"] = sync_cfg
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

        if not ok:
            current_app.logger.warning(
                "Family registry push failed: trigger=%s http=%s error=%s",
                trigger,
                response.status_code,
                sync_cfg["last_error"],
            )
        return {
            "ok": ok,
            "pushed": ok,
            "http_status": int(response.status_code),
            "response": resp_json if isinstance(resp_json, dict) else {"raw": str(resp_json)},
            "entries_count": len(entries),
        }
    except Exception as exc:
        sync_cfg["last_trigger"] = trigger
        sync_cfg["last_status"] = "error"
        sync_cfg["last_error"] = str(exc)
        sync_cfg["last_http_status"] = None
        cfg["family_panel_registry_sync"] = sync_cfg
        cfg["updated_at"] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        current_app.logger.warning("Family registry push error: trigger=%s err=%s", trigger, str(exc))
        return {"ok": False, "pushed": False, "reason": "request_failed", "error": str(exc)}
