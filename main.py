"""Sesli kişisel AI asistanı Jarvis."""

import json
import re
import sys
import threading
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import hands
import voice

# "python main.py --yazi" ile açarsan klavyeden yazarak konuşursun
VOICE_MODE = "--yazi" not in sys.argv
if VOICE_MODE:
    import ear

# .env dosyasındaki ANTHROPIC_API_KEY değerini yükler
load_dotenv()

# Haiku hızlı ve ucuz; daha akıllı ama yavaş cevaplar için "claude-opus-5-5"
MODEL = "claude-haiku-5-5"
SYSTEM_PROMPT = (
    "Senin adın Jarvis. Yusuf'un kişisel asistanısın. "
    "Türkçe, samimi ve kısa cevaplar ver. "
    "Cevapların sesli okunacak; emoji, madde işareti ve başlık kullanma, "
    "düz konuşma cümleleriyle yaz. "
    "Bilgisayarda uygulama, klasör, dosya ya da web sitesi açman istenirse araçlarını kullan "
    "ve ne yaptığını tek kısa cümleyle söyle. Bir şeyi açamadıysan nedenini söyle. "
    "Önceki konuşmalarınızın tamamı aşağıda; Yusuf'un anlattıklarını hatırla."
)
MAX_TOOL_ROUNDS = 6  # Bir soruda en fazla bu kadar araç turu
# Bütün konuşmalar bu dosyada saklanır. Silersen Jarvis her şeyi unutur.
MEMORY_FILE = Path(__file__).parent / "hafiza.json"

QUIT_WORDS = ("q", "quit", "exit", "çık")  # Yazılı modda programı kapatır
# Sesli modda uyutur: "kapan", "kapanabilirsin", "uyu artık", "tamam sus" gibi
SLEEP_WORD = re.compile(r"(kapan|kapat|uyu|sus|görüşürüz|yeter|hoşça ?kal|bay ?bay)\w*")
FILLER_WORDS = {"tamam", "artık", "lütfen", "hadi", "sen", "biraz", "şimdi", "peki"}
SHUTDOWN_WORDS = ("kendini kapat", "tamamen kapan")  # Sesli modda programı kapatır
AUTO_SLEEP_SECONDS = 40  # Uyanıkken bu kadar kimse konuşmazsa kendiliğinden uyur

# Claude'un Jarvis'i kendisi uyutabilmesi için (ör. "tamam bu kadar, teşekkürler")
SLEEP_TOOL = {
    "name": "go_to_sleep",
    "description": "Jarvis'i uyku moduna alır; Yusuf tekrar 'Jarvis' diyene kadar dinlemeyi bırakır. "
                   "Yusuf konuşmayı bitirdiğinde, kapanmanı, susmanı ya da uyumanı istediğinde kullan.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "strict": True,
    "eager_input_streaming": True,
}
# Jarvis'in kullanabileceği bütün araçlar; konuşma boyunca değişmez
ALL_TOOLS = hands.TOOLS + ([SLEEP_TOOL] if VOICE_MODE else [])


def load_history():
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    return []


def save_history(history):
    MEMORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def stream_reply(client, messages, speaker):
    """Claude'un bir cevabını kelime kelime ekrana yazar ve sesli okur.
    (yazı, tam_cevap) döndürür; araya girildiyse tam_cevap None olur."""
    text_so_far = ""
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=ALL_TOOLS,
        output_config={"effort": "low"},
        # Eski konuşmaları önbelleğe alır; uzun hafızada maliyeti düşürür
        cache_control={"type": "ephemeral"},
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            if speaker.stopped:
                return text_so_far, None  # Araya girildi, cevabın kalanına gerek yok
            print(text, end="", flush=True)
            speaker.feed(text)
            text_so_far += text
        return text_so_far, stream.get_final_message()


