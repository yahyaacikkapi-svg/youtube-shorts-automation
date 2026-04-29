# YouTube Shorts Auto-Pipeline

Günde 3-5 İngilizce eğitici (fun fact) Shorts üretip YouTube'a otomatik yükleyen pipeline. GitHub Actions üzerinde ücretsiz çalışır.

## Akış

1. Gemini AI ile 75-105 kelimelik script + başlık + etiketler üretilir
2. Microsoft Edge TTS ile İngilizce seslendirme yapılır (kelime kelime altyazı)
3. Pexels API'den portre stok video çekilir
4. FFmpeg ile 1080x1920 dikey video render edilir
5. YouTube API ile yüklenir

## Gerekli Secrets

GitHub repo > Settings > Secrets and variables > Actions altına ekle:

- `GEMINI_API_KEY` - aistudio.google.com/apikey
- `PEXELS_API_KEY` - pexels.com/api
- `YOUTUBE_CLIENT_ID` - Google Cloud Console OAuth desktop client
- `YOUTUBE_CLIENT_SECRET` - aynı yer
- `YOUTUBE_REFRESH_TOKEN` - oauthplayground.google.com ile alınır

## Schedule değiştirme

`.github/workflows/shorts.yml` içindeki cron satırı:

```yaml
- cron: "0 6,11,16 * * *"   # 3 video/gün
- cron: "0 6,9,12,15,18 * * *"  # 5 video/gün
```

Saatler UTC. Türkiye saati = UTC + 3.

## Manuel test

Repo > Actions > "YouTube Shorts Auto-Pipeline" > Run workflow.

## Local çalıştırma

```bash
pip install -r requirements.txt
python shorts_automation.py --auth          # ilk kez YouTube auth
python shorts_automation.py --no-upload     # üret ama yükleme
python shorts_automation.py                 # tam pipeline
```
