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
except ImportError as e:
    print(f"[hata] Eksik paket: {e.name}")
    print("Kurulum: pip install google-generativeai edge-tts google-auth google-auth-oauthlib "
          "google-api-python-client python-dotenv requests")
    sys.exit(1)


# --------- Config ---------
ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
CREDENTIALS_JSON = ROOT / "credentials.json"
TOKEN_JSON = ROOT / "token.json"
OUTPUT_DIR = ROOT / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

load_dotenv(ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

VIDEO_W, VIDEO_H = 1080, 1920  # 9:16
TARGET_DURATION_RANGE = (28, 55)  # seconds
VOICE = "en-US-AndrewNeural"  # warm, curious narrator-style for psychology content

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

    prompt = f"""You are a YouTube Shorts writer for a psychology / mind-science channel.
Your videos feel like a mix of Veritasium and a behavioral science podcast - curious,
slightly unsettling, deeply human. They make viewers say "wait, what?" in 3 seconds.

Topic seed: {topic_seed}

Voice rules:
- Open with a hook sentence that disrupts expectations (NEVER start with "Did you know").
  Examples of strong hooks: "Your brain just lied to you.", "The smartest people fall
  for this every time.", "There's a reason you hate Mondays - and it's not what you think."
- Use a real research finding or named effect when possible (Asch conformity, Dunning-Kruger,
  Zeigarnik effect, mere exposure, loss aversion, spotlight effect, etc.).
- Specific, concrete examples beat vague abstractions.
- End with: "Follow for more reasons your brain is weird."

Return ONLY valid JSON with these keys:
- "script": the spoken voiceover, 75-105 words, conversational, hook-first.
  NO emojis, NO markdown, NO sound effects in brackets, just plain spoken text.
- "title": YouTube Shorts title, max 70 chars, attention-grabbing, ends with #Shorts
- "description": 2 short sentences + 8 hashtags (include #psychology #mindset)
- "tags": JSON array of 12 SEO tags (mix of psychology, mind, brain, behavior terms)
- "keyword": ONE word for stock video search - pick something visually evocative that
  fits the topic mood (e.g. "brain", "crowd", "mirror", "eyes", "city", "abstract")

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
    communicate = edge_tts.Communicate(text, VOICE, rate="+5%")
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                sub_maker.feed(chunk)

    # Generate SRT-style subs (word-grouped to ~3 words per cue for Shorts feel)
    srt_lines = []
    cues = sub_maker.cues
    group_size = 3
    idx = 1
    for i in range(0, len(cues), group_size):
        group = cues[i:i + group_size]
        if not group:
            continue
        start_s = group[0].start.total_seconds()
        end_s = group[-1].end.total_seconds()
        text_chunk = " ".join(c.text for c in group).upper()
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
    params = {"query": keyword, "per_page": 15, "orientation": "portrait", "size": "medium"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("videos"):
        print(f"[pexels] '{keyword}' icin sonuc yok, 'abstract' deneniyor")
        params["query"] = "abstract"
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


# --------- 4. Render final video with ffmpeg ---------
def render_video(bg_video_path, audio_path, srt_path, audio_duration, output_path):
    srt_str = str(srt_path).replace("\\", "/").replace(":", "\\:")
    vf = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"subtitles='{srt_str}':force_style='"
        f"FontName=Impact,FontSize=18,PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,Outline=3,Shadow=0,Alignment=2,MarginV=140,Bold=1'"
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


def upload_to_youtube(video_path, title, description, tags, privacy="private"):
    creds = get_youtube_creds()
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": "27",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    print(f"[upload] Yuklendi: https://youtube.com/watch?v={response['id']}")
    return response["id"]


# --------- Main pipeline ---------
def run_pipeline(skip_upload=False, privacy="private"):
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

    out_path = workdir / "short.mp4"
    render_video(bg_path, audio_path, srt_path, duration, out_path)

    if skip_upload:
        print(f"[main] Yukleme atlandi. Video: {out_path}")
        return out_path
    video_id = upload_to_youtube(
        out_path,
        meta["title"],
        meta["description"],
        meta["tags"],
        privacy=privacy,
    )
    print(f"[main] Tamam. Video ID: {video_id}")
    return out_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--auth", action="store_true", help="Sadece YouTube OAuth (ilk kez)")
    p.add_argument("--no-upload", action="store_true", help="Uret ama yukleme")
    p.add_argument("--public", action="store_true", help="Public yayinla (default: private)")
    args = p.parse_args()

    if args.auth:
        creds = get_youtube_creds()
        print("[auth] Token kaydedildi:", TOKEN_JSON)
        return

    privacy = "public" if args.public else "private"
    run_pipeline(skip_upload=args.no_upload, privacy=privacy)


if __name__ == "__main__":
    main()
