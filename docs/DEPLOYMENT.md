# Deployment Rehberi

## Self-Hosted (Yerel / VPS)

```bash
pip install -r requirements.txt
cp .env.example .env   # .env'yi düzenle
python app.py          # geliştirme
# veya
gunicorn app:app --bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 120
```

## Docker

```bash
docker build -t musicplus .
docker run -p 8000:8000 --env-file .env musicplus
```

## Render
render.yaml hazır — repo bağla, env değişkenlerini gir, deploy et.

## Railway / Heroku
Procfile hazır. Env değişkenlerini platform dashboard'dan gir.

## Fly.io
```bash
fly launch && fly secrets set SECRET_KEY=... && fly deploy
```

## Spotify Callback URL
Spotify Developer Dashboard'a ekle:
- Production: `https://alan-adi.com/callback`
- Yerel: `http://localhost:5000/callback`
