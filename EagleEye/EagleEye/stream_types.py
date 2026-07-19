from enum import Enum


class StreamType(Enum):
    AUTO = "auto"                  # auto-detect
    SNAPSHOT_JPEG = "snapshot_jpeg"  # single JPEG per poll interval
    MJPEG = "mjpeg"                 # multipart/x-mixed-replace server push
    HTTP_VIDEO = "http_video"       # H.264/H.265 progressive over HTTP
    RTSP_TCP = "rtsp_tcp"           # RTSP over TCP
    RTSP_UDP = "rtsp_udp"           # RTSP over UDP (unicast)
    RTSP_MULTICAST = "rtsp_multicast"  # RTSP over UDP multicast
