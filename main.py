from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from flask import Flask, abort, jsonify, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from services.pipeline_adapters import (
    add_text_overlay_to_image,
    compose_multiple_overlays_image,
    compose_multiple_overlays_video,
    generate_gemini_script,
    generate_tts_audio,
    remove_image_background,
    remove_video_background,
    resize_background_image,
    sanitize_filename,
    search_and_download_pexels,
    standardize_pose_image,
)


APP_DIR = Path(__file__).resolve().parent
PUBLIC_CONFIG_PATH = APP_DIR / "config" / "app_config.json"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = int(os.environ.get("VIDEO_APP_PORT", "5000"))
VISIBLE_TOOL_KEYS = [
    "script_generation",
    "audio_generation",
    "background_remove",
    "overlay_compose",
    "image_resize",
    "text_overlay",
    "pexels_search",
]


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def utc_now() -> datetime:
    return datetime.now(UTC)


def today_key() -> str:
    return utc_now().strftime("%Y-%m-%d")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def get_runtime_root() -> Path:
    android_private = os.environ.get("ANDROID_PRIVATE")
    root = Path(android_private) if android_private else APP_DIR / ".runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_runtime_dir(name: str) -> Path:
    path = get_runtime_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_inputs_root() -> Path:
    return get_runtime_dir("inputs")


def get_outputs_root() -> Path:
    return get_runtime_dir("outputs")


def get_state_root() -> Path:
    return get_runtime_dir("state")


def get_public_config() -> dict[str, Any]:
    return load_json(PUBLIC_CONFIG_PATH, {})


def get_state_path(name: str) -> Path:
    return get_state_root() / name


def get_secrets() -> dict[str, str]:
    stored = load_json(get_state_path("secrets.json"), {})
    if not isinstance(stored, dict):
        stored = {}

    env_overrides = {
        "gemini_api_key": os.environ.get("VIDEO_APP_GEMINI_API_KEY", ""),
        "pexels_api_key": os.environ.get("VIDEO_APP_PEXELS_API_KEY", ""),
        "license_service_token": os.environ.get("VIDEO_APP_LICENSE_TOKEN", ""),
    }

    for key, value in env_overrides.items():
        if value:
            stored[key] = value
    return stored


def save_runtime_secrets(updates: dict[str, str]) -> list[str]:
    secrets = get_secrets()
    saved = []
    for key, value in updates.items():
        if value.strip():
            secrets[key] = value.strip()
            saved.append(key)
    if saved:
        save_json(get_state_path("secrets.json"), secrets)
    return saved


def get_device_id() -> str:
    path = get_state_path("device.json")
    state = load_json(path, {})
    existing = state.get("device_id")
    if existing:
        return str(existing)
    device_id = str(uuid.uuid4())
    save_json(path, {"device_id": device_id, "created_at": utc_now().isoformat()})
    return device_id


def get_license_state() -> dict[str, Any]:
    state = load_json(get_state_path("license.json"), {})
    if not isinstance(state, dict):
        return {}
    return state


def get_plan_status(config: dict[str, Any]) -> dict[str, Any]:
    state = get_license_state()
    expires_at = parse_iso(state.get("expires_at"))
    active = bool(expires_at and expires_at > utc_now() and state.get("plan") == "pro")
    return {
        "plan": "pro" if active else "free",
        "active": active,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "activation_key_tail": (state.get("activation_key") or "")[-6:],
        "activated_at": state.get("activated_at"),
        "checkout_url": config.get("billing", {}).get("checkout_url", ""),
        "price_label": config.get("billing", {}).get("price_label", ""),
        "plan_name": config.get("billing", {}).get("plan_name", "Pro"),
    }


def get_usage_state() -> dict[str, Any]:
    state = load_json(get_state_path("usage.json"), {})
    if not isinstance(state, dict):
        return {}
    return state


def get_feature_limit(config: dict[str, Any], feature_key: str, plan: str) -> int | None:
    tier_limits = config.get("limits", {}).get(plan, {}).get("daily_quotas", {})
    if feature_key not in tier_limits:
        return None
    value = tier_limits.get(feature_key)
    if value is None or int(value) < 0:
        return None
    return int(value)


