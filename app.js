const styles = [
  "stippling","cross-hatching","woodcut","copperplate",
  "mezzotint","technical-sketch","minimalist-logo","etched-obsidian"
];
const prompts = {
  "woodcut":"Ancient samurai wolf crest on stormy cliff, woodcut style",
  "copperplate":"Victorian alchemist portrait with ornate border, copperplate style",
  "cross-hatching":"Mechanical raven blueprint over moonlit ruins, cross-hatching",
  "stippling":"Moonlit raven perched on obsidian branch, stipple art",
  "mezzotint":"Haunted lighthouse at midnight, mezzotint shadow mood",
  "technical-sketch":"Futuristic drone exploded-view, technical blueprint",
  "minimalist-logo":"Wolf head geometric emblem, sharp symmetric logo",
  "etched-obsidian":"Fractured crystal cave formation, etched obsidian art"
};

let selectedStyle = "woodcut";
let aspect = "1:1";
let resolution = "1K";
let running = false;

const $ = s => document.querySelector(s);
const setStatus = t => $("#status").textContent = t;
const setProgress = v => $("#bar").style.width = `${v}%`;

function initStyles() {
  const grid = $("#styleGrid");
  styles.forEach(s => {
    const d = document.createElement("button");
    d.className = `style ${s === selectedStyle ? "active" : ""}`;
    d.innerHTML = `<div class="swatch"></div><small>${s}</small>`;
    d.onclick = () => {
      selectedStyle = s;
      [...grid.children].forEach(c => c.classList.remove("active"));
      d.classList.add("active");
    };
    grid.appendChild(d);
  });
}

function wireChips(container, cb) {
  container.querySelectorAll(".chip").forEach(b => b.onclick = () => {
    container.querySelectorAll(".chip").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    cb(b.dataset.v);
  });
}

