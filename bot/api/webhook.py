import json

import pymysql
from flask import g, jsonify, request

import announcements
import hotels
import venue_recommendations
from tenants import public_config


def parse_webhook_payload(req):
    proof_upload = req.path == "/member-deposit"
    hotel_upload = req.path == "/admin-hotel-save"
    delivery_upload = req.path == "/admin-delivery-upload"
    recommendation_upload = req.path == "/admin-venue-recommendation-save"
    if hotel_upload or delivery_upload or recommendation_upload:
        try:
            data = json.loads(req.form.get("payload", ""))
        except (ValueError, TypeError):
            return {"error": ("上传资料格式无效", 400)}
        if isinstance(data, dict):
            data["action"] = (
                "admin_venue_recommendation_save" if recommendation_upload else
                "admin_delivery_staff_upload" if delivery_upload else "admin_hotel_save"
            )
    else:
        data = (
            {**req.form, "action": "member_deposit"} if proof_upload else req.get_json(silent=True)
        )

    if not isinstance(data, dict):
        return {"error": ("请求体须为 JSON 对象", 400)}

    action = data.get("action")
    if not isinstance(action, str):
        return {"error": ("请提供有效的操作类型", 400)}

    return {
        "data": data,
        "action": action,
        "proof_upload": proof_upload,
        "hotel_upload": hotel_upload,
        "delivery_upload": delivery_upload,
        "recommendation_upload": recommendation_upload,
    }


def _tenant_config(_data):
    return jsonify({"tenant": public_config(g.tenant) if g.tenant else None})


def _get_hotels(data):
    try:
        return jsonify(hotels.catalog(data))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except pymysql.MySQLError:
        import api_server
        api_server.logger.exception("Hotel catalog unavailable")
        return jsonify({"error": "酒店推荐暂不可用，请稍后重试"}), 503


def _get_venue_recommendations(data):
    try:
        return jsonify(venue_recommendations.catalog(data))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except pymysql.MySQLError:
        import api_server
        api_server.logger.exception("Venue recommendations unavailable")
        return jsonify({"error": "会所推荐暂不可用，请稍后重试"}), 503


def _get_announcements(data):
    try:
        result = announcements.catalog(data.get("page", 1))
        if result is None:
            return jsonify({"error": "公告不存在或已下架"}), 404
        response = jsonify(result)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except pymysql.MySQLError:
        import api_server
        api_server.logger.exception("Announcement service unavailable")
        return jsonify({"error": "公告暂不可用，请稍后重试"}), 503


def _get_announcement(data):
    try:
        result = announcements.detail(data.get("id"))
        if result is None:
            return jsonify({"error": "公告不存在或已下架"}), 404
        response = jsonify(result)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except pymysql.MySQLError:
        import api_server
        api_server.logger.exception("Announcement service unavailable")
        return jsonify({"error": "公告暂不可用，请稍后重试"}), 503


# Public read actions migrated out of api_server.webhook; other actions stay in webhook().
PUBLIC_READ_HANDLERS = {
    "tenant_config": _tenant_config,
    "get_hotels": _get_hotels,
    "get_venue_recommendations": _get_venue_recommendations,
    "get_announcements": _get_announcements,
    "get_announcement": _get_announcement,
}


def try_public_read_handlers(action, data):
    handler = PUBLIC_READ_HANDLERS.get(action)
    if handler is None:
        return None
    return handler(data)