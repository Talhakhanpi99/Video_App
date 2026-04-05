from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter


YOUTUBE_SHORTS_VOICES = {
    "hamza": {"voice": "hi-IN-MadhurNeural", "rate": "+15%", "pitch": "+15Hz"},
    "rafay": {"voice": "hi-IN-MadhurNeural", "rate": "+20%", "pitch": "+25Hz"},
    "sayam": {"voice": "hi-IN-MadhurNeural", "rate": "+10%", "pitch": "+10Hz"},
    "sana": {"voice": "hi-IN-SwaraNeural", "rate": "+22%", "pitch": "+5Hz"},
}

ANIMATION_VOICES = {
    "hamza": {"voice": "hi-IN-MadhurNeural", "rate": "+5%", "pitch": "+15Hz"},
    "rafay": {"voice": "hi-IN-MadhurNeural", "rate": "+10%", "pitch": "+25Hz"},
    "sayam": {"voice": "hi-IN-MadhurNeural", "rate": "+0%", "pitch": "+10Hz"},
    "sana": {"voice": "hi-IN-SwaraNeural", "rate": "+5%", "pitch": "+15Hz"},
}


def sanitize_filename(name: str, fallback: str = "output") -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]+', "", (name or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:80] or fallback


def _json_post(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 60) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_to_path(url: str, destination: Path, headers: dict[str, str] | None = None, timeout: int = 120) -> Path:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    return destination


def build_script_prompt(user_inputs: dict[str, str], script_settings: dict[str, Any]) -> str:
    style_rules = "\n".join(f"- {rule}" for rule in script_settings.get("style_rules", []))
    character_count = int(user_inputs.get("character_count") or 1)

    return f"""
You are an expert scriptwriter for short scenes, skits, episodes, ad-style dialogues, and dramatic or comedic scripts.

CRITICAL FIXED CONTEXT:
- Language: Roman Urdu / Roman Hindi mix
- Audience region: Pakistan and India
- Tone: modern desi internet vibe
- Return format: plain readable script only
- Never return JSON
- Keep the script copyable
- Keep dialogue lines punchy and natural
- The user may ask for one scene, many scenes, a short script, or a longer dramatic setup
- Adapt to whatever story structure the user asks for instead of forcing one template

FOLLOW THESE STYLE RULES:
{style_rules}

USER BRIEF:
- Main instruction: {user_inputs.get("instructions", "").strip()}
- Character count: {character_count}
- Character names: {user_inputs.get("character_names", "").strip()}
- Desired tone: {user_inputs.get("tone", "").strip()}
- Desired format: {user_inputs.get("format_type", "").strip()}
- Desired length: {user_inputs.get("length_hint", "").strip()}
- Location or world: {user_inputs.get("location", "").strip()}
- Extra constraints: {user_inputs.get("extra_notes", "").strip()}
- Extra instructions: {user_inputs.get("extra_notes", "").strip()}

STRUCTURE REQUIRED:
- If the user asks for a single scene, return exactly that scene.
- If the user asks for multiple scenes, label them clearly.
- If the user asks for dialogue only, return dialogue only.
- If the user asks for a short film style script, include scene headings.
- Always keep the output easy to copy.

Hard rules:
- Use only the characters provided by the user.
- If no names are provided, invent suitable names only when needed.
- Avoid old fashioned Urdu.
- Avoid full English sentences unless a brand or slang word sounds better in English.
- Keep it advertiser-friendly.
- Every 2 to 3 lines should move conflict, humor, or tension forward.
- Make it flexible and universal rather than tied to one pipeline scene format.
""".strip()


def generate_gemini_script(
    user_inputs: dict[str, str],
    script_settings: dict[str, Any],
    gemini_api_key: str,
) -> str:
    if not gemini_api_key:
        raise RuntimeError("Gemini API key is missing. Save it in the app secrets panel first.")

    model_name = script_settings.get("model", "gemini-2.5-flash")
    payload = {
        "contents": [{"parts": [{"text": build_script_prompt(user_inputs, script_settings)}]}],
        "generationConfig": {
            "temperature": script_settings.get("temperature", 0.85),
            "topP": script_settings.get("top_p", 0.95),
            "topK": script_settings.get("top_k", 40),
            "maxOutputTokens": script_settings.get("max_output_tokens", 1600),
        },
    }

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={urllib.parse.quote(gemini_api_key)}"

    try:
        response = _json_post(endpoint, payload, timeout=int(script_settings.get("timeout_seconds", 90)))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini request failed: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini request failed: {exc.reason}") from exc

    candidates = response.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")

    parts = candidates[0].get("content", {}).get("parts", [])
    text_parts = [part.get("text", "") for part in parts if part.get("text")]
    generated = "\n".join(text_parts).strip()
    if not generated:
        raise RuntimeError("Gemini returned an empty script.")

    return generated


