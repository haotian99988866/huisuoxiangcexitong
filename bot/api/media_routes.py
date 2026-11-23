import io

import pymysql
from flask import Blueprint, Response, g, jsonify, request, send_file

import announcements
import hotels
import venue_recommendations
from movies import (
    DRAMA_CHANNEL_ID,
    thumb_file_id as movie_thumb_file_id,
)
from music import audio_file as music_audio_file
from music_stream import RemoteAudio, audio_response as music_stream_response
from delivery_posts import public_person_media

media_blueprint = Blueprint("media", __name__)


@media_blueprint.get("/photos/<int:photo_id>/image", endpoint="technician_photo_image")
def technician_photo_image(photo_id):
    import api_server
    from technician_photo_ocr import extract_labeled_technician_code

    conn = None
    cur = None
    try:
        conn = api_server.get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.file_id, p.venue_id, p.status, p.technician_code,
                   p.duty_date,
                   p.ocr_text,
                   EXISTS (
                       SELECT 1
                       FROM attendance_daily a
                       WHERE a.venue_id = p.venue_id
                             AND a.duty_date = p.duty_date
                             AND FIND_IN_SET(p.technician_code, a.technician_codes) > 0
                   ) AS is_on_roster
            FROM technician_photos p
            WHERE p.id = %s
            """,
            (photo_id,),
        )
        photo = cur.fetchone()
        underage_codes = set()
        adult_codes = set()
        if photo is not None and photo["venue_id"] in {4, 5, 6}:
            cur.execute(
                """
                SELECT technician_code, ocr_text
                FROM technician_photos
                WHERE venue_id = %s AND duty_date = %s
                """,
                (photo["venue_id"], photo["duty_date"]),
            )
            day_photos = cur.fetchall()
            if photo["venue_id"] in {4, 5, 6}:
                underage_codes = api_server._underage_technician_codes(day_photos)
                adult_codes = api_server._confirmed_adult_technician_codes(day_photos)
    except pymysql.MySQLError:
        api_server.logger.exception("Failed to look up technician photo %s", photo_id)
        return jsonify({"error": "数据库请求失败，请稍后重试"}), 500
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()

    if photo is None:
        return jsonify({"error": "找不到该照片"}), 404

    if photo["status"] != "matched":
        return jsonify({"error": "找不到该照片"}), 404

    is_matched_roster_photo = (
        photo["technician_code"] is not None
        and photo["is_on_roster"]
    )
    labeled_code = (
        photo["technician_code"]
        or extract_labeled_technician_code(photo["ocr_text"])
    )
    is_labeled_kaidi_photo = (
        photo["venue_id"] == 4
        and labeled_code is not None
        and labeled_code not in underage_codes
        and labeled_code in adult_codes
    )
    is_labeled_atlantic_photo = (
        photo["venue_id"] == 5
        and labeled_code is not None
        and labeled_code not in underage_codes
        and labeled_code in adult_codes
    )
    is_labeled_dihao_photo = (
        photo["venue_id"] == 6
        and labeled_code is not None
        and labeled_code not in underage_codes
        and labeled_code in adult_codes
    )
    if (
        not is_matched_roster_photo
        and not is_labeled_kaidi_photo
        and not is_labeled_atlantic_photo
        and not is_labeled_dihao_photo
    ):
        return jsonify({"error": "找不到该照片"}), 404

    return api_server._telegram_image(photo["file_id"])


@media_blueprint.get("/staff-photos/<int:photo_id>/image", endpoint="staff_photo_image")
def staff_photo_image(photo_id):
    import api_server
    from staff_archive import photo

    try:
        row = photo(photo_id)
        if not row:
            return jsonify({"error": "人员照片不存在或已停用"}), 404
        if row.get("media_type") == "video":
            return api_server.cached_video_response(
                row["file_id"], api_server.delivery_video_cache
            )
        return api_server._telegram_image(row["file_id"])
    except pymysql.MySQLError:
        api_server.logger.exception("Staff archive photo unavailable")
        return jsonify({"error": "人员照片暂不可用"}), 503


@media_blueprint.get("/ktv-videos/<int:video_id>", endpoint="ktv_video")
def ktv_video(video_id):
    import api_server

    conn = None
    try:
        conn = api_server.get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_id FROM ktv_room_videos WHERE id = %s AND venue_id = 3",
                (video_id,),
            )
            row = cur.fetchone()
    except pymysql.MySQLError:
        api_server.logger.exception("Failed to look up KTV video %s", video_id)
        return jsonify({"error": "数据库请求失败，请稍后重试"}), 500
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        return jsonify({"error": "找不到该视频"}), 404
    return api_server.cached_video_response(row["file_id"], api_server.video_cache)


@media_blueprint.get("/movie-thumb/<int:message_id>", endpoint="movie_thumb")
def movie_thumb(message_id):
    import api_server

    try:
        file_id = movie_thumb_file_id(message_id)
    except pymysql.MySQLError:
        api_server.logger.exception("Failed to look up movie thumb %s", message_id)
        return jsonify({"error": "数据库请求失败，请稍后重试"}), 500
    if not file_id:
        return jsonify({"error": "找不到封面图"}), 404
    return api_server._telegram_image(file_id)


@media_blueprint.get("/short-drama-thumb/<int:message_id>", endpoint="short_drama_thumb")
def short_drama_thumb(message_id):
    import api_server

    if g.tenant is not None:
        return jsonify({"error": "此租户入口暂未开放短剧"}), 403
    try:
        file_id = movie_thumb_file_id(message_id, DRAMA_CHANNEL_ID)
    except pymysql.MySQLError:
        api_server.logger.exception("Failed to look up short drama thumb %s", message_id)
        return jsonify({"error": "数据库请求失败，请稍后重试"}), 500
    if not file_id:
        return jsonify({"error": "找不到封面图"}), 404
    return api_server._telegram_image(file_id)


@media_blueprint.get("/music-audio/<int:track_id>", endpoint="music_audio")
def music_audio(track_id):
    import api_server

    try:
        result = music_audio_file(track_id, stream=True)
        if result is None:
            return jsonify({"error": "歌曲不存在或已下架"}), 404
        if isinstance(result, RemoteAudio):
            return music_stream_response(result)
        path, mimetype = result
        response = send_file(path, mimetype=mimetype, conditional=True, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except (ValueError, OSError):
        api_server.logger.exception("Local music file unavailable")
        return jsonify({"error": "音频文件暂不可用，请联系管理员"}), 503
    except pymysql.MySQLError:
        api_server.logger.exception("Music audio lookup failed")
        return jsonify({"error": "音乐服务暂不可用"}), 503


@media_blueprint.get("/delivery-media/<int:media_id>", endpoint="delivery_media")
def delivery_media(media_id):
    import api_server

    conn = None
    try:
        conn = api_server.get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id,m.person_id,m.venue_id,m.channel_id,m.message_id,
                       m.media_group_id,m.media_type,m.file_id,m.caption,m.age,
                       m.ocr_text,m.image_hash,m.created_at,
                       p.deleted_at,p.merged_into_id,p.consent_status
                FROM delivery_media m
                LEFT JOIN delivery_people p ON p.id=m.person_id
                WHERE m.id=%s
                """,
                (media_id,),
            )
            target = cur.fetchone()
            if target is None:
                return jsonify({"error": "找不到该媒体"}), 404
            if target["person_id"] is not None and (
                target["deleted_at"] is not None
                or target["merged_into_id"] is not None
                or target["consent_status"] != "granted"
            ):
                return jsonify({"error": "找不到该媒体"}), 404
            if target["person_id"] is not None:
                cur.execute(
                    """
                    SELECT id,person_id,message_id,channel_id,media_group_id,
                           media_type,caption,age,ocr_text,image_hash,created_at
                    FROM delivery_media
                    WHERE person_id=%s AND media_type IN ('photo','video')
                    ORDER BY created_at DESC,id DESC
                    """,
                    (target["person_id"],),
                )
            elif target["media_group_id"]:
                cur.execute(
                    """
                    SELECT id,person_id,message_id,channel_id,media_group_id,
                           media_type,caption,age,ocr_text,image_hash,created_at
                    FROM delivery_media
                    WHERE venue_id=%s AND channel_id=%s AND media_group_id=%s
                      AND media_type IN ('photo','video')
                    ORDER BY id
                    """,
                    (
                        target["venue_id"],
                        target["channel_id"],
                        target["media_group_id"],
                    ),
                )
            else:
                rows = [target]
            if target["person_id"] is not None or target["media_group_id"]:
                rows = cur.fetchall()
    except pymysql.MySQLError:
        api_server.logger.exception("Failed to look up delivery media %s", media_id)
        return jsonify({"error": "数据库请求失败，请稍后重试"}), 500
    finally:
        if conn is not None:
            conn.close()
    visible_ids = {item["id"] for item in public_person_media(rows)}
    if media_id not in visible_ids:
        return jsonify({"error": "找不到该媒体"}), 404
    if target["media_type"] == "photo":
        return api_server._telegram_image(target["file_id"])
    return api_server.cached_video_response(
        target["file_id"], api_server.delivery_video_cache
    )


