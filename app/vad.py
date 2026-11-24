"""Streaming VAD: turns raw 8 kHz PCM into utterance start / end events."""
import collections

import webrtcvad

from .config import settings

FRAME_MS = 20
FRAME_BYTES = 8000 * 2 * FRAME_MS // 1000  # 320


class VAD:
    def __init__(self):
        self.vad = webrtcvad.Vad(settings.vad_aggressiveness)
        self.buf = b""
        self.preroll = collections.deque(maxlen=15)  # ~300 ms kept before speech onset
        self.speech = bytearray()
        self.in_speech = False
        self.voiced = 0          # consecutive voiced frames
        self.silence = 0         # consecutive silent frames

    def feed(self, pcm: bytes):
        """Yield ('start', None) when speech begins, ('end', pcm) when the utterance finishes."""
        self.buf += pcm
        while len(self.buf) >= FRAME_BYTES:
            frame, self.buf = self.buf[:FRAME_BYTES], self.buf[FRAME_BYTES:]
            is_speech = self.vad.is_speech(frame, 8000)
            if not self.in_speech:
                self.preroll.append(frame)
                self.voiced = self.voiced + 1 if is_speech else 0
                if self.voiced * FRAME_MS >= settings.vad_min_speech_ms:
                    self.in_speech, self.silence = True, 0
                    self.speech = bytearray(b"".join(self.preroll))
                    self.preroll.clear()
                    yield ("start", None)
            else:
                self.speech += frame
                self.silence = 0 if is_speech else self.silence + 1
                if self.silence * FRAME_MS >= settings.vad_silence_ms:
                    utt = bytes(self.speech)
                    self.in_speech, self.voiced, self.speech = False, 0, bytearray()
                    yield ("end", utt)
