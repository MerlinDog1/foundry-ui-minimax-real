#!/usr/bin/env python3
"""
Generation worker — watches queue/ for pending jobs and processes them.
Uses subprocess to call node with a script that bridges to OpenClaw image_generate.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

QUEUE_DIR = Path(__file__).parent / "queue"
WORKING   = Path(__file__).parent / "working"
FOUNDRY_DIR = Path("/data/data/com.termux/files/home/.openclaw/workspace/skills/foundry")
SCRIPTS_DIR = FOUNDRY_DIR / "scripts"

# Template for node script that bridges to OpenClaw image_generate tool
NODE_SCRIPT_TEMPLATE = r"""
const path = require('path');
const fs = require('fs');

// Load OpenClaw's image_generate bridge
const openclawPath = '/data/data/com.termux/files/usr/lib/node_modules/openclaw/dist/index.js';

let imageGen;
try {
  const mod = require(openclawPath);
  imageGen = mod.image_generate || mod.generateImage || mod.default?.image_generate;
} catch(e) {
  console.error('OpenClaw bridge load failed:', e.message);
  process.exit(1);
}

const prompt = `%PROMPT%`;

(async () => {
  try {
    console.error('[gen] calling image_generate with prompt:', prompt.substring(0, 80));
    const result = await imageGen({
      prompt: prompt,
      aspect_ratio: '%ASPECT%',
      resolution: '%RES%'
    });
    console.error('[gen] result type:', typeof result, result ? result.substring(0,100) : 'null');
    // result is a media path or base64 data URL
    process.stdout.write(result || 'done');
    process.exit(0);
  } catch(e) {
    console.error('[gen] ERROR:', e.message);
    process.exit(1);
  }
})();
"""

def call_image_generate(prompt, aspect="1:1", resolution="1K"):
    """Call OpenClaw image_generate tool via node bridge."""
    script = NODE_SCRIPT_TEMPLATE \
        .replace("%PROMPT%", prompt.replace("`", "\\`").replace("$", "\\$")) \
        .replace("%ASPECT%", aspect) \
        .replace("%RES%", resolution)

    script_path = f"/tmp/foundry_gen_{os.getpid()}.mjs"
    Path(script_path).write_text(script)

    try:
        result = subprocess.run(
            ["node", "--experimental-vm-modules", script_path],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or "node script failed")
        return result.stdout.strip()
    finally:
        Path(script_path).unlink(missing_ok=True)


if __name__ == "__main__":
    import os, glob

    print("[generation_worker] started — watching queue/")
    while True:
        time.sleep(3)
        for job_file in sorted(glob.glob(str(QUEUE_DIR / "*.json"))):
            job = json.loads(Path(job_file).read_text())
            if job.get("status") != "pending":
                continue

            job_id = job["id"]
            print(f"[worker] processing job {job_id}: {job['prompt'][:60]}")
            job["status"] = "running"
            Path(job_file).write_text(json.dumps(job))

            try:
                result = call_image_generate(
                    prompt=job["prompt"],
                    aspect=job.get("aspect", "1:1"),
                    resolution=job.get("resolution", "1K")
                )
                print(f"[worker] generation result: {result[:80] if result else 'empty'}")

                # result is a data URL or file path — copy to working/generated.png
                if result:
                    if result.startswith("data:"):
                        import base64
                        header, data = result.split(",", 1)
                        png_bytes = base64.b64decode(data)
                        (WORKING / "generated.png").write_bytes(png_bytes)
                    elif result.startswith("/") or result.startswith("./"):
                        shutil.copy(result, WORKING / "generated.png")

                job["status"] = "done"
                job["progress"] = 100
                print(f"[worker] job {job_id} done")

            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e)[:200]
                print(f"[worker] job {job_id} FAILED: {e}")

            Path(job_file).write_text(json.dumps(job))
