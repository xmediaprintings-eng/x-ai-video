// X Media AI Video – front end (no framework)
const $ = (id) => document.getElementById(id);
const state = { files: [], aspect: "9:16", jobId: null, job: null, poll: null, shownVersion: null };

const CHIPS = [
  "ໂຄສະນາກ່ອງໄຟ 20 ວິ ສຳລັບ Reels — ເປີດດ້ວຍຊອດປ້າຍໄຟສວຍທີ່ສຸດ",
  "ສະແດງຂັ້ນຕອນຜະລິດປ້າຍ ແບບໄວໆ ຈົບດ້ວຍປ້າຍທີ່ຕິດຕັ້ງແລ້ວ",
  "ຜົນງານຕິດຕັ້ງໜ້າຮ້ານລູກຄ້າ ມີຂໍ້ຄວາມສັ້ນໆ ທຸກຊອດ",
  "ຕູ້ເຄົາເຕີ້ສັ່ງເຮັດ ສະແດງລາຍລະອຽດ ແລະ ວັດສະດຸ",
];

function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), 4000);
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((body && body.detail) || r.statusText);
  return body;
}

// ---------------------------------------------------------------- status
async function loadStatus() {
  try {
    const s = await api("/api/status");
    const p = $("statusPill");
    if (!s.ffmpeg) { p.className = "pill demo"; p.textContent = "⚠ ບໍ່ພົບ FFmpeg — ກະລຸນາຕິດຕັ້ງ"; return; }
    p.className = "pill " + (s.ai_ready ? "ok" : "demo");
    p.textContent = s.ai_ready ? `● AI ພ້ອມ · ${s.model}` : "● ໂໝດທົດລອງ (ຍັງບໍ່ມີ API key) — ແຕະ ⚙";
    if (!s.whisper) $("captions").parentElement.title = "pip install faster-whisper";
  } catch (e) { $("statusPill").textContent = "server offline"; }
}

// ---------------------------------------------------------------- new job form
function renderFiles() {
  const box = $("fileList");
  box.innerHTML = "";
  state.files.forEach((f, i) => {
    const d = document.createElement("div");
    d.className = "fitem";
    const url = URL.createObjectURL(f);
    const isVid = f.type.startsWith("video") || /\.(mp4|mov|m4v|webm|3gp|mkv)$/i.test(f.name);
    d.innerHTML = (isVid ? `<video src="${url}#t=0.5" muted playsinline preload="metadata"></video>` : `<img src="${url}">`) +
      `<button class="x" data-i="${i}">✕</button><div class="lbl">${i + 1}. ${f.name}</div>`;
    box.appendChild(d);
  });
  box.querySelectorAll(".x").forEach((b) => b.onclick = (e) => {
    e.preventDefault(); state.files.splice(+b.dataset.i, 1); renderFiles();
  });
}

$("files").onchange = (e) => { state.files.push(...e.target.files); e.target.value = ""; renderFiles(); };
$("duration").oninput = (e) => ($("durVal").textContent = e.target.value);
$("aspect").querySelectorAll("button").forEach((b) => b.onclick = () => {
  $("aspect").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
  b.classList.add("on"); state.aspect = b.dataset.v;
});
CHIPS.forEach((c) => {
  const b = document.createElement("button");
  b.className = "chip"; b.textContent = c;
  b.onclick = () => { $("instruction").value = c; };
  $("chips").appendChild(b);
});

$("btnCreate").onclick = () => {
  if (!state.files.length) return toast("ກະລຸນາເລືອກຄລິບ ຫຼື ຮູບກ່ອນ", true);
  const fd = new FormData();
  state.files.forEach((f) => fd.append("files", f, f.name));
  fd.append("instruction", $("instruction").value);
  fd.append("aspect", state.aspect);
  fd.append("duration", $("duration").value);
  fd.append("captions", $("captions").checked);
  fd.append("language", $("language").value);
  fd.append("quality", $("quality").value);
  if ($("music").files[0]) fd.append("music", $("music").files[0]);
  $("btnCreate").disabled = true;
  showProgress("ກຳລັງອັບໂຫຼດ... Uploading", 0);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) showProgress(`ກຳລັງອັບໂຫຼດ ${Math.round(100 * e.loaded / e.total)}%`, 0.25 * e.loaded / e.total);
  };
  xhr.onload = () => {
    $("btnCreate").disabled = false;
    let res = {};
    try { res = JSON.parse(xhr.responseText); } catch {}
    if (xhr.status >= 300) { $("progressBox").classList.add("hidden"); return toast(res.detail || "Upload failed", true); }
    openJob(res.id);
  };
  xhr.onerror = () => { $("btnCreate").disabled = false; toast("Upload failed", true); };
  xhr.send(fd);
};

