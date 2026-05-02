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
VOICE = "en-US-AndrewMultilingualNeural"  # most expressive storyteller voice

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
- Write WORDS YOU WANT SHOUTED in ALL CAPS (the TTS narrator uses caps as an emphasis cue,
  ride the rhythm — 1-2 caps words per sentence, never a whole sentence in caps).
- The opening hook MUST end with "!" or "?" (forces the voice pitch to rise sharply).
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
- "visual_keywords": JSON array of EXACTLY 4 specific, cinematic stock-footage search
  terms tied to the EXACT script content. Each entry = a different scene, different
  mood, but all clearly on-topic. Use 2-4 word phrases, no single words.
  Example for a memory script: ["empty hospital corridor", "old photographs scattered",
  "rain on window at night", "elderly hand writing letter"]
- "keyword": fallback ONE word for stock video search if a visual_keyword returns
  nothing (e.g. "brain", "crowd", "mirror", "eyes", "city", "abstract")
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
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Impact,80,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,5,0,2,40,40,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(s):
    """ASS time: H:MM:SS.cs (centiseconds, single-digit hour)."""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s - h * 3600 - m * 60
    cs = int(round((sec - int(sec)) * 100))
    if cs == 100:
        cs = 0
        sec += 1
    return f"{h}:{m:02d}:{int(sec):02d}.{cs:02d}"


def _build_ass(cues, time_offset, ass_path):
    """Write libass-compatible .ass file. Every 3rd word (the 2nd in each group of 3)
    is yellow via inline {\\c} override; the rest inherit Default white. ffmpeg's
    subtitles= filter renders these overrides correctly (SRT swallows them)."""
    yellow = r"{\c&H0000D7FF&}"  # ASS BGR -> golden yellow
    reset = r"{\r}"
    lines = [ASS_HEADER]
    group_size = 3
    word_idx = 0
    for i in range(0, len(cues), group_size):
        group = cues[i:i + group_size]
        if not group:
            continue
        start_s = group[0].start.total_seconds() + time_offset
        end_s = group[-1].end.total_seconds() + time_offset
        parts = []
        for c in group:
            word = c.content.upper()
            if word_idx % 3 == 1:
                parts.append(f"{yellow}{word}{reset}")
            else:
                parts.append(word)
            word_idx += 1
        text_chunk = " ".join(parts)
        lines.append(
            f"Dialogue: 0,{_ass_time(start_s)},{_ass_time(end_s)},Default,,0,0,0,,{text_chunk}"
        )
    Path(ass_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _generate_voice_async(text, audio_path, ass_path, time_offset=0.0):
    sub_maker = SubMaker()
    communicate = edge_tts.Communicate(text, VOICE, rate="+8%", boundary="WordBoundary")
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                sub_maker.feed(chunk)
    _build_ass(sub_maker.cues, time_offset, ass_path)


def generate_voice(text, audio_path, ass_path, time_offset=0.0):
    asyncio.run(_generate_voice_async(text, audio_path, ass_path, time_offset))
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    duration = float(out.stdout.strip())
    print(f"[voice] {duration:.1f}s ses uretildi -> {audio_path.name}")
    return duration


# --------- 3. Fetch portrait stock videos from Pexels ---------
def _pexels_search(headers, query, min_dur):
    """Returns (id, link, duration) tuple for the best portrait MP4 in `query`,
    or None if no usable result. sort=popular + size=large for cinematic clips."""
    params = {
        "query": query, "per_page": 15, "orientation": "portrait",
        "size": "large", "sort": "popular",
    }
    r = requests.get("https://api.pexels.com/videos/search",
                     headers=headers, params=params, timeout=30)
    r.raise_for_status()
    for v in r.json().get("videos", []):
        if v["duration"] < min_dur:
            continue
        for f in v["video_files"]:
            if f.get("file_type") == "video/mp4" and f.get("width", 0) >= 720:
                return v["id"], f["link"], v["duration"]
    return None


def fetch_pexels_clips(visual_keywords, fallback_keyword, n_clips=4,
                       min_duration_per_clip=5):
    """Returns list of (url, duration) tuples — one cinematic portrait clip per
    visual_keyword. Falls back to `fallback_keyword` for any query that returns
    nothing. De-duplicates by Pexels video id so no clip repeats."""
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY .env'de yok")
    headers = {"Authorization": PEXELS_API_KEY}

    queries = list(visual_keywords)[:n_clips]
    while len(queries) < n_clips:
        queries.append(fallback_keyword)

    seen_ids = set()
    results = []
    for q in queries:
        attempts = [q, f"{q} cinematic", fallback_keyword,
                    f"{fallback_keyword} aesthetic cinematic", "aesthetic abstract"]
        chosen = None
        for attempt in attempts:
            hit = _pexels_search(headers, attempt, min_duration_per_clip)
            if hit and hit[0] not in seen_ids:
                chosen = hit
                break
        if chosen:
            seen_ids.add(chosen[0])
            results.append((chosen[1], chosen[2]))
            print(f"[pexels] '{q}' -> id={chosen[0]} ({chosen[2]}s)")
        else:
            print(f"[pexels] '{q}' icin uygun klip yok, atlaniyor")

    if not results:
        raise RuntimeError("Pexels'tan hicbir uygun klip alinamadi")
    return results


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
def render_video(bg_clips, audio_path, ass_path, audio_duration, output_path):
    """Concat 1..N portrait clips with crossfade transitions, overlay subs, mux audio.
    Subs are a real .ass file (V4+ Styles + per-word color overrides) so the yellow
    accent words actually render — SRT swallows ASS override tags but .ass keeps them.
    Each scene runs for ~total/N seconds (clamped to 5..15). xfade chain produces
    final length = sum(per_clip) - (N-1)*xfade_dur ≈ audio_duration + 0.5."""
    if isinstance(bg_clips, (str, Path)):
        bg_clips = [Path(bg_clips)]
    bg_clips = [Path(c) for c in bg_clips]
    n = len(bg_clips)
    if n == 0:
        raise RuntimeError("render_video: hicbir klip verilmedi")

    xfade_dur = 0.4
    total_video_dur = audio_duration + 0.5
    # Try requested N; if per-clip < 5s, drop down to fewer scenes.
    while n > 1:
        per_clip = (total_video_dur + (n - 1) * xfade_dur) / n
        if per_clip >= 5.0:
            break
        n -= 1
    bg_clips = bg_clips[:n]
    per_clip = (total_video_dur + (n - 1) * xfade_dur) / n
    per_clip = min(per_clip, 15.0) if n > 1 else total_video_dur

    ass_str = str(ass_path).replace("\\", "/").replace(":", "\\:")

    inputs = []
    for clip in bg_clips:
        inputs.extend(["-stream_loop", "-1", "-i", str(clip)])
    inputs.extend(["-i", str(audio_path)])
    audio_input_idx = n

    fc_parts = []
    for i in range(n):
        fc_parts.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,trim=duration={per_clip:.3f},setpts=PTS-STARTPTS,"
            f"format=yuv420p[v{i}]"
        )
    if n == 1:
        chained_label = "[v0]"
    else:
        prev = "[v0]"
        for i in range(1, n):
            offset = i * (per_clip - xfade_dur)
            label = f"[x{i}]"
            fc_parts.append(
                f"{prev}[v{i}]xfade=transition=fade:duration={xfade_dur:.3f}"
                f":offset={offset:.3f}{label}"
            )
            prev = label
        chained_label = prev

    fc_parts.append(f"{chained_label}subtitles='{ass_str}'[outv]")
    fc = ";".join(fc_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[outv]", "-map", f"{audio_input_idx}:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        str(output_path),
    ]
    print(f"[render] {n} klip xfade chain, per-clip={per_clip:.1f}s, FFmpeg calisiyor...")
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


