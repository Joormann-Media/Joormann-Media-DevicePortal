from __future__ import annotations

import requests


def http_post_json(url: str, payload: dict, timeout: int = 6) -> tuple[int | None, dict | None, str]:
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = {'raw': (response.text or '')[:2000]}
        return response.status_code, data, ''
    except Exception as exc:
        return None, None, str(exc)


def http_get_json(url: str, timeout: int = 6) -> tuple[int | None, dict | None, str]:
    try:
        response = requests.get(url, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = {'raw': (response.text or '')[:2000]}
        return response.status_code, data, ''
    except Exception as exc:
        return None, None, str(exc)


def http_get_text(url: str, timeout: int = 6) -> tuple[int | None, str, str]:
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code, (response.text or '')[:2000], ''
    except Exception as exc:
        return None, '', str(exc)
