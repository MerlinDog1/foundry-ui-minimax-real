const styles = ["stippling","cross-hatching","woodcut","copperplate","mezzotint","technical-sketch","minimalist-logo","etched-obsidian"];
const prompts = {
  "woodcut":"Ancient samurai wolf crest on stormy cliff, woodcut style",
  "copperplate":"Victorian alchemist portrait with ornate border, copperplate style",
  "cross-hatching":"Mechanical raven blueprint over moonlit ruins, cross-hatching"
};

let selectedStyle = "woodcut";
let aspect = "1:1";
let resolution = "1K";

const $ = (s)=>document.querySelector(s);
const setStatus=(t)=>$("#status").textContent=t;
const setProgress=(v)=>$("#bar").style.width=`${v}%`;

function initStyles(){
  const grid = $("#styleGrid");
  styles.forEach(s=>{
    const d = document.createElement("button");
    d.className = `style ${s===selectedStyle?"active":""}`;
    d.innerHTML = `<div class='swatch'></div><small>${s}</small>`;
    d.onclick=()=>{selectedStyle=s; [...grid.children].forEach(c=>c.classList.remove("active")); d.classList.add("active");};
    grid.appendChild(d);
  });
}

function wireChips(container, cb){
  container.querySelectorAll(".chip").forEach(b=>b.onclick=()=>{
    container.querySelectorAll(".chip").forEach(x=>x.classList.remove("active"));
    b.classList.add("active"); cb(b.dataset.v);
  });
}

async function post(url, data, isForm=false){
  const opt = {method:"POST"};
  if (isForm) opt.body = data; else {opt.headers={"Content-Type":"application/json"}; opt.body=JSON.stringify(data||{});}
  const r = await fetch(url,opt);
  const j = await r.json();
  if(!r.ok) throw new Error(j.error||`HTTP ${r.status}`);
  return j;
}

function refreshPreviews(){
  $("#pvGenerated").src = `/preview/generated?t=${Date.now()}`;
  $("#pvStyled").src = `/preview/styled?t=${Date.now()}`;
  $("#pvUpscaled").src = `/preview/upscaled?t=${Date.now()}`;
  $("#pvTraced").data = `/preview/traced?t=${Date.now()}`;
}

function loadLib(){
  const lib = JSON.parse(localStorage.getItem("foundry-lib")||"[]");
  const wrap = $("#library"); wrap.innerHTML="";
  lib.forEach((it,idx)=>{
    const d = document.createElement("div"); d.className="lib";
    d.innerHTML = `<img src='${it.thumb}'/><small>${it.style}</small>
      <div class='row'><button data-a='rerun'>rerun</button><a href='${it.svg}' download='trace.svg'>dl</a><button data-a='del'>x</button></div>`;
    d.querySelector("button[data-a='rerun']").onclick=()=>{ $("#prompt").value = it.prompt; selectedStyle=it.style; };
    d.querySelector("button[data-a='del']").onclick=()=>{ lib.splice(idx,1); localStorage.setItem("foundry-lib",JSON.stringify(lib)); loadLib(); };
    wrap.appendChild(d);
  });
}

async function run(){
  try{
    setProgress(5); setStatus("Starting...");
    const source = document.querySelector("input[name='source']:checked").value;
    if(source==="upload"){
      const f = $("#uploadFile").files[0]; if(!f) throw new Error("Pick an image to upload");
      const fd = new FormData(); fd.append("file",f); await post("/upload",fd,true);
    } else {
      await post("/generate", {prompt:$("#prompt").value, aspect, resolution});
    }
    setProgress(25); setStatus("Generated"); refreshPreviews();

    if($("#doStyle").checked){ await post("/style",{style:selectedStyle}); setProgress(50); setStatus("Styled"); refreshPreviews(); }
    if($("#doUpscale").checked){ await post("/upscale",{}); setProgress(70); setStatus("Upscaled"); refreshPreviews(); }
    if($("#doTrace").checked){ await post("/trace",{speckle:+$("#speckle").value, format:$("#traceFormat").value}); setProgress(100); setStatus("Traced ✓"); refreshPreviews(); }

    const thumb = $("#pvGenerated").src;
    const lib = JSON.parse(localStorage.getItem("foundry-lib")||"[]");
    lib.unshift({prompt:$("#prompt").value, style:selectedStyle, thumb, svg:"/download/traced.svg"});
    localStorage.setItem("foundry-lib", JSON.stringify(lib.slice(0,20)));
    loadLib();
  }catch(e){ setStatus(`Error: ${e.message}`); }
}

async function exportZip(){
  const zip = new JSZip();
  for (const f of ["generated.png","styled.png","upscaled.png","traced.svg","traced.png"]) {
    try {
      const r = await fetch(`/download/${f}`); if(!r.ok) continue;
      zip.file(f, await r.blob());
    } catch {}
  }
  const blob = await zip.generateAsync({type:"blob"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download="foundry-export.zip"; a.click();
}

function init(){
  initStyles();
  wireChips($("#aspectChips"),v=>aspect=v);
  wireChips($("#resChips"),v=>resolution=v);
  $("#inspireBtn").onclick=()=>{ const keys=Object.keys(prompts); const k=keys[Math.floor(Math.random()*keys.length)]; $("#prompt").value=prompts[k]; selectedStyle=k; setStatus(`Inspired: ${k}`); };
  $("#runBtn").onclick=run;
  $("#zipBtn").onclick=exportZip;
  document.querySelectorAll("input[name='source']").forEach(r=>r.onchange=()=>{
    $("#genWrap").classList.toggle("hidden", r.value!=="generate" || !r.checked);
    $("#uploadWrap").classList.toggle("hidden", r.value!=="upload" || !r.checked);
  });
  loadLib();
}
init();
