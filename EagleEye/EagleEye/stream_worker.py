import logging
import platform
import threading
import time
from urllib.parse import quote, urlsplit, urlunsplit

import cv2
import numpy as np
import requests

from .detector import detect_stream_type
from .frame_buffer import LatestFrameBuffer
from .recorder import StreamRecorder
from .stream_config import StreamConfig
from .stream_types import StreamType

logger = logging.getLogger(__name__)

try:
    import av
    av.logging.set_level(av.logging.PANIC)
    HAVE_PYAV = True
except ImportError:  # pragma: no cover
    HAVE_PYAV = False

try:
    import sounddevice as sd
    HAVE_SOUNDDEVICE = True
except (ImportError, OSError):  # pragma: no cover
    HAVE_SOUNDDEVICE = False

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2

# Platform -> FFmpeg HW accel device type. NVDEC runs on Linux/Windows via
# "cuda" device type, macOS uses VideoToolbox, Linux alternatively VAAPI.
_HWACCEL_BY_PLATFORM = {
    "Darwin": ("videotoolbox",),
    "Windows": ("cuda", "d3d11va"),
    "Linux": ("vaapi", "cuda"),
}


def _build_hwaccel():
    """Create a HW accel context for the current platform with software fallback
    if the device is unavailable. Returns None if PyAV doesn't support the HWAccel
    API or no suitable device type is known; callers automatically fall back to
    software decoding."""
    if not hasattr(av.codec, "hwaccel"):
        return None
    device_types = _HWACCEL_BY_PLATFORM.get(platform.system(), ())
    for device_type in device_types:
        try:
            return av.codec.hwaccel.HWAccel(
                device_type=device_type, allow_software_fallback=True)
        except Exception:
            continue
    return None


