import os
import json
import logging

logger = logging.getLogger(__name__)

# ── Model tanımları ───────────────────────────────────────────────────────────
THINKING_MODELS = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]  # Düşünen — yedekli
FAST_MODEL      = "gemini-2.5-flash-lite-preview-06-17"      # Hızlı

# ── Limit tanımları ───────────────────────────────────────────────────────────
LIMIT_THINKING = 3000
LIMIT_FAST     = 500
LIMIT_TOTAL    = 3500

MODE_LABELS = {"thinking": "Düşünen", "fast": "Hızlı"}

SYSTEM_PROMPT = """Sen Music+ uygulamasının yapay zeka müzik asistanısın. Kullanıcının Spotify dinleme geçmişini ve alışkanlıklarını analiz ederek yardımcı olursun.

YAPABİLECEKLERİN:
- Müzik önerileri yapmak (dinleme alışkanlıklarına göre)
- Şarkı/sanatçı bilgisi vermek
- Playlist oluşturmak veya düzenlemek
- Mevcut playlistlere şarkı eklemek
- Dinleme analizleri yapmak (tüm zamanlar, ay bazlı, sanatçı/şarkı detaylı)
- Şarkı Spotify ID'si aramak

KOMUT FORMATLARI (bir işlem yapman gerektiğinde YANITININ SONUNA ekle):

1. Şarkı ID'si aramak:
{"action":"search_track","track":"Şarkı Adı","artist":"Sanatçı Adı"}

2. Playlist oluşturmak:
{"action":"create_playlist","name":"Playlist Adı","tracks":["Şarkı Adı - Sanatçı","Şarkı Adı 2 - Sanatçı 2"]}

3. Playlist'e şarkı eklemek:
{"action":"add_to_playlist","playlist_id":"PLAYLIST_ID","tracks":["Şarkı Adı - Sanatçı"]}

4. Mevcut playlist'i düzenlemek (şarkı ekle ve/veya adı değiştir):
{"action":"edit_playlist","playlist_id":"PLAYLIST_ID","tracks":["Şarkı Adı - Sanatçı"],"new_name":"Yeni Ad (opsiyonel)"}

5. Playlist karıştırmak:
{"action":"shuffle_playlist","playlist_id":"PLAYLIST_ID"}

KURALLAR:
- Türkçe konuş, samimi ve yardımsever ol
- Yanıtların kısa ve öz olsun, gereksiz uzatma
- Sadece gerçekten bir Spotify işlemi yapman gerekiyorsa komut JSON'u yaz
- JSON komutunu yanıtın en sonuna yaz, başka yerde yazma
- Kullanıcının dinleme geçmişine dayalı kişiselleştirilmiş öneriler sun
- Tam detaylı analiz yapman istendiğinde ay bazlı trendleri, sanatçı profilini ve dinleme kalıplarını kapsamlı şekilde açıkla
- Playlist düzenleme veya şarkı ekleme işlemlerinde MUTLAKA sağlanan playlist listesindeki gerçek ID'yi kullan. Playlist adını asla playlist_id olarak kullanma. ID her zaman "ID: XXXX" formatında sağlanır.
"""


class GeminiClient:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY", "")

    def _build_system_prompt(self, spotify_context: str) -> str:
        if spotify_context:
            return SYSTEM_PROMPT + f"\n\nKULLANICININ ŞU ANKİ SPOTIFY DURUMU:\n{spotify_context}"
        return SYSTEM_PROMPT

    @staticmethod
    def build_spotify_context(now_playing, recent_tracks):
        lines = []
        if now_playing and now_playing.get("playing"):
            durum = "çalıyor" if now_playing.get("is_playing") else "duraklatıldı"
            lines.append(f"Şu an {durum}: {now_playing['track_name']} — {now_playing['artist_name']} ({now_playing.get('album_name','')})")
        if recent_tracks:
            lines.append("Son dinlenenler (en yeni → eski):")
            for t in recent_tracks[:8]:
                lines.append(f"  • {t['track_name']} — {t['artist_name']}")
        return "\n".join(lines)

    @staticmethod
    def pretty_model_label(model: str) -> str:
        m = model.lower()
        if "31b" in m:
            return "Gemma 4 31B"
        if "26b" in m or "27b" in m:
            return "Gemma 4 26B"
        if "flash-lite" in m or "flash_lite" in m:
            return "Gemini 3.1 Flash Lite"
        if "flash" in m:
            return "Gemini Flash"
        if "gemma" in m:
            return "Gemma 4"
        return model

    def stream_chat(self, messages: list, spotify_context: str = "", mode: str = "thinking"):
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            yield json.dumps({"type": "error", "text": "google-genai paketi eksik."})
            return

        if not self.api_key:
            yield json.dumps({"type": "error", "text": "GEMINI_API_KEY ortam değişkeni ayarlanmamış."})
            return

        client = genai.Client(api_key=self.api_key)

        gemini_contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            gemini_contents.append(
                types.Content(role=role, parts=[types.Part(text=msg["content"])])
            )

        system_prompt = self._build_system_prompt(spotify_context)

        if mode == "fast":
            yield from self._stream_fast(client, types, gemini_contents, system_prompt)
        else:
            yield from self._stream_thinking(client, types, gemini_contents, system_prompt)

    def _stream_thinking(self, client, types, contents, system_prompt):
        last_error = None
        for model in THINKING_MODELS:
            try:
                logger.info(f"🧠 Düşünen mod: {model}")
                config = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
                    temperature=0.9,
                )
                for chunk in client.models.generate_content_stream(
                    model=model, contents=contents, config=config
                ):
                    if not chunk.candidates:
                        continue
                    content = chunk.candidates[0].content
                    if not content or not content.parts:
                        continue
                    for part in content.parts:
                        text = getattr(part, "text", "") or ""
                        if not text:
                            continue
                        if getattr(part, "thought", False):
                            yield json.dumps({"type": "thinking", "text": text}, ensure_ascii=False)
                        else:
                            yield json.dumps({"type": "text", "text": text}, ensure_ascii=False)

                yield json.dumps({"type": "done", "model": model, "mode": "thinking"}, ensure_ascii=False)
                return

            except Exception as e:
                logger.warning(f"⚠️ Düşünen model {model} başarısız: {e}")
                last_error = str(e)
                continue

        yield json.dumps(
            {"type": "error", "text": f"Tüm düşünen modeller başarısız. Son hata: {last_error}"},
            ensure_ascii=False,
        )

    def _stream_fast(self, client, types, contents, system_prompt):
        model = FAST_MODEL
        try:
            logger.info(f"⚡ Hızlı mod: {model}")
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                temperature=0.7,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            )
            for chunk in client.models.generate_content_stream(
                model=model, contents=contents, config=config
            ):
                if not chunk.candidates:
                    continue
                content = chunk.candidates[0].content
                if not content or not content.parts:
                    continue
                for part in content.parts:
                    text = getattr(part, "text", "") or ""
                    if not text:
                        continue
                    if getattr(part, "thought", False):
                        yield json.dumps({"type": "thinking", "text": text}, ensure_ascii=False)
                    else:
                        yield json.dumps({"type": "text", "text": text}, ensure_ascii=False)

            yield json.dumps({"type": "done", "model": model, "mode": "fast"}, ensure_ascii=False)

        except Exception as e:
            logger.error(f"❌ Hızlı model başarısız: {e}")
            yield json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False)
