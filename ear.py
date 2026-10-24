"""Jarvis'in kulağı: konuşmanı kaydeder ve yazıya çevirir."""

import os
import re
import site
import threading
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
FRAME = 1280  # 80 milisaniyelik ses parçası
FRAME_SECONDS = FRAME / SAMPLE_RATE
WHISPER_MODEL = "small"  # Daha hızlı ama daha az doğru: "base"
SILENCE_SECONDS = 0.6  # Bu kadar susunca cümlen bitti sayılır
PRE_ROLL_FRAMES = 4  # Konuşma başlamadan önceki 0.3 saniyeyi de tut, ilk hece kesilmesin
MAX_SECONDS = 20

# Araya girme: Jarvis konuşurken bu kadar yüksek sesle bu kadar süre konuşursan susar.
# Hoparlörde Jarvis kendi sesiyle susuyorsa INTERRUPT_MIN_LEVEL'ı artır.
INTERRUPT_MULTIPLIER = 8
INTERRUPT_MIN_LEVEL = 2000
INTERRUPT_FRAMES = 4  # 0.3 saniye

# Whisper "Jarvis"i bazen "Carvis", "Cervis" diye yazabiliyor, hepsini kabul ediyoruz
WAKE_WORD = re.compile(r"\b(?:j|c|ç|dj)[aâe]r[vw][iıy][sşz](?:['’]?\w+)?", re.IGNORECASE)
# Whisper'a bu kelimeleri duyma ihtimalinin yüksek olduğunu söyler
HOTWORDS = "Jarvis, kapan"

_whisper = None
_noise_floor = 200.0  # Odadaki sessizliğin seviyesi, dinlerken güncellenir


def add_cuda_libraries():
    """pip ile kurulan NVIDIA kütüphanelerini Windows'un bulabileceği yere ekler."""
    for packages in site.getsitepackages():
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
            folder = Path(packages) / sub
            if folder.is_dir():
                os.add_dll_directory(str(folder))
                os.environ["PATH"] = str(folder) + os.pathsep + os.environ["PATH"]


def load_models():
    """Konuşma tanıma modelini bir kere yükler: önce ekran kartında, olmazsa işlemcide."""
    global _whisper
    if _whisper is not None:
        return "zaten yüklü"
    try:
        add_cuda_libraries()
        _whisper = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
        # Isınma turu: kütüphaneler eksikse burada hata verir. İlk çalıştırmada ekran kartı
        # kendini hazırlarken bir kereliğine 1 dakika kadar sürebilir, sonra hızlıdır.
        list(_whisper.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="tr")[0])
        return "ekran kartı"
    except Exception:
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        return "işlemci"


def beep():
    """Dinlemeye başladığını belli eden kısa bir ses."""
    t = np.linspace(0, 0.12, int(0.12 * SAMPLE_RATE), endpoint=False)
    sd.play(0.2 * np.sin(2 * np.pi * 880 * t).astype(np.float32), SAMPLE_RATE)
    sd.wait()


def open_mic():
    return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME)


def read_frame(stream):
    frame, _ = stream.read(FRAME)
    frame = frame[:, 0].copy()
    return frame, float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def speech_threshold():
    return max(_noise_floor * 3, 300)


def record_rest(stream, frames):
    """Konuşma başladıktan sonra susana kadar kaydetmeye devam eder."""
    silent_for = 0.0
    while silent_for < SILENCE_SECONDS and len(frames) * FRAME_SECONDS < MAX_SECONDS:
        frame, level = read_frame(stream)
        frames.append(frame)
        silent_for = 0.0 if level > speech_threshold() else silent_for + FRAME_SECONDS
    return np.concatenate(frames).astype(np.float32) / 32768


def record_utterance(timeout=None):
    """Konuşmaya başlamanı bekler, susunca kaydı döndürür.
    timeout saniye içinde kimse konuşmazsa None döndürür (timeout yoksa sonsuza kadar bekler)."""
    global _noise_floor
    noise = deque(maxlen=25)  # Son 2 saniyelik ortam gürültüsü
    pre_roll = deque(maxlen=PRE_ROLL_FRAMES)
    waited = 0.0

    with open_mic() as stream:
        while timeout is None or waited < timeout:
            frame, level = read_frame(stream)
            waited += FRAME_SECONDS
            if len(noise) >= 4 and level > speech_threshold():
                return record_rest(stream, [*pre_roll, frame])
            noise.append(level)
            pre_roll.append(frame)
            if len(noise) >= 4:
                _noise_floor = float(np.median(noise))
    return None


def transcribe(audio):
    segments, _ = _whisper.transcribe(
        audio, language="tr", beam_size=1, vad_filter=True, hotwords=HOTWORDS
    )
    return " ".join(s.text.strip() for s in segments).strip()


def listen(timeout=None):
    """Bir cümle dinler ve yazı olarak döndürür (anlaşılmazsa boş).
    timeout içinde kimse konuşmazsa None döndürür."""
    audio = record_utterance(timeout)
    return None if audio is None else transcribe(audio)


def split_wake_word(text):
    """'Jarvis' geçiyorsa (True, sonrasında söylenen), geçmiyorsa (False, '') döndürür."""
    match = WAKE_WORD.search(text)
    if not match:
        return False, ""
    rest = text[match.end():].strip(" ,.!?")
    return True, rest


class InterruptListener:
    """Jarvis konuşurken mikrofonu dinler; sen konuşmaya başlarsan Jarvis'i susturur
    ve söylediğini kaydeder."""

    def __init__(self, on_interrupt):
        self._on_interrupt = on_interrupt
        self._stop = threading.Event()
        self.triggered = threading.Event()
        self.audio = None  # Araya girdiysen söylediğin buraya kaydedilir
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        level_needed = max(_noise_floor * INTERRUPT_MULTIPLIER, INTERRUPT_MIN_LEVEL)
        recent = deque(maxlen=PRE_ROLL_FRAMES + INTERRUPT_FRAMES)
        loud_frames = 0
        with open_mic() as stream:
            while not self._stop.is_set():
                frame, level = read_frame(stream)
                recent.append(frame)
                loud_frames = loud_frames + 1 if level > level_needed else 0
                if loud_frames >= INTERRUPT_FRAMES:
                    self.triggered.set()
                    self._on_interrupt()
                    self.audio = record_rest(stream, list(recent))
                    return

    def stop(self):
        """Araya girilmediyse dinlemeyi bırakır; girildiyse kaydın bitmesini bekler."""
        if not self.triggered.is_set():
            self._stop.set()
        self._thread.join()
