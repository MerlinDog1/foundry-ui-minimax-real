#!/usr/bin/env python3
import base64
import io
import json
import os
import subprocess
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_file
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
WORKING = BASE_DIR / "working"
WORKING.mkdir(exist_ok=True)

FOUNDRY_DIR = Path("/data/data/com.termux/files/home/.openclaw/workspace/skills/foundry")
SCRIPTS_DIR = FOUNDRY_DIR / "scripts"

GEN_PATH = WORKING / "generated.png"
STYLED_PATH = WORKING / "styled.png"
UPSCALED_PATH = WORKING / "upscaled.png"
TRACED_SVG_PATH = WORKING / "traced.svg"
TRACED_PNG_PATH = WORKING / "traced.png"

API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyC2SyH-PuLShCgQdd5-GJAfdIoCc4OoSns")
MODEL = "gemini-2.5-flash-image"

app = Flask(__name__, static_folder=".", static_url_path="")


def run_cmd(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return {"stdout": proc.stdout, "stderr": proc.stderr}


def _extract_inline_data(payload):
    candidates = payload.get("candidates", [])
    for c in candidates:
        content = c.get("content", {})
        for part in content.get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return inline["data"]
    return None


def gemini_generate_png(prompt, aspect="1:1", resolution="1K"):
    final_prompt = (
        f"{prompt}. Style hint: woodcut style. "
        "Binary / 1-bit Art, No Halftones / No Gradients, CNC-ready / Laser-cut / Vector Paths. "
        f"Aspect ratio {aspect}. Resolution target {resolution}."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": final_prompt}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        },
    }

    r = requests.post(url, json=payload, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini generation failed ({r.status_code}): {r.text}")
    data = r.json()
    b64 = _extract_inline_data(data)
    if not b64:
        raise RuntimeError(f"No image data in Gemini response: {json.dumps(data)[:500]}")
    png_bytes = base64.b64decode(b64)
    with open(GEN_PATH, "wb") as f:
        f.write(png_bytes)

    # Normalize to png via PIL (in case JPEG returned)
    img = Image.open(GEN_PATH).convert("RGB")
    img.save(GEN_PATH, format="PNG")


@app.get("/")
def root():
    return app.send_static_file("index.html")


@app.post("/upload")
def upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded"}), 400
    img = Image.open(f.stream).convert("RGB")
    img.save(GEN_PATH, format="PNG")
    return jsonify({"ok": True, "stage": "generated"})


@app.post("/generate")
def generate():
    body = request.get_json(force=True)
    prompt = (body.get("prompt") or "")[:800]
    aspect = body.get("aspect", "1:1")
    resolution = body.get("resolution", "1K")
    if not prompt.strip():
        return jsonify({"error": "Prompt is required"}), 400
    gemini_generate_png(prompt, aspect=aspect, resolution=resolution)
    return jsonify({"ok": True, "stage": "generated", "path": str(GEN_PATH.name)})


@app.post("/style")
def style():
    body = request.get_json(force=True)
    style_name = body.get("style", "woodcut")
    if not GEN_PATH.exists():
        return jsonify({"error": "No generated/uploaded source image"}), 400
    out = run_cmd([
        "python",
        str(SCRIPTS_DIR / "apply_style.py"),
        str(GEN_PATH),
        str(STYLED_PATH),
        style_name,
    ], cwd=str(FOUNDRY_DIR))
    return jsonify({"ok": True, "stage": "styled", **out})


@app.post("/upscale")
def upscale():
    source = STYLED_PATH if STYLED_PATH.exists() else GEN_PATH
    if not source.exists():
        return jsonify({"error": "No input image for upscale"}), 400
    out = run_cmd([
        "python",
        str(SCRIPTS_DIR / "upscale_image.py"),
        str(source),
        str(UPSCALED_PATH),
        "--scale",
        "4",
    ], cwd=str(FOUNDRY_DIR))
    return jsonify({"ok": True, "stage": "upscaled", **out})


@app.post("/trace")
def trace():
    body = request.get_json(force=True)
    speckle = str(int(body.get("speckle", 4)))
    out_format = body.get("format", "svg")
    source = UPSCALED_PATH if UPSCALED_PATH.exists() else (STYLED_PATH if STYLED_PATH.exists() else GEN_PATH)
    if not source.exists():
        return jsonify({"error": "No input image for trace"}), 400

    out = run_cmd([
        "python",
        str(SCRIPTS_DIR / "trace_vector.py"),
        str(source),
        str(TRACED_SVG_PATH),
        "--bw",
        "--filter-speckle",
        speckle,
    ], cwd=str(FOUNDRY_DIR))

    if out_format == "png":
        # Raster preview/export from svg
        try:
            import cairosvg
            cairosvg.svg2png(url=str(TRACED_SVG_PATH), write_to=str(TRACED_PNG_PATH))
        except Exception as e:
            return jsonify({"error": f"Trace SVG created but PNG conversion failed: {e}", "ok": False}), 500

    return jsonify({"ok": True, "stage": "traced", "format": out_format, **out})


@app.get("/preview/<stage>")
def preview(stage):
    mapping = {
        "generated": GEN_PATH,
        "styled": STYLED_PATH,
        "upscaled": UPSCALED_PATH,
        "traced": TRACED_SVG_PATH,
        "traced-png": TRACED_PNG_PATH,
    }
    p = mapping.get(stage)
    if not p or not p.exists():
        return jsonify({"error": "Not found"}), 404
    if p.suffix.lower() == ".svg":
        return send_file(str(p), mimetype="image/svg+xml")
    return send_file(str(p), mimetype="image/png")


@app.get("/download/<name>")
def download(name):
    mapping = {
        "generated.png": GEN_PATH,
        "styled.png": STYLED_PATH,
        "upscaled.png": UPSCALED_PATH,
        "traced.svg": TRACED_SVG_PATH,
        "traced.png": TRACED_PNG_PATH,
    }
    p = mapping.get(name)
    if not p or not p.exists():
        return jsonify({"error": "Not found"}), 404
    return send_file(str(p), as_attachment=True, download_name=name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787, debug=False)
