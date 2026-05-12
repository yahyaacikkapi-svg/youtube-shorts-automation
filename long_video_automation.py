"""
YouTube Long Video Otomasyonu (5 dakika)
=========================================
Kullanim:
  python long_video_automation.py             # tam pipeline + upload
  python long_video_automation.py --no-upload # uret, yukleme
  python long_video_automation.py --auth      # YouTube OAuth (ilk kez)
"""

import os
import sys
import json
import base64
import random
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

try:
    import requests
    from dotenv import load_dotenv
    import google.generativeai as genai
    from elevenlabs.client import ElevenLabs as ElevenLabsClient
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError as e:
    print(f"[hata] Eksik paket: {e.name}")
    print("Kurulum: pip install google-generativeai elevenlabs google-auth "
          "google-auth-oauthlib google-api-python-client python-dotenv requests")
    sys.exit(1)

# --------- Config ---------
ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
CREDENTIALS_JSON = ROOT / "credentials.json"
TOKEN_JSON = ROOT / "token.json"
OUTPUT_DIR = ROOT / "outputs_long"
OUTPUT_DIR.mkdir(exist_ok=True)
FONTS_DIR = ROOT / "fonts"
BRAND_DIR = ROOT / "brand"
_default_bg = r"C:\Users\pc\OneDrive\Masaüstü\youtube uzun videolar"
BG_VIDEO_DIR = Path(os.getenv("BG_VIDEO_DIR", _default_bg))

load_dotenv(ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = "J2FGlQG8Gd7x8uEDt2H8"
ELEVENLABS_MODEL = "eleven_multilingual_v2"

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
VIDEO_W, VIDEO_H = 1920, 1080
COLD_OPEN_DUR = 8.0
INTRO_DUR = 5.0
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "")
MUSIC_DIR = Path(r"C:\Users\pc\OneDrive\Masaüstü\youtube uzun video müzik")
INTRO_MUSIC_PATH = MUSIC_DIR / "intro muzik.mp3"


# --------- 1. Script (Gemini) ---------
SYSTEM_PROMPT = """You are a YouTube script writer for a psychology education channel.
Write a 5-minute video script (~650-700 words) about the given topic.

Structure:
- Hook (0-10s): Start mid-action, provocative question or surprising fact
- Problem definition: What is this and why does it happen
- Section 1: Core psychology mechanism
- Section 2: Real-life examples or consequences
- Section 3: What you can do about it (awareness / reframe)
- Awareness question: Direct question to make viewer reflect
- Outro: 1-2 sentences wrapping up the key insight
- CTA: "Like and subscribe if this resonated with you."

Rules:
- Conversational English, NOT academic tone
- STRICT word count: 650-700 words. Count before returning.
- No emojis, no markdown, no sound effect brackets
- Short punchy sentences. Vary rhythm.
- Write WORDS YOU WANT EMPHASIZED in ALL CAPS (1-2 per sentence max)

Return ONLY valid JSON with keys:
- "script": the full spoken voiceover (650-700 words)
- "title": YouTube title, SEO-optimized, under 60 chars, no clickbait
- "description": 150-200 word YouTube description with keywords
- "tags": list of 10-15 relevant tags (strings)
- "thumbnail_text": 3-5 word hook for thumbnail overlay
"""


def generate_script(topic):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY .env'de yok")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash-lite")
    prompt = f"{SYSTEM_PROMPT}\n\nTopic: {topic}"
    resp = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.85,
            response_mime_type="application/json",
        ),
    )
    raw = resp.text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(raw)
    print(f"[script] Konu: {topic}")
    print(f"[script] Baslik: {data['title']}")
    word_count = len(data["script"].split())
    print(f"[script] Kelime sayisi: {word_count}")
    return data


