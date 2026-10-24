"""Jarvis'in şu anki sesini dinlemek için küçük deneme."""

import voice

if __name__ == "__main__":
    print(f"Ses: {voice.VOICE}, hız: {voice.RATE}")
    voice.speak("Merhaba Yusuf, ben Jarvis. Bugün sana nasıl yardımcı olabilirim?")