def generate_tts_audio(text: str, speaker: str, output_path: Path, config: dict[str, Any]) -> Path:
    if not text.strip():
        raise RuntimeError("Text is required for audio generation.")

    try:
        import edge_tts  # type: ignore
    except ImportError as exc:
        raise RuntimeError("edge-tts is not installed. Add it to your runtime if you want on-device TTS.") from exc

    voice_map = config.get("voice_profiles", {})
    if voice_map:
        merged = voice_map
        default_key = config.get("default_speaker", next(iter(voice_map.keys()), "male_voice_1"))
        voice_cfg = merged.get(speaker.lower(), merged.get(default_key, next(iter(merged.values()))))
    else:
        source_pool = YOUTUBE_SHORTS_VOICES if config.get("voice_source") == "youtube_shorts" else ANIMATION_VOICES
        merged = {**source_pool}
        voice_cfg = merged.get(speaker.lower(), merged.get("hamza", ANIMATION_VOICES["hamza"]))

    async def _run() -> None:
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice_cfg["voice"],
            rate=voice_cfg.get("rate", "+0%"),
            pitch=voice_cfg.get("pitch", "+0Hz"),
        )
        await communicate.save(str(output_path))

    asyncio.run(_run())
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("TTS generation finished but the output file is empty.")
    return output_path


def resize_background_image(source_path: Path, output_path: Path, target_w: int, target_h: int) -> Path:
    img = Image.open(source_path).convert("RGB")
    img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)

    bg = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(40))

    x = (target_w - img.width) // 2
    y = (target_h - img.height) // 2
    bg.paste(img, (x, y))
    bg.save(output_path, quality=95)
    return output_path


def standardize_pose_image(
    source_path: Path,
    output_path: Path,
    canvas_width: int,
    canvas_height: int,
    character_height_percent: float,
) -> Path:
    char_img = Image.open(source_path).convert("RGBA")
    target_height = int(canvas_height * character_height_percent)
    aspect_ratio = char_img.width / char_img.height
    new_width = int(target_height * aspect_ratio)
    char_img = char_img.resize((new_width, target_height), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    paste_x = (canvas_width - new_width) // 2
    paste_y = canvas_height - target_height
    canvas.paste(char_img, (paste_x, paste_y), char_img)
    canvas.save(output_path, "PNG")
    return output_path


def compose_overlay_image(
    base_path: Path,
    overlay_path: Path,
    output_path: Path,
    x: int,
    y: int,
    width: int | None = None,
    height: int | None = None,
    opacity: float = 1.0,
) -> Path:
    base = Image.open(base_path).convert("RGBA")
    overlay = Image.open(overlay_path).convert("RGBA")

    if width and height:
        overlay = overlay.resize((width, height), Image.Resampling.LANCZOS)

    if opacity < 1.0:
        alpha = overlay.getchannel("A")
        alpha = alpha.point(lambda px: int(px * max(0.0, min(1.0, opacity))))
        overlay.putalpha(alpha)

    base.paste(overlay, (x, y), overlay)
    base.save(output_path, "PNG")
    return output_path


def compose_multiple_overlays_image(
    base_path: Path,
    overlay_specs: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    base = Image.open(base_path).convert("RGBA")
    for spec in overlay_specs:
        overlay = Image.open(spec["path"]).convert("RGBA")
        width = int(spec.get("width") or overlay.width)
        height = int(spec.get("height") or overlay.height)
        x = int(spec.get("x") or 0)
        y = int(spec.get("y") or 0)
        opacity = float(spec.get("opacity") or 1.0)

        overlay = overlay.resize((width, height), Image.Resampling.LANCZOS)
        if opacity < 1.0:
            alpha = overlay.getchannel("A")
            alpha = alpha.point(lambda px: int(px * max(0.0, min(1.0, opacity))))
            overlay.putalpha(alpha)
        base.paste(overlay, (x, y), overlay)

    base.save(output_path, "PNG")
    return output_path


def compose_overlay_video(
    base_path: Path,
    overlay_path: Path,
    output_path: Path,
    x: int,
    y: int,
    width: int | None = None,
    height: int | None = None,
    opacity: float = 1.0,
) -> Path:
    try:
        from moviepy import CompositeVideoClip, ImageClip, VideoFileClip  # type: ignore
        import moviepy.video.fx as vfx  # type: ignore
    except ImportError as exc:
        raise RuntimeError("moviepy is not installed. Add it if you want video overlay inside the APK.") from exc

    base_clip = VideoFileClip(str(base_path))
    ext = overlay_path.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        overlay_clip = ImageClip(str(overlay_path)).with_duration(base_clip.duration)
    else:
        overlay_clip = VideoFileClip(str(overlay_path)).without_audio().with_duration(base_clip.duration)

    if width or height:
        resize_kwargs = {}
        if width:
            resize_kwargs["width"] = width
        if height:
            resize_kwargs["height"] = height
        overlay_clip = overlay_clip.with_effects([vfx.Resize(**resize_kwargs)])

    overlay_clip = overlay_clip.with_position((x, y)).with_opacity(opacity)
    final = CompositeVideoClip([base_clip, overlay_clip], size=base_clip.size)
    final.write_videofile(str(output_path), codec="libx264", audio_codec="aac", logger=None)
    return output_path


def compose_multiple_overlays_video(
    base_path: Path,
    overlay_specs: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    try:
        from moviepy import CompositeVideoClip, ImageClip, VideoFileClip  # type: ignore
        import moviepy.video.fx as vfx  # type: ignore
    except ImportError as exc:
        raise RuntimeError("moviepy is not installed. Add it if you want video overlay inside the APK.") from exc

    base_clip = VideoFileClip(str(base_path))
    layers: list[Any] = [base_clip]

    for spec in overlay_specs:
        overlay_path = Path(spec["path"])
        ext = overlay_path.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            overlay_clip = ImageClip(str(overlay_path)).with_duration(base_clip.duration)
        else:
            overlay_clip = VideoFileClip(str(overlay_path)).without_audio().with_duration(base_clip.duration)

        width = spec.get("width")
        height = spec.get("height")
        resize_kwargs = {}
        if width:
            resize_kwargs["width"] = int(width)
        if height:
            resize_kwargs["height"] = int(height)
        if resize_kwargs:
            overlay_clip = overlay_clip.with_effects([vfx.Resize(**resize_kwargs)])

        overlay_clip = overlay_clip.with_position((int(spec.get("x") or 0), int(spec.get("y") or 0))).with_opacity(float(spec.get("opacity") or 1.0))
        layers.append(overlay_clip)

    final = CompositeVideoClip(layers, size=base_clip.size)
    final.write_videofile(str(output_path), codec="libx264", audio_codec="aac", logger=None)
    return output_path


def add_text_overlay_to_image(
    base_path: Path,
    output_path: Path,
    text: str,
    x: int,
    y: int,
    font_size: int = 48,
    fill_color: str = "#ffffff",
) -> Path:
    from PIL import ImageDraw, ImageFont

    base = Image.open(base_path).convert("RGBA")
    draw = ImageDraw.Draw(base)

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), text, font=font, fill=fill_color)
    base.save(output_path, "PNG")
    return output_path


def remove_image_background(source_path: Path, output_path: Path) -> Path:
    try:
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from torchvision.transforms.functional import normalize  # type: ignore
        from transformers import AutoModelForImageSegmentation  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Local image background removal needs torch, torchvision, transformers, and numpy. "
            "This adapter was wired from poses_bg.py, but those optional packages are not installed."
        ) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForImageSegmentation.from_pretrained("briaai/RMBG-1.4", trust_remote_code=True)
    model.to(device)
    model.eval()

    orig_im = Image.open(source_path).convert("RGB")
    width, height = orig_im.size
    resized = orig_im.resize((1024, 1024), Image.BILINEAR)
    image_tensor = torch.tensor(np.array(resized)).permute(2, 0, 1).float().to(device) / 255.0
    image_tensor = normalize(image_tensor, [0.5, 0.5, 0.5], [1.0, 1.0, 1.0]).unsqueeze(0)

    with torch.no_grad():
        result = model(image_tensor)

    mask = result[0][0].sigmoid().cpu().numpy().squeeze()
    mask_image = Image.fromarray((mask * 255).astype("uint8")).resize((width, height), Image.BILINEAR)
    rgba = orig_im.convert("RGBA")
    rgba.putalpha(mask_image)
    rgba.save(output_path, "PNG")
    return output_path


