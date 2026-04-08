from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import requests

from app.core.netcontrol import NetControlError, audio_outputs_status, audio_output_set
from app.core.config import ensure_config
from app.core.jsonio import write_json
from app.core.paths import CONFIG_PATH
from app.core.timeutil import utc_now
from app.services import bluetooth_service
from app.services.raspotify_service import status as raspotify_status
from app.services.radio_service import radio_service
from app.services.tts_service import tts_service


def _player_control_base_url() -> str:
    host = str(os.getenv("DEVICEPLAYER_CONTROL_API_HOST", "127.0.0.1") or "").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    try:
        port = int(str(os.getenv("DEVICEPLAYER_CONTROL_API_PORT", "5081") or "5081").strip())
    except Exception:
        port = 5081
    return f"http://{host}:{port}"


def _player_runtime_status() -> dict[str, Any]:
    url = f"{_player_control_base_url()}/player/status"
    try:
        response = requests.get(url, timeout=1.5)
    except Exception:
        return {}
    if int(response.status_code) >= 400:
        return {}
    try:
        data = response.json() if response.text else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_audio_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if isinstance(payload.get("status"), dict):
        return payload.get("status")  # type: ignore[return-value]
    if isinstance(payload.get("audio"), dict):
        return payload.get("audio")  # type: ignore[return-value]
    return payload if isinstance(payload, dict) else {}


def _normalize_base_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if not value.startswith("http://") and not value.startswith("https://"):
        value = f"http://{value}"
    return value.rstrip("/")


def _is_audio_repo_candidate(repo: dict[str, Any]) -> bool:
    name = str(repo.get("name") or "").strip().lower()
    repo_link = str(repo.get("repo_link") or "").strip().lower()
    if "audioplayer" in name or "audio-player" in name or "audioplayer" in repo_link:
        return True
    service_port = repo.get("service_port")
    if isinstance(service_port, int) and service_port in {5095, 5096, 5097}:
        return True
    tags = repo.get("tags") if isinstance(repo.get("tags"), list) else []
    for tag in tags:
        if str(tag or "").strip().lower() == "audio":
            return True
    capabilities = repo.get("capabilities") if isinstance(repo.get("capabilities"), list) else []
    for capability in capabilities:
        if str(capability or "").strip().lower().startswith("audio."):
            return True
    return False


def _managed_audio_status_urls() -> list[str]:
    cfg = ensure_config()
    repos = cfg.get("managed_install_repos") if isinstance(cfg.get("managed_install_repos"), list) else []
    urls: list[str] = []

    explicit = _normalize_base_url(str(os.getenv("DEVICEPORTAL_AUDIO_STATUS_BASE_URL", "") or ""))
    if explicit:
        urls.append(f"{explicit}/api/audio/status")

    for repo in repos:
        if not isinstance(repo, dict) or not _is_audio_repo_candidate(repo):
            continue
        base = _normalize_base_url(str(repo.get("api_base_url") or ""))
        if not base:
            continue
        # Avoid recursive self-call on DevicePortal status endpoint.
        parsed = urlparse(base)
        port = parsed.port
        if port == 5070:
            continue
        urls.append(f"{base}/api/audio/status")

    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = url.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(url)
    return deduped[:4]


def _managed_runtime_status() -> dict[str, Any]:
    for url in _managed_audio_status_urls():
        try:
            response = requests.get(url, timeout=1.2)
        except Exception:
            continue
        if int(response.status_code) >= 400:
            continue
        try:
            raw = response.json() if response.text else {}
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        data = _extract_audio_payload(raw)
        if not isinstance(data, dict):
            continue
        active_source = str(data.get("active_source") or "").strip().lower()
        radio = data.get("radio") if isinstance(data.get("radio"), dict) else {}
        tts = data.get("tts") if isinstance(data.get("tts"), dict) else {}
        radio_running = bool(radio.get("running"))
        tts_running = bool(tts.get("running"))
        if active_source in {"", "idle", "none"}:
            if tts_running:
                active_source = "tts"
            elif radio_running:
                active_source = "radio"
        if active_source in {"", "idle", "none"}:
            continue
        return {
            "active_source": active_source,
            "radio": radio,
            "tts": tts,
            "updated_at": str(data.get("updated_at") or ""),
            "status_url": url,
        }
    return {}