@media_blueprint.get(
    "/announcement-media/<int:ident>/<int:index>", endpoint="announcement_media"
)
def announcement_media(ident, index):
    import api_server

    try:
        media = announcements.media_file(ident, index)
        if media is None:
            return jsonify({"error": "公告媒体不存在或已下架"}), 404
        path = announcements.local_media_path(media["filename"])
        if not path.is_file():
            return jsonify({"error": "公告媒体暂不可用，请稍后重试"}), 503
        if media["kind"] == "video":
            from announcement_video import playback_path

            path = playback_path(path)
        content_type = "image/jpeg" if media["kind"] == "photo" else (
            "video/webm" if path.suffix == ".webm" else "video/mp4"
        )
        response = send_file(path, mimetype=content_type, conditional=True, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if media["kind"] == "video":
            response.headers["Accept-Ranges"] = "bytes"
            response.headers["X-Video-Prepared"] = (
                "faststart" if path.parent.name == "video-faststart" else "original"
            )
        return response
    except pymysql.MySQLError:
        api_server.logger.exception("Announcement media unavailable")
        return jsonify({"error": "公告媒体暂不可用"}), 503


@media_blueprint.get("/hotel-images/<hotel_id>", endpoint="hotel_image")
def hotel_image(hotel_id):
    import api_server

    try:
        picture = hotels.image(hotel_id)
        if picture is None:
            return jsonify({"error": "酒店照片不存在或已下架"}), 404
        response = Response(picture, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except pymysql.MySQLError:
        api_server.logger.exception("Hotel image unavailable")
        return jsonify({"error": "酒店照片暂不可用"}), 503


@media_blueprint.get("/venue-recommendation-images/<ident>", endpoint="venue_recommendation_image")
def venue_recommendation_image(ident):
    import api_server

    try:
        media = venue_recommendations.media(ident)
        if media is None:
            return jsonify({"error": "推荐媒体不存在、未开始或已下架"}), 404
        response = send_file(
            io.BytesIO(media["image"]),
            mimetype="video/mp4" if media["media_type"] == "video" else "image/jpeg",
            conditional=True,
            etag=False,
            max_age=0,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except pymysql.MySQLError:
        api_server.logger.exception("Venue recommendation image unavailable")
        return jsonify({"error": "推荐媒体暂不可用"}), 503