class StreamWorker(threading.Thread):
    """Decoding thread for a camera stream. Continuously decodes and writes
    frames to a LatestFrameBuffer. Automatically reconnects on errors."""

    def __init__(self, config: StreamConfig, buffer: LatestFrameBuffer, status_cb=None):
        super().__init__(daemon=True)
        self.config = config
        self.buffer = buffer
        self.status_cb = status_cb  # callback(stream_id, status_str, extra_dict)
        self._stop_event = threading.Event()
        self._resolved_type = None
        self._recorder = None
        self._recorder_lock = threading.Lock()

    def stop(self):
        self._stop_event.set()
        self.stop_recording()

    # Recording is independent of stream type; it hooks into the decoded frames
    # after post-processing.
    def start_recording(self, path):
        with self._recorder_lock:
            old = self._recorder
            self._recorder = StreamRecorder(path, fps=self.config.max_fps)
        if old:
            old.close()
        self._report("recording_started", path=path)

    def stop_recording(self):
        with self._recorder_lock:
            rec = self._recorder
            self._recorder = None
        if rec:
            rec.close()
            self._report("recording_stopped")

    def is_recording(self):
        with self._recorder_lock:
            return self._recorder is not None

    def _feed_recorder(self, frame):
        with self._recorder_lock:
            rec = self._recorder
        if rec is not None:
            rec.write(frame)

    def _report(self, status, **extra):
        if self.status_cb:
            try:
                self.status_cb(self.config.id, status, extra)
            except Exception:
                logger.exception("status_cb error")

    def _resolve_type(self):
        st = self.config.effective_stream_type()
        if st == StreamType.AUTO:
            st = detect_stream_type(self.config.effective_url())
            self._report("detected", stream_type=st.value)
        self._resolved_type = st
        return st

    def run(self):
        while not self._stop_event.is_set():
            st = self._resolve_type()
            try:
                if st == StreamType.SNAPSHOT_JPEG:
                    self._run_snapshot()
                elif st == StreamType.MJPEG:
                    self._run_mjpeg()
                elif st in (StreamType.HTTP_VIDEO, StreamType.RTSP_TCP,
                            StreamType.RTSP_UDP, StreamType.RTSP_MULTICAST):
                    self._run_av(st)
                else:
                    self._report("error", message=f"Unknown stream type: {st}")
                    return
            except Exception as e:
                logger.exception("Stream %s failed", self.config.id)
                self._report("error", message=str(e))

            if self._stop_event.is_set():
                break
            self._report("reconnecting")
            self._stop_event.wait(max(0.2, self.config.reconnect_delay_s))

    # Snapshot JPEG: simple HTTP GET polling
    def _run_snapshot(self):
        session = requests.Session()
        auth = self._auth()
        interval = max(0.02, self.config.snapshot_interval_ms / 1000.0)
        url = self.config.effective_url()
        self._report("connected")
        while not self._stop_event.is_set():
            t0 = time.time()
            try:
                resp = session.get(url, auth=auth, timeout=5)
                resp.raise_for_status()
                arr = np.frombuffer(resp.content, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    frame = self._post_process(frame)
                    self._feed_recorder(frame)
                    self.buffer.set(frame, fps=1.0 / max(1e-3, time.time() - t0))
            except Exception as e:
                if not self.config.mute_errors:
                    self._report("error", message=str(e))
                self._stop_event.wait(min(interval, self.config.reconnect_delay_s))
                continue
            elapsed = time.time() - t0
            sleep_left = interval - elapsed
            if sleep_left > 0:
                self._stop_event.wait(sleep_left)

    # MJPEG: manually parse multipart/x-mixed-replace (no FFmpeg needed,
    # making it lightweight and robust against broken boundaries)
    def _run_mjpeg(self):
        auth = self._auth()
        resp = requests.get(self.config.effective_url(), auth=auth, stream=True, timeout=10)
        resp.raise_for_status()
        self._report("connected")
        buf = b""
        last_t = time.time()
        max_buf = 8_000_000  # Safety limit against unbounded growth

        for chunk in resp.iter_content(chunk_size=8192):
            if self._stop_event.is_set():
                break
            if not chunk:
                continue
            buf += chunk

            while True:
                start = buf.find(b"\xff\xd8")  # JPEG Start-of-Image marker
                end = buf.find(b"\xff\xd9")    # JPEG End-of-Image marker
                if start != -1 and end != -1 and end > start:
                    jpg = buf[start:end + 2]
                    buf = buf[end + 2:]
                    arr = np.frombuffer(jpg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        now = time.time()
                        fps = 1.0 / max(1e-3, now - last_t)
                        last_t = now
                        frame = self._post_process(frame)
                        self._feed_recorder(frame)
                        self.buffer.set(frame, fps=fps)
                else:
                    break

            if len(buf) > max_buf:
                buf = buf[-500_000:]  # Discard garbage, continue

    # PyAV/FFmpeg: HTTP H.264/H.265, RTSP TCP/UDP/Multicast
    def _run_av(self, st: StreamType):
        if not HAVE_PYAV:
            raise RuntimeError("PyAV ('av') is not installed: pip install av")

        options = {
            "stimeout": "5000000",   # 5s socket timeout (microseconds, RTSP)
            "max_delay": "500000",   # 0.5s
            "fflags": "nobuffer",
            "flags": "low_delay",
        }
        if st == StreamType.RTSP_TCP:
            options["rtsp_transport"] = "tcp"
        elif st == StreamType.RTSP_UDP:
            options["rtsp_transport"] = "udp"
        elif st == StreamType.RTSP_MULTICAST:
            options["rtsp_transport"] = "udp_multicast"

        options.update(self.config.extra_options or {})

        url = self._url_with_credentials()
        container = av.open(url, options=options, timeout=10)
        self._report("connected")
        try:
            vstream = next(s for s in container.streams if s.type == "video")
            vstream.thread_type = "AUTO"  # FFmpeg internal multithreaded decoding

            if self.config.hw_accel:
                hwaccel = _build_hwaccel()
                if hwaccel is not None:
                    try:
                        vstream.codec_context.hwaccel = hwaccel
                        self._report("hwaccel", device=hwaccel.device_type)
                    except Exception as e:
                        logger.warning(
                            "HW decoding not available for %s (%s), using software decoding",
                            self.config.id, e)
                else:
                    logger.info(
                        "No suitable HW decoding for this platform/PyAV version, "
                        "using software decoding")

            last_t = time.time()

            # Audio: only if desired, a track is present, and sounddevice/PortAudio
            # are available. Audio path errors must never crash the video path
            # (try/except per packet).
            astream = None
            audio_resampler = None
            audio_out = None
            if self.config.audio_enabled:
                astream = next((s for s in container.streams if s.type == "audio"), None)
                if astream is None:
                    logger.info("Audio desired but no audio track in %s", self.config.id)
                elif not HAVE_SOUNDDEVICE:
                    logger.warning(
                        "sounddevice/PortAudio not available - audio skipped "
                        "(pip install sounddevice)")
                    astream = None
                else:
                    astream.thread_type = "AUTO"
                    audio_resampler = av.AudioResampler(
                        format="s16", layout="stereo", rate=AUDIO_SAMPLE_RATE)
                    try:
                        audio_out = sd.OutputStream(
                            samplerate=AUDIO_SAMPLE_RATE, channels=AUDIO_CHANNELS, dtype="int16")
                        audio_out.start()
                        self._report("audio_started")
                    except Exception as e:
                        logger.warning("Could not open audio output device: %s", e)
                        audio_out = None
                        astream = None

            demux_streams = [vstream] + ([astream] if astream is not None else [])

            try:
                for packet in container.demux(demux_streams):
                    if self._stop_event.is_set():
                        break

                    if packet.stream.type == "video":
                        for frame in packet.decode():
                            if self._stop_event.is_set():
                                break
                            img = frame.to_ndarray(format="bgr24")
                            img = self._post_process(img)
                            now = time.time()
                            fps = 1.0 / max(1e-3, now - last_t)
                            last_t = now
                            self._feed_recorder(img)
                            self.buffer.set(img, fps=fps)

                    elif packet.stream.type == "audio" and audio_out is not None:
                        try:
                            for aframe in packet.decode():
                                for resampled in audio_resampler.resample(aframe):
                                    pcm = resampled.to_ndarray().reshape(-1, AUDIO_CHANNELS)
                                    audio_out.write(pcm)
                        except Exception:
                            logger.exception(
                                "Audio playback error for %s, disabling audio for this session",
                                self.config.id)
                            audio_out.close()
                            audio_out = None  # further audio packets are simply ignored
            finally:
                if audio_out is not None:
                    audio_out.stop()
                    audio_out.close()
        finally:
            container.close()

    def _auth(self):
        if self.config.username:
            return (self.config.username, self.config.password)
        return None

    def _url_with_credentials(self) -> str:
        """FFmpeg (and thus PyAV) does not accept separate auth options for RTSP
        and HTTP video - credentials must be part of the URL (rtsp://user:pass@host:port/path).
        Applies to the currently active variant (main or sub stream). If the user
        already provided credentials in the URL, nothing is changed. Special characters
        in username/password are URL-encoded to prevent '@' or ':' in passwords
        from breaking the URL."""
        cfg = self.config
        base_url = cfg.effective_url()
        if not cfg.username:
            return base_url

        parts = urlsplit(base_url)
        if "@" in parts.netloc:
            # URL enthält schon Zugangsdaten -> nicht überschreiben
            return base_url

        user = quote(cfg.username, safe="")
        pwd = quote(cfg.password, safe="") if cfg.password else ""
        credentials = f"{user}:{pwd}" if pwd else user
        new_netloc = f"{credentials}@{parts.netloc}"
        return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))

    def _post_process(self, frame):
        cfg = self.config
        if cfg.scale != 1.0:
            frame = cv2.resize(frame, None, fx=cfg.scale, fy=cfg.scale,
                                interpolation=cv2.INTER_AREA)
        if cfg.rotate == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif cfg.rotate == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif cfg.rotate == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame
