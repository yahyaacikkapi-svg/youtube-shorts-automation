"""Standalone YouTube thumbnail diagnostic + uploader.

The pipeline keeps producing videos with no thumbnail despite all the
code-level fixes (16:9 spec, --upload-thumbnail flag, loud errors). This
tool isolates the thumbnail upload from the rest of the pipeline so we
can: (a) salvage already-uploaded videos by setting the thumbnail
retroactively, (b) see the real channel-side reason it keeps failing.

Usage:
  python set_thumbnail.py --diagnose
      Print channel status (verified, made-for-kids, etc.) so we know
      whether custom thumbnails are eligible at all.

  python set_thumbnail.py --video-id ID
      Find latest outputs/<ts>/thumbnail_yt.png (or thumbnail.png),
      upload it as VIDEO_ID's thumbnail, then verify via videos.list.

  python set_thumbnail.py --video-id ID --thumbnail PATH
      Use a specific thumbnail file.

  python set_thumbnail.py --video-id ID --regenerate
      Regenerate a fresh 1280x720 thumbnail from the latest run's first
      bg clip + that run's meta.json["thumbnail_text"], then upload.
"""

import argparse
import json
import sys
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from shorts_automation import (
    OUTPUT_DIR,
    generate_thumbnail,
    get_youtube_creds,
)


def diagnose(youtube):
    """Print everything we can about the channel that affects custom
    thumbnail eligibility, so failures stop being mysterious."""
    print("=" * 60)
    print("CHANNEL DIAGNOSTIC")
    print("=" * 60)
    resp = youtube.channels().list(
        part="snippet,status,brandingSettings,contentDetails",
        mine=True,
    ).execute()
    items = resp.get("items", [])
    if not items:
        print("[!] No channel found for this OAuth token.")
        return
    ch = items[0]
    snippet = ch.get("snippet", {})
    status = ch.get("status", {})
    branding = ch.get("brandingSettings", {}).get("channel", {})
    print(f"  Channel ID:           {ch.get('id')}")
    print(f"  Title:                {snippet.get('title')}")
    print(f"  Country:              {snippet.get('country')}")
    print(f"  Made for Kids:        {status.get('madeForKids')}")
    print(f"  Self-declared MFK:    {status.get('selfDeclaredMadeForKids')}")
    print(f"  Long uploads status:  {status.get('longUploadsStatus')}  "
          "(allowed = phone-verified)")
    print(f"  Privacy status:       {status.get('privacyStatus')}")
    print(f"  Branding country:     {branding.get('country')}")
    print(f"  Default language:     {branding.get('defaultLanguage')}")
    print()
    print("Likely thumbnail blockers if any of these are flagged:")
    print("  * longUploadsStatus != 'allowed'  -> phone verify")
    print("  * madeForKids = True              -> custom thumb disabled")
    print()


def upload_thumbnail(youtube, video_id, thumbnail_path):
    """Upload + verify. Raises on any anomaly."""
    p = Path(thumbnail_path)
    if not p.exists():
        raise FileNotFoundError(f"Thumbnail dosyasi yok: {p}")
    size_kb = p.stat().st_size / 1024
    print(f"[upload] Thumbnail: {p}  ({size_kb:.1f} KB)")
    if p.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(f"Thumbnail > 2MB ({size_kb:.0f} KB) — YouTube reddeder")

    print(f"[upload] thumbnails.set videoId={video_id} ...")
    resp = youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(p), mimetype="image/png"),
    ).execute()
    print(f"[upload] API response: {json.dumps(resp, indent=2)[:400]}")

    print(f"[verify] videos.list videoId={video_id} ...")
    v = youtube.videos().list(part="snippet,status", id=video_id).execute()
    items = v.get("items", [])
    if not items:
        raise RuntimeError(f"Video {video_id} bulunamadi (silinmis veya yetki yok)")
    snippet = items[0]["snippet"]
    thumbs = snippet.get("thumbnails", {})
    print("[verify] Snippet thumbnails URLs:")
    for size_name, info in thumbs.items():
        print(f"    {size_name:10}  {info.get('url')}")

    default_url = thumbs.get("default", {}).get("url", "")
    is_custom = "/hqdefault_custom" in default_url or "_custom" in default_url
    if is_custom:
        print("[verify] OK — URL'de '_custom' var, custom thumbnail uygulanmis.")
    else:
        print("[verify] DIKKAT — URL'lerde '_custom' yok. YouTube custom thumb'i ya"
              " kabul etmedi ya da Shorts icin asla gostermiyor olabilir.")
        print("[verify] Bu noktada: (a) channel diagnostic'e bak, (b) YT Studio'da"
              " video sayfasini ac, oradan custom thumb'i goruyor mu kontrol et.")


def find_latest_thumbnail():
    runs = sorted(OUTPUT_DIR.glob("*/"), key=lambda p: p.name, reverse=True)
    for run in runs:
        for name in ("thumbnail_yt.png", "thumbnail.png"):
            p = run / name
            if p.exists():
                return p
    return None


def regenerate_for_run(target_size=(1280, 720)):
    """Use the most recent run's bg_0.mp4 + meta.json to regenerate a
    fresh thumbnail. Returns path to the new file."""
    runs = sorted(OUTPUT_DIR.glob("*/"), key=lambda p: p.name, reverse=True)
    for run in runs:
        bg = run / "bg_0.mp4"
        meta_p = run / "meta.json"
        if bg.exists() and meta_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            text = meta.get("thumbnail_text", "BRAIN STATIC")
            out = run / f"thumbnail_regen_{target_size[0]}x{target_size[1]}.png"
            generate_thumbnail(bg, text, out, size=target_size)
            return out
    raise RuntimeError("Hicbir run'da bg_0.mp4 + meta.json bulunamadi")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diagnose", action="store_true",
                    help="Channel status'unu dump et, baska bir sey yapma")
    ap.add_argument("--video-id", help="Hedef video ID (watch?v= sonrasi)")
    ap.add_argument("--thumbnail", help="Yuklenecek thumbnail dosya yolu")
    ap.add_argument("--regenerate", action="store_true",
                    help="En son run'in bg_0.mp4 + meta.json'undan 1280x720 thumb'i sifirdan uret")
    args = ap.parse_args()

    creds = get_youtube_creds()
    youtube = build("youtube", "v3", credentials=creds)

    diagnose(youtube)

    if args.diagnose:
        return

    if not args.video_id:
        print("[!] --video-id gerekli (veya sadece --diagnose).")
        sys.exit(2)

    if args.regenerate:
        thumb_path = regenerate_for_run()
    elif args.thumbnail:
        thumb_path = Path(args.thumbnail)
    else:
        thumb_path = find_latest_thumbnail()
        if thumb_path is None:
            print("[!] outputs/ altinda thumbnail bulunamadi. --thumbnail PATH ver"
                  " veya --regenerate kullan.")
            sys.exit(2)
        print(f"[i] Otomatik secildi: {thumb_path}")

    upload_thumbnail(youtube, args.video_id, thumb_path)


if __name__ == "__main__":
    main()
