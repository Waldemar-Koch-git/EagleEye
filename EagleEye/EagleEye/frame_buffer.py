import threading


class LatestFrameBuffer:
    """Thread-safe single-slot buffer that only keeps the most recent frame.
    Intentionally NOT a queue: old frames are discarded -> constant low latency
    and constant memory usage regardless of source speed or GUI consumption rate."""

    def __init__(self):
        """Create an empty buffer with no frame stored yet."""
        self._lock = threading.Lock()
        self._frame = None      # numpy array (BGR)
        self._frame_id = 0
        self._fps = 0.0

    def set(self, frame, fps=None):
        """Store the latest decoded frame, overwriting any previous one.

        Increments the internal frame id so consumers polling via get()
        can detect that a new frame has arrived. fps is optional and, if
        given, updates the reported source frame rate.
        """
        with self._lock:
            self._frame = frame
            self._frame_id += 1
            if fps is not None:
                self._fps = fps

    def get(self, last_seen_id=-1):
        """Returns (frame, frame_id, fps) or (None, last_seen_id, fps)
        if no new frame has arrived since last_seen_id."""
        with self._lock:
            if self._frame_id == last_seen_id:
                return None, last_seen_id, self._fps
            return self._frame, self._frame_id, self._fps
