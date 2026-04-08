from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from app.core.config import ensure_config

_LAST_GOOD_BACKEND_URL: str = ""


def _normalize_base_url(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if not value.startswith("http://") and not value.startswith("https://"):
        value = f"http://{value}"
    return value.rstrip("/")


def _is_audio_repo_candidate(repo: dict[str, Any]) -> bool:
    name = str(repo.get("name") or repo.get("repo_name") or "").strip().lower()
    repo_link = str(repo.get("repo_link") or repo.get("repo_url") or "").strip().lower()
    if "audioplayer" in name or "audio-player" in name or "audioplayer" in repo_link:
        return True

    try:
        service_port = int(repo.get("service_port"))
    except Exception:
        service_port = 0
    if service_port in {5095, 5096, 5097}:
        return True

    tags = repo.get("tags") if isinstance(repo.get("tags"), list) else []
    if any(str(tag or "").strip().lower() == "audio" for tag in tags):
        return True

    capabilities = repo.get("capabilities") if isinstance(repo.get("capabilities"), list) else []
    for capability in capabilities:
        if str(capability or "").strip().lower().startswith("audio."):
            return True
    return False


def _local_host_names() -> set[str]:
    names = {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    try:
        names.add(socket.gethostname().strip().lower())
    except Exception:
        pass
    try:
        names.add(socket.getfqdn().strip().lower())
    except Exception:
        pass
    return {name for name in names if name}


def _candidate_score(repo: dict[str, Any], base_url: str, local_names: set[str]) -> int:
    score = 0
    parsed = urlparse(base_url)
    host = str(parsed.hostname or "").strip().lower()
    port = int(parsed.port or 0)
    if host in local_names:
        score += 220
    if host.startswith("127."):
        score += 220
    if port in {5096, 5097, 5095}:
        score += 150
    if port == 5070:
        score -= 120

    install_dir = str(repo.get("install_dir") or "").strip()
    if install_dir and Path(install_dir).exists():
        score += 260

    hostname = str(repo.get("hostname") or repo.get("node_name") or "").strip().lower()
    if hostname and hostname in local_names:
        score += 80

    return score


def audio_backend_base_urls(cfg: dict | None = None) -> list[str]:
    if not isinstance(cfg, dict):
        cfg = ensure_config()

    explicit = _normalize_base_url(os.getenv("DEVICEPORTAL_AUDIO_BACKEND_URL", ""))
    urls: list[tuple[int, str]] = []
    if explicit:
        urls.append((10_000, explicit))

    local_names = _local_host_names()
    candidates: list[dict[str, Any]] = []
    managed = cfg.get("managed_install_repos") if isinstance(cfg.get("managed_install_repos"), list) else []
    autodiscover = cfg.get("autodiscover_services") if isinstance(cfg.get("autodiscover_services"), list) else []
    candidates.extend([item for item in managed if isinstance(item, dict)])
    candidates.extend([item for item in autodiscover if isinstance(item, dict)])

    registry_candidate_count = 0
    for repo in candidates:
        if not _is_audio_repo_candidate(repo):
            continue
        base_url = _normalize_base_url(repo.get("api_base_url"))
        if not base_url:
            continue
        score = _candidate_score(repo, base_url, local_names)
        urls.append((score, base_url))
        registry_candidate_count += 1

    # Safety fallback only when no registry candidate exists.
    # Otherwise stale local ports can shadow the managed/reported backend.
    if registry_candidate_count == 0:
        for port in (5096, 5097, 5095):
            urls.append((180, f"http://127.0.0.1:{port}"))
        for host in sorted(local_names):
            if host in {"127.0.0.1", "::1", "localhost"}:
                continue
            for port in (5096, 5097, 5095):
                urls.append((120, f"http://{host}:{port}"))

    urls.sort(key=lambda row: row[0], reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for _, url in urls:
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def request_audio_backend(method: str, path: str, payload: dict | None = None, timeout: float = 6.0) -> tuple[int | None, dict[str, Any], str, str]:
    global _LAST_GOOD_BACKEND_URL
    cfg = ensure_config()
    method_up = str(method or "GET").strip().upper() or "GET"
    candidates = audio_backend_base_urls(cfg)
    if _LAST_GOOD_BACKEND_URL and _LAST_GOOD_BACKEND_URL in candidates:
        candidates = [_LAST_GOOD_BACKEND_URL] + [item for item in candidates if item != _LAST_GOOD_BACKEND_URL]

    first_error: tuple[int | None, dict[str, Any], str, str] | None = None
    first_success: tuple[int | None, dict[str, Any], str, str] | None = None
    wants_status = method_up == "GET" and path == "/api/audio/status"

    for base_url in candidates:
        url = f"{base_url}{path}"
        try:
            if method_up == "GET":
                response = requests.get(url, timeout=timeout)
            else:
                response = requests.post(url, json=payload or {}, timeout=timeout)
        except Exception as exc:
            if first_error is None:
                first_error = (None, {}, str(exc), base_url)
            continue

        status = int(response.status_code)
        try:
            data = response.json() if response.text else {}
        except Exception:
            data = {"raw": (response.text or "")[:4000]}
        if not isinstance(data, dict):
            data = {"raw": data}

        if status >= 400:
            if first_error is None:
                first_error = (status, data, f"http_{status}", base_url)
            continue

        if not wants_status:
            _LAST_GOOD_BACKEND_URL = base_url
            return status, data, "", base_url

        if first_success is None:
            first_success = (status, data, "", base_url)

        payload_data = data.get("data") if isinstance(data.get("data"), dict) else data
        if isinstance(payload_data, dict):
            active_source = str(payload_data.get("active_source") or "").strip().lower()
            radio = payload_data.get("radio") if isinstance(payload_data.get("radio"), dict) else {}
            tts = payload_data.get("tts") if isinstance(payload_data.get("tts"), dict) else {}
            radio_running = bool(radio.get("running"))
            tts_running = bool(tts.get("running"))
            if active_source in {"radio", "tts"} or radio_running or tts_running:
                _LAST_GOOD_BACKEND_URL = base_url
                return status, data, "", base_url

    if first_success is not None:
        status, data, err, base_url = first_success
        if base_url:
            _LAST_GOOD_BACKEND_URL = base_url
        return status, data, err, base_url
    if first_error is not None:
        return first_error
    return None, {}, "audio_backend_unreachable", ""
