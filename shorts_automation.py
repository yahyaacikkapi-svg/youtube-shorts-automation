"""
YouTube Shorts Otomasyonu
=========================
Tek komutla:
  - Gemini ile psikoloji/davranis bilimi script'i üretir
  - edge-tts ile İngilizce seslendirme yapar
  - Pexels'tan portre stok video çeker
  - FFmpeg ile 9:16 dikey video render eder (kelime kelime altyazılı)
  - YouTube'a Short olarak yükler

Kullanım:
  python shorts_automation.py             # tüm pipeline
  python shorts_automation.py --auth      # ilk seferki YouTube OAuth (sadece bir kez)
  python shorts_automation.py --no-upload # üretip yükleme (sandbox testleri için)
"""

import os
import sys
import json
import time
import random
import asyncio
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# --------- 3rd-party imports (try-except for friendlier errors) ---------
try:
    import requests
    from dotenv import load_dotenv
    import google.generativeai as genai
    import edge_tts
    from edge_tts import SubMaker
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError as e:
    print(f"[hata] Eksik paket: {e.name}")
    print("Kurulum: pip install google-generativeai edge-tts google-auth google-auth-oauthlib "
          "google-api-python-client python-dotenv requests Pillow")
    sys.exit(1)


# --------- Config ---------
ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
CREDENTIALS_JSON = ROOT / "credentials.json"
TOKEN_JSON = ROOT / "token.json"
OUTPUT_DIR = ROOT / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
BRAND_DIR = ROOT / "brand"