def get_usage_summary(config: dict[str, Any], plan_status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    usage_state = get_usage_state().get(today_key(), {})
    summary: dict[str, dict[str, Any]] = {}
    for feature_key, feature_cfg in config.get("features", {}).items():
        used = int(usage_state.get(feature_key, 0))
        limit = get_feature_limit(config, feature_key, plan_status["plan"])
        remaining = None if limit is None else max(0, limit - used)
        summary[feature_key] = {
            "label": feature_cfg.get("label", feature_key),
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "internet_required": bool(feature_cfg.get("internet_required")),
            "tier_note": feature_cfg.get("tier_note", ""),
        }
    return summary


def record_usage(feature_key: str) -> None:
    usage = get_usage_state()
    day = today_key()
    day_state = usage.setdefault(day, {})
    day_state[feature_key] = int(day_state.get(feature_key, 0)) + 1
    save_json(get_state_path("usage.json"), usage)


def ensure_quota(config: dict[str, Any], feature_key: str, plan_status: dict[str, Any]) -> None:
    usage_state = get_usage_state().get(today_key(), {})
    current = int(usage_state.get(feature_key, 0))
    limit = get_feature_limit(config, feature_key, plan_status["plan"])
    if limit is not None and current >= limit:
        raise RuntimeError(f"Daily limit reached for {config['features'][feature_key]['label']} on the {plan_status['plan'].title()} tier.")


def has_connectivity(config: dict[str, Any]) -> bool:
    network_cfg = config.get("network", {})
    host = network_cfg.get("connectivity_probe_host", "1.1.1.1")
    port = int(network_cfg.get("connectivity_probe_port", 53))
    timeout = int(network_cfg.get("timeout_seconds", 3))
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def require_internet_if_needed(config: dict[str, Any], feature_key: str) -> None:
    feature_cfg = config.get("features", {}).get(feature_key, {})
    if feature_cfg.get("internet_required") and not has_connectivity(config):
        raise RuntimeError(f"{feature_cfg.get('label', feature_key)} needs internet. Please reconnect and try again.")


def save_upload(file_storage, subfolder: str) -> Path:
    if not file_storage or not file_storage.filename:
        raise RuntimeError("Please upload a file first.")

    folder = get_inputs_root() / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}_{secure_filename(file_storage.filename)}"
    destination = folder / unique_name
    file_storage.save(destination)
    return destination


def register_output(feature_key: str, path: Path, title: str) -> dict[str, Any]:
    outputs_path = get_state_path("outputs.json")
    state = load_json(outputs_path, [])
    if not isinstance(state, list):
        state = []

    record = {
        "id": uuid.uuid4().hex,
        "feature": feature_key,
        "title": title,
        "relative_path": path.relative_to(get_outputs_root()).as_posix(),
        "created_at": utc_now().isoformat(),
    }
    state.insert(0, record)

    keep_latest = int(get_public_config().get("storage", {}).get("keep_latest_outputs", 60))
    extra = state[keep_latest:]
    state = state[:keep_latest]
    save_json(outputs_path, state)

    for item in extra:
        candidate = get_outputs_root() / item.get("relative_path", "")
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                pass

    record["download_url"] = url_for("download_output", filename=record["relative_path"])
    return record


def get_outputs_feed() -> list[dict[str, Any]]:
    records = load_json(get_state_path("outputs.json"), [])
    if not isinstance(records, list):
        return []

    feed = []
    for item in records:
        relative = item.get("relative_path", "")
        if not relative:
            continue
        path = get_outputs_root() / relative
        if not path.exists():
            continue
        feed.append({**item, "download_url": url_for("download_output", filename=relative)})
    return feed


def mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def verify_license(config: dict[str, Any], activation_key: str, email: str) -> dict[str, Any]:
    billing_cfg = config.get("billing", {})
    verify_url = billing_cfg.get("activation_verify_url", "").strip()
    secrets = get_secrets()

    if verify_url:
        payload = {
            "activation_key": activation_key,
            "email": email,
            "device_id": get_device_id(),
            "app_id": config.get("app", {}).get("package_name", "video-app"),
        }
        headers = {"Content-Type": "application/json"}
        token = secrets.get("license_service_token", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urlrequest.Request(
            verify_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urlrequest.urlopen(req, timeout=30) as response:
                verified = json.loads(response.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"License verification failed: {body}") from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"License verification failed: {exc.reason}") from exc

        if not verified.get("valid"):
            raise RuntimeError(verified.get("message") or "Activation key is not valid.")

        return {
            "plan": "pro",
            "activation_key": activation_key,
            "email": email,
            "expires_at": verified.get("expires_at"),
            "activated_at": utc_now().isoformat(),
            "source": "remote",
        }

    demo_prefix = billing_cfg.get("demo_key_prefix", "DEMO-PRO-")
    if activation_key.strip().upper().startswith(demo_prefix.upper()):
        expires_at = utc_now() + timedelta(days=int(billing_cfg.get("demo_duration_days", 30)))
        return {
            "plan": "pro",
            "activation_key": activation_key,
            "email": email,
            "expires_at": expires_at.isoformat(),
            "activated_at": utc_now().isoformat(),
            "source": "demo",
        }

    raise RuntimeError("No live license verification endpoint is configured. Add one in config/app_config.json or use a demo key.")


def ensure_output_folder(feature_key: str) -> Path:
    path = get_outputs_root() / feature_key
    path.mkdir(parents=True, exist_ok=True)
    return path


app = Flask(__name__, template_folder=str(APP_DIR / "templates"), static_folder=str(APP_DIR / "static"))


@app.route("/")
def index():
    config = get_public_config()
    plan_status = get_plan_status(config)
    usage_summary = get_usage_summary(config, plan_status)
    secrets = get_secrets()
    return render_template(
        "dashboard.html",
        config=config,
        plan_status=plan_status,
        usage_summary=usage_summary,
        outputs=get_outputs_feed(),
        online=has_connectivity(config),
        saved_secrets={key: mask_key(value) for key, value in secrets.items() if value},
        visible_tools=[{"key": key, **config["features"][key]} for key in VISIBLE_TOOL_KEYS],
    )


@app.route("/plans")
def plans():
    config = get_public_config()
    plan_status = get_plan_status(config)
    return render_template(
        "plans.html",
        config=config,
        plan_status=plan_status,
        online=has_connectivity(config),
    )


@app.route("/tool/<tool_key>")
def tool_page(tool_key: str):
    config = get_public_config()
    if tool_key not in config.get("features", {}) or tool_key not in VISIBLE_TOOL_KEYS:
        abort(404)
    return render_template(
        "tool.html",
        config=config,
        tool_key=tool_key,
        tool=config["features"][tool_key],
        plan_status=get_plan_status(config),
        usage_summary=get_usage_summary(config, get_plan_status(config)),
        online=has_connectivity(config),
        voice_options=list(config.get("ai", {}).get("tts", {}).get("voice_profiles", {}).keys()),
    )


@app.route("/downloads/<path:filename>")
def download_output(filename: str):
    root = get_outputs_root().resolve()
    path = (root / filename).resolve()
    if root not in path.parents and path != root:
        abort(404)
    if not path.exists():
        abort(404)
    return send_from_directory(str(path.parent), path.name, as_attachment=True)


@app.route("/api/status")
def api_status():
    config = get_public_config()
    plan_status = get_plan_status(config)
    return jsonify(
        {
            "ok": True,
            "online": has_connectivity(config),
            "plan_status": plan_status,
            "usage_summary": get_usage_summary(config, plan_status),
            "outputs": get_outputs_feed(),
        }
    )


@app.post("/api/secrets")
def api_save_secrets():
    config = get_public_config()
    allowed_slots = set(config.get("secrets", {}).get("slots", []))
    payload = {}
    for key in allowed_slots:
        incoming = request.form.get(key, "")
        if incoming.strip():
            payload[key] = incoming.strip()

    saved = save_runtime_secrets(payload)
    return jsonify({"ok": True, "message": "Runtime secrets saved in app-private storage.", "saved": saved})


@app.post("/api/activate")
def api_activate():
    config = get_public_config()
    activation_key = request.form.get("activation_key", "").strip()
    email = request.form.get("email", "").strip()
    if not activation_key:
        return jsonify({"ok": False, "message": "Activation key is required."}), 400

    try:
        verified = verify_license(config, activation_key, email)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    save_json(get_state_path("license.json"), verified)
    return jsonify({"ok": True, "message": "Pro tier activated.", "plan_status": get_plan_status(config)})


@app.post("/api/deactivate")
def api_deactivate():
    path = get_state_path("license.json")
    if path.exists():
        path.unlink()
    config = get_public_config()
    return jsonify({"ok": True, "message": "Plan reset to Free tier.", "plan_status": get_plan_status(config)})


@app.post("/api/script/generate")
def api_script_generate():
    config = get_public_config()
    plan_status = get_plan_status(config)
    feature_key = "script_generation"

    try:
        ensure_quota(config, feature_key, plan_status)
        require_internet_if_needed(config, feature_key)
        text = generate_gemini_script(
            {key: value for key, value in request.form.items()},
            config.get("ai", {}).get("script_generation", {}),
            get_secrets().get("gemini_api_key", ""),
        )
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    output_dir = ensure_output_folder(feature_key)
    title_seed = request.form.get("title_seed", "script")
    file_path = output_dir / f"{sanitize_filename(title_seed, 'script')}_{uuid.uuid4().hex[:6]}.txt"
    file_path.write_text(text, encoding="utf-8")

    record_usage(feature_key)
    output = register_output(feature_key, file_path, "Generated Script")
    return jsonify({"ok": True, "message": "Script generated.", "script_text": text, "output": output})


@app.post("/api/audio/generate")
def api_audio_generate():
    config = get_public_config()
    plan_status = get_plan_status(config)
    feature_key = "audio_generation"
    text = request.form.get("tts_text", "")
    speaker = request.form.get("speaker", "hamza")

    try:
        ensure_quota(config, feature_key, plan_status)
        require_internet_if_needed(config, feature_key)
        output_dir = ensure_output_folder(feature_key)
        output_path = output_dir / f"{sanitize_filename(speaker, 'voice')}_{uuid.uuid4().hex[:6]}.mp3"
        generate_tts_audio(text, speaker, output_path, config.get("ai", {}).get("tts", {}))
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    record_usage(feature_key)
    output = register_output(feature_key, output_path, f"TTS Audio ({speaker.title()})")
    return jsonify({"ok": True, "message": "Audio generated.", "output": output})


@app.post("/api/background/remove")
def api_background_remove():
    config = get_public_config()
    plan_status = get_plan_status(config)
    feature_key = "background_remove"

    try:
        ensure_quota(config, feature_key, plan_status)
        upload = save_upload(request.files.get("media_file"), feature_key)
        output_dir = ensure_output_folder(feature_key)
        if upload.suffix.lower() in {".mp4", ".mov", ".webm"}:
            output_path = output_dir / f"{upload.stem}_transparent.mov"
            remove_video_background(upload, output_path)
        else:
            output_path = output_dir / f"{upload.stem}_transparent.png"
            remove_image_background(upload, output_path)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    record_usage(feature_key)
    output = register_output(feature_key, output_path, "Background Removed")
    return jsonify({"ok": True, "message": "Background removed.", "output": output})


@app.post("/api/compose/overlay")
def api_overlay_compose():
    config = get_public_config()
    plan_status = get_plan_status(config)
    feature_key = "overlay_compose"

    try:
        ensure_quota(config, feature_key, plan_status)
        base_file = save_upload(request.files.get("base_media"), f"{feature_key}/base")
        placement_specs = json.loads(request.form.get("placements_json", "[]") or "[]")
        overlay_files = request.files.getlist("overlay_media")
        saved_overlays = []
        overlay_dir = get_inputs_root() / f"{feature_key}/overlay"
        overlay_dir.mkdir(parents=True, exist_ok=True)

        for index, upload in enumerate(overlay_files):
            if not upload or not upload.filename:
                continue
            destination = overlay_dir / f"{uuid.uuid4().hex}_{secure_filename(upload.filename)}"
            upload.save(destination)
            spec = placement_specs[index] if index < len(placement_specs) else {}
            spec["path"] = str(destination)
            saved_overlays.append(spec)

        if not saved_overlays:
            raise RuntimeError("Please add at least one overlay file.")

        output_dir = ensure_output_folder(feature_key)
        if base_file.suffix.lower() in {".mp4", ".mov", ".webm"}:
            output_path = output_dir / f"{base_file.stem}_overlay.mp4"
            compose_multiple_overlays_video(base_file, saved_overlays, output_path)
        else:
            output_path = output_dir / f"{base_file.stem}_overlay.png"
            compose_multiple_overlays_image(base_file, saved_overlays, output_path)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    record_usage(feature_key)
    output = register_output(feature_key, output_path, "Overlay Composed")
    return jsonify({"ok": True, "message": "Overlay composed.", "output": output})


@app.post("/api/layout/save")
def api_layout_save():
    config = get_public_config()
    plan_status = get_plan_status(config)
    feature_key = "position_preset"

    try:
        ensure_quota(config, feature_key, plan_status)
        scene_name = request.form.get("scene_name", "").strip() or "default_scene"
        asset_name = request.form.get("asset_name", "").strip() or "asset"
        preset = {
            "x": int(request.form.get("preset_x", 0) or 0),
            "y": int(request.form.get("preset_y", 0) or 0),
            "width": int(request.form.get("preset_width", 0) or 0),
            "height": int(request.form.get("preset_height", 0) or 0),
            "saved_at": utc_now().isoformat(),
        }
        path = get_state_path("layout_presets.json")
        state = load_json(path, {})
        scene = state.setdefault(scene_name, {})
        scene[asset_name] = preset
        save_json(path, state)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    record_usage(feature_key)
    return jsonify({"ok": True, "message": "Layout preset saved.", "preset": preset})


@app.post("/api/background/resize")
def api_background_resize():
    config = get_public_config()
    plan_status = get_plan_status(config)
    feature_key = "image_resize"

    try:
        ensure_quota(config, feature_key, plan_status)
        upload = save_upload(request.files.get("background_file"), feature_key)
        target_width = int(request.form.get("target_width", 720) or 720)
        target_height = int(request.form.get("target_height", 1280) or 1280)
        output_dir = ensure_output_folder(feature_key)
        output_path = output_dir / f"{upload.stem}_bg_ready.jpg"
        resize_background_image(upload, output_path, target_width, target_height)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    record_usage(feature_key)
    output = register_output(feature_key, output_path, "Image Resized")
    return jsonify({"ok": True, "message": "Image resized.", "output": output})


@app.post("/api/text/overlay")
def api_text_overlay():
    config = get_public_config()
    plan_status = get_plan_status(config)
    feature_key = "text_overlay"

    try:
        ensure_quota(config, feature_key, plan_status)
        upload = save_upload(request.files.get("background_file"), feature_key)
        text = request.form.get("overlay_text", "").strip()
        x = int(request.form.get("text_x", 40) or 40)
        y = int(request.form.get("text_y", 40) or 40)
        font_size = int(request.form.get("font_size", 48) or 48)
        fill_color = request.form.get("fill_color", "#ffffff").strip() or "#ffffff"
        output_dir = ensure_output_folder(feature_key)
        output_path = output_dir / f"{upload.stem}_text.png"
        add_text_overlay_to_image(upload, output_path, text, x, y, font_size, fill_color)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    record_usage(feature_key)
    output = register_output(feature_key, output_path, "Text Overlay")
    return jsonify({"ok": True, "message": "Text overlay created.", "output": output})


@app.post("/api/pexels/search")
def api_pexels_search():
    config = get_public_config()
    plan_status = get_plan_status(config)
    feature_key = "pexels_search"
    query = request.form.get("query", "").strip()
    asset_type = request.form.get("asset_type", "image").strip().lower()

    try:
        ensure_quota(config, feature_key, plan_status)
        require_internet_if_needed(config, feature_key)
        output_dir = ensure_output_folder(feature_key)
        items = search_and_download_pexels(
            query=query,
            asset_type=asset_type,
            output_dir=output_dir,
            api_key=get_secrets().get("pexels_api_key", ""),
            per_page=int(config.get("pexels", {}).get("per_page", 4)),
        )
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    if not items:
        return jsonify({"ok": False, "message": "No Pexels assets found."}), 404

    record_usage(feature_key)
    outputs = []
    for item in items:
        path = Path(item["local_path"])
        outputs.append(register_output(feature_key, path, item["title"]))
    return jsonify({"ok": True, "message": f"Downloaded {len(outputs)} Pexels asset(s).", "outputs": outputs})


if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=False)
