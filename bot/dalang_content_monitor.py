"""大浪频道新消息的文字和图片风险提示。"""

import logging
import re

import pymysql
import requests
from telebot import apihelper

from db import get_conn
from delivery_age import ocr_image_text
from visit_stats import admin_ids


logger = logging.getLogger(__name__)
CHANNEL_ID = -1002219327758
CHANNEL_URL = "https://t.me/dlts02"

_AGE = re.compile(
    r"(?:年龄|年齡|芳龄|芳齡|年纪|年紀)\s*[:：;；]?\s*(\d{1,2})(?!\d)"
    r"|(?<!\d)(\d{1,2})\s*(?:周岁|周歲|岁|歲)"
)
_MINOR = re.compile(
    r"未成年(?:人|少女|少年)?"
    r"|(?:未满|未滿|不足|未及|不满|不滿)\s*(?:18|十八)\s*(?:周岁|周歲|岁|歲)"
    r"|(?:18|十八)\s*(?:周岁|周歲|岁|歲)\s*(?:以下|以内|以內)"
)
_DRUGS = ("毒品", "吸毒", "贩毒", "販毒", "冰毒", "海洛因", "大麻")
_GAMBLING = ("赌博", "賭博", "博彩", "网络博彩", "網絡博彩", "网络赌博", "網絡賭博", "赌球", "賭球")


def matches(message):
    return message.chat.id == CHANNEL_ID


def inspect_text(text):
    text = text or ""
    ages = [int(a or b) for a, b in _AGE.findall(text)]
    risks = []
    if _MINOR.search(text) or any(age < 18 for age in ages):
        risks.append("疑似未成年人信息")
    if any(word in text for word in _DRUGS):
        risks.append("疑似毒品相关信息")
    if any(word in text for word in _GAMBLING):
        risks.append("疑似赌博相关信息")
    return risks, bool(ages)


def _quarantine_indexed(message):
    """停止已入库的同一消息及关联人员继续公开。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE staff_members m SET m.blocked=1
                WHERE m.venue_id=4 AND (
                    EXISTS (SELECT 1 FROM staff_photos p
                        WHERE p.staff_id=m.id AND p.channel_id=%s AND p.msg_id=%s)
                    OR EXISTS (SELECT 1 FROM staff_photo_sources s
                        JOIN technician_photos t ON t.id=s.source_id
                        WHERE s.staff_id=m.id AND t.venue_id=4
                          AND t.channel_id=%s AND t.msg_id=%s)
                    OR EXISTS (SELECT 1 FROM technician_photos t
                        WHERE t.venue_id=4 AND t.channel_id=%s AND t.msg_id=%s
                          AND t.technician_code=m.code)
                )""", (str(CHANNEL_ID), message.message_id) * 3)
            cur.execute("""UPDATE technician_photos SET status='pending_review',file_id=''
                WHERE venue_id=4 AND channel_id=%s AND msg_id=%s""",
                        (str(CHANNEL_ID), message.message_id))
        conn.commit()
        return True
    except pymysql.MySQLError:
        conn.rollback()
        logger.exception("Could not quarantine indexed channel message %s", message.message_id)
        return False
    finally:
        conn.close()


def _notify(bot, message, reasons, status):
    recipients = admin_ids()
    if not recipients:
        logger.error("Dalang monitor has no administrator recipients")
        return
    text = (f"大浪频道内容提醒：{'、'.join(reasons)}\n"
            f"消息：{CHANNEL_URL}/{message.message_id}\n"
            f"处理：{status}\n"
            "请人工核对关联档案及历史消息；自动识别不能证明真实年龄，也可能漏报。")
    for recipient in recipients:
        try:
            bot.send_message(recipient, text, disable_web_page_preview=True)
        except (apihelper.ApiTelegramException, requests.RequestException) as exc:
            logger.error("Dalang monitor alert failed message=%s admin=%s error=%s",
                         message.message_id, recipient, type(exc).__name__)


def handle(bot, message):
    """返回 True 表示停止后续业务处理。"""
    text = "\n".join(filter(None, (getattr(message, "text", None),
                                   getattr(message, "caption", None))))
    risks, has_age = inspect_text(text)
    uncertain = None
    if getattr(message, "photo", None):
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            image = bot.download_file(file_info.file_path)
            ocr_text = ocr_image_text(image)
        except (apihelper.ApiTelegramException, requests.RequestException, OSError,
                RuntimeError, ValueError, ImportError) as exc:
            logger.error("Dalang monitor image unavailable message=%s error=%s",
                         message.message_id, type(exc).__name__)
            ocr_text = ""
        image_risks, image_has_age = inspect_text(ocr_text)
        risks = list(dict.fromkeys(risks + image_risks))
        has_age = has_age or image_has_age
        if not ocr_text:
            uncertain = "图片识别失败或未识别到文字"
        elif not has_age:
            uncertain = "图片未识别到年龄"
    elif getattr(message, "content_type", None) not in ("text", "photo"):
        uncertain = "媒体内容无法可靠检查"

    if risks:
        quarantined = False
        try:
            quarantined = _quarantine_indexed(message)
        except pymysql.MySQLError:
            logger.exception("Dalang monitor database unavailable message=%s", message.message_id)
        try:
            deleted = bot.delete_message(CHANNEL_ID, message.message_id)
            status = "频道消息已删除" if deleted else "删除未确认，请立即手动处理"
        except (apihelper.ApiTelegramException, requests.RequestException) as exc:
            logger.error("Dalang monitor deletion failed message=%s error=%s",
                         message.message_id, type(exc).__name__)
            status = "删除失败，请立即手动处理"
        if not quarantined:
            status += "；档案隔离未确认"
        _notify(bot, message, risks, status)
        return True

    if uncertain:
        _notify(bot, message, [uncertain], "保留，需人工核查")
    return False