# --------- 2. TTS + subtitles (ElevenLabs) ---------
LONG_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat Bold,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,1,0,2,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s - h * 3600 - m * 60
    cs = int(round((sec - int(sec)) * 100))
    if cs == 100:
        cs = 0
        sec += 1
    return f"{h}:{m:02d}:{int(sec):02d}.{cs:02d}"


class _WordCue:
    def __init__(self, content, start_s, end_s):
        self.content = content
        self.start = timedelta(seconds=start_s)
        self.end = timedelta(seconds=end_s)


def _char_to_word_cues(characters, start_times, end_times):
    cues, word_chars, word_start, word_end = [], [], 0.0, 0.0
    for char, start, end in zip(characters, start_times, end_times):
        if char in (" ", "\n", "\t"):
            if word_chars:
                cues.append(_WordCue("".join(word_chars), word_start, word_end))
                word_chars = []
        else:
            if not word_chars:
                word_start = start
            word_chars.append(char)
            word_end = end
    if word_chars:
        cues.append(_WordCue("".join(word_chars), word_start, word_end))
    return cues


def _build_ass_long(cues, ass_path):
    lines = [LONG_ASS_HEADER]
    group_size = 4
    for i in range(0, len(cues), group_size):
        group = cues[i:i + group_size]
        if not group:
            continue
        start_s = group[0].start.total_seconds()
        end_s = group[-1].end.total_seconds()
        text_chunk = " ".join(c.content.upper() for c in group)
        lines.append(
            f"Dialogue: 0,{_ass_time(start_s)},{_ass_time(end_s)},Default,,0,0,0,,{text_chunk}"
        )
    Path(ass_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _split_ass(cues, split_at, cold_ass_path, main_ass_path):
    """Split word cues at split_at seconds into two ASS files."""
    cold_cues = [c for c in cues if c.start.total_seconds() < split_at]
    main_cues = [
        _WordCue(c.content,
                 c.start.total_seconds() - split_at,
                 c.end.total_seconds() - split_at)
        for c in cues if c.start.total_seconds() >= split_at
    ]
    _build_ass_long(cold_cues, cold_ass_path)
    _build_ass_long(main_cues, main_ass_path)


def generate_voice_long(text, audio_path, ass_path):
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY .env'de yok")
    client = ElevenLabsClient(api_key=ELEVENLABS_API_KEY)
    response = client.text_to_speech.convert_with_timestamps(
        voice_id=ELEVENLABS_VOICE_ID,
        text=text,
        model_id=ELEVENLABS_MODEL,
        output_format="mp3_44100_128",
    )
    Path(audio_path).write_bytes(base64.b64decode(response.audio_base_64))
    al = response.alignment
    cues = _char_to_word_cues(
        al.characters,
        al.character_start_times_seconds,
        al.character_end_times_seconds,
    )
    _build_ass_long(cues, ass_path)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    duration = float(out.stdout.strip())
    print(f"[voice] {duration:.1f}s ses uretildi -> {Path(audio_path).name}")
    return duration, cues


# --------- 3. Thumbnail ---------
THUMB_FONT_CANDIDATES = [
    str(FONTS_DIR / "Montserrat-Bold.ttf"),
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
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
    """16:9 (1280x720) branded thumbnail for YouTube custom thumbnail upload."""
    target_w, target_h = 1280, 720
    workdir = Path(out_path).parent
    frame_path = workdir / "_thumb_frame.png"
    res = subprocess.run(
        ["ffmpeg", "-y", "-i", str(bg_video_path), "-vframes", "1", "-q:v", "2", str(frame_path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or not frame_path.exists():
        raise RuntimeError("Thumbnail frame alinamadi")

    img = Image.open(frame_path).convert("RGB")
    w, h = img.size
    target_ratio = target_w / target_h
    src_ratio = w / h
    if src_ratio > target_ratio:
        new_w = int(h * target_ratio)
        img = img.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
    else:
        new_h = int(w / target_ratio)
        img = img.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
    img = img.resize((target_w, target_h), Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(radius=8))
    dark = Image.new("RGB", img.size, (0, 0, 0))
    img = Image.blend(img, dark, 0.45)
    img = img.convert("RGBA")

    text = (thumbnail_text or "").upper().strip() or "PSYCHOLOGY"
    draw = ImageDraw.Draw(img)
    max_text_width = target_w - int(target_w * 0.12)
    font_size = max(60, min(140, target_h // 4))
    while font_size > 40:
        font = _pick_thumb_font(font_size)
        lines = _wrap_lines(text, font, max_text_width, draw)
        line_h = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
        total_h = len(lines) * (line_h + 16)
        widest = max((draw.textbbox((0, 0), ln, font=font)[2] for ln in lines), default=0)
        if widest <= max_text_width and total_h <= target_h * 0.6:
            break
        font_size -= 10
    font = _pick_thumb_font(font_size)
    lines = _wrap_lines(text, font, max_text_width, draw)
    line_h = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
    total_h = len(lines) * (line_h + 16)
    y = (target_h - total_h) // 2
    shift = max(3, font_size // 28)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (target_w - (bbox[2] - bbox[0])) // 2
        draw.text((x - shift, y), line, font=font, fill=(0, 229, 255, 220))
        draw.text((x + shift, y), line, font=font, fill=(255, 0, 128, 220))
        draw.text((x, y + 3), line, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h + 16

    logo_path = BRAND_DIR / "profile.png"
    if logo_path.exists():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((100, 100), Image.LANCZOS)
            img.paste(logo, (40, target_h - logo.size[1] - 40), logo)
        except Exception:
            pass

    img.convert("RGB").save(out_path, "PNG", optimize=True)
    try:
        frame_path.unlink()
    except OSError:
        pass
    print(f"[thumb] Hazir -> {Path(out_path).name} {target_w}x{target_h}")


# --------- 4. Intro + cold open ---------
def _render_intro_clip(workdir):
    """Brain Static intro: siyah bg + logo fade in/out + intro muzigi. 5 saniye."""
    logo_path = BRAND_DIR / "profile.png"
    out_path = workdir / "_intro.mp4"
    has_logo = logo_path.exists()
    has_music = INTRO_MUSIC_PATH.exists()

    # Timing: 0-1s fade in, 1-3s visible, 3-4s fade out, 4-5s black
    fade_in_d = 1.0
    fade_out_st = 3.0
    fade_out_d = 1.0

    inputs = [
        "-f", "lavfi", "-i",
        f"color=c=black:s={VIDEO_W}x{VIDEO_H}:d={INTRO_DUR}:r=30",
    ]
    fc = []
    next_idx = 1

    if has_logo:
        inputs += ["-loop", "1", "-t", str(INTRO_DUR), "-i", str(logo_path)]
        fc.append(
            f"[{next_idx}:v]"
            f"scale=400:400:force_original_aspect_ratio=decrease,"
            f"pad=400:400:(ow-iw)/2:(oh-ih)/2:color=black@0,"
            f"format=rgba,"
            f"fade=t=in:st=0:d={fade_in_d}:alpha=1,"
            f"fade=t=out:st={fade_out_st}:d={fade_out_d}:alpha=1"
            f"[logo]"
        )
        logo_y = "(H-h)/2" if not CHANNEL_NAME else "(H-h)/2-50"
        fc.append(f"[0:v][logo]overlay=(W-w)/2:{logo_y}[v1]")
        next_idx += 1
        prev_v = "[v1]"
    else:
        prev_v = "[0:v]"

    if CHANNEL_NAME:
        font_path = _ffmpeg_path(FONTS_DIR / "Montserrat-Bold.ttf")
        escaped = CHANNEL_NAME.replace("'", r"\'").replace(":", r"\:")
        fc.append(
            f"{prev_v}"
            f"drawtext=fontfile='{font_path}':text='{escaped}':"
            f"fontsize=60:fontcolor=white:x=(w-text_w)/2:y=(h+400)/2+10:"
            f"alpha='if(lt(t,{fade_in_d}),t/{fade_in_d},"
            f"if(gt(t,{fade_out_st}),({fade_out_st}+{fade_out_d}-t)/{fade_out_d},1))'"
            f"[v2]"
        )
        prev_v = "[v2]"

    fc.append(
        f"{prev_v}"
        f"fade=t=in:st=0:d={fade_in_d},"
        f"fade=t=out:st={fade_out_st}:d={fade_out_d}"
        f"[outv]"
    )

    if has_music:
        inputs += ["-i", str(INTRO_MUSIC_PATH)]
        fc.append(
            f"[{next_idx}:a]"
            f"atrim=0:{INTRO_DUR},"
            f"afade=t=in:st=0:d={fade_in_d},"
            f"afade=t=out:st={fade_out_st}:d={fade_out_d}"
            f"[outa]"
        )
    else:
        inputs += ["-f", "lavfi", "-t", str(INTRO_DUR), "-i", "anullsrc=r=44100:cl=stereo"]
        fc.append(f"[{next_idx}:a]asetpts=PTS-STARTPTS[outa]")

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", ";".join(fc),
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(INTRO_DUR),
        "-pix_fmt", "yuv420p",
        "-r", "30",
        str(out_path),
    ]
    print(f"[intro] {INTRO_DUR}s Brain Static intro render ediliyor...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-2000:])
        raise RuntimeError("Intro render basarisiz")
    print(f"[intro] Hazir -> {out_path.name}")
    return out_path


def _render_cold_open_clip(bg_videos, audio_path, cold_ass_path, output_path):
    """ilk COLD_OPEN_DUR saniye: bg video + voice, muzik yok."""
    bg = bg_videos[0]
    ass_str = _ffmpeg_path(cold_ass_path)
    fonts_str = _ffmpeg_path(FONTS_DIR)
    cmd = [
        "ffmpeg", "-y",
        "-t", str(COLD_OPEN_DUR), "-i", str(bg),
        "-t", str(COLD_OPEN_DUR), "-i", str(audio_path),
        "-filter_complex",
        f"[0:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_W}:{VIDEO_H},setpts=PTS-STARTPTS,format=yuv420p,fps=30,setsar=1,"
        f"subtitles='{ass_str}':fontsdir='{fonts_str}'[outv]",
        "-map", "[outv]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(COLD_OPEN_DUR),
        "-pix_fmt", "yuv420p",
        "-r", "30",
        str(output_path),
    ]
    print(f"[cold_open] {COLD_OPEN_DUR}s cold open render ediliyor...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-2000:])
        raise RuntimeError("Cold open render basarisiz")
    print(f"[cold_open] Hazir -> {Path(output_path).name}")


def _concat_three(part_a, part_b, part_c, output_path):
    """cold open + intro + main body birlestirir."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(part_a),
        "-i", str(part_b),
        "-i", str(part_c),
        "-filter_complex",
        "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[outv][outa]",
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        str(output_path),
    ]
    print("[concat] cold open + intro + main body birlestiriliyor...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-2000:])
        raise RuntimeError("3-part concat basarisiz")
    print(f"[concat] Final -> {Path(output_path).name}")


# --------- 5. Background video segments ---------
def _pick_music():
    files = [f for f in sorted(MUSIC_DIR.glob("*.mp3")) if f != INTRO_MUSIC_PATH]
    if not files:
        print("[music] Arka plan muzik bulunamadi, muzik eklenmeyecek")
        return None
    chosen = random.choice(files)
    print(f"[music] Secilen: {chosen.name}")
    return chosen


def _get_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _plan_segments(video_paths, target_duration, chunk_size=75.0):
    """Alternate between video_paths in chunk_size chunks until target_duration covered."""
    durations = {str(p): _get_duration(p) for p in video_paths}
    positions = {str(p): 0.0 for p in video_paths}
    segments = []
    elapsed = 0.0
    idx = 0
    while elapsed < target_duration:
        vp = video_paths[idx % len(video_paths)]
        key = str(vp)
        remaining = target_duration - elapsed
        seg_dur = min(chunk_size, remaining + 1.0)
        ss = positions[key]
        if ss + seg_dur > durations[key]:
            positions[key] = 0.0
            ss = 0.0
        positions[key] = ss + seg_dur
        segments.append((vp, ss, seg_dur))
        elapsed += seg_dur
        idx += 1
    return segments


def _ffmpeg_path(p):
    s = str(p).replace("\\", "/")
    return s.replace(":", "\\:", 1) if ":" in s else s


def render_long_video(segments, audio_path, ass_path, output_path, music_path=None, voice_start=0.0):
    xfade_dur = 0.5
    n = len(segments)
    ass_str = _ffmpeg_path(ass_path)
    fonts_str = _ffmpeg_path(FONTS_DIR)

    inputs = []
    for vp, ss, dur in segments:
        inputs.extend(["-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", str(vp)])
    if voice_start > 0:
        inputs.extend(["-ss", f"{voice_start:.3f}", "-i", str(audio_path)])
    else:
        inputs.extend(["-i", str(audio_path)])
    audio_input_idx = n

    music_input_idx = None
    if music_path and Path(music_path).exists():
        inputs.extend(["-i", str(music_path)])
        music_input_idx = n + 1

    fc_parts = []
    for i, (_, _, dur) in enumerate(segments):
        fc_parts.append(
            f"[{i}:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H},setpts=PTS-STARTPTS,"
            f"format=yuv420p,fps=30,setsar=1[v{i}]"
        )

    if n == 1:
        bg_label = "[v0]"
    else:
        prev = "[v0]"
        for i in range(1, n):
            offset = i * (segments[i - 1][2] - xfade_dur)
            label = f"[x{i}]"
            fc_parts.append(
                f"{prev}[v{i}]xfade=transition=fade:duration={xfade_dur:.3f}"
                f":offset={offset:.3f}{label}"
            )
            prev = label
        bg_label = prev

    fc_parts.append(f"{bg_label}subtitles='{ass_str}':fontsdir='{fonts_str}'[outv]")

    if music_input_idx is not None:
        voice_dur = _get_duration(audio_path) - voice_start
        fade_out_st = max(0.0, voice_dur - 3.0)
        fc_parts.append(
            f"[{music_input_idx}:a]"
            f"aloop=loop=-1:size=2000000000,"
            f"atrim=0:{voice_dur:.3f},"
            f"asetpts=PTS-STARTPTS,"
            f"volume=0.30,"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fade_out_st:.3f}:d=3"
            f"[bgm]"
        )
        fc_parts.append(
            f"[{audio_input_idx}:a][bgm]amix=inputs=2:duration=first:normalize=0[outa]"
        )
        map_audio = "[outa]"
    else:
        map_audio = f"{audio_input_idx}:a"

    fc = ";".join(fc_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[outv]", "-map", map_audio,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        str(output_path),
    ]
    print(f"[render] {n} segment, FFmpeg calisiyor...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-3000:])
        raise RuntimeError("FFmpeg basarisiz")
    print(f"[render] Hazir -> {output_path.name}")


# --------- 6. YouTube upload ---------
def get_youtube_creds():
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


def _next_sunday_publish_at():
    now = datetime.now(timezone.utc)
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 8:
        days_until_sunday = 7
    target = (now + timedelta(days=days_until_sunday)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    return target.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def upload_to_youtube(video_path, title, description, tags, publish_at, thumbnail_path=None):
    creds = get_youtube_creds()
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": "22",  # Education
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True,
                            chunksize=8 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = response["id"]
    print(f"[upload] Yuklendi: https://youtube.com/watch?v={video_id}")
    print(f"[upload] Public olacak: {publish_at}")

    if thumbnail_path and Path(thumbnail_path).exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png"),
            ).execute()
            print(f"[upload] Thumbnail yuklendi: {Path(thumbnail_path).name}")
        except Exception as e:
            print(f"[upload] Thumbnail yuklenemedi (devam): {e}")

    return video_id


# --------- 7. Pipeline ---------
def run_pipeline(skip_upload=False):
    from topics_long import TOPICS
    topic = random.choice(TOPICS)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    workdir = OUTPUT_DIR / ts
    workdir.mkdir(exist_ok=True)
    print(f"[main] Calisma klasoru: {workdir}")

    meta = generate_script(topic)
    (workdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    audio_path = workdir / "voice_long.mp3"
    ass_path = workdir / "subs_long.ass"
    duration, cues = generate_voice_long(meta["script"], audio_path, ass_path)

    bg_videos = sorted(BG_VIDEO_DIR.glob("*.mp4"))
    if not bg_videos:
        raise RuntimeError(f"MP4 bulunamadi: {BG_VIDEO_DIR}")
    print(f"[bg] {len(bg_videos)} arkaplan videosu bulundu")

    thumb_path = workdir / "thumbnail.png"
    try:
        generate_thumbnail(bg_videos[0], meta.get("thumbnail_text", ""), thumb_path)
    except Exception as e:
        print(f"[thumb] uretilemedi, atlanacak: {e}")
        thumb_path = None

    music_path = _pick_music()

    # --- Altyazıları cold open / main olarak ikiye böl ---
    cold_ass_path = workdir / "subs_cold_open.ass"
    main_ass_path = workdir / "subs_main.ass"
    _split_ass(cues, COLD_OPEN_DUR, cold_ass_path, main_ass_path)

    # --- 1. Cold open: ilk 8s, müzik yok ---
    cold_open_path = workdir / "_cold_open.mp4"
    _render_cold_open_clip(bg_videos, audio_path, cold_ass_path, cold_open_path)

    # --- 2. Brain Static intro: 5s ---
    intro_path = _render_intro_clip(workdir)

    # --- 3. Ana gövde: 8s sonrası, arka plan müziği ---
    main_dur = duration - COLD_OPEN_DUR
    segments_main = _plan_segments(bg_videos, main_dur, chunk_size=75.0)
    print(f"[bg] {len(segments_main)} segment (main), toplam ~{sum(s[2] for s in segments_main):.0f}s")
    body_path = workdir / "_body.mp4"
    render_long_video(
        segments_main, audio_path, main_ass_path, body_path,
        music_path=music_path, voice_start=COLD_OPEN_DUR,
    )

    # --- Birleştir: cold open → intro → main body ---
    out_path = workdir / "long_video.mp4"
    _concat_three(cold_open_path, intro_path, body_path, out_path)
    for p in [cold_open_path, intro_path, body_path]:
        try:
            p.unlink()
        except OSError:
            pass

    if skip_upload:
        print(f"[main] Yukleme atlandi. Video: {out_path}")
        return out_path

    publish_at = _next_sunday_publish_at()
    print(f"[main] publishAt: {publish_at} (Pazar TR 11:00)")
    upload_to_youtube(
        out_path, meta["title"], meta["description"], meta["tags"],
        publish_at, thumbnail_path=thumb_path,
    )
    print("[main] Tamam.")
    return out_path


# --------- Main ---------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--auth", action="store_true", help="YouTube OAuth (ilk kez)")
    p.add_argument("--no-upload", action="store_true", help="Uret ama yukleme")
    args = p.parse_args()

    if args.auth:
        get_youtube_creds()
        print("[auth] Token kaydedildi:", TOKEN_JSON)
        return

    run_pipeline(skip_upload=args.no_upload)


if __name__ == "__main__":
    main()
