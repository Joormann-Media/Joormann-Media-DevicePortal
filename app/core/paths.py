from __future__ import annotations

import os

CONFIG_PATH = os.getenv('CONFIG_PATH', '/etc/device/config.json')
DEVICE_PATH = os.getenv('DEVICE_PATH', '/etc/device/device.json')
FINGERPRINT_PATH = os.getenv('FINGERPRINT_PATH', '/etc/device/fingerprint.json')
STATE_PATH = os.getenv('STATE_PATH', '/etc/device/state.json')
PLAN_PATH = os.getenv('PLAN_PATH', '/etc/device/plan.json')
ASSET_DIR = os.getenv('ASSET_DIR', '/var/lib/deviceportal/assets')
