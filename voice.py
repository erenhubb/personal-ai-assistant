"""Jarvis'in sesi: yazıyı Türkçe konuşmaya çevirip hoparlörden çalar."""

import asyncio
import io
import queue
import re
import threading
import unicodedata

import av
import edge_tts
import numpy as np
import sounddevice as sd

# Erkek sesi "tr-TR-AhmetNeural" şu an Microsoft tarafında çalışmıyor
VOICE = "tr-TR-EmelNeural"
RATE = "+15%"  # Konuşma hızı: "+0%" normal, "+30%" daha hızlı
VOLUME = "+0%"  # Ses yüksekliği: "+0%" normal, "-40%" daha kısık
SAMPLE_RATE = 24000

# Aynı anda iki ses çalmasın (ör. hatırlatma ile cevap çakışmasın)
AUDIO_LOCK = threading.Lock()
# Jarvis konuşurken açık olur; kulak bu sırada duyduğunu Eren'in sesi sanmasın
SPEAKING = threading.Event()


def play(audio):
    with AUDIO_LOCK:
        SPEAKING.set()
        try:
            sd.play(audio, SAMPLE_RATE)
            sd.wait()
        finally:
            SPEAKING.clear()


def clean_for_speech(text):
    """Emoji ve yıldız gibi okunmaması gereken işaretleri temizler."""
    text = re.sub(r"[*_#`>|]", "", text)
    return "".join(c for c in text if unicodedata.category(c) != "So")


def text_to_audio(text):
    """Yazıyı Microsoft'un ücretsiz sesiyle mp3'e, sonra çalınabilir sese çevirir."""
    async def download():
        chunks = []
        async for chunk in edge_tts.Communicate(text, VOICE, rate=RATE, volume=VOLUME).stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    try:
        mp3 = asyncio.run(download())
    except Exception as e:
        print(f"(Ses üretilemedi: {type(e).__name__})")
        return None
    if not mp3:
        return None

    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
    parts = []
    with av.open(io.BytesIO(mp3)) as container:
        for frame in container.decode(audio=0):
            parts.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
    parts.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    return np.concatenate(parts) if parts else None


def speak(text):
    text = clean_for_speech(text).strip()
    if not text:
        return
    audio = text_to_audio(text)
    if audio is not None:
        play(audio)


class SentenceSpeaker:
    """Cevap gelirken her cümleyi bitir bitmez okumaya başlar.

    Bir cümle çalınırken sonraki cümlenin sesi arka planda hazırlanır,
    böylece cevabın tamamını beklemeye gerek kalmaz.
    """

    SENTENCE_END = re.compile(r"(.+?[.!?…:]+)(\s+|$)", re.DOTALL)

    def __init__(self):
        self._buffer = ""
        self._texts = queue.Queue()
        self._audios = queue.Queue()
        self._done = threading.Event()
        self.stopped = False
        threading.Thread(target=self._synthesize, daemon=True).start()
        threading.Thread(target=self._play, daemon=True).start()

    def feed(self, chunk):
        """Claude'dan gelen yazı parçasını ekler; tamamlanan cümleleri sıraya koyar."""
        self._buffer += chunk
        while True:
            match = self.SENTENCE_END.match(self._buffer)
            if not match or not match.group(2):
                break
            self._queue_sentence(match.group(1))
            self._buffer = self._buffer[match.end():]

    def finish(self):
        """Kalan yazıyı da okur ve hepsi bitene kadar bekler."""
        if not self.stopped:
            self._queue_sentence(self._buffer)
            self._texts.put(None)
        self._buffer = ""
        self._done.wait()

    def stop(self):
        """Araya girildiğinde hemen susar ve kalan cümleleri atar."""
        self.stopped = True
        sd.stop()
        self._done.set()

    def _queue_sentence(self, sentence):
        sentence = clean_for_speech(sentence).strip()
        if sentence and not self.stopped:
            self._texts.put(sentence)

    def _synthesize(self):
        while (text := self._texts.get()) is not None and not self.stopped:
            audio = text_to_audio(text)
            if audio is not None:
                self._audios.put(audio)
        self._audios.put(None)

    def _play(self):
        while (audio := self._audios.get()) is not None and not self.stopped:
            play(audio)
        self._done.set()
