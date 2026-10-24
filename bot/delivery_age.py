import io
import logging
import re

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from technician_photo_ocr import _get_engine

logger = logging.getLogger(__name__)

MIN_AGE = 18

_LABELED_AGE = re.compile(r"(?:年龄|年齡|芳龄|芳齡|年纪|年紀)\s*[:：;；]?\s*(\d{1,2})")
_SUFFIX_AGE = re.compile(r"(?<!\d)(\d{2})\s*(?:岁|歲|周岁|周歲)")
_WORK_NO = re.compile(r"工号\s*[:：]?\s*([A-Za-z0-9]{1,6})")
_PRICE_LINE = re.compile(r"^\s*([A-Za-z0-9])\s*牌\s*(\d+)\s*[＄$]?", re.MULTILINE)


def extract_work_no(*texts):
    """取照片上的工号，如 S39、808；找不到返回 None。"""
    text = "\n".join(t for t in texts if t)
    match = _WORK_NO.search(text)
    return match.group(1).upper() if match else None


def parse_price_table(text):
    """解析价目表：'6牌 150＄' -> {'6': '150', ...}。"""
    return {key.upper(): price for key, price in _PRICE_LINE.findall(text or "")}


def price_for_work_no(work_no, table):
    """工号第一位对应价目表里的牌；对不上返回 None。"""
    if not work_no or not table:
        return None
    return table.get(work_no[0].upper())


def extract_age(*texts):
    """从文字里取年龄；出现多个时取最小值（保守），找不到返回 None。"""
    text = "\n".join(t for t in texts if t)
    ages = [int(m) for m in _LABELED_AGE.findall(text)]
    if not ages:
        ages = [int(m) for m in _SUFFIX_AGE.findall(text)]
    ages = [age for age in ages if 5 <= age <= 80]
    return min(ages) if ages else None


def ocr_image_text(image_bytes):
    """返回图片里识别出的文字（按行），失败返回空字符串。"""
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image_array = np.asarray(image)
        output = _get_engine()(image_array)
    except (UnidentifiedImageError, OSError, RuntimeError, ValueError) as exc:
        logger.error("Delivery OCR failed (%s)", type(exc).__name__)
        return ""
    texts = output.txts if output.txts is not None else []
    return "\n".join(str(text) for text in texts)
