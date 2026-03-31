from __future__ import annotations

import os

from app import create_app

app = create_app()

if __name__ == '__main__':
    port_raw = os.getenv("PORTAL_PORT", "5070").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 5070
    app.run(host='0.0.0.0', port=port, debug=False)
