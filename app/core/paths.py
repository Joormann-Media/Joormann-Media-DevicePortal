from __future__ import annotations

import os
from pathlib import Path

CONFIG_PATH = os.getenv('CONFIG_PATH', '/etc/device/config.json')
DEVICE_PATH = os.getenv('DEVICE_PATH', '/etc/device/device.json')
FINGERPRINT_PATH = os.getenv('FINGERPRINT_PATH', '/etc/device/fingerprint.json')
STATE_PATH = os.getenv('STATE_PATH', '/etc/device/state.json')
PLAN_PATH = os.getenv('PLAN_PATH', '/etc/device/plan.json')
PORTAL_DIR = str(Path(__file__).resolve().parents[2])
ASSET_DIR = os.getenv('ASSET_DIR', str(Path(PORTAL_DIR) / 'var' / 'assets'))
