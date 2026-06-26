from scanners.base import RateLimiter


def test_compute_block_until_reads_retry_after():
    now = 1000.0
    assert RateLimiter.compute_block_until(429, {"Retry-After": "30"}, now) == 1030.0


def test_compute_block_until_reads_ratelimit_reset_when_remaining_zero():
    now = 1000.0
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1200"}
    assert RateLimiter.compute_block_until(200, headers, now) == 1200.0


def test_compute_block_until_no_block_when_quota_remains():
    headers = {"X-RateLimit-Remaining": "57", "X-RateLimit-Reset": "1200"}
    assert RateLimiter.compute_block_until(200, headers, 1000.0) == 0.0


def test_compute_block_until_falls_back_on_bare_403():
    assert RateLimiter.compute_block_until(403, {}, 1000.0) == 1060.0


def test_note_response_persists_block_to_store():
    class FakeStore:
        def __init__(self):
            self.calls = []
            self._v = None

        def get_block_until(self, r):
            return self._v

        def set_block_until(self, r, ts):
            self.calls.append((r, ts))
            self._v = ts

    store = FakeStore()
    rl = RateLimiter(resource="github_search", store=store)
    rl.note_response(429, {"Retry-After": "0"})  # 0s -> no future block
    rl.note_response(429, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"})
    assert store.calls and store.calls[-1][0] == "github_search"
    assert store.calls[-1][1] == 9999999999.0
