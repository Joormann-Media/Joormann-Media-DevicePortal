from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlencode

import requests
from flask import Blueprint, jsonify, request

from app.core.config import _panel_url, _safe_base_url, ensure_config
from app.core.display import get_display_snapshot
from app.core.device import ensure_device
from app.core.fingerprint import collect_fingerprint, ensure_fingerprint
from app.core.gitinfo import get_repo_update_info, get_update_info
from app.core.httpclient import http_get_json, http_get_text, http_post_json
from app.core.jsonio import write_json
from app.core.netcontrol import (
    NetControlError,
    get_ap_clients,
    get_ap_status,
    get_bluetooth_status,
    get_network_info,
    get_wifi_status,
    player_service_action,
)
from app.core.paths import CONFIG_PATH, DEVICE_PATH
from app.core.state import update_state
from app.core.storage_state import get_storage_state
from app.core.systeminfo import get_hostname, get_ip, parse_load_stats, parse_mem_stats_kb
from app.core.timeutil import utc_now

bp_panel = Blueprint('panel', __name__)


def _normalize_node_type(value: str) -> str:
    raw = str(value or '').strip().lower()
    aliases = {
        'raspi': 'raspi_node',
        'raspberrypi': 'raspi_node',
        'raspi_node': 'raspi_node',
        'raspi-node': 'raspi_node',
        'server': 'server',
        'workstation': 'workstation',
    }
    return aliases.get(raw, 'raspi_node')


def _update_panel_link_state(cfg: dict, **kwargs) -> dict:
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    st.setdefault('linked', False)
    st.setdefault('last_check', None)
    st.setdefault('last_http', None)
    st.setdefault('last_response', None)
    st.setdefault('last_error', '')
    st.update(kwargs)
    st['last_check'] = utc_now()
    cfg['panel_link_state'] = st
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)
    return st


def _current_linked(cfg: dict) -> bool:
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    return bool(st.get('linked'))


def _response_indicates_unlinked(code: int | None, resp: object) -> bool:
    # 404 can be a route/proxy mismatch and should not hard-unlink immediately.
    if code in (401, 403, 410):
        return True
    if not isinstance(resp, dict):
        return False

    parts: list[str] = []
    for key in ('error', 'code', 'status', 'message', 'detail', 'reason'):
        value = resp.get(key)
        if value is not None:
            parts.append(str(value).strip().lower())

    text = ' '.join(parts)
    markers = (
        'unlinked',
        'not_linked',
        'not linked',
        'device_not_found',
        'unknown_device',
        'unknown device',
        'device missing',
        'auth_invalid',
        'invalid_auth',
        'invalid auth',
        'auth key invalid',
        'authkey invalid',
        'revoked',
    )
    return any(marker in text for marker in markers)


def _sticky_linked(cfg: dict, next_linked: bool, code: int | None = None, resp: object = None) -> bool:
    if next_linked:
        return True
    node_type = str(cfg.get('node_runtime_type') or '').strip().lower()
    if node_type in ('server', 'workstation'):
        # Hardware nodes should not be marked unlinked by Raspi-only auth endpoints.
        # Keep existing linked state (or previously known linked users) unless explicitly unlinked by user action.
        existing_users = cfg.get('panel_linked_users') if isinstance(cfg.get('panel_linked_users'), list) else []
        return _current_linked(cfg) or bool(existing_users)
    if _response_indicates_unlinked(code, resp):
        return False
    return _current_linked(cfg)


def _persist_node_type_choice(cfg: dict, node_type: str) -> None:
    normalized = _normalize_node_type(node_type)
    changed = False
    if str(cfg.get('node_runtime_type') or '').strip() != normalized:
        cfg['node_runtime_type'] = normalized
        changed = True
    # Keep ping path in sync with selected node class to avoid wrong endpoint checks after restart/update.
    desired_ping = '/api/device/ping' if normalized == 'raspi_node' else '/api/hardware-device/ping'
    if str(cfg.get('panel_ping_path') or '').strip() != desired_ping:
        cfg['panel_ping_path'] = desired_ping
        changed = True
    if changed:
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)


