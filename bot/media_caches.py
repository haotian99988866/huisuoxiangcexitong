import os
import tempfile

from image_cache import ImageCache
from video_cache import VideoCache

_image_root = os.path.join(
    os.getenv("LOCALAPPDATA") or tempfile.gettempdir(), "Huisuo", "image-cache"
)
_ktv_video_root = os.path.join(
    os.getenv("LOCALAPPDATA") or tempfile.gettempdir(), "Huisuo", "ktv-video-cache"
)
_delivery_video_root = os.path.join(
    os.getenv("LOCALAPPDATA") or tempfile.gettempdir(), "Huisuo", "delivery-video-cache"
)

image_cache = ImageCache(_image_root)
video_cache = VideoCache(_ktv_video_root)
delivery_video_cache = VideoCache(_delivery_video_root)