async function post(url, data, isForm = false) {
  const opt = { method: "POST" };
  if (isForm) {
    opt.body = data;
  } else {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(data || {});
  }
  const r = await fetch(url, opt);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

function refreshPreviews() {
  const t = Date.now();
  $("#pvGenerated").src = `/preview/generated?t=${t}`;
  $("#pvStyled").src   = `/preview/styled?t=${t}`;
  $("#pvUpscaled").src = `/preview/upscaled?t=${t}`;
  const traced = $("#pvTraced");
  if (traced.tagName === "IMG") {
    traced.src = `/preview/traced?t=${t}`;
  } else {
    traced.data = `/preview/traced?t=${t}`;
  }
}

async function pollJob(jobId, label = "Generating") {
  // Poll /status/{jobId} every 2s until done/failed
  return new Promise((resolve, reject) => {
    const interval = setInterval(async () => {
      try {
        const r = await fetch(`/status/${jobId}`);
        const j = await r.json();
        if (j.status === "running") {
          setStatus(`${label}… ${j.progress || 0}%`);
        } else if (j.status === "done") {
          clearInterval(interval);
          resolve();
        } else if (j.status === "failed") {
          clearInterval(interval);
          reject(new Error(`Generation failed`));
        } else if (j.status === "unknown") {
          clearInterval(interval);
          reject(new Error("Job not found"));
        }
      } catch (e) {
        // keep polling
      }
    }, 2000);
  });
}

function loadLib() {
  const lib = JSON.parse(localStorage.getItem("foundry-lib") || "[]");
  const wrap = $("#library");
  wrap.innerHTML = "";
  lib.forEach((it, idx) => {
    const d = document.createElement("div");
    d.className = "lib";
    d.innerHTML = `
      <img src="${it.thumb || ''}"/>
      <small>${it.style} · ${it.prompt?.substring(0, 20) || ''}</small>
      <div class="row">
        <button data-a="rerun">rerun</button>
        <button data-a="dl" onclick="window.location='/download/traced.svg'">dl svg</button>
        <button data-a="del">x</button>
      </div>`;
    d.querySelector("button[data-a='rerun']").onclick = () => {
      $("#prompt").value = it.prompt || "";
      selectedStyle = it.style || "woodcut";
      initStyles();
    };
    d.querySelector("button[data-a='del']").onclick = () => {
      lib.splice(idx, 1);
      localStorage.setItem("foundry-lib", JSON.stringify(lib));
      loadLib();
    };
    wrap.appendChild(d);
  });
}

async function run() {
  if (running) return;
  running = true;
  $("#runBtn").disabled = true;
  try {
    setProgress(5);
    setStatus("Starting…");
    const source = document.querySelector("input[name='source']:checked").value;

    if (source === "upload") {
      const f = $("#uploadFile").files[0];
      if (!f) throw new Error("Pick an image to upload");
      const fd = new FormData();
      fd.append("file", f);
      await post("/upload", fd, true);
      setProgress(20);
      setStatus("Image uploaded");
      refreshPreviews();
    } else {
      // Async generation with polling
      setStatus("Starting generation…");
      const res = await post("/generate", {
        prompt: $("#prompt").value,
        aspect,
        resolution
      });
      setProgress(10);
      setStatus("Generating image… (gemini-3-pro-image-preview)");
      await pollJob(res.job_id, "Generating");
      setProgress(30);
      setStatus("Generated ✓");
      refreshPreviews();
    }

    if ($("#doStyle").checked) {
      setProgress(45);
      setStatus("Applying style…");
      await post("/style", { style: selectedStyle });
      setProgress(60);
      setStatus("Styled ✓");
      refreshPreviews();
    }

    if ($("#doUpscale").checked) {
      setProgress(70);
      setStatus("Upscaling…");
      await post("/upscale", {});
      setProgress(85);
      setStatus("Upscaled ✓");
      refreshPreviews();
    }

    if ($("#doTrace").checked) {
      setProgress(92);
      setStatus("Tracing vector…");
      await post("/trace", {
        speckle: +$("#speckle").value,
        format: $("#traceFormat").value
      });
      setProgress(100);
      setStatus("Done! ✓");
      refreshPreviews();
    }

    // Save to library
    try {
      const thumb = `/preview/styled?t=${Date.now()}`;
      const lib = JSON.parse(localStorage.getItem("foundry-lib") || "[]");
      lib.unshift({
        prompt: $("#prompt").value,
        style: selectedStyle,
        thumb
      });
      localStorage.setItem("foundry-lib", JSON.stringify(lib.slice(0, 20)));
      loadLib();
    } catch (_) {}

    setStatus("Complete! Download or run again.");
  } catch (e) {
    setStatus(`Error: ${e.message}`);
    console.error(e);
  } finally {
    running = false;
    $("#runBtn").disabled = false;
  }
}

async function exportZip() {
  setStatus("Creating zip…");
  const { default: JSZip } = await import("/jszip.js");
  const zip = new JSZip();
  for (const f of ["generated.png","styled.png","upscaled.png","traced.svg","traced.png"]) {
    try {
      const r = await fetch(`/download/${f}`);
      if (!r.ok) continue;
      zip.file(f, await r.blob());
    } catch {}
  }
  const blob = await zip.generateAsync({ type: "blob" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "foundry-export.zip";
  a.click();
  setStatus("Zip downloaded!");
}

function init() {
  initStyles();
  wireChips($("#aspectChips"), v => aspect = v);
  wireChips($("#resChips"), v => resolution = v);
  $("#inspireBtn").onclick = () => {
    const keys = Object.keys(prompts);
    const k = selectedStyle in prompts ? selectedStyle : keys[Math.floor(Math.random() * keys.length)];
    $("#prompt").value = prompts[k] || "A wolf in bold woodcut style";
    setStatus(`Inspired: ${k}`);
  };
  $("#runBtn").onclick = run;
  $("#zipBtn").onclick = exportZip;

  document.querySelectorAll("input[name='source']").forEach(r => r.onchange = () => {
    $("#genWrap").classList.toggle("hidden", r.value !== "generate");
    $("#uploadWrap").classList.toggle("hidden", r.value !== "upload");
  });

  // Show generate panel by default
  $("#genWrap").classList.remove("hidden");
  $("#uploadWrap").classList.add("hidden");

  loadLib();
  setStatus("Ready — enter a prompt and hit Run!");
}

init();