def ask_claude(client, history, user_input):
    """Soruyu Claude'a sorar, cevabı yazarken sesli okur ve hafızaya kaydeder.
    Claude bilgisayarda bir şey açmak isterse araçları çalıştırıp sonucu ona geri verir.

    (araya_girilen_ses, uyusun_mu) döndürür: Jarvis konuşurken araya girersen susar ve
    söylediğin sesi verir; Claude Jarvis'i uyutmak isterse uyusun_mu True olur.
    """
    history.append({"role": "user", "content": user_input})
    # Bu turun mesajları; araç kullanımı bitene kadar sadece sona eklenir
    messages = list(history)
    speaker = voice.SentenceSpeaker()
    interrupt = ear.InterruptListener(on_interrupt=speaker.stop) if VOICE_MODE else None
    reply_parts = []
    actions = []
    refused = False
    wants_sleep = False

    print("Jarvis: ", end="", flush=True)
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            text, response = stream_reply(client, messages, speaker)
            if text:
                reply_parts.append(text)
            if response is None or response.stop_reason != "tool_use":
                refused = response is not None and response.stop_reason == "refusal"
                break

            # Claude bir şey açmak istiyor: araçları çalıştır, sonuçları geri gönder
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == SLEEP_TOOL["name"]:
                    ok, result = True, "Uyku moduna geçiliyor."
                    wants_sleep = True
                else:
                    ok, result = hands.run_tool(block.name, block.input)
                print(f"\n  [{block.name}] {result}", flush=True)
                actions.append(result)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": result, "is_error": not ok})
            messages.append({"role": "user", "content": results})
    except anthropic.AuthenticationError:
        print("\nAPI anahtarı geçersiz. .env dosyasını kontrol et.")
    except anthropic.RateLimitError:
        print("\nÇok hızlı istek gönderildi, biraz bekleyip tekrar dene.")
    except anthropic.APIStatusError as e:
        print(f"\nAPI hatası ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        print("\nİnternet bağlantısı kurulamadı.")
    except ValueError:
        print("\n(Claude'dan bozuk bir araç isteği geldi, tekrar dener misin?)")
    speaker.finish()
    if interrupt:
        interrupt.stop()
    print("\n")

    if speaker.stopped:
        print("(Sözümü kestin, dinliyorum.)")
    if refused:
        print("(Bu isteğe cevap verilemedi.)\n")

    # Hafızaya sadece konuşmanın yazısını ve yapılan işleri kaydediyoruz
    reply = " ".join(reply_parts).strip()
    if actions:
        reply = (reply + "\n" if reply else "") + "(Yapılanlar: " + " | ".join(actions) + ")"
    if not reply or refused:
        history.pop()
    else:
        history.append({"role": "assistant", "content": reply})
        save_history(history)
    return (interrupt.audio if interrupt else None), wants_sleep


def as_command(text):
    """'Jarvis, kapan!' gibi bir cümleyi 'kapan' haline getirir."""
    return re.sub(r"[.,!?]", " ", ear.WAKE_WORD.sub("", text)).lower().strip()


def is_sleep_command(command):
    """'kapan', 'tamam kapan artık', 'uyuyabilirsin' -> evet; 'Spotify'ı kapat' -> hayır."""
    words = [w for w in command.split() if w not in FILLER_WORDS]
    return len(words) == 1 and SLEEP_WORD.fullmatch(words[0]) is not None


def run_text_mode(client, history):
    print(f"Jarvis hazır ({len(history) // 2} eski mesaj hatırlanıyor). Çıkmak için 'q' yaz.\n")
    while True:
        user_input = input("Sen: ").strip()
        if not user_input:
            continue
        if user_input.lower() in QUIT_WORDS:
            break
        ask_claude(client, history, user_input)


def run_voice_mode(client, history):
    print("Kulaklarım hazırlanıyor, birkaç saniye sürebilir...")
    device = ear.load_models()
    print(f"Jarvis hazır, kulaklar {device} üzerinde ({len(history) // 2} eski mesaj hatırlanıyor).")

    awake = False
    interrupted_audio = None  # Jarvis'in sözünü kestiysen söylediğin
    while True:
        if interrupted_audio is not None:
            text = ear.transcribe(interrupted_audio)
            interrupted_audio = None
        else:
            if not awake:
                print("\n(Uyuyorum. Uyandırmak için 'Jarvis' de.)")
            text = ear.listen(timeout=AUTO_SLEEP_SECONDS if awake else None)
            if text is None:
                print("(Bir süredir kimse konuşmadı, uyuyorum.)")
                awake = False
                continue
        if not text:
            continue

        if not awake:
            woke, text = ear.split_wake_word(text)
            if not woke:
                continue
            awake = True
            ear.beep()
            print("(Uyandım. Uyutmak için 'kapan' de.)\n")
            # "Jarvis, hava nasıl?" dendiyse soruyu hemen cevapla
            if not text:
                continue

        print(f"Sen: {text}")
        command = as_command(text)
        if any(words in command for words in SHUTDOWN_WORDS):
            voice.speak("Kendimi kapatıyorum, görüşürüz Yusuf.")
            break
        if is_sleep_command(command):
            voice.speak("Tamam, uyuyorum.")
            awake = False
            continue
        interrupted_audio, wants_sleep = ask_claude(client, history, text)
        if wants_sleep:
            awake = False


def main():
    client = anthropic.Anthropic()
    history = load_history()
    # Uygulama listesini arka planda hazırla ki ilk "aç" isteği beklemesin
    threading.Thread(target=hands.load_apps, daemon=True).start()
    if VOICE_MODE:
        run_voice_mode(client, history)
    else:
        run_text_mode(client, history)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGörüşürüz!")
