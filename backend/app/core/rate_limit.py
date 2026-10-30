import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class LoginLimiter:
    """Stops password guessing: after `max_failures` wrong attempts for the same email from the
    same address inside the window, further attempts are refused until the window passes.

    It lives in memory, so it works for a single API process. With several processes it would
    need a shared store such as Redis (noted for M14).
    """

    def __init__(
        self,
        max_failures: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str) -> deque[float]:
        attempts = self._failures[key]
        cutoff = self._clock() - self._window
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed, or 0 if one is allowed now."""
        with self._lock:
            attempts = self._trim(key)
            if len(attempts) < self._max:
                return 0
            return max(1, int(attempts[0] + self._window - self._clock()) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._trim(key).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