def remove_video_background(source_path: Path, output_path: Path) -> Path:
    command = [
        "backgroundremover",
        "-i",
        str(source_path),
        "-tv",
        "-o",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("backgroundremover CLI is not installed. This adapter mirrors clip_bg.py and needs that tool.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or "Video background removal failed.") from exc

    return output_path


def search_and_download_pexels(
    query: str,
    asset_type: str,
    output_dir: Path,
    api_key: str,
    per_page: int = 4,
) -> list[dict[str, str]]:
    if not api_key:
        raise RuntimeError("Pexels API key is missing. Save it in the app secrets panel first.")

    headers = {"Authorization": api_key}
    encoded_query = urllib.parse.quote(query.strip())
    results: list[dict[str, str]] = []

    if asset_type == "video":
        url = f"https://api.pexels.com/videos/search?query={encoded_query}&per_page={per_page}&orientation=portrait"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))

        for index, item in enumerate(payload.get("videos", []), start=1):
            files = item.get("video_files") or []
            if not files:
                continue
            best = next((file for file in files if file.get("width", 0) >= 720), files[0])
            destination = output_dir / f"{sanitize_filename(query, 'pexels')}_{index}.mp4"
            _download_to_path(best["link"], destination, timeout=120)
            results.append(
                {
                    "title": f"{query.title()} clip {index}",
                    "local_path": str(destination),
                    "source_url": best["link"],
                }
            )
        return results

    url = f"https://api.pexels.com/v1/search?query={encoded_query}&per_page={per_page}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))

    for index, item in enumerate(payload.get("photos", []), start=1):
        img_url = item.get("src", {}).get("large2x") or item.get("src", {}).get("original")
        if not img_url:
            continue
        destination = output_dir / f"{sanitize_filename(query, 'pexels')}_{index}.jpg"
        _download_to_path(img_url, destination, timeout=120)
        results.append(
            {
                "title": f"{query.title()} image {index}",
                "local_path": str(destination),
                "source_url": img_url,
            }
        )
    return results
