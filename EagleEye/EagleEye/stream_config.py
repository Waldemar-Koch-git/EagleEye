from dataclasses import dataclass, field
from .stream_types import StreamType


@dataclass
class StreamConfig:
    id: str
    name: str
    url: str
    stream_type: StreamType = StreamType.AUTO

    # Sub-stream (e.g. lower resolution for multi-stream view) - optional.
    # Many RTSP cameras offer a second, lighter stream at a separate URL.
    sub_url: str = ""
    sub_stream_type: StreamType = StreamType.AUTO
    active_variant: str = "main"   # "main" or "sub" - which stream is currently active

    # Enable/disable - disabled streams don't run (no worker active)
    enabled: bool = True

    # Snapshot-specific
    snapshot_interval_ms: int = 200

    # Performance / Decoding
    scale: float = 1.0          # Scaling factor after decoding (0.1 - 1.0)
    max_fps: int = 30           # Display FPS cap (independent of source)
    hw_accel: bool = False      # Try hardware decoding (if available)

    # Connection
    reconnect_delay_s: float = 2.0
    username: str = ""
    password: str = ""

    # Presentation
    rotate: int = 0              # 0 / 90 / 180 / 270
    mute_errors: bool = False    # Don't report errors loudly (e.g. known flaky cameras)
    show_fps: bool = False       # Show FPS display on the tile

    # Audio - only relevant for RTSP/HTTP video (PyAV) if the source provides
    # an audio track. For clarity, only one stream plays audio at a time
    # (see MainWindow._set_audio_stream).
    audio_enabled: bool = False

    extra_options: dict = field(default_factory=dict)  # E.g. additional FFmpeg options

    def has_sub_stream(self) -> bool:
        return bool(self.sub_url.strip())

    def effective_url(self) -> str:
        if self.active_variant == "sub" and self.has_sub_stream():
            return self.sub_url
        return self.url

    def effective_stream_type(self) -> StreamType:
        if self.active_variant == "sub" and self.has_sub_stream():
            return self.sub_stream_type
        return self.stream_type

    def other_variant(self) -> str:
        return "sub" if self.active_variant == "main" else "main"