// ---------------------------------------------------------------- job view
function showProgress(msg, p, detail = "") {
  $("progressBox").classList.remove("hidden");
  $("progMsg").textContent = msg;
  $("progBar").style.width = Math.round(100 * Math.max(0.02, p)) + "%";
  $("progDetail").textContent = detail;
}

function openJob(id) {
  state.jobId = id; state.shownVersion = null; state._lastCount = null;
  history.replaceState(null, "", "#job=" + id);
  $("newJob").classList.add("hidden");
  clearInterval(state.poll);
  state.poll = setInterval(pollJob, 1500);
  pollJob();
}

async function pollJob() {
  if (!state.jobId) return;
  let job;
  try { job = await api(`/api/jobs/${state.jobId}`); } catch (e) { clearInterval(state.poll); return toast(e.message, true); }
  state.job = job;
  const busy = !["done", "error"].includes(job.status);
  if (busy) showProgress(job.message, job.progress, `${Math.round(job.progress * 100)}%`);
  else $("progressBox").classList.add("hidden");
  if (job.status === "error") {
    clearInterval(state.poll);
    showProgress("❌ " + job.message, 1, job.error || "");
    $("progressBox").classList.remove("hidden");
  }
  if (!busy) { clearInterval(state.poll); loadRecent(); }
  renderResult(job, busy);
}

function fmt(t) { return (Math.round(t * 10) / 10).toFixed(1); }

function renderResult(job, busy) {
  const vs = job.versions || [];
  if (!vs.length) { $("result").classList.add("hidden"); return; }
  $("result").classList.remove("hidden");
  const latest = vs[vs.length - 1];
  if (state._lastCount !== vs.length) { state.shownVersion = latest.v; state._lastCount = vs.length; }
  const v = vs.find((x) => x.v === state.shownVersion) || latest;
  const src = `/api/jobs/${job.id}/file/${v.file}`;
  if ($("video").dataset.src !== src) {
    $("video").dataset.src = src;
    $("video").src = src;
    if (v.poster) $("video").poster = `/api/jobs/${job.id}/file/${v.poster}`;
  }
  $("btnDownload").href = src + "?download=true";
  $("btnDownload").setAttribute("download", v.file);
  // versions
  $("versions").innerHTML = vs.map((x) => `<button data-v="${x.v}" class="${x.v === v.v ? "on" : ""}">v${x.v} · ${fmt(x.duration)}s</button>`).join("");
  $("versions").querySelectorAll("button").forEach((b) => b.onclick = () => { state.shownVersion = +b.dataset.v; renderResult(state.job, false); });
  // summary + warnings
  const src2 = v.plan._source === "claude" ? "🤖 Claude" : v.plan._source === "manual" ? "✍ JSON" : "⚙ ໂໝດທົດລອງ";
  $("summary").textContent = `${src2}: ${v.plan.summary || ""}`;
  const w = job.warnings || [];
  $("warnings").classList.toggle("hidden", !w.length);
  $("warnings").textContent = w.join("\n");
  // timeline
  const clips = Object.fromEntries((job.clips || []).map((c) => [c.id, c]));
  const segs = v.plan.segments || [];
  $("timeline").innerHTML = (v.timeline || []).map((it, i) => {
    if (it.kind === "outro") return `<div class="shot"><div class="ph">X</div><b>Outro</b><br>${fmt(it.dur)}s</div>`;
    const s = segs[i] || {};
    const c = clips[it.clip] || {};
    let fr = (c.frames || [])[0];
    if (c.frames && c.frames.length && s.start != null) {
      fr = c.frames.reduce((a, b) => (b.t != null && Math.abs(b.t - (s.start + s.end) / 2) < Math.abs((a.t ?? 1e9) - (s.start + s.end) / 2) ? b : a), c.frames[0]);
    }
    const img = fr ? `<img src="/api/jobs/${job.id}/file/${fr.file}" loading="lazy">` : `<div class="ph">${it.clip}</div>`;
    const tr = i > 0 && it.transition !== "cut" ? ` · ${it.transition}` : "";
    return `<div class="shot" title="${(s.why || "").replace(/"/g, "'")}">${img}<b>${it.clip}</b> ${fmt(s.start ?? 0)}–${fmt(s.end ?? 0)}<br>${fmt(it.dur)}s${tr}</div>`;
  }).join("");
  // chat
  $("chat").innerHTML = (job.history || []).filter((m) => m.text).map((m) => `<div class="msg ${m.role}">${escapeHtml(m.text)}</div>`).join("");
  $("chat").scrollTop = 1e6;
  if (!busy && document.activeElement !== $("planJson")) {
    const p = Object.fromEntries(Object.entries(v.plan).filter(([k]) => !k.startsWith("_")));
    $("planJson").value = JSON.stringify(p, null, 2);
  }
  $("btnRevise").disabled = busy;
  $("btnRenderJson").disabled = busy;
}

