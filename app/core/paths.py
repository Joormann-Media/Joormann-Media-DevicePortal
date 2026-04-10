from __future__ import annotations

import os
from pathlib import Path

PORTAL_DIR = str(Path(__file__).resolve().parents[2])
DATA_DIR = str(Path(PORTAL_DIR) / 'var' / 'data')
CONFIG_PATH = os.getenv('CONFIG_PATH', str(Path(DATA_DIR) / 'config.json'))
STORAGE_CONFIG_PATH = os.getenv('STORAGE_CONFIG_PATH', str(Path(DATA_DIR) / 'config-storage.json'))
DEVICE_PATH = os.getenv('DEVICE_PATH', str(Path(DATA_DIR) / 'device.json'))
FINGERPRINT_PATH = os.getenv('FINGERPRINT_PATH', str(Path(DATA_DIR) / 'fingerprint.json'))
STATE_PATH = os.getenv('STATE_PATH', str(Path(DATA_DIR) / 'state.json'))
PLAN_PATH = os.getenv('PLAN_PATH', str(Path(DATA_DIR) / 'plan.json'))
ASSET_DIR = os.getenv('ASSET_DIR', str(Path(PORTAL_DIR) / 'var' / 'assets'))
SCREENSHOTS_DIR = os.getenv('SCREENSHOTS_DIR', '/mnt/deviceportal/media/screenshots')