def _build_sinks_payload(outputs: dict[str, Any]) -> dict[str, Any]:
    sinks: list[dict[str, Any]] = []
    default_sink = None
    current_output = str(outputs.get("current_output") or "").strip()
    available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []

    for item in available:
        if not isinstance(item, dict):
            continue
        sink_name = str(item.get("sink_name") or "").strip()
        if not sink_name:
            continue
        sink = {
            "name": sink_name,
            "description": str(item.get("label") or item.get("description") or sink_name),
            "output_id": str(item.get("id") or ""),
            "type": str(item.get("type") or ""),
            "is_default": str(item.get("id") or "") == current_output,
            "volume_percent": item.get("volume_percent"),
        }
        if sink["is_default"]:
            default_sink = sink_name
        sinks.append(sink)

    return {"default_sink": default_sink, "sinks": sinks}


def _pick_fallback_output(outputs: dict[str, Any]) -> str:
    available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
    for preferred in ("local_speaker", "local_hdmi"):
        for item in available:
            if str(item.get("id") or "") == preferred and item.get("available"):
                return preferred
    for item in available:
        if item.get("available"):
            return str(item.get("id") or "")
    return ""


def _auto_restore_output(outputs: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(outputs, dict) or not outputs.get("ok"):
        return outputs
    current_output = str(outputs.get("current_output") or "").strip()
    if current_output:
        return outputs

    cfg = ensure_config()
    profile = cfg.get("audio_output") if isinstance(cfg.get("audio_output"), dict) else {}
    selected = str(profile.get("selected_output") or "").strip()
    available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
    available_ids = {str(item.get("id") or "") for item in available if item.get("available")}

    target = ""
    if selected and selected in available_ids:
        target = selected
    else:
        target = _pick_fallback_output(outputs)

    if not target:
        return outputs

    try:
        audio_output_set(target)
        refreshed = audio_outputs_status()
        refreshed["ok"] = True
        if target != selected:
            profile["selected_output"] = target
            profile["updated_at"] = utc_now()
            cfg["audio_output"] = profile
            cfg["updated_at"] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
        return refreshed
    except NetControlError:
        return outputs


def collect_status(
    service_name: str = "",
    service_user: str = "",
    service_scope: str = "",
    service_candidates: str = "",
    include_bluetooth: bool = True,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    cfg = ensure_config()
    output_profile = cfg.get("audio_output") if isinstance(cfg.get("audio_output"), dict) else {}
    selected_output = str(output_profile.get("selected_output") or "").strip()

    bluetooth: dict[str, Any] = {"ok": False}
    if include_bluetooth:
        try:
            bt_status = bluetooth_service.status()
            bt_devices = bluetooth_service.list_devices()
            devices = bt_devices.get("devices") if isinstance(bt_devices.get("devices"), list) else []
            connected = [d for d in devices if isinstance(d, dict) and d.get("connected")]
            bluetooth = {
                "ok": True,
                "status": bt_status,
                "devices": devices,
                "connected_count": len(connected),
            }
        except NetControlError as exc:
            errors.append({"scope": "bluetooth", "message": exc.message})
            bluetooth = {"ok": False, "error": exc.message, "detail": exc.detail}

    outputs: dict[str, Any] = {"ok": False}
    try:
        outputs = audio_outputs_status()
        outputs["ok"] = True
        outputs = _auto_restore_output(outputs)
        if isinstance(outputs, dict):
            outputs["saved"] = {
                "selected_output": selected_output,
                "updated_at": str(output_profile.get("updated_at") or ""),
            }
            if not str(outputs.get("current_output") or "").strip() and selected_output:
                outputs["current_output"] = selected_output
    except NetControlError as exc:
        errors.append({"scope": "audio_outputs", "message": exc.message})
        outputs = {"ok": False, "error": exc.message, "detail": exc.detail}

    raspotify: dict[str, Any] = {"ok": False}
    try:
        rsp = raspotify_status(
            service_name=service_name,
            service_user=service_user,
            service_scope=service_scope,
            service_candidates=service_candidates,
        )
        raspotify = {"ok": True, **rsp}
    except NetControlError as exc:
        errors.append({"scope": "raspotify", "message": exc.message})
        raspotify = {"ok": False, "error": exc.message, "detail": exc.detail}

    radio = radio_service.status()
    tts = tts_service.status()

    sinks_payload = _build_sinks_payload(outputs if isinstance(outputs, dict) else {})

    active_source = "idle"
    active_source_detail: dict[str, Any] = {}
    if tts.get("running"):
        active_source = "tts"
        active_source_detail = {
            "type": "tts",
            "label": "TTS",
            "running": True,
            "file_path": str(tts.get("file_path") or "").strip(),
            "pid": tts.get("pid"),
        }
    elif radio.get("running"):
        active_source = "radio"
        rtsp_adapter = radio.get("rtsp_adapter") if isinstance(radio.get("rtsp_adapter"), dict) else {}
        active_source_detail = {
            "type": "radio",
            "label": "Radio",
            "running": True,
            "stream_url": str(radio.get("stream_url") or "").strip(),
            "playback_url": str(radio.get("playback_url") or "").strip(),
            "rtsp_adapter_active": bool(rtsp_adapter.get("active")),
            "rtsp_adapter_target_url": str(rtsp_adapter.get("target_url") or "").strip(),
        }
    else:
        runtime = _player_runtime_status()
        runtime_state = str(runtime.get("state") or "").strip().lower()
        runtime_source_type = str(runtime.get("source_type") or "").strip().lower()
        runtime_has_source = runtime_state in {"playing", "buffering", "paused"} and runtime_source_type not in {"", "none", "idle"}
        if runtime_has_source:
            active_source = runtime_source_type
            active_source_detail = {
                "type": runtime_source_type,
                "label": f"Player ({runtime_source_type})",
                "state": runtime_state,
                "source": str(runtime.get("source") or "").strip(),
                "output": str(runtime.get("output") or "").strip(),
                "volume": runtime.get("volume"),
            }
        else:
            managed_runtime = _managed_runtime_status()
            managed_active_source = str(managed_runtime.get("active_source") or "").strip().lower()
            if managed_active_source not in {"", "idle", "none"}:
                active_source = managed_active_source
                if managed_active_source == "radio":
                    radio_data = managed_runtime.get("radio") if isinstance(managed_runtime.get("radio"), dict) else {}
                    active_source_detail = {
                        "type": "radio",
                        "label": "Radio",
                        "running": bool(radio_data.get("running")),
                        "stream_url": str(radio_data.get("stream_url") or "").strip(),
                        "playback_url": str(radio_data.get("playback_url") or "").strip(),
                        "status_url": str(managed_runtime.get("status_url") or "").strip(),
                    }
                elif managed_active_source == "tts":
                    tts_data = managed_runtime.get("tts") if isinstance(managed_runtime.get("tts"), dict) else {}
                    active_source_detail = {
                        "type": "tts",
                        "label": "TTS",
                        "running": bool(tts_data.get("running")),
                        "file_path": str(tts_data.get("file_path") or "").strip(),
                        "status_url": str(managed_runtime.get("status_url") or "").strip(),
                    }
                else:
                    active_source_detail = {
                        "type": managed_active_source,
                        "label": f"Managed ({managed_active_source})",
                        "status_url": str(managed_runtime.get("status_url") or "").strip(),
                    }

    return {
        "bluetooth": bluetooth,
        "outputs": outputs,
        "raspotify": raspotify,
        "radio": radio,
        "tts": tts,
        "sources": {"radio": radio, "tts": tts},
        "active_source_detail": active_source_detail,
        "sinks": sinks_payload,
        "active_source": active_source,
        "updated_at": utc_now(),
        "errors": errors,
    }
