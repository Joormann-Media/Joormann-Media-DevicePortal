from __future__ import annotations

import json
import os
from typing import Any


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {} if default is None else default
    except Exception:
        return {} if default is None else default


def write_json(path: str, data: Any, mode: int = 0o600) -> tuple[bool, str]:
    try:
        dirpath = os.path.dirname(path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        tmp = f'{path}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except Exception:
            pass
        return True, ''
    except Exception as exc:
        return False, str(exc)