load_dotenv(ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

VIDEO_W, VIDEO_H = 1080, 1920  # 9:16
TARGET_DURATION_RANGE = (28, 55)  # seconds
VOICE = "en-US-GuyNeural"  # high-energy narrator

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


# --------- 1. Generate script with Gemini ---------
def generate_fun_fact():
    """Returns dict: {script, title, description, tags, keyword}.

    Niche: Psychology / mind games / behavioral science. Curiosity-driven
    educational shorts about how the human mind actually works.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY .env'de yok")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash-lite")  # ücretsiz, hızlı

    topics = [
        "a cognitive bias that secretly controls everyday decisions",
        "a counterintuitive truth about how memory actually works",
        "a manipulation tactic used by con artists and advertisers",
        "a quirk of human attention or perception that feels like a glitch",
        "a social psychology experiment with a disturbing result",
        "a weird thing the brain does during sleep or dreaming",
        "a mind hack backed by research that improves focus, sleep, or mood",
        "an unsettling fact about why people lie, cheat, or conform",
        "a hidden mechanism behind anxiety, motivation, or procrastination",
        "a habit-formation insight that sounds simple but is rarely applied",
    ]
    topic_seed = random.choice(topics)

    prompt = f"""You are a viral YouTube Shorts scriptwriter for a psychology / mind-science channel.
Tone: exciting, mysterious, fast-paced, addictive. Make viewers say "wait, what?" in 2 seconds
and refuse to scroll away.

Topic seed: {topic_seed}

Script rules (MANDATORY):
- First 2 seconds = a brutal hook that disrupts expectations. NEVER start with "Did you know".
  Strong examples: "Your brain just lied to you.", "Most people fail this in 3 seconds.",
  "There is a reason you cannot stop overthinking - and it is engineered."
- Every 5-7 seconds, drop a mini cliffhanger or curiosity spike ("but here is the twist...",
  "and this is where it gets dark...", "wait until you hear what they did next...").
- Sentences must be SHORT, SHARP, PUNCHY. Spoken English, conversational, no fluff.
- No slow intros, no "in this video", no throat-clearing. Start mid-action.
- Use a real research finding or named effect when possible (Asch conformity, Dunning-Kruger,
  Zeigarnik effect, mere exposure, loss aversion, spotlight effect, etc.).
- Concrete examples beat abstractions. Exaggerate the delivery, never invent false facts.
- End with a punchline + this exact CTA: "Follow for more reasons your brain is weird."

Return ONLY valid JSON with these keys:
- "script": the spoken voiceover, 90-140 words (~35-55 seconds), hook-first, cliffhanger-paced.
  NO emojis, NO markdown, NO sound effects in brackets, just plain spoken text.
- "title": YouTube Shorts title, max 70 chars, curiosity-driven, ends with #Shorts
- "description": 2 short sentences + 8 hashtags (include #psychology #mindset)
- "tags": JSON array of 12 SEO tags (mix of psychology, mind, brain, behavior terms)
- "keyword": ONE word for stock video search - pick something visually evocative that
  fits the topic mood (e.g. "brain", "crowd", "mirror", "eyes", "city", "abstract")
- "thumbnail_text": MAX 4 WORDS, ALL CAPS, the punchiest version of the hook.
  Examples: "YOUR BRAIN LIES", "THE 3-SECOND TRICK", "WHY YOU CAN'T STOP", "STOP DOING THIS".
  No emojis, no punctuation except hyphen. Must be screen-readable at thumbnail scale.

Output JSON only, nothing else."""

    response = model.generate_content(
        prompt,
        generation_config={
            "temperature": 1.1,
            "max_output_tokens": 800,
            "response_mime_type": "application/json",
        },
    )
    data = json.loads(response.text)
    print(f"[script] Konu: {data['keyword']}")
    print(f"[script] Baslik: {data['title']}")
    return data


# --------- 2. Generate TTS audio with subtitles ---------
async def _generate_voice_async(text, audio_path, srt_path):
    sub_maker = SubMaker()
    communicate = edge_tts.Communicate(text, VOICE, rate="+10%", boundary="WordBoundary")
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                sub_maker.feed(chunk)

    # Generate SRT subs with alternating white/yellow word colors (ASS override tags)
    srt_lines = []
    cues = sub_maker.cues
    group_size = 3
    idx = 1
    word_idx = 0
    yellow = r"{\c&H0000D7FF&}"
    white = r"{\c&H00FFFFFF&}"
    for i in range(0, len(cues), group_size):
        group = cues[i:i + group_size]
        if not group:
            continue
        start_s = group[0].start.total_seconds()
        end_s = group[-1].end.total_seconds()
        parts = []
        for c in group:
            color = yellow if word_idx % 3 == 1 else white
            parts.append(f"{color}{c.content.upper()}")
            word_idx += 1
        text_chunk = " ".join(parts)
        srt_lines.append(f"{idx}\n{_fmt_time(start_s)} --> {_fmt_time(end_s)}\n{text_chunk}\n")
        idx += 1
    Path(srt_path).write_text("\n".join(srt_lines), encoding="utf-8")


def _fmt_time(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",")


def generate_voice(text, audio_path, srt_path):
    asyncio.run(_generate_voice_async(text, audio_path, srt_path))
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    duration = float(out.stdout.strip())
    print(f"[voice] {duration:.1f}s ses uretildi -> {audio_path.name}")
    return duration


# --------- 3. Fetch portrait stock videos from Pexels ---------
def fetch_pexels_video(keyword, min_duration_s):
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY .env'de yok")
    headers = {"Authorization": PEXELS_API_KEY}
    url = "https://api.pexels.com/videos/search"
    aesthetic_query = f"{keyword} aesthetic cinematic"
    params = {"query": aesthetic_query, "per_page": 15, "orientation": "portrait", "size": "medium"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("videos"):
        print(f"[pexels] '{aesthetic_query}' sonuc yok, '{keyword}' deneniyor")
        params["query"] = keyword
        r = requests.get(url, headers=headers, params=params, timeout=30)
        data = r.json()
    if not data.get("videos"):
        print(f"[pexels] sonuc yok, 'aesthetic abstract' deneniyor")
        params["query"] = "aesthetic abstract"
        r = requests.get(url, headers=headers, params=params, timeout=30)
        data = r.json()

    for v in data["videos"]:
        if v["duration"] >= min_duration_s:
            for f in v["video_files"]:
                if f.get("file_type") == "video/mp4" and f.get("width", 0) >= 720:
                    print(f"[pexels] {f['width']}x{f['height']}, {v['duration']}s")
                    return f["link"]
    longest = max(data["videos"], key=lambda v: v["duration"])
    for f in longest["video_files"]:
        if f.get("file_type") == "video/mp4":
            return f["link"]
    raise RuntimeError("Pexels'tan uygun video bulunamadi")


def download_file(url, dest):
    r = requests.get(url, stream=True, timeout=120)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"[download] {dest.name}")


# --------- 4a. Generate thumbnail (1080x1920, branded) ---------
THUMB_FONT_CANDIDATES = [
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Black.ttf",
]


def _pick_thumb_font(size):
    for path in THUMB_FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap_lines(text, font, max_width, draw):
    words = text.split()
    lines, current = [], ""
    for w in words:
        candidate = (current + " " + w).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def generate_thumbnail(bg_video_path, thumbnail_text, out_path):
    """1080x1920 thumbnail: blurred frame + chromatic aberration text + B logo."""
    workdir = Path(out_path).parent
    frame_path = workdir / "_thumb_frame.png"
    cmd = ["ffmpeg", "-y", "-i", str(bg_video_path), "-vframes", "1",
           "-q:v", "2", str(frame_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not frame_path.exists():
        print(res.stderr[-1000:])
        raise RuntimeError("Thumbnail icin frame cikarilamadi")

    img = Image.open(frame_path).convert("RGB")
    w, h = img.size
    target_ratio = VIDEO_W / VIDEO_H
    src_ratio = w / h
    if src_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize((VIDEO_W, VIDEO_H), Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(radius=10))
    dark = Image.new("RGB", img.size, (0, 0, 0))
    img = Image.blend(img, dark, 0.45)
    img = img.convert("RGBA")

    text = (thumbnail_text or "").upper().strip() or "BRAIN STATIC"
    draw = ImageDraw.Draw(img)
    max_text_width = VIDEO_W - 160
    font_size = 220
    while font_size > 80:
        font = _pick_thumb_font(font_size)
        lines = _wrap_lines(text, font, max_text_width, draw)
        line_h = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
        total_h = len(lines) * (line_h + 24)
        widest = max((draw.textbbox((0, 0), ln, font=font)[2] for ln in lines), default=0)
        if widest <= max_text_width and total_h <= VIDEO_H * 0.55:
            break
        font_size -= 12
    font = _pick_thumb_font(font_size)
    lines = _wrap_lines(text, font, max_text_width, draw)
    line_h = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
    total_h = len(lines) * (line_h + 24)
    y = (VIDEO_H - total_h) // 2

    cyan = (0, 229, 255, 220)
    magenta = (255, 0, 128, 220)
    white = (255, 255, 255, 255)
    shift = max(4, font_size // 28)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (VIDEO_W - text_w) // 2
        draw.text((x - shift, y), line, font=font, fill=cyan)
        draw.text((x + shift, y), line, font=font, fill=magenta)
        draw.text((x, y + 4), line, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=font, fill=white)
        y += line_h + 24

    logo_path = BRAND_DIR / "profile.png"
    if logo_path.exists():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((150, 150), Image.LANCZOS)
            img.paste(logo, (60, VIDEO_H - logo.size[1] - 60), logo)
        except Exception as e:
            print(f"[thumb] logo eklenemedi: {e}")

    img.convert("RGB").save(out_path, "PNG", optimize=True)
    try:
        frame_path.unlink()
    except OSError:
        pass
    print(f"[thumb] Hazir -> {out_path.name} ({font_size}px, {len(lines)} line)")


# --------- 4. Render final video with ffmpeg ---------
def render_video(bg_video_path, audio_path, srt_path, audio_duration, output_path):
    srt_str = str(srt_path).replace("\\", "/").replace(":", "\\:")
    vf = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"subtitles='{srt_str}':force_style='"
        f"FontName=Impact,FontSize=11,PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,Outline=3,Shadow=0,Alignment=2,MarginV=80,Bold=1'"
    )
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-t", str(audio_duration + 0.5),
        "-i", str(bg_video_path),
        "-i", str(audio_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        str(output_path),
    ]
    print("[render] FFmpeg calisiyor...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-2000:])
        raise RuntimeError("FFmpeg render basarisiz")
    print(f"[render] Hazir -> {output_path.name}")


# --------- 5. YouTube OAuth + Upload ---------
def get_youtube_creds():
    """
    Iki mod:
      1) GitHub Actions / headless: env var'larda CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN
         varsa onlari kullan (interaktif degil).
      2) Local gelistirme: credentials.json + token.json kullan, gerekirse browser ac.
    """
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")

    if refresh_token and client_id and client_secret:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=YOUTUBE_SCOPES,
        )
        creds.refresh(Request())
        return creds

    creds = None
    if TOKEN_JSON.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_JSON), YOUTUBE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_JSON.exists():
                raise RuntimeError(f"credentials.json yok: {CREDENTIALS_JSON}")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_JSON), YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0, open_browser=True)
        TOKEN_JSON.write_text(creds.to_json())
    return creds


def upload_to_youtube(video_path, title, description, tags, privacy="private",
                      publish_at=None, thumbnail_path=None):
    creds = get_youtube_creds()
    youtube = build("youtube", "v3", credentials=creds)
    status = {
        "privacyStatus": "private" if publish_at else privacy,
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status["publishAt"] = publish_at
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": "27",
        },
        "status": status,
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        s, response = request.next_chunk()
    video_id = response["id"]
    print(f"[upload] Yuklendi: https://youtube.com/watch?v={video_id}")
    if publish_at:
        print(f"[upload] Public olacak: {publish_at}")

    if thumbnail_path and Path(thumbnail_path).exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png"),
            ).execute()
            print(f"[upload] Thumbnail set")
        except HttpError as e:
            print(f"[upload] Thumbnail atlandi (kanal henuz custom thumbnail yetkisiz olabilir): {e}")

    return video_id


# --------- Main pipeline ---------
def run_pipeline(skip_upload=False, privacy="private", auto_public_after=0):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    workdir = OUTPUT_DIR / ts
    workdir.mkdir(exist_ok=True)
    print(f"[main] Calisma klasoru: {workdir}")

    meta = generate_fun_fact()
    (workdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    audio_path = workdir / "voice.mp3"
    srt_path = workdir / "subs.srt"
    duration = generate_voice(meta["script"], audio_path, srt_path)

    bg_url = fetch_pexels_video(meta["keyword"], min_duration_s=duration)
    bg_path = workdir / "bg.mp4"
    download_file(bg_url, bg_path)

    thumb_path = workdir / "thumbnail.png"
    try:
        generate_thumbnail(bg_path, meta.get("thumbnail_text", ""), thumb_path)
    except Exception as e:
        print(f"[thumb] uretilemedi, atlanacak: {e}")
        thumb_path = None

    out_path = workdir / "short.mp4"
    render_video(bg_path, audio_path, srt_path, duration, out_path)

    if skip_upload:
        print(f"[main] Yukleme atlandi. Video: {out_path}")
        return out_path
    publish_at = None
    if auto_public_after > 0 and privacy != "public":
        from datetime import timedelta, timezone
        dt = datetime.now(timezone.utc) + timedelta(seconds=auto_public_after)
        publish_at = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    video_id = upload_to_youtube(
        out_path,
        meta["title"],
        meta["description"],
        meta["tags"],
        privacy=privacy,
        publish_at=publish_at,
        thumbnail_path=thumb_path,
    )
    print(f"[main] Tamam. Video ID: {video_id}")
    return out_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--auth", action="store_true", help="Sadece YouTube OAuth (ilk kez)")
    p.add_argument("--no-upload", action="store_true", help="Uret ama yukleme")
    p.add_argument("--public", action="store_true", help="(deprecated) --privacy public ile ayni")
    p.add_argument("--privacy", choices=["private", "public", "unlisted"], default=None)
    p.add_argument("--auto-public-after", type=int, default=0,
                   help="Saniye sonra video private->public'e cevrilir (privacy public degilse)")
    args = p.parse_args()

    if args.auth:
        creds = get_youtube_creds()
        print("[auth] Token kaydedildi:", TOKEN_JSON)
        return

    if args.privacy:
        privacy = args.privacy
    elif args.public:
        privacy = "public"
    else:
        privacy = "private"
    run_pipeline(
        skip_upload=args.no_upload,
        privacy=privacy,
        auto_public_after=args.auto_public_after,
    )


if __name__ == "__main__":
    main()
