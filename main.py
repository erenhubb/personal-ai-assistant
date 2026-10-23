"""Terminalde çalışan basit kişisel AI asistanı."""

import json
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# .env dosyasındaki ANTHROPIC_API_KEY değerini yükler
load_dotenv()

MODEL = "claude-opus-5-5"
SYSTEM_PROMPT = (
    "Senin adın Jarvis. Yusuf'un kişisel asistanısın. "
    "Türkçe, samimi ve kısa cevaplar ver. "
    "Önceki konuşmalarınızın tamamı aşağıda; Yusuf'un anlattıklarını hatırla."
)
# Bütün konuşmalar bu dosyada saklanır. Silersen Jarvis her şeyi unutur.
MEMORY_FILE = Path(__file__).parent / "hafiza.json"


def load_history():
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    return []


def save_history(history):
    MEMORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    client = anthropic.Anthropic()
    history = load_history()

    print(f"Jarvis hazır ({len(history) // 2} eski mesaj hatırlanıyor). Çıkmak için 'q' yaz.\n")
    while True:
        user_input = input("Sen: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("q", "quit", "exit", "çık"):
            break

        history.append({"role": "user", "content": user_input})

        print("Jarvis: ", end="", flush=True)
        try:
            with client.beta.messages.stream(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low"},
                # Eski konuşmaları önbelleğe alır; uzun hafızada maliyeti düşürür
                cache_control={"type": "ephemeral"},
                # Güvenlik filtresi isteği reddederse başka bir modele otomatik geçer
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=history,
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                response = stream.get_final_message()
        except anthropic.AuthenticationError:
            print("\nAPI anahtarı geçersiz. .env dosyasını kontrol et.")
            history.pop()
            continue
        except anthropic.RateLimitError:
            print("\nÇok hızlı istek gönderildi, biraz bekleyip tekrar dene.")
            history.pop()
            continue
        except anthropic.APIStatusError as e:
            print(f"\nAPI hatası ({e.status_code}): {e.message}")
            history.pop()
            continue
        except anthropic.APIConnectionError:
            print("\nİnternet bağlantısı kurulamadı.")
            history.pop()
            continue
        print("\n")

        if response.stop_reason == "refusal":
            print("(Bu isteğe cevap verilemedi.)\n")
            history.pop()
            continue

        # Dosyaya sadece cevabın yazı kısmını kaydediyoruz
        reply = "".join(b.text for b in response.content if b.type == "text")
        if not reply:
            history.pop()
            continue
        history.append({"role": "assistant", "content": reply})
        save_history(history)


if __name__ == "__main__":
    main()
