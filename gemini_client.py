import os
import json
import logging

logger = logging.getLogger(__name__)

# Model öncelik sırası — ilk başarısız olursa diğerine geçilir
MODELS = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]

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
    def build_spotify_context(now_playing: dict | None, recent_tracks: list | None) -> str:
        lines = []
        if now_playing and now_playing.get("playing"):
            durum = "çalıyor" if now_playing.get("is_playing") else "duraklatıldı"
            lines.append(f"Şu an {durum}: {now_playing['track_name']} — {now_playing['artist_name']} ({now_playing.get('album_name','')})")
        if recent_tracks:
            lines.append("Son dinlenenler (en yeni → eski):")
            for t in recent_tracks[:8]:
                lines.append(f"  • {t['track_name']} — {t['artist_name']}")
        return "\n".join(lines)

    def stream_chat(self, messages: list, spotify_context: str = ""):
        """
        Generator: her chunk için SSE-uyumlu JSON satırı yield eder.

        Chunk tipleri:
          {"type":"thinking", "text":"..."}   — düşünme süreci (collapsible)
          {"type":"text",     "text":"..."}   — asıl yanıt (stream)
          {"type":"done",  "model":"..."}     — tamamlandı
          {"type":"error",    "text":"..."}   — hata
        """
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            yield json.dumps({"type": "error", "text": "google-genai paketi eksik. Lütfen sunucuya yükleyin: pip install google-genai"})
            return

        if not self.api_key:
            yield json.dumps({"type": "error", "text": "GEMINI_API_KEY ortam değişkeni ayarlanmamış."})
            return

        client = genai.Client(api_key=self.api_key)

        # Mesajları Gemini formatına çevir
        gemini_contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            gemini_contents.append(
                types.Content(role=role, parts=[types.Part(text=msg["content"])])
            )

        system_prompt = self._build_system_prompt(spotify_context)

        last_error = None
        for model in MODELS:
            try:
                logger.info(f"🤖 Gemini isteği: {model}, {len(messages)} mesaj")

                config = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
                    temperature=0.9,
                )

                for chunk in client.models.generate_content_stream(
                    model=model,
                    contents=gemini_contents,
                    config=config,
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
                        is_thought = getattr(part, "thought", False)
                        if is_thought:
                            yield json.dumps({"type": "thinking", "text": text}, ensure_ascii=False)
                        else:
                            yield json.dumps({"type": "text", "text": text}, ensure_ascii=False)

                yield json.dumps({"type": "done", "model": model}, ensure_ascii=False)
                return

            except Exception as e:
                logger.warning(f"⚠️ Model {model} başarısız: {e}")
                last_error = str(e)
                # Bir sonraki modele geç
                continue

        yield json.dumps(
            {"type": "error", "text": f"Tüm modeller başarısız oldu. Son hata: {last_error}"},
            ensure_ascii=False,
        )