def _panel_ping_payload(dev: dict, fp: dict, host: str, ip: str, cfg: dict | None = None) -> dict:
    payload = {
        'deviceUuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
        'hostname': host,
        'ipAddress': ip,
        'fingerprint': {
            'machineId': dev.get('machine_id') or fp.get('machine_id') or '',
            'piSerial': dev.get('pi_serial') or ((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else ''),
            'os': fp.get('os') or {},
            'kernel': fp.get('kernel') or '',
        },
    }
    if isinstance(cfg, dict):
        keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
        portal_key = str((keys.get('raspi_to_admin') if isinstance(keys, dict) else '') or '').strip()
        if portal_key:
            payload['apiKey'] = portal_key
            payload['portalApiKey'] = portal_key
    return payload


def _panel_register_payload(cfg: dict, dev: dict, fp: dict, host: str, ip: str, token: str) -> dict:
    payload = _panel_ping_payload(dev, fp, host, ip, cfg)
    payload['registrationToken'] = token
    payload['panelRegisterPath'] = cfg.get('panel_register_path')
    # Panel register DTO expects these fields at top-level (not only nested in fingerprint).
    pi_serial = (
        (dev.get('pi_serial') or '').strip()
        or (((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else '') or '').strip()
        or f"unknown-{(dev.get('device_uuid') or 'device').replace('-', '')[:8]}"
    )
    machine_id = (
        (dev.get('machine_id') or '').strip()
        or (fp.get('machine_id') or '').strip()
        or f"unknown-{(dev.get('device_uuid') or 'device').replace('-', '')[:8]}"
    )
    payload['piSerial'] = pi_serial
    payload['machineId'] = machine_id
    snapshots = _collect_runtime_snapshots(cfg, dev, fp, host, ip)
    network = snapshots.get('network') if isinstance(snapshots.get('network'), dict) else {}
    primary_mac = str(network.get('primaryMacAddress') or network.get('macAddress') or '').strip()
    if primary_mac:
        payload['primaryMacAddress'] = primary_mac
        payload['macAddress'] = primary_mac
        payload['mac_address'] = primary_mac
    payload.update(snapshots)
    return payload


def _panel_jarvis_register_payload(cfg: dict, dev: dict, fp: dict, host: str, ip: str, token: str, node_type: str) -> dict:
    os_info = fp.get('os') if isinstance(fp.get('os'), dict) else {}
    payload = {
        'registrationToken': token,
        'registerToken': token,
        'token': token,
        'nodeName': host or 'Jarvis Node',
        'hostname': host,
        'nodeType': node_type,
        'type': node_type,
        'os': os_info.get('pretty_name') or os_info.get('id') or '',
        'operatingSystem': os_info.get('pretty_name') or os_info.get('id') or '',
        'osVersion': os_info.get('version') or '',
        'localIp': ip,
        'publicIp': '',
        'platform': fp.get('machine') or '',
        'fingerprintHash': dev.get('machine_id') or '',
    }
    return payload


def _safe_slug(value: str, fallback: str = 'deviceportal-node') -> str:
    raw = (value or '').strip().lower()
    if not raw:
        return fallback
    slug = re.sub(r'[^a-z0-9]+', '-', raw).strip('-')
    return slug or fallback


def _safe_read_text(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return (handle.read() or '').strip()
    except Exception:
        return ''


def _cpu_vendor() -> str:
    try:
        with open('/proc/cpuinfo', 'r', encoding='utf-8') as handle:
            for line in handle:
                lowered = line.lower()
                if lowered.startswith('vendor_id') or lowered.startswith('hardware'):
                    return line.split(':', 1)[1].strip()
    except Exception:
        return ''
    return ''


def _detect_public_ip() -> str:
    # Optional best-effort lookup; failures are acceptable.
    for service_url in ('https://api.ipify.org', 'https://ifconfig.me/ip'):
        code, text, _err = http_get_text(service_url, timeout=4)
        if code is not None and 200 <= code < 300:
            ip = (text or '').strip()
            if ip and len(ip) <= 128:
                return ip
    return ''


def _tailscale_runtime_status(network_info: dict | None = None) -> dict:
    source = (network_info or {}).get('tailscale') if isinstance(network_info, dict) else {}
    source = source if isinstance(source, dict) else {}

    present = bool(source.get('present'))
    ip = str(source.get('ip') or '').strip()
    service_active = False
    service_enabled = False

    try:
        service_active = subprocess.run(
            ['systemctl', 'is-active', '--quiet', 'tailscaled'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).returncode == 0
    except Exception:
        service_active = False

    try:
        service_enabled = subprocess.run(
            ['systemctl', 'is-enabled', '--quiet', 'tailscaled'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).returncode == 0
    except Exception:
        service_enabled = False

    if service_active or service_enabled:
        present = True

    tailscale_bin = shutil.which('tailscale')

    def _extract_ipv4(text: str) -> str:
        for line in (text or '').splitlines():
            candidate = line.strip().split()[0] if line.strip() else ''
            if candidate and re.fullmatch(r'(\d{1,3}\.){3}\d{1,3}', candidate):
                return candidate
        return ''

    if present and not ip and tailscale_bin:
        try:
            proc = subprocess.run(
                [tailscale_bin, 'ip', '-4'],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
            ip = _extract_ipv4(proc.stdout or '')
        except Exception:
            ip = ''

    if present and not ip and tailscale_bin:
        try:
            proc = subprocess.run(
                [tailscale_bin, 'status'],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
            ip = _extract_ipv4(proc.stdout or '')
        except Exception:
            ip = ''

    # Fallback for hosts where tailscale CLI requires elevated rights.
    if present and not ip and tailscale_bin and shutil.which('sudo'):
        try:
            proc = subprocess.run(
                ['sudo', '-n', tailscale_bin, 'ip', '-4'],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
            ip = _extract_ipv4(proc.stdout or '')
        except Exception:
            ip = ''

    return {
        **source,
        'present': present,
        'ip': ip,
        'service_active': service_active,
        'service_enabled': service_enabled,
    }


def _network_identity(network: dict, fallback_ip: str) -> tuple[str, str, str, str]:
    interfaces = network.get('network_info', {}).get('interfaces') if isinstance(network.get('network_info'), dict) else {}
    lan = interfaces.get('lan') if isinstance(interfaces, dict) and isinstance(interfaces.get('lan'), dict) else {}
    wifi = interfaces.get('wifi') if isinstance(interfaces, dict) and isinstance(interfaces.get('wifi'), dict) else {}
    tailscale = network.get('tailscale') if isinstance(network.get('tailscale'), dict) else {}

    local_lan_ip = str(lan.get('ip') or wifi.get('ip') or fallback_ip or '').strip()
    primary_mac = str(lan.get('mac') or wifi.get('mac') or '').strip()
    if not primary_mac:
        primary_mac = _detect_primary_mac(local_lan_ip)
    tailscale_ip = str(tailscale.get('ip') or '').strip()
    public_ip = _detect_public_ip()
    return local_lan_ip, public_ip, tailscale_ip, primary_mac


def _normalize_mac(value: str) -> str:
    raw = str(value or '').strip().upper().replace('-', ':')
    if re.fullmatch(r'([0-9A-F]{2}:){5}[0-9A-F]{2}', raw):
        return raw
    return ''


def _detect_primary_mac(preferred_ip: str = '') -> str:
    preferred_ip = str(preferred_ip or '').strip()
    rows: list[tuple[str, str]] = []
    try:
        import subprocess

        proc = subprocess.run(
            ['ip', '-o', '-4', 'addr', 'show', 'up'],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        for line in (proc.stdout or '').splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            ifname = parts[1]
            ipv4 = parts[3].split('/')[0].strip()
            if ifname.startswith(('lo', 'docker', 'veth', 'br-', 'virbr', 'tun', 'tap')):
                continue
            if not ipv4:
                continue
            mac_path = Path(f'/sys/class/net/{ifname}/address')
            if not mac_path.exists():
                continue
            mac = _normalize_mac(mac_path.read_text(encoding='utf-8').strip())
            if not mac:
                continue
            rows.append((ifname, mac))
            if preferred_ip and ipv4 == preferred_ip:
                return mac
    except Exception:
        pass

    for ifname in ('eth0', 'enp0s3', 'enp3s0', 'wlan0', 'wlp2s0'):
        mac_path = Path(f'/sys/class/net/{ifname}/address')
        if not mac_path.exists():
            continue
        mac = _normalize_mac(mac_path.read_text(encoding='utf-8').strip())
        if mac:
            return mac

    return rows[0][1] if rows else ''


def _hardware_components_from_snapshots(fp: dict, snapshots: dict, primary_mac: str, local_lan_ip: str) -> list[dict]:
    components: list[dict] = []
    sort_order = 10
    now = utc_now()

    network_info = snapshots.get('network', {}).get('network_info') if isinstance(snapshots.get('network'), dict) else {}
    interfaces = network_info.get('interfaces') if isinstance(network_info, dict) and isinstance(network_info.get('interfaces'), dict) else {}
    for if_key in ('lan', 'wifi'):
        iface = interfaces.get(if_key)
        if not isinstance(iface, dict):
            continue
        ifname = str(iface.get('ifname') or if_key).strip()
        mac = str(iface.get('mac') or '').strip()
        ipv4 = str(iface.get('ip') or '').strip()
        is_primary = bool((primary_mac and mac and primary_mac.lower() == mac.lower()) or (local_lan_ip and ipv4 and local_lan_ip == ipv4))
        components.append(
            {
                'componentType': 'network_interface',
                'name': ifname,
                'manufacturer': '',
                'vendor': '',
                'model': ifname,
                'serialNumber': '',
                'firmwareVersion': '',
                'interfaceType': if_key,
                'capacityBytes': None,
                'sizeLabel': '',
                'slotName': ifname,
                'macAddress': mac,
                'ipv4': ipv4,
                'ipv6': '',
                'isPrimary': is_primary,
                'sortOrder': sort_order,
                'rawData': iface,
                'fingerprintRelevant': True,
                'notes': '',
                'createdAt': now,
                'updatedAt': now,
            }
        )
        sort_order += 10

    disks = fp.get('disks') if isinstance(fp.get('disks'), dict) else {}
    block_devices = disks.get('blockdevices') if isinstance(disks, dict) and isinstance(disks.get('blockdevices'), list) else []
    for disk in block_devices:
        if not isinstance(disk, dict):
            continue
        dev_type = str(disk.get('type') or '').strip().lower()
        if dev_type not in ('disk', 'part'):
            continue
        size_label = str(disk.get('size') or '').strip()
        name = str(disk.get('name') or '').strip()
        mountpoint = str(disk.get('mountpoint') or '').strip()
        components.append(
            {
                'componentType': 'storage',
                'name': name,
                'manufacturer': '',
                'vendor': '',
                'model': str(disk.get('model') or name),
                'serialNumber': '',
                'firmwareVersion': '',
                'interfaceType': str(disk.get('tran') or '').strip(),
                'capacityBytes': None,
                'sizeLabel': size_label,
                'slotName': mountpoint,
                'macAddress': '',
                'ipv4': '',
                'ipv6': '',
                'isPrimary': bool(mountpoint in ('/', '/boot', '/boot/firmware')),
                'sortOrder': sort_order,
                'rawData': disk,
                'fingerprintRelevant': False,
                'notes': '',
                'createdAt': now,
                'updatedAt': now,
            }
        )
        sort_order += 10

    cpu_model = ((fp.get('cpu') or {}).get('model') if isinstance(fp.get('cpu'), dict) else '') or ''
    if cpu_model:
        components.append(
            {
                'componentType': 'cpu',
                'name': 'cpu',
                'manufacturer': '',
                'vendor': _cpu_vendor(),
                'model': str(cpu_model),
                'serialNumber': str(((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else '') or ''),
                'firmwareVersion': '',
                'interfaceType': '',
                'capacityBytes': None,
                'sizeLabel': '',
                'slotName': '',
                'macAddress': '',
                'ipv4': '',
                'ipv6': '',
                'isPrimary': True,
                'sortOrder': sort_order,
                'rawData': fp.get('cpu') if isinstance(fp.get('cpu'), dict) else {},
                'fingerprintRelevant': True,
                'notes': '',
                'createdAt': now,
                'updatedAt': now,
            }
        )
        sort_order += 10

    mem_total_kb = int(((fp.get('memory') or {}).get('mem_total_kb') if isinstance(fp.get('memory'), dict) else 0) or 0)
    if mem_total_kb > 0:
        components.append(
            {
                'componentType': 'memory',
                'name': 'ram',
                'manufacturer': '',
                'vendor': '',
                'model': 'system memory',
                'serialNumber': '',
                'firmwareVersion': '',
                'interfaceType': '',
                'capacityBytes': int(mem_total_kb) * 1024,
                'sizeLabel': f"{round(mem_total_kb / 1024 / 1024, 1)} GB",
                'slotName': '',
                'macAddress': '',
                'ipv4': '',
                'ipv6': '',
                'isPrimary': True,
                'sortOrder': sort_order,
                'rawData': fp.get('memory') if isinstance(fp.get('memory'), dict) else {},
                'fingerprintRelevant': True,
                'notes': '',
                'createdAt': now,
                'updatedAt': now,
            }
        )

    return components


def _panel_hardware_register_payload(cfg: dict, dev: dict, fp: dict, host: str, ip: str, token: str, node_type: str) -> dict:
    snapshots = _collect_runtime_snapshots(cfg, dev, fp, host, ip)
    network = snapshots.get('network') if isinstance(snapshots.get('network'), dict) else {}
    local_lan_ip, public_ip, tailscale_ip, primary_mac = _network_identity(network, ip)
    now = utc_now()

    os_info = fp.get('os') if isinstance(fp.get('os'), dict) else {}
    mem_total_kb = int(((fp.get('memory') or {}).get('mem_total_kb') if isinstance(fp.get('memory'), dict) else 0) or 0)
    fingerprint_hash = hashlib.sha256(
        json.dumps(fp if isinstance(fp, dict) else {}, ensure_ascii=False, sort_keys=True).encode('utf-8')
    ).hexdigest()

    hardware_device = {
        'id': None,
        'uuid': str(dev.get('device_uuid') or ''),
        'slug': _safe_slug(host),
        'deviceName': str(host or 'DevicePortal Node'),
        'hostname': str(host or ''),
        'description': f"DevicePortal First-Run ({node_type})",
        'type': node_type,
        'lifecycleStatus': 'active',
        'isActive': True,
        'isLocked': False,
        'registerToken': token,
        'registerTokenCreatedAt': now,
        'registerTokenExpiresAt': None,
        'registeredAt': now,
        'linkedAt': now,
        'lastSeenAt': now,
        'lastSyncAt': now,
        'apiCommunicationEnabled': True,
        'clientId': str(dev.get('machine_id') or dev.get('device_uuid') or host or ''),
        'developerApiKey': str((((cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}) or {}).get('raspi_to_admin') or '')).strip(),
        'fingerprintHash': fingerprint_hash,
        'fingerprintVersion': 'v1',
        'osName': str(os_info.get('pretty_name') or os_info.get('id') or ''),
        'osVersion': str(os_info.get('version') or ''),
        'architecture': str(fp.get('machine') or ''),
        'machineId': str(dev.get('machine_id') or fp.get('machine_id') or ''),
        'productUuid': _safe_read_text('/sys/class/dmi/id/product_uuid'),
        'biosVersion': _safe_read_text('/sys/class/dmi/id/bios_version'),
        'biosVendor': _safe_read_text('/sys/class/dmi/id/bios_vendor'),
        'boardVendor': _safe_read_text('/sys/class/dmi/id/board_vendor'),
        'boardName': _safe_read_text('/sys/class/dmi/id/board_name'),
        'boardSerial': _safe_read_text('/sys/class/dmi/id/board_serial'),
        'cpuVendor': _cpu_vendor(),
        'cpuModel': str(((fp.get('cpu') or {}).get('model') if isinstance(fp.get('cpu'), dict) else '') or ''),
        'ramTotal': int(mem_total_kb) * 1024 if mem_total_kb > 0 else None,
        'primaryMacAddress': primary_mac,
        'macAddress': primary_mac,
        'localLanIp': local_lan_ip,
        'publicIp': public_ip,
        'tailscaleIp': tailscale_ip,
        'local_lan_ip': local_lan_ip,
        'public_ip': public_ip,
        'tailscale_ip': tailscale_ip,
        'rawData': snapshots,
        'searchSanitized': f"{host} {node_type} {local_lan_ip} {tailscale_ip} {primary_mac}".strip().lower(),
        'notes': '',
        'nodeCapabilityProfile': snapshots.get('capabilities') if isinstance(snapshots.get('capabilities'), dict) else {},
        'nodeRuntimeStatus': snapshots.get('runtime') if isinstance(snapshots.get('runtime'), dict) else {},
        'nodeConfiguration': {
            'panel_register_path': str(cfg.get('panel_register_path') or ''),
            'panel_hardware_register_path': str(cfg.get('panel_hardware_register_path') or ''),
        },
        'createdAt': now,
        'updatedAt': now,
    }
    components = _hardware_components_from_snapshots(fp, snapshots, primary_mac=primary_mac, local_lan_ip=local_lan_ip)
    hardware_device['components'] = components

    return {
        'registerToken': token,
        'register_token': token,
        'nodeType': node_type,
        'node_type': node_type,
        'deviceUuid': dev.get('device_uuid') or '',
        'device_uuid': dev.get('device_uuid') or '',
        'machineId': dev.get('machine_id') or fp.get('machine_id') or '',
        'machine_id': dev.get('machine_id') or fp.get('machine_id') or '',
        'primaryMacAddress': primary_mac,
        'macAddress': primary_mac,
        'mac_address': primary_mac,
        'authKey': dev.get('auth_key') or '',
        'auth_key': dev.get('auth_key') or '',
        'hardwareDevice': hardware_device,
        'hardware_device': hardware_device,
        'hardwareComponents': components,
        'hardware_components': components,
        'fingerprint': fp,
        'runtimeSnapshots': snapshots,
    }


def _http_post_json_with_headers(url: str, payload: dict, headers: dict[str, str], timeout: int = 10) -> tuple[int | None, dict | None, str]:
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = {'raw': (response.text or '')[:2000]}
        return response.status_code, data, ''
    except Exception as exc:
        return None, None, str(exc)


def _http_get_json_with_headers(url: str, headers: dict[str, str], timeout: int = 10) -> tuple[int | None, dict | None, str]:
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = {'raw': (response.text or '')[:2000]}
        return response.status_code, data, ''
    except Exception as exc:
        return None, None, str(exc)


def _normalize_component_type(value: str) -> str:
    raw = (value or '').strip().lower()
    mapping = {
        'network_interface': 'nic',
        'storage': 'disk',
        'memory': 'ram',
    }
    return mapping.get(raw, raw if raw in {'disk', 'cpu', 'ram', 'mainboard', 'nic', 'gpu', 'other'} else 'other')


def _panel_hardware_auto_import(base_url: str, client_id: str, api_key: str, register_payload: dict) -> dict:
    if not base_url or not client_id or not api_key:
        return {'ok': False, 'error': 'missing_import_credentials'}

    hardware_device = register_payload.get('hardwareDevice') if isinstance(register_payload.get('hardwareDevice'), dict) else {}
    hardware_components = register_payload.get('hardwareComponents') if isinstance(register_payload.get('hardwareComponents'), list) else []
    network = (register_payload.get('runtimeSnapshots') or {}).get('network') if isinstance(register_payload.get('runtimeSnapshots'), dict) else {}

    hardware_payload = {
        'deviceName': str(hardware_device.get('deviceName') or '').strip(),
        'hostname': str(hardware_device.get('hostname') or '').strip(),
        'description': str(hardware_device.get('description') or '').strip(),
        'osName': str(hardware_device.get('osName') or '').strip(),
        'osVersion': str(hardware_device.get('osVersion') or '').strip(),
        'architecture': str(hardware_device.get('architecture') or '').strip(),
        'machineId': str(hardware_device.get('machineId') or '').strip(),
        'productUuid': str(hardware_device.get('productUuid') or '').strip(),
        'biosVersion': str(hardware_device.get('biosVersion') or '').strip(),
        'biosVendor': str(hardware_device.get('biosVendor') or '').strip(),
        'boardVendor': str(hardware_device.get('boardVendor') or '').strip(),
        'boardName': str(hardware_device.get('boardName') or '').strip(),
        'boardSerial': str(hardware_device.get('boardSerial') or '').strip(),
        'cpuVendor': str(hardware_device.get('cpuVendor') or '').strip(),
        'cpuModel': str(hardware_device.get('cpuModel') or '').strip(),
        'ramTotal': hardware_device.get('ramTotal'),
        'primaryMacAddress': str(hardware_device.get('primaryMacAddress') or '').strip(),
        'localLanIp': str(hardware_device.get('localLanIp') or '').strip(),
        'publicIp': str(hardware_device.get('publicIp') or '').strip(),
        'tailscaleIp': str(hardware_device.get('tailscaleIp') or '').strip(),
        'network': network if isinstance(network, dict) else {},
        'rawData': register_payload.get('runtimeSnapshots') if isinstance(register_payload.get('runtimeSnapshots'), dict) else register_payload,
    }

    components_payload = {
        'components': [
            {
                **item,
                'componentType': _normalize_component_type(str(item.get('componentType') or '')),
                'name': str(item.get('name') or item.get('slotName') or 'component').strip() or 'component',
            }
            for item in hardware_components
            if isinstance(item, dict)
        ]
    }

    headers = {'X-Client-Id': client_id, 'X-API-Key': api_key}
    hardware_candidates = ['/api/hardware-device/hardware-import', '/api/hardware/device/hardware-import']
    component_candidates = ['/api/hardware-device/component-import', '/api/hardware/device/component-import']

    hardware_result = {'ok': False, 'http': None, 'path': '', 'error': ''}
    for path in hardware_candidates:
        code, resp, err = _http_post_json_with_headers(f'{base_url}{path}', hardware_payload, headers, timeout=10)
        if code is not None and 200 <= code < 300:
            hardware_result = {'ok': True, 'http': code, 'path': path, 'response': resp}
            break
        hardware_result = {'ok': False, 'http': code, 'path': path, 'response': resp, 'error': _extract_response_message(resp) or str(err or '')}

    component_result = {'ok': False, 'http': None, 'path': '', 'error': ''}
    if components_payload['components']:
        for path in component_candidates:
            code, resp, err = _http_post_json_with_headers(f'{base_url}{path}', components_payload, headers, timeout=10)
            if code is not None and 200 <= code < 300:
                component_result = {'ok': True, 'http': code, 'path': path, 'response': resp}
                break
            component_result = {'ok': False, 'http': code, 'path': path, 'response': resp, 'error': _extract_response_message(resp) or str(err or '')}
    else:
        component_result = {'ok': True, 'http': 0, 'path': '', 'response': {'status': 'skipped', 'reason': 'no_components'}}

    return {
        'ok': bool(hardware_result.get('ok')) and bool(component_result.get('ok')),
        'hardware_import': hardware_result,
        'component_import': component_result,
    }


def _panel_sync_payload(cfg: dict, dev: dict, fp: dict, host: str, ip: str) -> dict:
    payload = _panel_ping_payload(dev, fp, host, ip, cfg)
    payload['panelRegisterPath'] = cfg.get('panel_register_path')
    pi_serial = (
        (dev.get('pi_serial') or '').strip()
        or (((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else '') or '').strip()
        or f"unknown-{(dev.get('device_uuid') or 'device').replace('-', '')[:8]}"
    )
    machine_id = (
        (dev.get('machine_id') or '').strip()
        or (fp.get('machine_id') or '').strip()
        or f"unknown-{(dev.get('device_uuid') or 'device').replace('-', '')[:8]}"
    )
    payload['piSerial'] = pi_serial
    payload['machineId'] = machine_id
    snapshots = _collect_runtime_snapshots(cfg, dev, fp, host, ip)
    network = snapshots.get('network') if isinstance(snapshots.get('network'), dict) else {}
    primary_mac = str(network.get('primaryMacAddress') or network.get('macAddress') or '').strip()
    if primary_mac:
        payload['primaryMacAddress'] = primary_mac
        payload['macAddress'] = primary_mac
        payload['mac_address'] = primary_mac
    payload.update(snapshots)
    return payload


def _safe_call(default: object, fn):
    try:
        value = fn()
        if value is None:
            return default
        return value
    except Exception:
        return default


def _software_snapshot(update_info: dict, player_update_info: dict, network_info: dict, storage_info: dict) -> list[dict]:
    tailscale = _tailscale_runtime_status(network_info)
    tailscale_present = bool(tailscale.get('present'))
    tailscale_ip = str(tailscale.get('ip') or '').strip()
    local_version = str(update_info.get('local_version') or '').strip()
    local_commit = str(update_info.get('local_commit') or '').strip()
    remote_commit = str(update_info.get('remote_commit') or '').strip()
    items = [
        {
            'name': 'DevicePortal',
            'component': 'deviceportal',
            'category': 'portal',
            'type': 'git',
            'status': 'installed',
            'version': local_version or local_commit[:12],
            'source': 'portal',
            'notes': 'local branch: ' + str(update_info.get('local_branch') or '').strip() + (f' | commit: {local_commit[:12]}' if local_commit else ''),
        },
        {
            'name': 'Portal Update',
            'component': 'portal-update',
            'category': 'update',
            'type': 'git',
            'status': 'update_available' if bool(update_info.get('available')) else ('unknown' if str(update_info.get('error') or '').strip() else 'up_to_date'),
            'version': remote_commit[:12] or local_commit[:12],
            'source': 'origin',
            'notes': str(update_info.get('error') or '').strip(),
        },
        {
            'name': 'DevicePlayer',
            'component': 'deviceplayer',
            'category': 'player',
            'type': 'git',
            'status': 'update_available' if bool(player_update_info.get('available')) else ('unknown' if str(player_update_info.get('error') or '').strip() else 'installed'),
            'version': str(player_update_info.get('local_version') or '').strip() or str(player_update_info.get('local_commit') or '').strip()[:12],
            'source': 'player',
            'notes': (
                str(player_update_info.get('error') or '').strip()
                or ('branch: ' + str(player_update_info.get('local_branch') or '').strip())
            ),
        },
        {
            'name': 'Tailscale',
            'component': 'tailscale',
            'category': 'network',
            'type': 'apt',
            'status': 'connected' if tailscale_present and tailscale_ip else ('installed' if tailscale_present else 'missing'),
            'version': tailscale_ip or '',
            'source': 'network_info',
            'notes': (
                'tailscaled active' if bool(tailscale.get('service_active'))
                else ('tailscaled enabled' if bool(tailscale.get('service_enabled')) else '')
            ),
        },
        {
            'name': 'Storage Service',
            'component': 'storage',
            'category': 'storage',
            'type': 'managed',
            'status': 'active' if int(storage_info.get('known_count') or 0) > 0 else 'idle',
            'version': str(storage_info.get('known_count') or 0),
            'source': 'portal',
            'notes': '',
        },
    ]
    return items


def _resolve_player_repo_path(cfg: dict) -> str:
    raw = str(cfg.get('player_repo_dir') or cfg.get('player_repo_link') or '').strip()
    repo_root = Path(__file__).resolve().parents[2]
    workspace_root = repo_root.parent

    def _from_existing_path(path: Path) -> str:
        if path.exists() and (path / '.git').exists():
            return str(path.resolve())
        return ''

    # Local path already configured.
    if raw != '' and '://' not in raw:
        direct = Path(raw).expanduser()
        resolved = _from_existing_path(direct)
        if resolved:
            return resolved

    # URL-style config: infer clone target from update script convention.
    service_user = str(cfg.get('player_service_user') or '').strip()
    repo_name = 'Joormann-Media-DevicePlayer'
    if raw != '' and ('://' in raw or raw.startswith('git@') or raw.startswith('ssh://')):
        repo_name = raw.rstrip('/').split('/')[-1].replace('.git', '').strip() or repo_name

    candidates: list[Path] = []
    if service_user:
        candidates.append(Path(f"/home/{service_user}/projects/{repo_name}"))
        candidates.append(Path(f"/home/{service_user}/{repo_name}"))
    candidates.append(workspace_root / repo_name)
    candidates.append(workspace_root / "Joormann-Media-DevicePlayer")

    for candidate in candidates:
        resolved = _from_existing_path(candidate)
        if resolved:
            return resolved

    return ''


def _storage_snapshot(storage_info: dict) -> dict:
    drives = storage_info.get('drives')
    internal = storage_info.get('internal')
    out_devices: list[dict] = []

    if isinstance(drives, list):
        for item in drives:
            if not isinstance(item, dict):
                continue
            out_devices.append(
                {
                    'name': item.get('drive_name') or item.get('name') or item.get('label') or item.get('id') or '',
                    'driveName': item.get('drive_name') or '',
                    'uuid': item.get('uuid') or '',
                    'partUuid': item.get('part_uuid') or '',
                    'label': item.get('label') or '',
                    'filesystem': item.get('filesystem') or '',
                    'sizeBytes': int(item.get('size_bytes') or item.get('total_bytes') or 0),
                    'total_bytes': int(item.get('total_bytes') or 0),
                    'used_bytes': int(item.get('used_bytes') or 0),
                    'free_bytes': int(item.get('free_bytes') or 0),
                    'used_percent': int(item.get('used_percent') or 0),
                    'devicePath': item.get('current_device_path') or item.get('last_seen_device_path') or '',
                    'mountPath': item.get('mount_path') or item.get('current_mount_path') or '',
                    'mountOptions': item.get('mount_options') or '',
                    'isMounted': bool(item.get('mounted')),
                    'isPresent': bool(item.get('present')),
                    'isEnabled': bool(item.get('is_enabled', True)),
                    'allowMediaStorage': bool(item.get('allow_media_storage', False)),
                    'allowPortalStorage': bool(item.get('allow_portal_storage', False)),
                    'vendor': item.get('vendor') or '',
                    'model': item.get('model') or '',
                    'serial': item.get('serial') or '',
                    'transport': item.get('transport') or '',
                    'state': item.get('state') or '',
                    'is_internal': bool(item.get('is_internal', False)),
                }
            )

    if isinstance(internal, dict):
        out_devices.append(
            {
                'name': internal.get('drive_name') or internal.get('id') or 'internal-media',
                'driveName': internal.get('drive_name') or 'internal-media',
                'uuid': '',
                'partUuid': '',
                'label': internal.get('drive_name') or 'internal-media',
                'filesystem': internal.get('filesystem') or internal.get('expected_filesystem') or '',
                'sizeBytes': int(internal.get('loop_total_bytes') or internal.get('total_bytes') or 0),
                'total_bytes': int(internal.get('loop_total_bytes') or internal.get('total_bytes') or 0),
                'used_bytes': int(internal.get('loop_used_bytes') or internal.get('used_bytes') or 0),
                'free_bytes': int(internal.get('loop_free_bytes') or internal.get('free_bytes') or 0),
                'used_percent': int(internal.get('loop_used_percent') or internal.get('used_percent') or 0),
                'devicePath': internal.get('mounted_source') or internal.get('image_path') or '',
                'mountPath': internal.get('mount_path') or '',
                'mountOptions': '',
                'isMounted': bool(internal.get('mounted')),
                'isPresent': bool(internal.get('present')),
                'isEnabled': bool(internal.get('enabled', True)),
                'allowMediaStorage': bool(internal.get('allow_media_storage', True)),
                'allowPortalStorage': bool(internal.get('allow_portal_storage', True)),
                'vendor': '',
                'model': '',
                'serial': '',
                'transport': 'internal_loop',
                'state': internal.get('state') or '',
                'is_internal': True,
            }
        )

    return {
        'known_count': int(storage_info.get('known_count') or 0),
        'present_count': int(storage_info.get('present_count') or 0),
        'mounted_count': int(storage_info.get('mounted_count') or 0),
        'drives': drives if isinstance(drives, list) else [],
        'internal': internal if isinstance(internal, dict) else {},
        'devices': out_devices,
    }


def _collect_runtime_snapshots(cfg: dict, dev: dict, fp: dict, host: str, ip: str) -> dict:
    net_info = _safe_call({}, get_network_info)
    wifi_status = _safe_call({}, lambda: get_wifi_status(ifname='wlan0'))
    bt_status = _safe_call({}, get_bluetooth_status)
    ap_status = _safe_call({}, lambda: get_ap_status(ifname='wlan0', profile='jm-hotspot'))
    ap_clients = _safe_call({'clients': []}, lambda: get_ap_clients(ifname='wlan0'))
    storage_info = _safe_call({}, get_storage_state)
    update_info = _safe_call({}, get_update_info)
    player_repo_path = _resolve_player_repo_path(cfg)
    player_update_info = _safe_call({}, lambda: get_repo_update_info(player_repo_path))
    display_info = _safe_call({}, lambda: get_display_snapshot(cfg))
    memory = _safe_call({}, parse_mem_stats_kb)
    load = _safe_call({}, parse_load_stats)

    def _cpu_temp_c() -> float | None:
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r', encoding='utf-8') as f:
                raw = (f.read() or '').strip()
            if raw and raw.lstrip('-').isdigit():
                return round(int(raw) / 1000.0, 1)
        except Exception:
            pass
        return None

    def _uptime_seconds() -> int | None:
        try:
            with open('/proc/uptime', 'r', encoding='utf-8') as f:
                first = (f.read() or '').split()[0]
            return int(float(first))
        except Exception:
            return None

    def _uptime_human(seconds: int | None) -> str:
        if seconds is None or seconds < 0:
            return ''
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        parts: list[str] = []
        if days:
            parts.append(f'{days}d')
        if hours or days:
            parts.append(f'{hours}h')
        parts.append(f'{minutes}m')
        return ' '.join(parts)

    mem_total_kb = int(memory.get('mem_total_kb') or 0) if isinstance(memory, dict) else 0
    mem_free_kb = int(memory.get('mem_free_kb') or 0) if isinstance(memory, dict) else 0
    mem_avail_kb = int(memory.get('mem_available_kb') or 0) if isinstance(memory, dict) else 0
    used_kb = max(0, mem_total_kb - (mem_avail_kb or mem_free_kb))
    cpu_percent = None
    if isinstance(load, dict):
        raw_percent = load.get('cpu_percent_estimate')
        if isinstance(raw_percent, (int, float)):
            cpu_percent = float(raw_percent)

    uptime_sec = _uptime_seconds()
    cpu_temp = _cpu_temp_c()

    identity = {
        'deviceUuid': dev.get('device_uuid') or '',
        'machineId': dev.get('machine_id') or fp.get('machine_id') or '',
        'piSerial': dev.get('pi_serial') or ((fp.get('cpu') or {}).get('serial') if isinstance(fp.get('cpu'), dict) else ''),
        'hostname': host,
        'ipAddress': ip,
        'os': fp.get('os') if isinstance(fp.get('os'), dict) else {},
        'kernel': fp.get('kernel') or '',
        'cpuModel': ((fp.get('cpu') or {}).get('model') if isinstance(fp.get('cpu'), dict) else '') or '',
        'fingerprint': fp,
        'displayCount': int(((display_info.get('display_summary') or {}).get('total') if isinstance(display_info, dict) and isinstance(display_info.get('display_summary'), dict) else 0) or 0),
    }

    network = {
        'network_info': net_info if isinstance(net_info, dict) else {},
        'wifi': wifi_status if isinstance(wifi_status, dict) else {},
        'bluetooth': bt_status if isinstance(bt_status, dict) else {},
        'ap': {
            'active': bool((ap_status or {}).get('active')) if isinstance(ap_status, dict) else False,
            'status': ap_status if isinstance(ap_status, dict) else {},
            'clients': ap_clients.get('clients') if isinstance(ap_clients, dict) and isinstance(ap_clients.get('clients'), list) else [],
            'clients_count': len(ap_clients.get('clients') or []) if isinstance(ap_clients, dict) and isinstance(ap_clients.get('clients'), list) else 0,
        },
        'tailscale': _tailscale_runtime_status(net_info if isinstance(net_info, dict) else {}),
    }
    local_lan_ip, public_ip, tailscale_ip, primary_mac = _network_identity(network, ip)
    network['primaryMacAddress'] = primary_mac
    network['macAddress'] = primary_mac
    network['localLanIp'] = local_lan_ip
    network['publicIp'] = public_ip
    network['tailscaleIp'] = tailscale_ip
    identity['primaryMacAddress'] = primary_mac

    storage = _storage_snapshot(storage_info if isinstance(storage_info, dict) else {})
    software = _software_snapshot(
        update_info if isinstance(update_info, dict) else {},
        player_update_info if isinstance(player_update_info, dict) else {},
        net_info if isinstance(net_info, dict) else {},
        storage_info if isinstance(storage_info, dict) else {},
    )
    stream_storage_target = '/mnt/deviceportal/media'
    if isinstance(storage_info, dict):
        internal = storage_info.get('internal')
        if isinstance(internal, dict) and bool(internal.get('mounted')) and bool(internal.get('allow_media_storage', False)):
            stream_storage_target = str(internal.get('mount_path') or stream_storage_target)
        else:
            known = storage_info.get('known')
            if isinstance(known, list):
                for item in known:
                    if not isinstance(item, dict):
                        continue
                    if not bool(item.get('mounted')) or not bool(item.get('allow_media_storage', False)):
                        continue
                    mount_path = str(item.get('current_mount_path') or item.get('mount_path') or '').strip()
                    if mount_path:
                        stream_storage_target = mount_path
                        break

    stream = {
        'selected_stream_slug': str(cfg.get('selected_stream_slug') or '').strip(),
        'selected_stream_name': str(cfg.get('selected_stream_name') or '').strip(),
        'selected_stream_updated_at': str(cfg.get('selected_stream_updated_at') or '').strip(),
        'stream_manifest_version': str(cfg.get('stream_manifest_version') or '').strip(),
        'stream_manifest_sha256': str(cfg.get('stream_manifest_sha256') or '').strip(),
        'stream_last_sync_at': str(cfg.get('stream_last_sync_at') or '').strip(),
        'stream_asset_count': int(cfg.get('stream_asset_count') or 0),
        'storage_target': stream_storage_target,
    }

    player_service_name = str(cfg.get('player_service_name') or 'joormann-media-jarvis-displayplayer.service').strip() or 'joormann-media-jarvis-displayplayer.service'
    player_status = _safe_call({}, lambda: player_service_action('status', player_service_name))
    stream_player = {
        'service_name': player_service_name,
        'service_user': str(cfg.get('player_service_user') or '').strip(),
        'repo_url': str(cfg.get('player_repo_link') or cfg.get('player_repo_dir') or '').strip(),
        'repo_path': player_repo_path,
        'version': str((player_update_info or {}).get('local_version') or '').strip(),
        'commit': str((player_update_info or {}).get('local_commit') or '').strip(),
        'update': player_update_info if isinstance(player_update_info, dict) else {},
        'status': 'active' if bool((player_status or {}).get('active')) else ('inactive' if isinstance(player_status, dict) and player_status != {} else 'unknown'),
        'active': bool((player_status or {}).get('active')) if isinstance(player_status, dict) else False,
        'substate': str((player_status or {}).get('substate') or '').strip() if isinstance(player_status, dict) else '',
        'last_message': str((player_status or {}).get('message') or '').strip() if isinstance(player_status, dict) else '',
    }

    return {
        'panelBaseUrl': _safe_base_url(cfg.get('admin_base_url', '')),
        'identity': identity,
        'health': {
            'cpu': {
                'load_1m': load.get('load_1m') if isinstance(load, dict) else None,
                'load_5m': load.get('load_5m') if isinstance(load, dict) else None,
                'load_15m': load.get('load_15m') if isinstance(load, dict) else None,
                'cpu_cores': load.get('cpu_cores') if isinstance(load, dict) else None,
                'cpu_percent_estimate': cpu_percent,
                'temperature_c': cpu_temp,
            },
            'memory': memory if isinstance(memory, dict) else {},
            'memory_usage': {
                'used_kb': used_kb if mem_total_kb > 0 else None,
                'total_kb': mem_total_kb if mem_total_kb > 0 else None,
                'used_mb': round((used_kb / 1024.0), 1) if mem_total_kb > 0 else None,
                'total_mb': round((mem_total_kb / 1024.0), 1) if mem_total_kb > 0 else None,
                'used_percent': round((used_kb / mem_total_kb) * 100.0, 1) if mem_total_kb > 0 else None,
            },
            'load': load if isinstance(load, dict) else {},
            'uptime_seconds': uptime_sec,
            'uptime_human': _uptime_human(uptime_sec),
            'observedAt': utc_now(),
        },
        'network': network,
        'storage': storage,
        'display': display_info if isinstance(display_info, dict) else {},
        'displays': (display_info.get('displays') if isinstance(display_info, dict) and isinstance(display_info.get('displays'), list) else []),
        'primaryDisplay': (display_info.get('primary_display') if isinstance(display_info, dict) and isinstance(display_info.get('primary_display'), dict) else {}),
        'displaySummary': (display_info.get('display_summary') if isinstance(display_info, dict) and isinstance(display_info.get('display_summary'), dict) else {}),
        'software': software,
        'stream': stream,
        'stream_player': stream_player,
        'portal': {
            'update': update_info if isinstance(update_info, dict) else {},
            'playerUpdate': player_update_info if isinstance(player_update_info, dict) else {},
            'linkState': cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {},
            'registerPath': cfg.get('panel_register_path') or '',
            'pingPath': cfg.get('panel_ping_path') or '',
        },
    }


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'ok', 'valid', 'linked', 'success')
    return False


def _extract_link_ids(resp: dict | None) -> tuple[list[int], list[int]]:
    if not isinstance(resp, dict):
        return [], []
    candidates: list[dict] = [resp]
    data = resp.get('data')
    if isinstance(data, dict):
        candidates.append(data)

    user_ids: set[int] = set()
    customer_ids: set[int] = set()

    def _add_int(value: object, bucket: set[int]) -> None:
        try:
            iv = int(value)
        except Exception:
            return
        if iv > 0:
            bucket.add(iv)

    for source in candidates:
        _add_int(source.get('linkedUserId'), user_ids)
        _add_int(source.get('linked_user_id'), user_ids)
        _add_int(source.get('linkedCustomerId'), customer_ids)
        _add_int(source.get('linked_customer_id'), customer_ids)

        users = source.get('linkedUsers')
        if isinstance(users, list):
            for row in users:
                if isinstance(row, dict):
                    _add_int(row.get('id'), user_ids)
                else:
                    _add_int(row, user_ids)
        customers = source.get('linkedCustomers')
        if isinstance(customers, list):
            for row in customers:
                if isinstance(row, dict):
                    _add_int(row.get('id'), customer_ids)
                else:
                    _add_int(row, customer_ids)

    return sorted(user_ids), sorted(customer_ids)


def _extract_link_rows(resp: dict | None) -> tuple[list[object], list[object]]:
    if not isinstance(resp, dict):
        return [], []

    candidates: list[dict] = [resp]
    data = resp.get('data')
    if isinstance(data, dict):
        candidates.append(data)

    user_rows: list[object] = []
    customer_rows: list[object] = []

    for source in candidates:
        users = source.get('linkedUsers') or source.get('linked_users')
        if isinstance(users, list):
            user_rows = users
        customers = source.get('linkedCustomers') or source.get('linked_customers')
        if isinstance(customers, list):
            customer_rows = customers

    return user_rows, customer_rows


def _normalize_link_rows(items: list[object], row_type: str) -> list[dict]:
    rows_by_id: dict[int, dict] = {}
    for item in items:
        if isinstance(item, dict):
            try:
                row_id = int(item.get('id') or item.get('user_id') or item.get('customer_id') or 0)
            except Exception:
                row_id = 0
            if row_id <= 0:
                continue
            current = rows_by_id.get(row_id, {'id': row_id})
            if row_type == 'user':
                for key in ('username', 'email', 'displayName', 'display_name', 'avatar', 'avatarUrl', 'avatar_url', 'userDir', 'user_dir'):
                    value = item.get(key)
                    if value not in (None, ''):
                        current[key] = value
            rows_by_id[row_id] = current
            continue
        try:
            row_id = int(item)
        except Exception:
            row_id = 0
        if row_id > 0:
            rows_by_id.setdefault(row_id, {'id': row_id})

    return [rows_by_id[k] for k in sorted(rows_by_id.keys())]


def _persist_link_targets(cfg: dict, user_items: list[object], customer_items: list[object]) -> None:
    cfg['panel_linked_users'] = _normalize_link_rows(user_items, 'user')
    cfg['panel_linked_customers'] = _normalize_link_rows(customer_items, 'customer')
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)


def _panel_handshake_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/handshake"


def _extract_handshake_register_path(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ''
    direct = str(payload.get('registerPath') or payload.get('register_path') or '').strip()
    if direct:
        return direct
    data = payload.get('data')
    if isinstance(data, dict):
        nested = str(data.get('registerPath') or data.get('register_path') or '').strip()
        if nested:
            return nested
    return ''


def _handshake_schema_ok(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if _extract_handshake_register_path(payload):
        return True
    if any(key in payload for key in ('ok', 'success', 'message', 'data', 'version')):
        return True
    data = payload.get('data')
    if isinstance(data, dict) and any(key in data for key in ('ok', 'success', 'message', 'version', 'registerPath', 'register_path')):
        return True
    return False


def _probe_panel_register_route(base_url: str, register_path: str) -> tuple[int | None, str]:
    path = (register_path or '/api/device/link/register').strip()
    if not path.startswith('/'):
        path = f'/{path}'
    probe_url = f"{base_url}{path}"
    code, _, err = http_get_text(probe_url, timeout=6)
    return code, err


def _panel_verify_token_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/verify-token"


def _panel_jarvis_verify_url(base_url: str, cfg: dict | None = None) -> str:
    if isinstance(cfg, dict):
        path = str(cfg.get('panel_jarvis_verify_path') or '/api/jarvis/node/verify-token').strip() or '/api/jarvis/node/verify-token'
        if not path.startswith('/'):
            path = f'/{path}'
        return f"{base_url}{path}"
    return f"{base_url}/api/jarvis/node/verify-token"


def _panel_jarvis_register_url(base_url: str, cfg: dict | None = None) -> str:
    if isinstance(cfg, dict):
        path = str(cfg.get('panel_jarvis_register_path') or '/api/jarvis/node/register').strip() or '/api/jarvis/node/register'
        if not path.startswith('/'):
            path = f'/{path}'
        return f"{base_url}{path}"
    return f"{base_url}/api/jarvis/node/register"


def _registration_target_from_node_type(node_type: str) -> str:
    nt = str(node_type or '').strip().lower()
    if nt in ('server', 'workstation', 'hardware_node', 'hardware'):
        return 'hardware'
    if nt in ('jarvis_node', 'jarvis'):
        return 'jarvis'
    if nt in ('smarthome_node', 'smarthome'):
        return 'smarthome'
    return 'smarthome'


def _detect_token_target(base_url: str, token: str, dev: dict, cfg: dict, preferred: str = '') -> dict:
    payload = {
        'registerToken': token,
        'registrationToken': token,
        'register_token': token,
        'token': token,
        'deviceUuid': dev.get('device_uuid') or '',
        'device_uuid': dev.get('device_uuid') or '',
    }

    candidates: list[tuple[str, str]] = []
    preferred = preferred.strip().lower()
    if preferred in ('smarthome', 'jarvis', 'hardware'):
        candidates.append((preferred, 'preferred'))

    for target in ('smarthome', 'jarvis', 'hardware'):
        if target == preferred:
            continue
        candidates.append((target, 'fallback'))

    result = {
        'target': '',
        'verify_url': '',
        'http': None,
        'response': None,
        'skipped': False,
        'skipped_reason': '',
    }

    for target, _label in candidates:
        if target == 'smarthome':
            verify_url = _panel_verify_token_url(base_url)
        elif target == 'jarvis':
            verify_url = _panel_jarvis_verify_url(base_url, cfg)
        else:
            configured_verify_path = str(cfg.get('panel_hardware_verify_path') or '/api/hardware-device/verify-token').strip()
            verify_url = configured_verify_path if configured_verify_path.startswith('http') else f"{base_url}{configured_verify_path if configured_verify_path.startswith('/') else f'/{configured_verify_path}'}"
        code, resp, err = http_post_json(verify_url, payload, timeout=8)
        result.update({'verify_url': verify_url, 'http': code, 'response': resp})
        if code is None:
            continue
        if code in (404, 405):
            result['skipped'] = True
            result['skipped_reason'] = 'verify_endpoint_missing'
            continue
        valid = _response_indicates_success(code, resp) or (bool((resp or {}).get('valid')) if isinstance(resp, dict) else False)
        if valid:
            result['target'] = target
            return result

    return result

def _panel_assign_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/assign"


def _panel_sync_status_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/sync-status"


def _panel_auth_context_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/auth-context"


def _panel_bootstrap_pull_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/bootstrap-keys/pull"


def _panel_bootstrap_ack_url(base_url: str) -> str:
    return f"{base_url}/api/device/link/bootstrap-keys/ack"


def _extract_api_key_bootstrap(resp: dict | None) -> dict:
    if not isinstance(resp, dict):
        return {'mode': 'none', 'items': []}

    direct = resp.get('apiKeyBootstrap')
    if isinstance(direct, dict):
        return direct

    data = resp.get('data')
    if isinstance(data, dict):
        nested = data.get('apiKeyBootstrap')
        if isinstance(nested, dict):
            return nested

    return {'mode': 'none', 'items': []}


def _apply_api_key_bootstrap(cfg: dict, bootstrap: dict | None) -> dict:
    result = {
        'updated': False,
        'mode': 'none',
        'ids': [],
        'activated_directions': [],
    }
    if not isinstance(bootstrap, dict):
        return result

    mode = str(bootstrap.get('mode') or 'none').strip().lower() or 'none'
    items = bootstrap.get('items') if isinstance(bootstrap.get('items'), list) else []
    if mode == 'none' or not items:
        return result

    keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
    if not isinstance(keys, dict):
        keys = {}
    activated_directions: list[str] = []
    ids: list[int] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        direction = str(item.get('direction') or '').strip().lower()
        api_key = str(item.get('api_key') or '').strip()
        bootstrap_id = item.get('id')
        if isinstance(bootstrap_id, int) and bootstrap_id > 0:
            ids.append(bootstrap_id)
        elif isinstance(bootstrap_id, str) and bootstrap_id.isdigit():
            ids.append(int(bootstrap_id))
        if direction in ('raspi_to_admin', 'admin_to_raspi') and api_key:
            keys[direction] = api_key
            if direction not in activated_directions:
                activated_directions.append(direction)

    if not activated_directions:
        return result

    keys['updated_at'] = utc_now()
    cfg['panel_api_keys'] = keys
    state = cfg.get('panel_api_key_bootstrap') if isinstance(cfg.get('panel_api_key_bootstrap'), dict) else {}
    state.update({
        'mode': mode,
        'status': 'exchanged',
        'last_pull_at': utc_now(),
        'last_error': '',
    })
    cfg['panel_api_key_bootstrap'] = state
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    result['updated'] = True
    result['mode'] = mode
    result['ids'] = sorted(set(ids))
    result['activated_directions'] = activated_directions
    return result


def _ack_api_key_bootstrap(cfg: dict, dev: dict, ids: list[int], activated_directions: list[str]) -> tuple[bool, str]:
    base = _safe_base_url(cfg.get('admin_base_url', ''))
    if not base:
        return False, 'admin_base_url missing'
    if not ids:
        return True, ''

    payload = {
        'deviceUuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
        'bootstrapIds': ids,
        'activatedDirections': activated_directions,
    }
    keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
    portal_key = str((keys.get('raspi_to_admin') if isinstance(keys, dict) else '') or '').strip()
    if portal_key:
        payload['apiKey'] = portal_key

    code, resp, err = http_post_json(_panel_bootstrap_ack_url(base), payload, timeout=8)
    if code is None:
        return False, str(err or 'ack failed')
    if not (200 <= code < 300):
        return False, _extract_response_message(resp if isinstance(resp, dict) else None) or f'http {code}'

    st = cfg.get('panel_api_key_bootstrap') if isinstance(cfg.get('panel_api_key_bootstrap'), dict) else {}
    st.update({
        'status': 'active',
        'last_ack_at': utc_now(),
        'last_error': '',
    })
    cfg['panel_api_key_bootstrap'] = st
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)
    return True, ''


def _pull_and_apply_bootstrap_keys(cfg: dict, dev: dict) -> dict:
    node_type = _normalize_node_type(cfg.get('node_runtime_type') or 'raspi_node')
    if node_type in ('server', 'workstation'):
        # Hardware nodes use hardware-device auth/import flow and do not expose
        # Raspi bootstrap key pull endpoints.
        return {'ok': True, 'updated': False, 'skipped': True, 'reason': 'hardware_node'}

    base = _safe_base_url(cfg.get('admin_base_url', ''))
    if not base:
        return {'ok': False, 'error': 'admin_base_url missing'}

    payload = {
        'deviceUuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
    }
    keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
    portal_key = str((keys.get('raspi_to_admin') if isinstance(keys, dict) else '') or '').strip()
    if portal_key:
        payload['apiKey'] = portal_key

    code, resp, err = http_post_json(_panel_bootstrap_pull_url(base), payload, timeout=8)
    if code is None:
        return {'ok': False, 'error': str(err or 'pull failed')}
    if not (200 <= code < 300):
        return {'ok': False, 'error': _extract_response_message(resp if isinstance(resp, dict) else None) or f'http {code}'}

    bootstrap = _extract_api_key_bootstrap(resp if isinstance(resp, dict) else None)
    applied = _apply_api_key_bootstrap(cfg, bootstrap)
    if not applied.get('updated'):
        return {'ok': True, 'updated': False}

    ack_ok, ack_error = _ack_api_key_bootstrap(cfg, dev, applied.get('ids') or [], applied.get('activated_directions') or [])
    return {
        'ok': ack_ok,
        'updated': True,
        'mode': applied.get('mode') or 'none',
        'error': ack_error,
    }


def _refresh_link_targets_from_admin_context(cfg: dict, dev: dict) -> None:
    base = _safe_base_url(cfg.get('admin_base_url', ''))
    device_uuid = str(dev.get('device_uuid') or '').strip()
    auth_key = str(dev.get('auth_key') or '').strip()
    if not base or not device_uuid or not auth_key:
        return

    code, resp, _err = http_post_json(
        _panel_auth_context_url(base),
        {'deviceUuid': device_uuid, 'authKey': auth_key},
        timeout=6,
    )
    if code is None or code < 200 or code >= 300 or not isinstance(resp, dict):
        return

    should_write = False
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    if not bool(st.get('linked')) or int(st.get('last_http') or 0) != int(code) or str(st.get('last_error') or '').strip():
        st['linked'] = True
        st['last_http'] = int(code)
        st['last_check'] = utc_now()
        st['last_error'] = ''
        cfg['panel_link_state'] = st
        should_write = True

    linked_user_ids, linked_customer_ids = _extract_link_ids(resp)
    linked_users_rows: list[object] = []
    linked_customers_rows: list[object] = []
    candidates: list[dict] = [resp]
    data = resp.get('data')
    if isinstance(data, dict):
        candidates.append(data)
    for source in candidates:
        users = source.get('linkedUsers') or source.get('linked_users')
        if isinstance(users, list):
            linked_users_rows = users
        customers = source.get('linkedCustomers') or source.get('linked_customers')
        if isinstance(customers, list):
            linked_customers_rows = customers

    if linked_users_rows or linked_customers_rows:
        _persist_link_targets(cfg, linked_users_rows or linked_user_ids, linked_customers_rows or linked_customer_ids)
        should_write = True
    elif linked_user_ids or linked_customer_ids:
        _persist_link_targets(cfg, linked_user_ids, linked_customer_ids)
        should_write = True

    if should_write:
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)


def _response_indicates_success(code: int | None, resp: dict | None) -> bool:
    if code is None:
        return False
    if code < 200 or code >= 300:
        return False
    if not isinstance(resp, dict):
        return True
    for key in ('ok', 'success', 'valid', 'linked', 'registered', 'assigned'):
        if key in resp:
            return _is_truthy(resp.get(key))
    data = resp.get('data')
    if isinstance(data, dict):
        for key in ('ok', 'success', 'valid', 'linked', 'registered', 'assigned'):
            if key in data:
                return _is_truthy(data.get(key))
        if data.get('deviceId') or data.get('deviceSlug'):
            return True
    # If backend returned non-JSON fallback payload, do not treat it as success.
    if isinstance(resp, dict) and isinstance(resp.get('raw'), str):
        return False
    if resp.get('deviceId') or resp.get('deviceSlug'):
        return True
    return True


def _extract_response_message(resp: dict | None) -> str:
    if not isinstance(resp, dict):
        return ''
    candidates: list[object] = [
        resp.get('message'),
        resp.get('error'),
        resp.get('detail'),
        resp.get('error_code'),
        resp.get('raw'),
        (resp.get('data') or {}).get('message') if isinstance(resp.get('data'), dict) else '',
        (resp.get('data') or {}).get('detail') if isinstance(resp.get('data'), dict) else '',
        (resp.get('data') or {}).get('error') if isinstance(resp.get('data'), dict) else '',
        (resp.get('data') or {}).get('error_code') if isinstance(resp.get('data'), dict) else '',
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw = resp.get('raw') if isinstance(resp, dict) else ''
    if isinstance(raw, str) and raw.strip():
        lowered = raw.lower()
        marker = 'no route found for'
        if marker in lowered:
            idx = lowered.find(marker)
            excerpt = raw[idx:idx + 220].replace('\n', ' ').replace('\r', ' ')
            return excerpt.strip()
        return raw[:220].replace('\n', ' ').replace('\r', ' ').strip()
    return ''


def _extract_items(resp: dict | None) -> list[dict]:
    if not isinstance(resp, dict):
        return []
    direct_candidates = (
        resp.get('items'),
        resp.get('results'),
        resp.get('users'),
        resp.get('customers'),
    )
    for candidate in direct_candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    data = resp.get('data')
    if isinstance(data, dict):
        for key in ('items', 'results', 'users', 'customers'):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
    return []


def _extract_panel_device_flags(resp: dict | None) -> dict | None:
    if not isinstance(resp, dict):
        return None

    def _coerce_bool(value: object) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ('1', 'true', 'yes', 'on', 'active', 'locked'):
                return True
            if lowered in ('0', 'false', 'no', 'off', 'inactive', 'unlocked'):
                return False
        return None

    sources: list[dict] = [resp]
    data = resp.get('data')
    if isinstance(data, dict):
        sources.append(data)
        data_device = data.get('device')
        if isinstance(data_device, dict):
            sources.append(data_device)
    root_device = resp.get('device')
    if isinstance(root_device, dict):
        sources.append(root_device)

    for source in sources:
        if not isinstance(source, dict):
            continue
        is_active = _coerce_bool(source.get('isActive', source.get('is_active')))
        is_locked = _coerce_bool(source.get('isLocked', source.get('is_locked')))
        if is_active is None and is_locked is None:
            continue
        return {
            'is_active': is_active,
            'is_locked': is_locked,
            'updated_at': utc_now(),
        }

    return None


def _extract_panel_node_roles(resp: dict | None) -> list[str]:
    if not isinstance(resp, dict):
        return []

    sources: list[dict] = [resp]
    data = resp.get('data')
    if isinstance(data, dict):
        sources.append(data)
        data_device = data.get('device')
        if isinstance(data_device, dict):
            sources.append(data_device)
    root_device = resp.get('device')
    if isinstance(root_device, dict):
        sources.append(root_device)

    roles: list[str] = []

    def _add_role(value: object) -> None:
        if not isinstance(value, str):
            return
        raw = value.strip().lower()
        if not raw:
            return
        aliases = {
            'raspi_node': 'raspi',
            'raspi-node': 'raspi',
            'raspberrypi': 'raspi',
            'raspi': 'raspi',
            'hardware_node': 'hardware',
            'hardware-node': 'hardware',
            'hardware': 'hardware',
            'jarvis_node': 'jarvis',
            'jarvis-node': 'jarvis',
            'jarvis': 'jarvis',
            'smarthome_node': 'smarthome',
            'smarthome-node': 'smarthome',
            'smarthome': 'smarthome',
        }
        mapped = aliases.get(raw, raw)
        if mapped not in ('raspi', 'hardware', 'jarvis', 'smarthome'):
            return
        if mapped not in roles:
            roles.append(mapped)

    def _add_bool(flag: object, role: str) -> None:
        if isinstance(flag, bool):
            if flag and role not in roles:
                roles.append(role)
            return
        if isinstance(flag, (int, float)):
            if flag != 0 and role not in roles:
                roles.append(role)
            return
        if isinstance(flag, str):
            lowered = flag.strip().lower()
            if lowered in ('1', 'true', 'yes', 'on', 'active'):
                if role not in roles:
                    roles.append(role)

    for source in sources:
        if not isinstance(source, dict):
            continue
        role_value = source.get('node_role') or source.get('nodeRole') or source.get('role')
        _add_role(role_value)
        for key in ('node_roles', 'nodeRoles', 'roles'):
            raw_roles = source.get(key)
            if isinstance(raw_roles, list):
                for item in raw_roles:
                    _add_role(item)
        _add_bool(source.get('is_raspi_node', source.get('isRaspiNode')), 'raspi')
        _add_bool(source.get('is_hardware_node', source.get('isHardwareNode')), 'hardware')
        _add_bool(source.get('is_jarvis_node', source.get('isJarvisNode')), 'jarvis')
        _add_bool(source.get('is_smarthome_node', source.get('isSmarthomeNode')), 'smarthome')

    return roles


def _normalize_node_roles(roles: object) -> list[str]:
    out: list[str] = []
    if not isinstance(roles, list):
        return out
    for role_raw in roles:
        role = str(role_raw or '').strip().lower()
        if role in ('raspi', 'hardware', 'jarvis', 'smarthome') and role not in out:
            out.append(role)
    return out


def _node_roles_changed(previous_roles: object, current_roles: object) -> bool:
    prev = sorted(_normalize_node_roles(previous_roles))
    curr = sorted(_normalize_node_roles(current_roles))
    if curr == []:
        return False
    return prev != curr


def _trigger_full_resync_after_role_change(cfg: dict, dev: dict, fp: dict, host: str, ip: str) -> dict:
    base_url = _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return {'ok': False, 'reason': 'missing_base_url'}

    node_type = _normalize_node_type(cfg.get('node_runtime_type') or 'raspi_node')
    if node_type in ('server', 'workstation'):
        client_id = str(cfg.get('client_id') or '').strip()
        panel_keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
        api_key = str((panel_keys.get('raspi_to_admin') if isinstance(panel_keys, dict) else '') or '').strip()
        if not client_id or not api_key:
            return {'ok': False, 'reason': 'missing_hardware_credentials'}
        payload = _panel_hardware_register_payload(cfg, dev, fp, host, ip, str(cfg.get('registration_token') or ''), node_type=node_type)
        return _panel_hardware_auto_import(base_url, client_id, api_key, payload)

    register_url = _panel_url(cfg, 'panel_register_path')
    if not register_url or not (register_url.startswith('http://') or register_url.startswith('https://')):
        return {'ok': False, 'reason': 'invalid_register_url'}

    payload = _panel_sync_payload(cfg, dev, fp, host, ip)
    code, resp, err = http_post_json(register_url, payload, timeout=10)
    if code is None:
        return {'ok': False, 'reason': 'request_failed', 'detail': str(err)}
    return {
        'ok': _response_indicates_success(code, resp),
        'http': code,
        'response': resp,
        'detail': '' if _response_indicates_success(code, resp) else (_extract_response_message(resp if isinstance(resp, dict) else None) or f'http {code}'),
    }


def _extract_rotated_device_credentials(resp: dict | None) -> dict:
    if not isinstance(resp, dict):
        return {}
    sources: list[dict] = [resp]
    data = resp.get('data')
    if isinstance(data, dict):
        sources.append(data)
        nested_device = data.get('device')
        if isinstance(nested_device, dict):
            sources.append(nested_device)
    device_block = resp.get('device')
    if isinstance(device_block, dict):
        sources.append(device_block)
    credentials_block = resp.get('credentials')
    if isinstance(credentials_block, dict):
        sources.append(credentials_block)
    if isinstance(data, dict):
        nested_credentials = data.get('credentials')
        if isinstance(nested_credentials, dict):
            sources.append(nested_credentials)

    device_uuid = ''
    auth_key = ''
    for source in sources:
        if not device_uuid:
            device_uuid = str(
                source.get('deviceUuid')
                or source.get('device_uuid')
                or source.get('uuid')
                or ''
            ).strip()
        if not auth_key:
            auth_key = str(
                source.get('authKey')
                or source.get('auth_key')
                or source.get('deviceAuthKey')
                or source.get('device_auth_key')
                or ''
            ).strip()
        if device_uuid and auth_key:
            break
    out: dict = {}
    if device_uuid:
        out['device_uuid'] = device_uuid
    if auth_key:
        out['auth_key'] = auth_key
    return out


def _apply_rotated_device_credentials(dev: dict, resp: dict | None) -> bool:
    rotated = _extract_rotated_device_credentials(resp)
    if not rotated:
        return False
    changed = False
    new_uuid = str(rotated.get('device_uuid') or '').strip()
    new_auth = str(rotated.get('auth_key') or '').strip()
    if new_uuid and new_uuid != str(dev.get('device_uuid') or '').strip():
        dev['device_uuid'] = new_uuid
        changed = True
    if new_auth and new_auth != str(dev.get('auth_key') or '').strip():
        dev['auth_key'] = new_auth
        changed = True
    if changed:
        write_json(DEVICE_PATH, dev, mode=0o600)
    return changed


def _portal_auth_valid(data: dict, dev: dict, cfg: dict | None = None) -> tuple[bool, str]:
    uuid_in = str(data.get('deviceUuid') or data.get('device_uuid') or '').strip()
    auth_in = str(data.get('authKey') or data.get('auth_key') or '').strip()
    inbound_api_key = str(data.get('apiKey') or data.get('api_key') or data.get('adminApiKey') or '').strip()
    uuid_ref = str(dev.get('device_uuid') or '').strip()
    auth_ref = str(dev.get('auth_key') or '').strip()

    if not uuid_in:
        return False, 'device_auth_missing'
    if not uuid_ref or not auth_ref:
        return False, 'device_auth_unavailable'
    if not hmac.compare_digest(uuid_in, uuid_ref):
        return False, 'device_uuid_invalid'
    if auth_in and hmac.compare_digest(auth_in, auth_ref):
        return True, ''

    keys = cfg.get('panel_api_keys') if isinstance(cfg, dict) and isinstance(cfg.get('panel_api_keys'), dict) else {}
    admin_api_key = str((keys.get('admin_to_raspi') if isinstance(keys, dict) else '') or '').strip()
    if admin_api_key and inbound_api_key and hmac.compare_digest(inbound_api_key, admin_api_key):
        return True, ''

    if not auth_in and not inbound_api_key:
        return False, 'device_auth_missing'
    if auth_in and not hmac.compare_digest(auth_in, auth_ref):
        return False, 'auth_key_invalid'

    return False, 'auth_key_invalid'


def _normalize_search_items(items: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for item in items:
        ident = str(item.get('id') or item.get('uuid') or item.get('slug') or '').strip()
        if not ident:
            continue
        name = str(item.get('name') or item.get('fullName') or item.get('displayName') or '').strip()
        if not name:
            first = str(item.get('firstName') or item.get('first_name') or '').strip()
            last = str(item.get('lastName') or item.get('last_name') or '').strip()
            name = " ".join(part for part in (first, last) if part).strip() or ident
        subtitle = str(item.get('email') or item.get('company') or item.get('slug') or '').strip()
        normalized.append(
            {
                'id': ident,
                'name': name,
                'subtitle': subtitle,
                'raw': item,
            }
        )
    return normalized


@bp_panel.post('/api/panel/test-url')
def api_panel_test_url():
    data = request.get_json(force=True, silent=True) or {}
    raw = (data.get('url') or '').strip()
    if not raw:
        return jsonify(ok=False, error='Missing url'), 400

    base = _safe_base_url(raw)
    code, _, err = http_get_text(base, timeout=5)
    if code is None:
        return jsonify(ok=False, error=f'URL unreachable: {err}'), 400
    handshake_url = _panel_handshake_url(base)
    hs_code, hs_resp, hs_err = http_get_json(handshake_url, timeout=6)
    if hs_code is None:
        return jsonify(
            ok=False,
            error='panel_handshake_unreachable',
            detail=str(hs_err),
            base_url=base,
            handshake_url=handshake_url,
            base_http=code,
        ), 400
    schema_ok = isinstance(hs_resp, dict) and any(
        key in hs_resp for key in ('ok', 'success', 'message', 'data', 'version')
    )
    if hs_code >= 400 or not schema_ok:
        return jsonify(
            ok=False,
            error='panel_handshake_invalid',
            detail='Panel link handshake endpoint missing or unexpected response',
            base_url=base,
            base_http=code,
            handshake_url=handshake_url,
            handshake_http=hs_code,
            handshake_response=hs_resp,
        ), 400

    cfg = ensure_config()
    cfg['admin_base_url'] = base
    if isinstance(hs_resp, dict):
        register_path = str(hs_resp.get('registerPath') or '').strip()
        if register_path:
            cfg['panel_register_path'] = register_path
    cfg['updated_at'] = utc_now()
    ok_write, write_err = write_json(CONFIG_PATH, cfg, mode=0o600)
    if not ok_write:
        return jsonify(ok=False, error=f'Could not persist admin_base_url: {write_err}', base_url=base), 500

    dev = ensure_device()
    fp = ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()

    existing_device = {
        'checked': False,
        'found': False,
        'http': None,
        'code': '',
        'message': '',
        'device_slug': '',
    }
    ping_url = _panel_url(cfg, 'panel_ping_path')
    if ping_url and (ping_url.startswith('http://') or ping_url.startswith('https://')):
        ping_payload = _panel_ping_payload(dev, fp, host, ip, cfg)
        p_code, p_resp, _p_err = http_post_json(ping_url, ping_payload, timeout=8)
        existing_device['checked'] = True
        existing_device['http'] = p_code
        if p_code is not None and 200 <= p_code < 300:
            existing_device['found'] = True
            existing_device['code'] = 'device_found'
            existing_device['message'] = 'Gerät ist im Panel bereits bekannt.'
            if isinstance(p_resp, dict):
                p_data = p_resp.get('data') if isinstance(p_resp.get('data'), dict) else {}
                existing_device['device_slug'] = str(
                    p_resp.get('deviceSlug')
                    or p_data.get('deviceSlug')
                    or p_resp.get('slug')
                    or p_data.get('slug')
                ).strip()
        elif isinstance(p_resp, dict):
            err_code = str(p_resp.get('error_code') or p_resp.get('error') or '').strip().lower()
            if err_code in ('device_not_found', 'not_found'):
                existing_device['code'] = 'device_not_found'
                existing_device['message'] = 'Gerät ist im Panel noch nicht registriert.'
            else:
                existing_device['code'] = err_code or 'device_check_unknown'
                existing_device['message'] = str(p_resp.get('message') or p_resp.get('detail') or '').strip()

    update_state(cfg, dev, fp, mode='setup', message='panel URL updated')
    return jsonify(
        ok=True,
        base_url=base,
        http=code,
        handshake_url=handshake_url,
        handshake_http=hs_code,
        handshake_response=hs_resp,
        existing_device=existing_device,
    )


@bp_panel.post('/api/panel/ping')
def api_panel_ping():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()
    data = request.get_json(force=True, silent=True) or {}

    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    url = _panel_url(cfg, 'panel_ping_path')
    if not url:
        return jsonify(
            ok=False,
            error='admin_base_url missing',
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_ping_path=cfg.get('panel_ping_path'),
        ), 400
    if not (url.startswith('http://') or url.startswith('https://')):
        return jsonify(
            ok=False,
            error='invalid_panel_url',
            resolved_url=url,
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_ping_path=cfg.get('panel_ping_path'),
        ), 400

    node_type = str(cfg.get('node_runtime_type') or '').strip().lower()
    if node_type in ('server', 'workstation'):
        client_id = str(cfg.get('client_id') or '').strip()
        panel_keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
        api_key = str((panel_keys.get('raspi_to_admin') if isinstance(panel_keys, dict) else '') or '').strip()
        if client_id and api_key:
            headers = {'X-Client-Id': client_id, 'X-API-Key': api_key}
            code, resp, err = _http_get_json_with_headers(url, headers, timeout=8)
        else:
            code, resp, err = None, None, 'hardware_ping_credentials_missing'
    else:
        payload = _panel_ping_payload(dev, fp, host, ip, cfg)
        code, resp, err = http_post_json(url, payload, timeout=8)

    if code is None:
        st = _update_panel_link_state(
            cfg,
            linked=_sticky_linked(cfg, False, None, None),
            last_http=None,
            last_response=None,
            last_error=str(err),
        )
        fail_mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
        update_state(cfg, dev, fp, mode=fail_mode, message='panel ping failed', panel_state_overrides=st)
        return jsonify(ok=False, error=str(err), panel_link_state=st), 502

    linked = _sticky_linked(cfg, _response_indicates_success(code, resp), code, resp)
    flags = _extract_panel_device_flags(resp if isinstance(resp, dict) else None)
    if isinstance(flags, dict):
        cfg['panel_device_flags'] = flags
    node_roles = _extract_panel_node_roles(resp if isinstance(resp, dict) else None)
    if node_roles:
        cfg['panel_node_roles'] = node_roles
    panel_error = '' if linked else (_extract_response_message(resp if isinstance(resp, dict) else None) or f'http {code}')

    if isinstance(resp, dict):
        slug = (resp.get('deviceSlug') or resp.get('slug') or '').strip()
        if slug:
            cfg['device_slug'] = slug
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
        elif isinstance(flags, dict) or node_roles:
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)

    st = _update_panel_link_state(cfg, linked=linked, last_http=code, last_response=resp, last_error=panel_error)
    mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='panel ping', panel_state_overrides=st)
    bootstrap_refresh = _pull_and_apply_bootstrap_keys(cfg, dev) if linked else {'ok': False, 'updated': False}

    return jsonify(
        ok=(code == 200),
        panel_link_state=st,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        api_key_bootstrap=bootstrap_refresh,
        response=resp,
        http=code,
        resolved_url=url,
    )


@bp_panel.post('/api/panel/register')
def api_panel_register():
    cfg = ensure_config()
    dev = ensure_device()
    fp = collect_fingerprint()
    host = get_hostname()
    ip = get_ip()

    data = request.get_json(force=True, silent=True) or {}
    raw_node_type = str(data.get('node_type') or data.get('nodeType') or '').strip().lower()
    node_type_aliases = {
        'raspi': 'raspi_node',
        'raspberrypi': 'raspi_node',
        'raspi_node': 'raspi_node',
        'raspi-node': 'raspi_node',
        'server': 'server',
        'workstation': 'workstation',
    }
    node_type = node_type_aliases.get(raw_node_type, '')
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    node_type = _normalize_node_type(data.get('node_type') or data.get('nodeType') or cfg.get('node_runtime_type') or 'raspi_node')
    _persist_node_type_choice(cfg, node_type)

    token = (data.get('registration_token') or cfg.get('registration_token') or '').strip()
    if not token:
        return jsonify(ok=False, error='registration_token missing'), 400

    base_url = _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    registration_target = str(data.get('registration_target') or '').strip().lower()
    if not registration_target:
        registration_target = str(cfg.get('panel_register_target') or '').strip().lower()
    if not registration_target:
        preferred = _registration_target_from_node_type(node_type)
        detect = _detect_token_target(base_url, token, dev, cfg, preferred=preferred)
        if detect.get('target'):
            registration_target = detect.get('target')
            cfg['panel_register_target'] = registration_target
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
        else:
            registration_target = preferred

    if registration_target == 'hardware':
        return api_panel_register_hardware()
    if registration_target == 'jarvis':
        jarvis_url = _panel_jarvis_register_url(base_url, cfg)
        if not (jarvis_url.startswith('http://') or jarvis_url.startswith('https://')):
            return jsonify(ok=False, error='invalid_panel_url', resolved_url=jarvis_url), 400

        jarvis_node_type = 'workstation' if node_type in ('workstation', 'server') else 'raspberrypi'
        payload = _panel_jarvis_register_payload(cfg, dev, fp, host, ip, token, node_type=jarvis_node_type)
        code, resp, err = http_post_json(jarvis_url, payload, timeout=10)
        if code is None:
            st = _update_panel_link_state(
                cfg,
                linked=_sticky_linked(cfg, False, None, None),
                last_http=None,
                last_response=None,
                last_error=str(err),
            )
            update_state(cfg, dev, fp, mode='setup', message='jarvis register failed', panel_state_overrides=st)
            return jsonify(ok=False, error=str(err), panel_link_state=st), 502

        linked = _response_indicates_success(code, resp)
        panel_error = '' if linked else (_extract_response_message(resp if isinstance(resp, dict) else None) or f'http {code}')
        if linked:
            cfg['registration_token'] = token
            cfg['panel_register_target'] = 'jarvis'
            node_roles = _extract_panel_node_roles(resp if isinstance(resp, dict) else None)
            cfg['panel_node_roles'] = node_roles or ['jarvis']
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)

        st = _update_panel_link_state(cfg, linked=linked, last_http=code, last_response=resp, last_error=panel_error)
        update_state(cfg, dev, fp, mode='setup', message='jarvis register', panel_state_overrides=st)
        if linked:
            return jsonify(ok=True, panel_link_state=st, response=resp, http=code, resolved_url=jarvis_url), 200

        return jsonify(
            ok=False,
            error='register_failed',
            detail=panel_error or 'Registrierung fehlgeschlagen.',
            panel_link_state=st,
            response=resp,
            http=code,
            resolved_url=jarvis_url,
        ), 400

    url = _panel_url(cfg, 'panel_register_path')
    if not url:
        return jsonify(
            ok=False,
            error='admin_base_url missing',
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_register_path=cfg.get('panel_register_path'),
        ), 400
    if not (url.startswith('http://') or url.startswith('https://')):
        return jsonify(
            ok=False,
            error='invalid_panel_url',
            resolved_url=url,
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_register_path=cfg.get('panel_register_path'),
        ), 400

    payload = _panel_register_payload(cfg, dev, fp, host, ip, token)
    # Optional wizard context for panel-side direct assignment hooks.
    assignment = data.get('assignment') if isinstance(data.get('assignment'), dict) else {}
    if assignment:
        payload['assignment'] = assignment
    link_target_type = (data.get('link_target_type') or '').strip()
    link_target_id = str(data.get('link_target_id') or '').strip()
    if link_target_type in ('user', 'customer') and link_target_id:
        payload['linkTargetType'] = link_target_type
        payload['linkTargetId'] = link_target_id
        payload['link_target_type'] = link_target_type
        payload['link_target_id'] = link_target_id
    code, resp, err = http_post_json(url, payload, timeout=8)

    if code is None:
        st = _update_panel_link_state(
            cfg,
            linked=_sticky_linked(cfg, False, None, None),
            last_http=None,
            last_response=None,
            last_error=str(err),
        )
        fail_mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
        update_state(cfg, dev, fp, mode=fail_mode, message='register failed', panel_state_overrides=st)
        return jsonify(ok=False, error=str(err), panel_link_state=st), 502

    linked = _sticky_linked(cfg, _response_indicates_success(code, resp), code, resp)
    flags = _extract_panel_device_flags(resp if isinstance(resp, dict) else None)
    if isinstance(flags, dict):
        cfg['panel_device_flags'] = flags
    panel_error = '' if linked else (_extract_response_message(resp if isinstance(resp, dict) else None) or f'http {code}')

    if _response_indicates_success(code, resp):
        if isinstance(resp, dict):
            _apply_rotated_device_credentials(dev, resp)
        cfg['registration_token'] = token
        cfg['panel_register_target'] = registration_target or 'smarthome'
        if node_type:
            cfg['node_runtime_type'] = node_type
        node_roles = _extract_panel_node_roles(resp if isinstance(resp, dict) else None)
        if not node_roles:
            if registration_target == 'hardware':
                node_roles = ['hardware']
            elif registration_target == 'jarvis':
                node_roles = ['jarvis']
            else:
                node_roles = ['raspi', 'smarthome']
        cfg['panel_node_roles'] = node_roles
        linked_user_ids, linked_customer_ids = _extract_link_ids(resp if isinstance(resp, dict) else None)
        linked_user_rows, linked_customer_rows = _extract_link_rows(resp if isinstance(resp, dict) else None)
        if link_target_type == 'user' and link_target_id.isdigit():
            linked_user_ids = sorted(set(linked_user_ids + [int(link_target_id)]))
        if link_target_type == 'customer' and link_target_id.isdigit():
            linked_customer_ids = sorted(set(linked_customer_ids + [int(link_target_id)]))
        if linked_user_rows or linked_customer_rows:
            _persist_link_targets(
                cfg,
                linked_user_rows or linked_user_ids,
                linked_customer_rows or linked_customer_ids,
            )
        else:
            _persist_link_targets(cfg, linked_user_ids, linked_customer_ids)
        bootstrap = _extract_api_key_bootstrap(resp if isinstance(resp, dict) else None)
        applied = _apply_api_key_bootstrap(cfg, bootstrap)
        if applied.get('updated'):
            _ack_api_key_bootstrap(cfg, dev, applied.get('ids') or [], applied.get('activated_directions') or [])
    if isinstance(resp, dict):
        slug = (resp.get('deviceSlug') or resp.get('slug') or '').strip()
        if slug:
            cfg['device_slug'] = slug
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    st = _update_panel_link_state(cfg, linked=linked, last_http=code, last_response=resp, last_error=panel_error)
    mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='panel register', panel_state_overrides=st)

    if _response_indicates_success(code, resp):
        return jsonify(
            ok=True,
            panel_link_state=st,
            panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
            response=resp,
            http=code,
            resolved_url=url,
        ), 200

    panel_msg = _extract_response_message(resp) or panel_error or 'Registrierung fehlgeschlagen.'
    return jsonify(
        ok=False,
        error='register_failed',
        detail=panel_msg,
        panel_link_state=st,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        response=resp,
        http=code,
        resolved_url=url,
    ), 400


@bp_panel.post('/api/panel/register-hardware')
def api_panel_register_hardware():
    cfg = ensure_config()
    dev = ensure_device()
    fp = collect_fingerprint()
    host = get_hostname()
    ip = get_ip()

    data = request.get_json(force=True, silent=True) or {}
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    if request_base:
        cfg['admin_base_url'] = request_base

    token = (data.get('registration_token') or cfg.get('registration_token') or '').strip()
    base = _safe_base_url(cfg.get('admin_base_url', ''))
    preferred_target = str(data.get('registration_target') or '').strip().lower()
    if token and base and preferred_target and preferred_target != 'hardware':
        return api_panel_register()
    if token and base and not preferred_target:
        detect = _detect_token_target(base, token, dev, cfg, preferred='hardware')
        if detect.get('target') and detect.get('target') != 'hardware':
            return api_panel_register()

    node_type = _normalize_node_type(data.get('node_type') or data.get('nodeType') or cfg.get('node_runtime_type') or 'server')
    if node_type == 'raspi_node':
        node_type = 'server'
    _persist_node_type_choice(cfg, node_type)

    if not token:
        return jsonify(ok=False, error='registration_token missing'), 400

    if not base:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    configured_path = str(cfg.get('panel_hardware_register_path') or '/api/hardware-device/register').strip()
    candidates: list[str] = [configured_path, '/api/hardware-device/register', '/api/hardware/device/register']
    deduped_paths: list[str] = []
    for candidate in candidates:
        candidate = str(candidate or '').strip()
        if not candidate:
            continue
        if not candidate.startswith('http://') and not candidate.startswith('https://') and not candidate.startswith('/'):
            candidate = f'/{candidate}'
        if candidate not in deduped_paths:
            deduped_paths.append(candidate)

    payload = _panel_hardware_register_payload(cfg, dev, fp, host, ip, token, node_type=node_type)
    last_error = 'register failed'
    last_http = None
    last_resp = None
    resolved_url = ''

    for path in deduped_paths:
        url = path if path.startswith('http://') or path.startswith('https://') else f'{base}{path}'
        code, resp, err = http_post_json(url, payload, timeout=10)
        resolved_url = url
        last_http = code
        last_resp = resp
        if code is None:
            last_error = str(err or 'request failed')
            continue
        if 200 <= code < 300 and _response_indicates_success(code, resp):
            if isinstance(resp, dict):
                _apply_rotated_device_credentials(dev, resp)
            next_token = token
            cfg['node_runtime_type'] = node_type
            cfg['panel_register_target'] = 'hardware'
            cfg['panel_hardware_register_path'] = path
            flags = _extract_panel_device_flags(resp if isinstance(resp, dict) else None)
            if isinstance(flags, dict):
                cfg['panel_device_flags'] = flags
            node_roles = _extract_panel_node_roles(resp if isinstance(resp, dict) else None)
            cfg['panel_node_roles'] = node_roles or ['hardware']
            exchanged_key = ''
            resolved_client_id = ''
            if isinstance(resp, dict):
                api_key_exchange = resp.get('apiKeyExchange')
                if isinstance(api_key_exchange, dict):
                    exchanged_key = str(api_key_exchange.get('apiKey') or '').strip()
                    if exchanged_key:
                        keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
                        if not isinstance(keys, dict):
                            keys = {}
                        keys['raspi_to_admin'] = exchanged_key
                        keys['updated_at'] = utc_now()
                        cfg['panel_api_keys'] = keys
                device_payload = resp.get('device')
                if isinstance(device_payload, dict):
                    resolved_client_id = str(device_payload.get('clientId') or '').strip()
                    rotated_token = str(device_payload.get('registerToken') or '').strip()
                    if rotated_token:
                        next_token = rotated_token
            if isinstance(resp, dict):
                slug = str(
                    resp.get('slug')
                    or resp.get('deviceSlug')
                    or ((resp.get('data') or {}).get('slug') if isinstance(resp.get('data'), dict) else '')
                    or ((resp.get('data') or {}).get('deviceSlug') if isinstance(resp.get('data'), dict) else '')
                ).strip()
                if slug:
                    cfg['device_slug'] = slug
                if not next_token:
                    alt_token = str(
                        ((resp.get('data') or {}).get('registerToken') if isinstance(resp.get('data'), dict) else '')
                        or resp.get('registerToken')
                        or ''
                    ).strip()
                    if alt_token:
                        next_token = alt_token
            cfg['registration_token'] = next_token or token
            if resolved_client_id:
                cfg['client_id'] = resolved_client_id
            if not resolved_client_id:
                resolved_client_id = str(cfg.get('client_id') or '').strip()
            if not isinstance(cfg.get('panel_device_flags'), dict):
                cfg['panel_device_flags'] = {}
            if cfg['panel_device_flags'].get('is_active') is None:
                cfg['panel_device_flags']['is_active'] = True
            if cfg['panel_device_flags'].get('is_locked') is None:
                cfg['panel_device_flags']['is_locked'] = False
            cfg['panel_device_flags']['updated_at'] = utc_now()
            import_api_key = exchanged_key
            if not import_api_key:
                keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
                if isinstance(keys, dict):
                    import_api_key = str(keys.get('raspi_to_admin') or '').strip()
            auto_import = {'ok': False, 'error': 'auto_import_not_attempted'}
            if resolved_client_id and import_api_key:
                auto_import = _panel_hardware_auto_import(base, resolved_client_id, import_api_key, payload)
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
            st = _update_panel_link_state(cfg, linked=True, last_http=code, last_response=resp, last_error='')
            mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
            update_state(cfg, dev, fp, mode=mode, message='hardware register', panel_state_overrides=st)
            return jsonify(
                ok=True,
                node_type=node_type,
                panel_link_state=st,
                response=resp,
                http=code,
                resolved_url=url,
                auto_import=auto_import,
            ), 200
        last_error = _extract_response_message(resp if isinstance(resp, dict) else None) or (str(err or '') if code is None else f'http {code}')

    st = _update_panel_link_state(
        cfg,
        linked=_sticky_linked(cfg, False, last_http, last_resp),
        last_http=last_http,
        last_response=last_resp,
        last_error=last_error,
    )
    mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='hardware register failed', panel_state_overrides=st)
    return jsonify(
        ok=False,
        error='register_hardware_failed',
        detail=last_error,
        panel_link_state=st,
        response=last_resp,
        http=last_http,
        resolved_url=resolved_url,
    ), 400


@bp_panel.post('/api/panel/sync-status')
def api_panel_sync_status():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()

    data = request.get_json(force=True, silent=True) or {}
    panel_state = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    linked = bool(panel_state.get('linked'))
    has_auth_key = bool(str(dev.get('auth_key') or '').strip())
    has_device_uuid = bool(str(dev.get('device_uuid') or '').strip())
    has_base_url = bool(_safe_base_url(cfg.get('admin_base_url', '')))
    can_attempt_recovery = has_base_url and has_auth_key and has_device_uuid
    if not linked and not can_attempt_recovery:
        return jsonify(
            ok=False,
            error='device_not_linked',
            detail='Gerät ist noch nicht verknüpft. Bitte Setup-Assistent erneut ausführen.',
            hint='setup_required',
            panel_link_state=panel_state,
        ), 409

    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    base_url = request_base or _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    node_type = _normalize_node_type(cfg.get('node_runtime_type') or 'raspi_node')
    if node_type in ('server', 'workstation'):
        ping_path = str(cfg.get('panel_ping_path') or '/api/hardware-device/ping').strip()
        if not ping_path:
            ping_path = '/api/hardware-device/ping'
        if not ping_path.startswith('/'):
            ping_path = f'/{ping_path}'
        sync_url = f"{base_url}{ping_path}"

        client_id = str(cfg.get('client_id') or '').strip()
        panel_keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
        api_key = str((panel_keys.get('raspi_to_admin') if isinstance(panel_keys, dict) else '') or '').strip()
        if not client_id or not api_key:
            return jsonify(
                ok=False,
                error='device_auth_invalid',
                detail='Hardware-Credentials fehlen (client_id/api_key). Bitte erneut registrieren.',
                hint='relink_required',
                sync_url=sync_url,
                node_type=node_type,
            ), 400

        code, resp, err = _http_get_json_with_headers(sync_url, {'X-Client-Id': client_id, 'X-API-Key': api_key}, timeout=8)
        if code is None:
            return jsonify(ok=False, error='sync_status_failed', detail=str(err), sync_url=sync_url, node_type=node_type), 502

        if code < 200 or code >= 300:
            detail_msg = _extract_response_message(resp) or (resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'
            st = _update_panel_link_state(
                cfg,
                linked=_sticky_linked(cfg, False, code, resp),
                last_http=code,
                last_response=resp,
                last_error=detail_msg,
            )
            err_code = str((resp.get('error') if isinstance(resp, dict) else '') or '').strip().lower()
            if err_code in ('auth_invalid', 'invalid_auth', 'auth_failed'):
                return jsonify(
                    ok=False,
                    error='device_auth_invalid',
                    detail=detail_msg,
                    hint='relink_required',
                    http=code,
                    sync_url=sync_url,
                    response=resp,
                    panel_link_state=st,
                    node_type=node_type,
                ), 400
            return jsonify(
                ok=False,
                error='sync_status_failed',
                detail=detail_msg,
                http=code,
                sync_url=sync_url,
                response=resp,
                panel_link_state=st,
                node_type=node_type,
            ), 400

        st = _update_panel_link_state(
            cfg,
            linked=True,
            last_http=code,
            last_response=resp,
            last_error='',
        )
        previous_roles = cfg.get('panel_node_roles') if isinstance(cfg.get('panel_node_roles'), list) else []
        node_roles = _extract_panel_node_roles(resp if isinstance(resp, dict) else None)
        role_change_resync = {'ok': True, 'skipped': True}
        if node_roles:
            cfg['panel_node_roles'] = node_roles
            if _node_roles_changed(previous_roles, node_roles):
                role_change_resync = _trigger_full_resync_after_role_change(cfg, dev, fp, host, ip)
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
        if request_base:
            cfg['admin_base_url'] = request_base
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)

        return jsonify(
            ok=True,
            http=code,
            sync_url=sync_url,
            response=resp,
            panel_link_state=st,
            node_type=node_type,
            panel_node_roles=(cfg.get('panel_node_roles') if isinstance(cfg.get('panel_node_roles'), list) else []),
            role_change_resync=role_change_resync,
            api_key_bootstrap={'ok': True, 'updated': False, 'skipped': True, 'reason': 'hardware_node'},
        ), 200

    sync_url = _panel_sync_status_url(base_url)
    payload = {
        'deviceUuid': dev.get('device_uuid') or '',
        'device_uuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
        'auth_key': dev.get('auth_key') or '',
    }
    panel_keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
    portal_api_key = str((panel_keys.get('raspi_to_admin') if isinstance(panel_keys, dict) else '') or '').strip()
    if portal_api_key:
        payload['apiKey'] = portal_api_key
        payload['portalApiKey'] = portal_api_key

    code, resp, err = http_post_json(sync_url, payload, timeout=8)
    if code is None:
        return jsonify(ok=False, error='sync_status_failed', detail=str(err), sync_url=sync_url), 502
    if code < 200 or code >= 300:
        detail_msg = _extract_response_message(resp) or (resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'
        err_code = str((resp.get('error_code') if isinstance(resp, dict) else '') or (resp.get('error') if isinstance(resp, dict) else '')).strip().lower()
        st = _update_panel_link_state(
            cfg,
            linked=_sticky_linked(cfg, False, code, resp),
            last_http=code,
            last_response=resp,
            last_error=(resp.get('error') if isinstance(resp, dict) else '') or f'http {code}',
        )
        if err_code in ('device_not_found', 'not_found'):
            return jsonify(
                ok=False,
                error='device_not_registered',
                detail=detail_msg or 'Gerät im Panel nicht gefunden. Bitte Setup-Assistent (URL + Token) erneut ausführen.',
                hint='setup_required',
                http=code,
                sync_url=sync_url,
                response=resp,
                panel_link_state=st,
            ), 400
        if err_code in ('auth_invalid', 'invalid_auth'):
            return jsonify(
                ok=False,
                error='device_auth_invalid',
                detail=detail_msg or 'Device-Credentials im Panel nicht gültig.',
                hint='relink_required',
                http=code,
                sync_url=sync_url,
                response=resp,
                panel_link_state=st,
            ), 400
        return jsonify(
            ok=False,
            error='sync_status_failed',
            detail=detail_msg,
            http=code,
            sync_url=sync_url,
            response=resp,
            panel_link_state=st,
        ), 400

    ping_http = None
    ping_error = ''
    role_change_resync = {'ok': True, 'skipped': True}
    ping_url = _panel_url(cfg, 'panel_ping_path')
    if ping_url and (ping_url.startswith('http://') or ping_url.startswith('https://')):
        fp = ensure_fingerprint()
        host = get_hostname()
        ip = get_ip()
        ping_payload = _panel_ping_payload(dev, fp, host, ip, cfg)
        ping_http, ping_resp, ping_err = http_post_json(ping_url, ping_payload, timeout=8)
        if ping_http is not None and 200 <= ping_http < 300:
            flags = _extract_panel_device_flags(ping_resp if isinstance(ping_resp, dict) else None)
            if isinstance(flags, dict):
                cfg['panel_device_flags'] = flags
            node_roles = _extract_panel_node_roles(ping_resp if isinstance(ping_resp, dict) else None)
            if node_roles:
                cfg['panel_node_roles'] = node_roles
            if isinstance(flags, dict) or node_roles:
                cfg['updated_at'] = utc_now()
                write_json(CONFIG_PATH, cfg, mode=0o600)
        elif ping_http is None:
            ping_error = str(ping_err or '')

    _update_panel_link_state(
        cfg,
        linked=_sticky_linked(cfg, _response_indicates_success(code, resp), code, resp),
        last_http=code,
        last_response=resp,
        last_error='',
    )
    linked_user_ids, linked_customer_ids = _extract_link_ids(resp if isinstance(resp, dict) else None)
    if linked_user_ids or linked_customer_ids:
        _persist_link_targets(cfg, linked_user_ids, linked_customer_ids)

    node_roles = _extract_panel_node_roles(resp if isinstance(resp, dict) else None)
    if node_roles:
        previous_roles = cfg.get('panel_node_roles') if isinstance(cfg.get('panel_node_roles'), list) else []
        cfg['panel_node_roles'] = node_roles
        if _node_roles_changed(previous_roles, node_roles):
            role_change_resync = _trigger_full_resync_after_role_change(cfg, dev, fp, host, ip)
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    return jsonify(
        ok=True,
        http=code,
        sync_url=sync_url,
        response=resp,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        panel_node_roles=(cfg.get('panel_node_roles') if isinstance(cfg.get('panel_node_roles'), list) else []),
        role_change_resync=role_change_resync,
        ping_http=ping_http,
        ping_error=ping_error,
        api_key_bootstrap=_pull_and_apply_bootstrap_keys(cfg, dev),
    ), 200


@bp_panel.post('/api/panel/sync-now')
def api_panel_sync_now():
    cfg = ensure_config()
    dev = ensure_device()
    fp = collect_fingerprint()
    host = get_hostname()
    ip = get_ip()

    data = request.get_json(force=True, silent=True) or {}
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)

    node_type = _normalize_node_type(data.get('node_type') or data.get('nodeType') or cfg.get('node_runtime_type') or 'raspi_node')
    if node_type in ('server', 'workstation'):
        _persist_node_type_choice(cfg, node_type)
        base_url = _safe_base_url(cfg.get('admin_base_url', ''))
        ping_path = str(cfg.get('panel_ping_path') or '/api/hardware-device/ping').strip()
        if ping_path and not ping_path.startswith('/'):
            ping_path = f'/{ping_path}'
        ping_url = f"{base_url}{ping_path}" if base_url and ping_path else ''
        panel_keys = cfg.get('panel_api_keys') if isinstance(cfg.get('panel_api_keys'), dict) else {}
        client_id = str(cfg.get('client_id') or '').strip()
        api_key = str((panel_keys.get('raspi_to_admin') if isinstance(panel_keys, dict) else '') or '').strip()
        if ping_url.startswith('http://') or ping_url.startswith('https://'):
            if client_id and api_key:
                ping_code, ping_resp, _ping_err = _http_get_json_with_headers(
                    ping_url,
                    {'X-Client-Id': client_id, 'X-API-Key': api_key},
                    timeout=8,
                )
                if ping_code is not None and 200 <= ping_code < 300:
                    st = _update_panel_link_state(cfg, linked=True, last_http=ping_code, last_response=ping_resp, last_error='')
                    return jsonify(
                        ok=True,
                        synced=True,
                        mode='hardware_ping',
                        node_type=node_type,
                        http=ping_code,
                        panel_ping_url=ping_url,
                        response=ping_resp,
                        panel_link_state=st,
                        api_key_bootstrap={'ok': True, 'updated': False, 'skipped': True, 'reason': 'hardware_node'},
                    ), 200
        # Hardware fallback: full register flow with token.
        return api_panel_register_hardware()

    url = _panel_url(cfg, 'panel_register_path')
    if not url:
        return jsonify(
            ok=False,
            error='admin_base_url missing',
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_register_path=cfg.get('panel_register_path'),
        ), 400
    if not (url.startswith('http://') or url.startswith('https://')):
        return jsonify(
            ok=False,
            error='invalid_panel_url',
            resolved_url=url,
            admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
            panel_register_path=cfg.get('panel_register_path'),
        ), 400

    # First try ping without token. If device already exists in panel, this is enough
    # and avoids unnecessary token errors.
    ping_url = _panel_url(cfg, 'panel_ping_path')
    ping_code = None
    ping_resp = None
    if ping_url and (ping_url.startswith('http://') or ping_url.startswith('https://')):
        ping_payload = _panel_ping_payload(dev, fp, host, ip, cfg)
        ping_code, ping_resp, _ping_err = http_post_json(ping_url, ping_payload, timeout=8)
        if ping_code is not None and 200 <= ping_code < 300:
            flags = _extract_panel_device_flags(ping_resp if isinstance(ping_resp, dict) else None)
            if isinstance(flags, dict):
                cfg['panel_device_flags'] = flags
                cfg['updated_at'] = utc_now()
                write_json(CONFIG_PATH, cfg, mode=0o600)
            linked_user_ids, linked_customer_ids = _extract_link_ids(ping_resp if isinstance(ping_resp, dict) else None)
            if linked_user_ids or linked_customer_ids:
                _persist_link_targets(cfg, linked_user_ids, linked_customer_ids)
            _update_panel_link_state(cfg, linked=True, last_http=ping_code, last_response=ping_resp, last_error='')
            bootstrap_refresh = _pull_and_apply_bootstrap_keys(cfg, dev)
            return jsonify(
                ok=True,
                synced=True,
                mode='ping',
                http=ping_code,
                panel_ping_url=ping_url,
                response=ping_resp,
                panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
                api_key_bootstrap=bootstrap_refresh,
            ), 200

    payload = _panel_sync_payload(cfg, dev, fp, host, ip)
    code, resp, err = http_post_json(url, payload, timeout=10)

    if code is None:
        return jsonify(ok=False, error='sync_now_failed', detail=str(err), panel_register_url=url), 502

    success = _response_indicates_success(code, resp)
    if not success:
        _update_panel_link_state(
            cfg,
            linked=_sticky_linked(cfg, False, code, resp),
            last_http=code,
            last_response=resp,
            last_error=(resp.get('error') if isinstance(resp, dict) else '') or f'http {code}',
        )
        panel_msg = _extract_response_message(resp) or f'http {code}'
        err_code = str((resp.get('error_code') if isinstance(resp, dict) else '') or (resp.get('error') if isinstance(resp, dict) else '')).strip().lower()
        if err_code in ('token_missing', 'token_invalid'):
            return jsonify(
                ok=False,
                error='setup_token_required',
                detail='Gerät ist im Panel noch nicht registriert. Bitte Setup-Assistent mit Token ausführen.',
                hint='open_setup_wizard',
                http=code,
                panel_register_url=url,
                panel_ping_http=ping_code,
                panel_ping_response=ping_resp,
                response=resp,
                panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
            ), 400
        return jsonify(
            ok=False,
            error='sync_now_failed',
            detail=panel_msg,
            http=code,
            panel_register_url=url,
            response=resp,
            panel_ping_http=ping_code,
            panel_ping_response=ping_resp,
            panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        ), 400

    if isinstance(resp, dict):
        slug = (resp.get('deviceSlug') or resp.get('slug') or '').strip()
        if slug:
            cfg['device_slug'] = slug
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
        flags = _extract_panel_device_flags(resp)
        if isinstance(flags, dict):
            cfg['panel_device_flags'] = flags
            cfg['updated_at'] = utc_now()
            write_json(CONFIG_PATH, cfg, mode=0o600)
        linked_user_ids, linked_customer_ids = _extract_link_ids(resp)
        if linked_user_ids or linked_customer_ids:
            _persist_link_targets(cfg, linked_user_ids, linked_customer_ids)
        bootstrap = _extract_api_key_bootstrap(resp)
        applied = _apply_api_key_bootstrap(cfg, bootstrap)
        if applied.get('updated'):
            _ack_api_key_bootstrap(cfg, dev, applied.get('ids') or [], applied.get('activated_directions') or [])

    _update_panel_link_state(cfg, linked=True, last_http=code, last_response=resp, last_error='')

    return jsonify(
        ok=True,
        synced=True,
        mode='register',
        http=code,
        panel_register_url=url,
        response=resp,
        panel_ping_http=ping_code,
        panel_ping_response=ping_resp,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        api_key_bootstrap=_pull_and_apply_bootstrap_keys(cfg, dev),
    ), 200


@bp_panel.post('/api/panel/admin-sync-payload')
def api_panel_admin_sync_payload():
    cfg = ensure_config()
    dev = ensure_device()
    data = request.get_json(force=True, silent=True) or {}

    auth_ok, auth_error = _portal_auth_valid(data, dev, cfg)
    if not auth_ok:
        return jsonify(ok=False, error=auth_error), 401

    refresh_fingerprint = _is_truthy(data.get('refresh_fingerprint', True))
    fp = collect_fingerprint() if refresh_fingerprint else ensure_fingerprint()
    host = get_hostname()
    ip = get_ip()

    payload = _panel_sync_payload(cfg, dev, fp, host, ip)
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}

    mode = 'play' if st.get('linked') and cfg.get('selected_stream_slug') else 'setup'
    update_state(cfg, dev, fp, mode=mode, message='admin live payload', panel_state_overrides=st)

    return jsonify(
        ok=True,
        payload=payload,
        panel_link_state=st,
        collected_at=utc_now(),
    ), 200


@bp_panel.post('/api/panel/rebuild-fingerprint')
def api_panel_rebuild_fingerprint():
    cfg = ensure_config()
    dev = ensure_device()
    host = get_hostname()
    ip = get_ip()

    fp = collect_fingerprint()
    update_state(
        cfg,
        dev,
        fp,
        mode='play' if cfg.get('selected_stream_slug') else 'setup',
        message='fingerprint rebuilt',
    )

    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    linked = bool(st.get('linked'))
    base = _safe_base_url(cfg.get('admin_base_url', ''))
    if not linked or not base:
        return jsonify(
            ok=True,
            rebuilt=True,
            synced=False,
            reason='not_linked',
            fingerprint=fp,
            panel_link_state=st,
        ), 200

    url = _panel_url(cfg, 'panel_register_path')
    if not url or not (url.startswith('http://') or url.startswith('https://')):
        return jsonify(
            ok=True,
            rebuilt=True,
            synced=False,
            reason='invalid_panel_url',
            resolved_url=url or '',
            fingerprint=fp,
            panel_link_state=st,
        ), 200

    payload = _panel_sync_payload(cfg, dev, fp, host, ip)
    code, resp, err = http_post_json(url, payload, timeout=10)
    if code is None:
        st = _update_panel_link_state(cfg, linked=_sticky_linked(cfg, False, None, None), last_http=None, last_response=None, last_error=str(err))
        return jsonify(
            ok=False,
            rebuilt=True,
            synced=False,
            error='sync_failed',
            detail=str(err),
            fingerprint=fp,
            panel_link_state=st,
        ), 502

    success = _response_indicates_success(code, resp)
    st = _update_panel_link_state(
        cfg,
        linked=_sticky_linked(cfg, success, code, resp),
        last_http=code,
        last_response=resp,
        last_error='' if success else ((resp.get('error') if isinstance(resp, dict) else '') or f'http {code}'),
    )
    if not success:
        panel_msg = _extract_response_message(resp) or f'http {code}'
        return jsonify(
            ok=False,
            rebuilt=True,
            synced=False,
            error='sync_failed',
            detail=panel_msg,
            http=code,
            response=resp,
            fingerprint=fp,
            panel_link_state=st,
        ), 400

    return jsonify(
        ok=True,
        rebuilt=True,
        synced=True,
        http=code,
        response=resp,
        fingerprint=fp,
        panel_link_state=st,
    ), 200


@bp_panel.post('/api/panel/validate-token')
def api_panel_validate_token():
    cfg = ensure_config()
    dev = ensure_device()

    data = request.get_json(force=True, silent=True) or {}
    raw_node_type = str(data.get('node_type') or data.get('nodeType') or cfg.get('node_runtime_type') or 'raspi_node').strip().lower()
    node_type_aliases = {
        'raspi': 'raspi_node',
        'raspberrypi': 'raspi_node',
        'raspi_node': 'raspi_node',
        'raspi-node': 'raspi_node',
        'server': 'server',
        'workstation': 'workstation',
    }
    node_type = node_type_aliases.get(raw_node_type, 'raspi_node')
    _persist_node_type_choice(cfg, node_type)
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    base_url = request_base or _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    token = (data.get('registration_token') or '').strip()
    if not token:
        return jsonify(ok=False, error='registration_token missing'), 400
    preferred_target = str(data.get('registration_target') or '').strip().lower()
    if not preferred_target:
        preferred_target = _registration_target_from_node_type(node_type)

    result = _detect_token_target(base_url, token, dev, cfg, preferred=preferred_target)
    if result.get('target'):
        if request_base:
            cfg['admin_base_url'] = request_base
        cfg['panel_register_target'] = result.get('target')
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
        return jsonify(
            ok=True,
            valid=True,
            node_type=node_type,
            registration_target=result.get('target'),
            http=result.get('http'),
            verify_url=result.get('verify_url'),
            response=result.get('response'),
        ), 200

    if result.get('skipped'):
        return jsonify(
            ok=True,
            valid=True,
            skipped=True,
            node_type=node_type,
            registration_target=preferred_target or _registration_target_from_node_type(node_type),
            message='Token-Prüfung wird beim Registrieren durchgeführt.',
            http=result.get('http'),
            verify_url=result.get('verify_url'),
            response=result.get('response'),
        ), 200

    panel_msg = _extract_response_message(result.get('response')) if isinstance(result.get('response'), dict) else ''
    panel_msg = panel_msg or 'Token ungültig oder abgelaufen.'
    return jsonify(
        ok=False,
        error='token_invalid',
        detail=panel_msg,
        valid=False,
        node_type=node_type,
        http=result.get('http'),
        verify_url=result.get('verify_url'),
        response=result.get('response'),
    ), 400


def _search_proxy(target: str):
    cfg = ensure_config()
    dev = ensure_device()

    q = (request.args.get('q') or '').strip()
    token = (request.args.get('registration_token') or request.args.get('token') or '').strip()
    request_base = _safe_base_url((request.args.get('admin_base_url') or ''))
    base_url = request_base or _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400
    if len(q) < 2:
        return jsonify(ok=False, error='query_too_short', detail='Search query must be at least 2 chars'), 400
    search_path = 'users' if target == 'users' else 'customers'
    search_url = f"{base_url}/api/device/link/search/{search_path}"
    payload = {
        'q': q,
        'query': q,
        'deviceUuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
    }
    if token:
        payload['registrationToken'] = token
        payload['token'] = token
    code, resp, err = http_post_json(search_url, payload, timeout=8)
    if code is not None and code >= 400:
        # Backward compatible fallback if panel endpoint expects GET query params.
        query_payload = {
            'q': q,
            'deviceUuid': dev.get('device_uuid') or '',
            'authKey': dev.get('auth_key') or '',
        }
        if token:
            query_payload['token'] = token
            query_payload['registrationToken'] = token
        fallback_url = f"{search_url}?{urlencode(query_payload)}"
        get_code, get_resp, get_err = http_get_json(fallback_url, timeout=8)
        if get_code is not None and 200 <= get_code < 300:
            code, resp, err = get_code, get_resp, ''
        elif get_code is None and err == '':
            err = get_err
    if code is None:
        return jsonify(ok=False, error='search_failed', detail=str(err), search_url=search_url), 502
    if code < 200 or code >= 300:
        return jsonify(ok=False, error='search_failed', http=code, search_url=search_url, response=resp), 400

    items = _normalize_search_items(_extract_items(resp))
    if request_base:
        cfg['admin_base_url'] = request_base
        cfg['updated_at'] = utc_now()
        write_json(CONFIG_PATH, cfg, mode=0o600)
    return jsonify(ok=True, items=items, http=code, response=resp, search_url=search_url)


@bp_panel.get('/api/panel/search-users')
def api_panel_search_users():
    return _search_proxy('users')


@bp_panel.get('/api/panel/search-customers')
def api_panel_search_customers():
    return _search_proxy('customers')


@bp_panel.post('/api/panel/assign')
def api_panel_assign():
    cfg = ensure_config()
    dev = ensure_device()

    data = request.get_json(force=True, silent=True) or {}
    request_base = _safe_base_url((data.get('admin_base_url') or ''))
    base_url = request_base or _safe_base_url(cfg.get('admin_base_url', ''))
    if not base_url:
        return jsonify(ok=False, error='admin_base_url missing'), 400

    token = (data.get('registration_token') or data.get('token') or '').strip()

    target_type = (data.get('target_type') or data.get('link_target_type') or '').strip().lower()
    target_id = str(data.get('target_id') or data.get('link_target_id') or '').strip()
    if target_type not in ('user', 'customer'):
        return jsonify(ok=False, error='target_type invalid'), 400
    if not target_id:
        return jsonify(ok=False, error='target_id missing'), 400

    assign_url = _panel_assign_url(base_url)
    payload = {
        'targetType': target_type,
        'targetId': target_id,
        'target_type': target_type,
        'target_id': target_id,
        'deviceUuid': dev.get('device_uuid') or '',
        'device_uuid': dev.get('device_uuid') or '',
        'authKey': dev.get('auth_key') or '',
        'auth_key': dev.get('auth_key') or '',
    }
    if token:
        payload['registrationToken'] = token
        payload['token'] = token
    code, resp, err = http_post_json(assign_url, payload, timeout=8)
    if code is None:
        return jsonify(ok=False, error='assign_failed', detail=str(err), assign_url=assign_url), 502
    assigned = _response_indicates_success(code, resp)
    if not assigned:
        return jsonify(ok=False, error='assign_failed', http=code, assign_url=assign_url, response=resp), 400

    if request_base:
        cfg['admin_base_url'] = request_base
    selected_user = data.get('selected_user') if isinstance(data.get('selected_user'), dict) else {}
    selected_customer = data.get('selected_customer') if isinstance(data.get('selected_customer'), dict) else {}
    current_users: list[int] = []
    for row in cfg.get('panel_linked_users', []):
        if not isinstance(row, dict):
            continue
        try:
            uid = int(row.get('id') or 0)
        except Exception:
            uid = 0
        if uid > 0:
            current_users.append(uid)
    current_customers: list[int] = []
    for row in cfg.get('panel_linked_customers', []):
        if not isinstance(row, dict):
            continue
        try:
            cid = int(row.get('id') or 0)
        except Exception:
            cid = 0
        if cid > 0:
            current_customers.append(cid)
    if target_type == 'user' and target_id.isdigit():
        current_users = sorted(set(current_users + [int(target_id)]))
    if target_type == 'customer' and target_id.isdigit():
        current_customers = sorted(set(current_customers + [int(target_id)]))

    linked_user_rows, linked_customer_rows = _extract_link_rows(resp if isinstance(resp, dict) else None)
    if target_type == 'user' and target_id.isdigit():
        if linked_user_rows:
            pass
        elif selected_user:
            linked_user_rows = [selected_user]
    if target_type == 'customer' and target_id.isdigit():
        if linked_customer_rows:
            pass
        elif selected_customer:
            linked_customer_rows = [selected_customer]

    if linked_user_rows or linked_customer_rows:
        merged_users: list[object] = []
        merged_users.extend(cfg.get('panel_linked_users') if isinstance(cfg.get('panel_linked_users'), list) else [])
        merged_users.extend(linked_user_rows or current_users)
        merged_customers: list[object] = []
        merged_customers.extend(cfg.get('panel_linked_customers') if isinstance(cfg.get('panel_linked_customers'), list) else [])
        merged_customers.extend(linked_customer_rows or current_customers)
        _persist_link_targets(cfg, merged_users, merged_customers)
    else:
        _persist_link_targets(cfg, current_users, current_customers)
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)
    return jsonify(ok=True, assigned=True, http=code, assign_url=assign_url, response=resp)


@bp_panel.get('/api/panel/link-status')
def api_panel_link_status():
    cfg = ensure_config()
    dev = ensure_device()
    _refresh_link_targets_from_admin_context(cfg, dev)
    st = cfg.get('panel_link_state') if isinstance(cfg.get('panel_link_state'), dict) else {}
    bootstrap_refresh = {'ok': False, 'updated': False}
    if bool(st.get('linked')):
        bootstrap_refresh = _pull_and_apply_bootstrap_keys(cfg, dev)
    return jsonify(
        ok=True,
        linked=bool(st.get('linked')),
        admin_base_url=_safe_base_url(cfg.get('admin_base_url', '')),
        panel_register_path=cfg.get('panel_register_path'),
        panel_ping_path=cfg.get('panel_ping_path'),
        panel_link_state=st,
        panel_device_flags=(cfg.get('panel_device_flags') if isinstance(cfg.get('panel_device_flags'), dict) else {}),
        panel_api_key_bootstrap=(cfg.get('panel_api_key_bootstrap') if isinstance(cfg.get('panel_api_key_bootstrap'), dict) else {}),
        panel_api_keys={
            'raspi_to_admin_configured': bool(str(((cfg.get('panel_api_keys') or {}).get('raspi_to_admin') if isinstance(cfg.get('panel_api_keys'), dict) else '') or '').strip()),
            'admin_to_raspi_configured': bool(str(((cfg.get('panel_api_keys') or {}).get('admin_to_raspi') if isinstance(cfg.get('panel_api_keys'), dict) else '') or '').strip()),
            'updated_at': ((cfg.get('panel_api_keys') or {}).get('updated_at') if isinstance(cfg.get('panel_api_keys'), dict) else None),
        },
        api_key_bootstrap=bootstrap_refresh,
        panel_linked_users=(cfg.get('panel_linked_users') if isinstance(cfg.get('panel_linked_users'), list) else []),
        panel_linked_customers=(cfg.get('panel_linked_customers') if isinstance(cfg.get('panel_linked_customers'), list) else []),
        device_slug=cfg.get('device_slug') or '',
    )


@bp_panel.post('/api/panel/unlink')
def api_panel_unlink():
    cfg = ensure_config()
    dev = ensure_device()
    fp = ensure_fingerprint()

    cfg['registration_token'] = ''
    cfg['panel_link_state'] = {
        'linked': False,
        'last_check': utc_now(),
        'last_http': None,
        'last_response': None,
        'last_error': 'unlinked',
    }
    cfg['panel_device_flags'] = {
        'is_active': None,
        'is_locked': None,
        'updated_at': utc_now(),
    }
    cfg['panel_linked_users'] = []
    cfg['panel_linked_customers'] = []
    cfg['panel_api_keys'] = {
        'raspi_to_admin': '',
        'admin_to_raspi': '',
        'updated_at': utc_now(),
    }
    cfg['panel_api_key_bootstrap'] = {
        'mode': 'none',
        'status': 'none',
        'last_pull_at': None,
        'last_ack_at': None,
        'last_error': '',
    }
    cfg['updated_at'] = utc_now()
    write_json(CONFIG_PATH, cfg, mode=0o600)

    update_state(cfg, dev, fp, mode='setup', message='panel unlinked', panel_state_overrides=cfg['panel_link_state'])
    return jsonify(ok=True, panel_link_state=cfg['panel_link_state'], panel_device_flags=cfg['panel_device_flags'])