def _next_publish_tr_slot(slots_str, now_utc=None):
    """Given comma-separated TR times like '19:00,01:00', return the next
    upcoming occurrence as ISO UTC string for YouTube publishAt.
    `now_utc` is injectable for testing."""
    from datetime import datetime, timedelta, timezone
    tr_offset = timedelta(hours=3)  # TR is UTC+3 fixed (no DST)
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_tr = (now_utc + tr_offset).replace(tzinfo=None)
    candidates = []
    for s in slots_str.split(","):
        hh, mm = (int(x) for x in s.strip().split(":"))
        for day_off in (-1, 0, 1):
            cand_tr = now_tr.replace(hour=hh, minute=mm, second=0, microsecond=0) \
                      + timedelta(days=day_off)
            candidates.append(cand_tr)
    future = [c for c in candidates if c > now_tr + timedelta(seconds=60)]
    target_tr = min(future)
    target_utc = target_tr - tr_offset
    return target_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --------- Main pipeline ---------
def run_pipeline(skip_upload=False, privacy="private", auto_public_after=0,
                 publish_at_tr_slots=None):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    workdir = OUTPUT_DIR / ts
    workdir.mkdir(exist_ok=True)
    print(f"[main] Calisma klasoru: {workdir}")

    meta = generate_fun_fact()
    (workdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    audio_path = workdir / "voice.mp3"
    subs_path = workdir / "subs.ass"
    duration = generate_voice(meta["script"], audio_path, subs_path)

    visual_keywords = meta.get("visual_keywords") or [meta["keyword"]]
    clips_meta = fetch_pexels_clips(
        visual_keywords, meta["keyword"],
        n_clips=4, min_duration_per_clip=5,
    )
    clip_paths = []
    for i, (clip_url, _dur) in enumerate(clips_meta):
        p = workdir / f"bg_{i}.mp4"
        download_file(clip_url, p)
        clip_paths.append(p)

    thumb_path = workdir / "thumbnail.png"
    try:
        generate_thumbnail(clip_paths[0], meta.get("thumbnail_text", ""), thumb_path)
    except Exception as e:
        print(f"[thumb] uretilemedi, atlanacak: {e}")
        thumb_path = None

    out_path = workdir / "short.mp4"
    render_video(clip_paths, audio_path, subs_path, duration, out_path)

    if skip_upload:
        print(f"[main] Yukleme atlandi. Video: {out_path}")
        return out_path
    publish_at = None
    if publish_at_tr_slots and privacy != "public":
        publish_at = _next_publish_tr_slot(publish_at_tr_slots)
        print(f"[main] publishAt (next TR slot): {publish_at}")
    elif auto_public_after > 0 and privacy != "public":
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
    p.add_argument("--publish-at-tr", default=None,
                   help="Virgulle ayrilmis TR saatleri (orn: '19:00,01:00'). "
                        "En yakin gelecek slotu publishAt olarak kullanir, GitHub gecikmesinden bagimsiz.")
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
        publish_at_tr_slots=args.publish_at_tr,
    )


if __name__ == "__main__":
    main()
