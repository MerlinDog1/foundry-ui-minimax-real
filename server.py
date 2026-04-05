#!/usr/bin/env python3
"""
Foundry UI Server
- Style / Upscale / Trace: runs locally via Python scripts
- Image generation: handled by a background agent process
  (spawned separately; calls OpenClaw image_generate tool)
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from flask import Flask, jsonify, request, send_file
from PIL import Image

BASE_DIR   = Path(__file__).resolve().parent
WORKING    = BASE_DIR / "working"
QUEUE_DIR  = BASE_DIR / "queue"
FOUNDRY_DIR = Path("/data/data/com.termux/files/home/.openclaw/workspace/skills/foundry")
SCRIPTS_DIR = FOUNDRY_DIR / "scripts"

WORKING.mkdir(exist_ok=True)
QUEUE_DIR.mkdir(exist_ok=True)

GEN_PATH     = WORKING / "generated.png"
STYLED_PATH  = WORKING / "styled.png"
UPSCALED_PATH = WORKING / "upscaled.png"
TRACED_SVG   = WORKING / "traced.svg"
TRACED_PNG   = WORKING / "traced.png"

app = Flask(__name__, static_folder=".", static_url_path="")


def run_cmd(cmd, cwd=None, timeout=120):
    r = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stderr[:300]}")
    return True


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return app.send_static_file("index.html")


@app.post("/generate")
def generate():
    """Queue a generation job. Returns job_id immediately."""
    body = request.get_json(force=True) or {}
    prompt = (body.get("prompt") or "")[:800]
    if not prompt.strip():
        return jsonify({"error": "Prompt required"}), 400

    job_id = str(uuid.uuid4())
    (QUEUE_DIR / f"{job_id}.json").write_text(json.dumps({
        "id": job_id, "prompt": prompt,
        "aspect": body.get("aspect", "1:1"),
        "resolution": body.get("resolution", "1K"),
        "status": "pending", "progress": 0,
        "created_at": time.time()
    }))
    return jsonify({"ok": True, "job_id": job_id, "stage": "generating"})


@app.get("/status/<job_id>")
def status(job_id):
    path = QUEUE_DIR / f"{job_id}.json"
    if not path.exists():
        return jsonify({"status": "unknown"}), 404
    job = json.loads(path.read_text())
    return jsonify({"status": job["status"], "progress": job.get("progress", 0)})


@app.post("/upload")
def upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file"}), 400
    img = Image.open(f.stream).convert("RGB")
    img.save(GEN_PATH, "PNG")
    return jsonify({"ok": True, "stage": "generated"})


@app.post("/style")
def style():
    body = (request.get_json(force=True) or {})
    style_name = body.get("style", "woodcut")
    if not GEN_PATH.exists():
        return jsonify({"error": "Generate or upload an image first"}), 400
    try:
        run_cmd([
            sys.executable, str(SCRIPTS_DIR / "apply_style.py"),
            str(GEN_PATH), str(STYLED_PATH), style_name
        ], cwd=str(FOUNDRY_DIR))
        return jsonify({"ok": True, "stage": "styled"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/upscale")
def upscale():
    source = STYLED_PATH if STYLED_PATH.exists() else GEN_PATH
    if not source.exists():
        return jsonify({"error": "No source image"}), 400
    try:
        run_cmd([
            sys.executable, str(SCRIPTS_DIR / "upscale_image.py"),
            str(source), str(UPSCALED_PATH), "--scale", "4"
        ], cwd=str(FOUNDRY_DIR))
        return jsonify({"ok": True, "stage": "upscaled"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/trace")
def trace():
    body = (request.get_json(force=True) or {})
    speckle = str(int(body.get("speckle", 4)))
    out_fmt = body.get("format", "svg")
    source = (UPSCALED_PATH if UPSCALED_PATH.exists()
              else STYLED_PATH if STYLED_PATH.exists()
              else GEN_PATH)
    if not source.exists():
        return jsonify({"error": "No source image"}), 400
    try:
        run_cmd([
            sys.executable, str(SCRIPTS_DIR / "trace_vector.py"),
            str(source), str(TRACED_SVG), "--bw", "--filter-speckle", speckle
        ], cwd=str(FOUNDRY_DIR))

        if out_fmt == "png":
            try:
                import cairosvg
                cairosvg.svg2png(url=str(TRACED_SVG), write_to=str(TRACED_PNG))
            except ImportError:
                # Fallback: serve SVG as PNG via PIL
                from PIL import Image
                img = Image.open(TRACED_SVG).convert("RGB")
                img.save(TRACED_PNG, format="PNG")
            except Exception as e:
                return jsonify({"error": f"SVG done, PNG conversion failed: {e}"}), 500

        return jsonify({"ok": True, "stage": "traced", "format": out_fmt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/preview/<stage>")
def preview(stage):
    m = {"generated": GEN_PATH, "styled": STYLED_PATH,
         "upscaled": UPSCALED_PATH, "traced": TRACED_SVG, "traced-png": TRACED_PNG}
    p = m.get(stage)
    if not p or not p.exists():
        return jsonify({"error": "Not found"}), 404
    return send_file(str(p),
                     mimetype="image/svg+xml" if p.suffix == ".svg" else "image/png")


@app.get("/download/<name>")
def download(name):
    m = {"generated.png": GEN_PATH, "styled.png": STYLED_PATH,
         "upscaled.png": UPSCALED_PATH, "traced.svg": TRACED_SVG,
         "traced.png": TRACED_PNG}
    p = m.get(name)
    if not p or not p.exists():
        return jsonify({"error": "Not found"}), 404
    return send_file(str(p), as_attachment=True, download_name=name)


# ─── Generation worker (background thread) ───────────────────────────────────
# Uses the Foundry skill's generate_image.py which knows how to bridge to OpenClaw

def generation_worker():
    import glob, sys
    while True:
        time.sleep(4)
        for jf in sorted(glob.glob(str(QUEUE_DIR / "*.json"))):
            job = json.loads(Path(jf).read_text())
            if job.get("status") != "pending":
                continue

            job_id = job["id"]
            print(f"[worker] generating: {job['prompt'][:50]}", flush=True)

            # Write running status
            job["status"] = "running"
            Path(jf).write_text(json.dumps(job))

            out = WORKING / f"gen_{job_id}.png"
            try:
                # Call the skill's generate_image.py which bridges to OpenClaw
                r = subprocess.run(
                    [sys.executable, str(FOUNDRY_DIR / "scripts" / "generate_image.py"),
                     "--prompt", job["prompt"],
                     "--filename", str(out),
                     "--aspect", job.get("aspect", "1:1"),
                     "--resolution", job.get("resolution", "1K")],
                    capture_output=True, text=True, timeout=180
                )
                if r.returncode == 0 and out.exists():
                    shutil.copy(out, GEN_PATH)
                    job["status"] = "done"
                    job["progress"] = 100
                    print(f"[worker] done: {job_id}", flush=True)
                else:
                    raise RuntimeError(r.stderr or "no output")
            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e)[:200]
                print(f"[worker] FAILED {job_id}: {e}", flush=True)

            Path(jf).write_text(json.dumps(job))


threading.Thread(target=generation_worker, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787, debug=False)
