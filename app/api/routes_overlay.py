from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.overlay_state import (
    clear_category,
    read_overlay_state,
    reset_overlay_state,
    sanitize_flash,
    sanitize_popup,
    sanitize_ticker,
    upsert_category_item,
    write_overlay_state,
)


bp_overlay = Blueprint("overlay", __name__)


def _payload() -> dict:
    data = request.get_json(force=True, silent=True) or {}
    return data if isinstance(data, dict) else {}


@bp_overlay.get("/api/player/overlay/state")
def api_overlay_state_get():
    state, path = read_overlay_state()
    return jsonify(ok=True, data=state, path=str(path))


@bp_overlay.post("/api/player/overlay/flash")
def api_overlay_flash_save():
    data = _payload()
    item = sanitize_flash(data)
    if not item.get("title") and not item.get("message"):
        return jsonify(ok=False, error="flash_title_or_message_required"), 400

    state, path = upsert_category_item("flashMessages", item)
    ok, err, _ = write_overlay_state(state)
    if not ok:
        return jsonify(ok=False, error=str(err or "overlay_write_failed"), path=str(path)), 500

    return jsonify(ok=True, message="Flash gespeichert.", data=state, path=str(path), item=item)


@bp_overlay.post("/api/player/overlay/ticker")
def api_overlay_ticker_save():
    data = _payload()
    item = sanitize_ticker(data)
    if not item.get("text"):
        return jsonify(ok=False, error="ticker_text_required"), 400

    state, path = upsert_category_item("tickers", item)
    ok, err, _ = write_overlay_state(state)
    if not ok:
        return jsonify(ok=False, error=str(err or "overlay_write_failed"), path=str(path)), 500

    return jsonify(ok=True, message="Ticker gespeichert.", data=state, path=str(path), item=item)


@bp_overlay.post("/api/player/overlay/popup")
def api_overlay_popup_save():
    data = _payload()
    item = sanitize_popup(data)
    if (
        not item.get("title")
        and not item.get("message")
        and not item.get("popupContent")
        and not item.get("imagePath")
        and not item.get("imageUrl")
        and not item.get("imageData")
    ):
        return jsonify(ok=False, error="popup_content_required"), 400

    state, path = upsert_category_item("popups", item)
    ok, err, _ = write_overlay_state(state)
    if not ok:
        return jsonify(ok=False, error=str(err or "overlay_write_failed"), path=str(path)), 500

    return jsonify(ok=True, message="Popup gespeichert.", data=state, path=str(path), item=item)


@bp_overlay.post("/api/player/overlay/clear")
def api_overlay_clear():
    data = _payload()
    category = str(data.get("category") or "").strip()
    allowed = {
        "flash": "flashMessages",
        "flashMessages": "flashMessages",
        "ticker": "tickers",
        "tickers": "tickers",
        "popup": "popups",
        "popups": "popups",
    }
    key = allowed.get(category)
    if not key:
        return jsonify(ok=False, error="invalid_category"), 400

    ok, err, path, state = clear_category(key)
    if not ok:
        return jsonify(ok=False, error=str(err or "overlay_write_failed"), path=str(path)), 500
    return jsonify(ok=True, message=f"{key} geleert.", data=state, path=str(path))


@bp_overlay.post("/api/player/overlay/reset")
def api_overlay_reset():
    ok, err, path, state = reset_overlay_state()
    if not ok:
        return jsonify(ok=False, error=str(err or "overlay_write_failed"), path=str(path)), 500
    return jsonify(ok=True, message="Overlay-Status zurückgesetzt.", data=state, path=str(path))
