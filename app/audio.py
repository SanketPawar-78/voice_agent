try:
    import audioop
except ModuleNotFoundError:  # Python 3.13+
    import audioop_lts as audioop
import base64
import io
import wave

SAMPLE_RATE = 8000          # Exotel streams 8 kHz, 16-bit, mono PCM
CHUNK_BYTES = 3200          # 100 ms; Exotel wants multiples of 320 bytes


def wav_bytes(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm8k(wav: bytes) -> bytes:
    """Decode a WAV (any rate/channels) to 8 kHz mono 16-bit PCM."""
    with wave.open(io.BytesIO(wav)) as w:
        pcm, rate, ch, sw = w.readframes(w.getnframes()), w.getframerate(), w.getnchannels(), w.getsampwidth()
    if sw != 2:
        pcm = audioop.lin2lin(pcm, sw, 2)
    if ch > 1:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if rate != SAMPLE_RATE:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, SAMPLE_RATE, None)
    return pcm


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def chunks(pcm: bytes, size: int = CHUNK_BYTES):
    for i in range(0, len(pcm), size):
        c = pcm[i:i + size]
        if len(c) % 320:                       # pad last chunk to a 320-byte multiple
            c += b"\x00" * (320 - len(c) % 320)
        yield c


class Recorder:
    """Builds a time-aligned stereo recording: left = caller, right = agent."""

    def __init__(self):
        import time
        self._time = time.time
        self.t0 = time.time()
        self.user = bytearray()
        self.agent = bytearray()

    def now_ms(self) -> int:
        return int((self._time() - self.t0) * 1000)

    def _pos(self) -> int:
        p = int((self._time() - self.t0) * SAMPLE_RATE) * 2
        return p - p % 2

    def add_user(self, pcm: bytes):
        pos = self._pos()
        if len(self.user) < pos:
            self.user += bytes(pos - len(self.user))   # fill gaps with silence
        self.user += pcm

    def add_agent(self, pcm: bytes) -> int:
        """Queue agent audio after whatever is still 'playing'. Returns start offset in ms."""
        pos = max(len(self.agent), self._pos())
        if len(self.agent) < pos:
            self.agent += bytes(pos - len(self.agent))
        self.agent += pcm
        return pos // 16

    def cut_agent(self):
        """Barge-in: drop agent audio that had not been played yet."""
        del self.agent[self._pos():]

    def save(self, path: str):
        n = max(len(self.user), len(self.agent))
        u = bytes(self.user) + bytes(n - len(self.user))
        a = bytes(self.agent) + bytes(n - len(self.agent))
        stereo = audioop.add(audioop.tostereo(u, 2, 1, 0), audioop.tostereo(a, 2, 0, 1), 2)
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(stereo)
