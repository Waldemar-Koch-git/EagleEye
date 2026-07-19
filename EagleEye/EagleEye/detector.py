import logging
from urllib.parse import urlparse

import requests

from .stream_types import StreamType

logger = logging.getLogger(__name__)


def detect_stream_type(url: str, timeout: float = 3.0) -> StreamType:
    """Attempt to detect the stream type:
    - rtsp:// -> RTSP (transport variant must be chosen manually if needed,
      default assumption: TCP, as most robust)
    - http(s):// -> Content-Type is checked via GET headers:
        multipart/x-mixed-replace -> MJPEG
        image/jpeg                -> Snapshot
        video/* / octet-stream    -> HTTP video (H.264/H.265)
      Fallback: file extension / path heuristic
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme == "rtsp":
        return StreamType.RTSP_TCP

    if scheme in ("http", "https"):
        try:
            resp = requests.get(url, stream=True, timeout=timeout)
            ctype = resp.headers.get("Content-Type", "").lower()
            resp.close()
            if "multipart/x-mixed-replace" in ctype:
                return StreamType.MJPEG
            if "image/jpeg" in ctype or "image/jpg" in ctype:
                return StreamType.SNAPSHOT_JPEG
            if "video/" in ctype or "application/octet-stream" in ctype:
                return StreamType.HTTP_VIDEO
        except requests.RequestException as e:
            logger.info("Detect: HTTP probe failed (%s), using heuristics", e)

        path = parsed.path.lower()
        if path.endswith((".jpg", ".jpeg")):
            return StreamType.SNAPSHOT_JPEG
        if "mjpg" in path or "mjpeg" in path:
            return StreamType.MJPEG
        return StreamType.HTTP_VIDEO

    # Unbekanntes Schema -> bester Versuch über PyAV/FFmpeg als HTTP-Video
    return StreamType.HTTP_VIDEO