function escapeHtml(s) { return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

async function revise(body) {
  try {
    await api(`/api/jobs/${state.jobId}/revise`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    clearInterval(state.poll);
    state.poll = setInterval(pollJob, 1500);
    pollJob();
  } catch (e) { toast(e.message, true); }
}
$("btnRevise").onclick = () => {
  const t = $("reviseText").value.trim();
  if (!t) return;
  $("reviseText").value = "";
  revise({ instruction: t });
};
$("btnRenderJson").onclick = () => {
  try { revise({ plan: JSON.parse($("planJson").value) }); } catch (e) { toast("JSON error: " + e.message, true); }
};
$("btnNew").onclick = () => {
  clearInterval(state.poll);
  state.jobId = null; state.files = []; renderFiles();
  history.replaceState(null, "", "#");
  $("result").classList.add("hidden"); $("progressBox").classList.add("hidden");
  $("newJob").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
};

// ---------------------------------------------------------------- recent jobs
async function loadRecent() {
  try {
    const list = await api("/api/jobs");
    $("recent").innerHTML = list.length ? list.map((j) => `
      <div class="rjob" data-id="${j.id}">
        ${j.poster ? `<img src="/api/jobs/${j.id}/file/${j.poster}">` : `<div class="ph"></div>`}
        <div style="min-width:0"><div class="t">${escapeHtml(j.instruction || "(ບໍ່ມີຄຳສັ່ງ)")}</div>
        <div class="s">${new Date(j.created * 1000).toLocaleString()} · ${j.status} · ${j.versions} ver</div></div>
      </div>`).join("") : `<div class="muted">ຍັງບໍ່ມີວຽກ</div>`;
    $("recent").querySelectorAll(".rjob").forEach((r) => r.onclick = () => { openJob(r.dataset.id); window.scrollTo({ top: 0, behavior: "smooth" }); });
  } catch {}
}

// ---------------------------------------------------------------- settings
async function openSettings() {
  const s = await api("/api/settings");
  $("sApiKey").value = "";
  $("sApiKey").placeholder = s.has_api_key ? `ມີແລ້ວ: ${s.api_key_hint}` : "sk-ant-...";
  $("sModel").innerHTML = s.models.map((m) => `<option ${m === s.model ? "selected" : ""}>${m}</option>`).join("");
  $("sShop").value = s.shop_name; $("sTagline").value = s.tagline; $("sPhones").value = s.phones; $("sAddress").value = s.address;
  $("sWhisper").value = s.whisper_model;
  $("sLogoPreview").src = "/api/brand/logo?" + Date.now();
  $("settings").classList.remove("hidden");
}
$("btnSettings").onclick = openSettings;
$("btnCloseSettings").onclick = () => $("settings").classList.add("hidden");
$("btnSaveSettings").onclick = async () => {
  const fd = new FormData();
  if ($("sApiKey").value.trim()) fd.append("api_key", $("sApiKey").value.trim());
  fd.append("model", $("sModel").value);
  fd.append("shop_name", $("sShop").value);
  fd.append("tagline", $("sTagline").value);
  fd.append("phones", $("sPhones").value);
  fd.append("address", $("sAddress").value);
  fd.append("whisper_model", $("sWhisper").value);
  if ($("sLogo").files[0]) fd.append("logo", $("sLogo").files[0]);
  try {
    await api("/api/settings", { method: "POST", body: fd });
    toast("ບັນທຶກແລ້ວ ✓");
    $("settings").classList.add("hidden");
    loadStatus();
  } catch (e) { toast(e.message, true); }
};

// ---------------------------------------------------------------- boot
loadStatus();
loadRecent();
function fromHash() {
  const m = location.hash.match(/job=([\w-]+)/);
  if (m && m[1] !== state.jobId) openJob(m[1]);
}
window.addEventListener("hashchange", fromHash);
fromHash();
