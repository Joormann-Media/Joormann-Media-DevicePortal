from __future__ import annotations

import base64
import io


def build_qr_svg_data_uri(payload: str) -> str:
    text = str(payload or "").strip()
    if not text:
        return ""

    try:
        import qrcode
        import qrcode.image.svg
    except Exception:
        return ""

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgImage)

    buffer = io.BytesIO()
    img.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
