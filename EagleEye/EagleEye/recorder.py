import logging
import threading

import av

logger = logging.getLogger(__name__)


class StreamRecorder:
    """Records a running stream to a video file. Receives already-decoded (and
    optionally scaled/rotated) BGR frames from StreamWorker instead of remuxing
    raw packets - this allows recording to work uniformly across all stream types
    (Snapshot/MJPEG/RTSP/HTTP video) regardless of input codec. Downside: re-encoding
    is necessary.

    Initialized lazily on first frame so width/height don't need to be known
    beforehand.
    """

    # Order by preference; libx264 (software) is most compatible,
    # h264_videotoolbox uses HW encoding on macOS if available.
    PREFERRED_CODECS = ("libx264", "h264_videotoolbox", "mpeg4")

    def __init__(self, path: str, fps: float = 25.0):
        """Prepare a recorder for the given output path. The actual encoder
        and container are not opened yet (see _open), since width/height
        are only known once the first frame arrives."""
        self.path = path
        self._fps = max(1.0, min(fps, 60.0))
        self._container = None
        self._stream = None
        self._lock = threading.Lock()
        self._closed = False
        self._frame_count = 0

    def _open(self, width, height):
        """Open the output container and pick the first working encoder
        from PREFERRED_CODECS for the given frame size. Called lazily on
        the first call to write()."""
        self._container = av.open(self.path, mode="w")
        last_err = None
        for codec_name in self.PREFERRED_CODECS:
            try:
                self._stream = self._container.add_stream(
                    codec_name, rate=int(round(self._fps)))
                break
            except Exception as e:  # Codec might not be in this FFmpeg build
                last_err = e
                continue
        if self._stream is None:
            raise RuntimeError(f"No suitable video encoder found: {last_err}")

        # Width/height must be even for most encoders
        self._stream.width = width - (width % 2)
        self._stream.height = height - (height % 2)
        self._stream.pix_fmt = "yuv420p"

    def write(self, bgr_frame):
        """Encode and mux a single decoded BGR frame. Opens the container
        lazily on the first call, then re-encodes and writes every
        subsequent frame. Fails silently (logging the error) so a broken
        recorder never brings down the decode thread feeding it."""
        with self._lock:
            if self._closed:
                return
            h, w = bgr_frame.shape[:2]
            if self._container is None:
                try:
                    self._open(w, h)
                except Exception:
                    logger.exception("Recorder failed to start (%s)", self.path)
                    self._closed = True
                    return

            if w != self._stream.width or h != self._stream.height:
                bgr_frame = bgr_frame[:self._stream.height, :self._stream.width]

            frame = av.VideoFrame.from_ndarray(bgr_frame, format="bgr24")
            frame.pts = self._frame_count
            self._frame_count += 1
            try:
                for packet in self._stream.encode(frame):
                    self._container.mux(packet)
            except Exception:
                logger.exception("Error encoding frame (%s)", self.path)

    def close(self):
        """Flush any buffered frames and close the output file. Safe to
        call multiple times."""
        with self._lock:
            if self._closed:
                return
            if self._container is not None:
                try:
                    for packet in self._stream.encode(None):  # flush remaining frames
                        self._container.mux(packet)
                except Exception:
                    logger.exception("Error closing recording (%s)", self.path)
                finally:
                    self._container.close()
            self._closed = True
