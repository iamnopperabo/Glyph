(() => {
  "use strict";

  const MAX_UPLOAD = Number(document.body.dataset.maxUpload) || 100 * 1024 * 1024;
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DEFAULT_OPTIONS = {
    width: window.innerWidth < 600 ? 64 : 100,
    ramp: "classic",
    invert: false,
    brightness: 100,
    contrast: 100,
    fps: 12,
  };

  const el = {
    themeToggle: document.getElementById("themeToggle"),
    sourceEmpty: document.getElementById("sourceEmpty"),
    sourceCard: document.getElementById("sourceCard"),
    sourceExt: document.getElementById("sourceExt"),
    sourceName: document.getElementById("sourceName"),
    sourceMeta: document.getElementById("sourceMeta"),
    replaceBtn: document.getElementById("replaceBtn"),
    controls: document.getElementById("controls"),
    width: document.getElementById("width"),
    widthValue: document.getElementById("widthValue"),
    ramp: document.getElementById("ramp"),
    brightness: document.getElementById("brightness"),
    brightnessValue: document.getElementById("brightnessValue"),
    contrast: document.getElementById("contrast"),
    contrastValue: document.getElementById("contrastValue"),
    invert: document.getElementById("invert"),
    fps: document.getElementById("fps"),
    fpsValue: document.getElementById("fpsValue"),
    motionControl: document.getElementById("motionControl"),
    motionHint: document.getElementById("motionHint"),
    resetBtn: document.getElementById("resetBtn"),
    viewerMeta: document.getElementById("viewerMeta"),
    dropzone: document.getElementById("dropzone"),
    browseBtn: document.getElementById("browseBtn"),
    fileInput: document.getElementById("fileInput"),
    uploadError: document.getElementById("uploadError"),
    stage: document.getElementById("stage"),
    stageStatus: document.getElementById("stageStatus"),
    ascii: document.getElementById("asciiOutput"),
    statusText: document.getElementById("statusText"),
    outputMeta: document.getElementById("outputMeta"),
    actions: document.getElementById("actions"),
    playBtn: document.getElementById("playBtn"),
    copyBtn: document.getElementById("copyBtn"),
    downloadBtn: document.getElementById("downloadBtn"),
    toast: document.getElementById("toast"),
  };

  const state = {
    token: null,
    source: null,
    columns: 0,
    rows: 0,
    frames: [],
    delays: [],
    frameIndex: 0,
    playing: false,
    playTimer: null,
    renderTimer: null,
    uploadBusy: false,
    renderSeq: 0,
    renderAbort: null,
    toastTimer: null,
  };

  let fitFrame = 0;

  function setTheme(theme, persist = true) {
    const isPaper = theme === "paper";
    document.documentElement.dataset.theme = isPaper ? "paper" : "ink";
    if (persist) {
      try {
        localStorage.setItem("glyph-theme", isPaper ? "paper" : "ink");
      } catch (_) {
        /* localStorage can be unavailable in private windows */
      }
    }
    el.themeToggle.textContent = isPaper ? "ink" : "paper";
    el.themeToggle.setAttribute(
      "aria-label",
      isPaper ? "Switch to the ink theme" : "Switch to the paper theme"
    );
  }

  function initTheme() {
    let saved = "ink";
    try {
      saved = localStorage.getItem("glyph-theme") || "ink";
    } catch (_) {
      /* use the default */
    }
    setTheme(saved === "paper" ? "paper" : "ink", false);
  }

  function syncLabels() {
    el.widthValue.textContent = el.width.value;
    el.brightnessValue.textContent = `${el.brightness.value}%`;
    el.contrastValue.textContent = `${el.contrast.value}%`;
    el.fpsValue.textContent = el.fps.value;
  }

  function readOptions() {
    return {
      width: Number(el.width.value),
      ramp: el.ramp.value,
      invert: el.invert.getAttribute("aria-checked") === "true",
      brightness: Number(el.brightness.value) / 100,
      contrast: Number(el.contrast.value) / 100,
      fps: Number(el.fps.value),
    };
  }

  function scheduleRender(delay = 280) {
    if (!state.token) return;
    window.clearTimeout(state.renderTimer);
    state.renderTimer = window.setTimeout(() => renderNow(), delay);
  }

  function setRendering(active) {
    el.stageStatus.hidden = !active;
    [el.playBtn, el.copyBtn, el.downloadBtn].forEach((button) => {
      button.disabled = active;
    });
    if (active) {
      el.statusText.textContent = "converting…";
    }
  }

  function setUploadBusy(busy) {
    state.uploadBusy = busy;
    el.browseBtn.disabled = busy;
    el.browseBtn.textContent = busy ? "reading…" : "choose file";
  }

  function clearPreview() {
    pausePlayback();
    state.frames = [];
    state.delays = [];
    state.columns = 0;
    state.rows = 0;
    state.frameIndex = 0;
    el.ascii.textContent = "";
    el.outputMeta.textContent = "";
    el.actions.hidden = true;
    el.stageStatus.hidden = false;
  }

  async function handleFile(file) {
    if (!file) return;
    if (state.uploadBusy) {
      showToast("Still reading the previous file.");
      return;
    }
    if (file.size === 0) {
      showUploadError("That file is empty.");
      return;
    }
    if (file.size > MAX_UPLOAD) {
      showUploadError(`That file is over ${formatBytes(MAX_UPLOAD)}.`);
      return;
    }

    state.renderSeq += 1;
    window.clearTimeout(state.renderTimer);
    if (state.renderAbort) state.renderAbort.abort();
    setRendering(false);
    pausePlayback();
    hideUploadError();
    setUploadBusy(true);
    setStatus(state.token ? "reading new file…" : "reading file…");

    const form = new FormData();
    form.append("file", file, file.name || "clipboard.png");

    try {
      const response = await fetch("/api/upload", {
        method: "POST",
        body: form,
      });
      const data = await readJson(response);
      if (!response.ok) {
        throw new Error(data.error || "Upload failed.");
      }

      state.token = data.token;
      state.source = data.source;
      showSource(data.source);
      el.controls.disabled = false;
      el.dropzone.hidden = true;
      el.stage.hidden = false;
      el.actions.hidden = true;
      el.viewerMeta.textContent = "converting…";
      clearPreview();
      setUploadBusy(false);
      await renderNow();
    } catch (error) {
      if (error.name === "AbortError") return;
      const message = friendlyError(error);
      showUploadError(message);
      showToast(message);
      setStatus("Ready when you are.");
      if (state.token) {
        el.viewerMeta.textContent =
          state.source && state.columns
            ? `${state.source.format} / ${state.columns} × ${state.rows}`
            : "waiting for input";
      } else {
        el.stage.hidden = true;
        el.dropzone.hidden = false;
      }
    } finally {
      setUploadBusy(false);
    }
  }

  async function renderNow() {
    if (!state.token) return;

    const sequence = ++state.renderSeq;
    if (state.renderAbort) state.renderAbort.abort();
    state.renderAbort = new AbortController();

    pausePlayback();
    setRendering(true);

    try {
      const response = await fetch("/api/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: state.token,
          options: readOptions(),
        }),
        signal: state.renderAbort.signal,
      });
      const data = await readJson(response);
      if (!response.ok) {
        throw new Error(data.error || "Conversion failed.");
      }
      if (sequence !== state.renderSeq) return;
      applyResult(data);
    } catch (error) {
      if (error.name === "AbortError") return;
      if (sequence !== state.renderSeq) return;
      const message = friendlyError(error);
      showToast(message);
      el.statusText.textContent = "couldn't render";
      el.viewerMeta.textContent = "error";
    } finally {
      if (sequence === state.renderSeq) {
        setRendering(false);
      }
    }
  }

  function applyResult(data) {
    if (!Array.isArray(data.frames) || !data.frames.length) {
      throw new Error("The converter returned an empty result.");
    }

    pausePlayback();
    state.columns = data.columns;
    state.rows = data.rows;
    state.frames = data.frames;
    state.delays = Array.isArray(data.delays) ? data.delays : [];
    state.frameIndex = 0;
    state.playing = false;

    showFrame();
    fitPreview();

    const animated = state.frames.length > 1;
    el.playBtn.hidden = !animated;
    el.playBtn.textContent = "play";
    el.copyBtn.hidden = false;
    el.downloadBtn.textContent = animated ? "save .html" : "save .txt";
    el.actions.hidden = false;
    el.stageStatus.hidden = true;

    const sourceFormat = state.source ? state.source.format : "file";
    el.viewerMeta.textContent = `${sourceFormat} / ${data.columns} × ${data.rows}`;
    el.statusText.textContent = `converted in ${formatDuration(data.elapsed_ms)}`;

    const parts = [
      `${data.columns} × ${data.rows}`,
      animated ? `${state.frames.length} frames` : "still",
    ];
    if (animated) {
      parts.push(`${motionFps()} fps`);
      if (data.trimmed) parts.push("trimmed");
    }
    el.outputMeta.textContent = parts.join(" · ");

    if (animated && !REDUCED_MOTION) {
      startPlayback();
    }
  }

  function showSource(source) {
    el.sourceEmpty.hidden = true;
    el.sourceCard.hidden = false;
    el.sourceExt.textContent = source.format || "FILE";
    el.sourceName.textContent = source.name;
    el.sourceName.title = source.name;

    const bits = [`${source.width} × ${source.height}`, formatBytes(source.size)];
    if (source.frames > 1) bits.push(`${source.frames} frames`);
    if (source.duration_ms) bits.push(`${(source.duration_ms / 1000).toFixed(1)} s`);
    el.sourceMeta.textContent = bits.join(" · ");

    const isVideo = source.kind === "video";
    el.fps.disabled = !isVideo;
    el.motionControl.classList.toggle("is-disabled", !isVideo);
    if (isVideo) {
      el.motionHint.textContent = "applies to video";
    } else if (source.kind === "animation") {
      el.motionHint.textContent = "gif timing is kept";
    } else {
      el.motionHint.textContent = "video only";
    }
  }

  function showFrame() {
    el.ascii.textContent = state.frames[state.frameIndex] || "";
    el.playBtn.textContent = state.playing ? "pause" : "play";
  }

  function startPlayback() {
    if (state.frames.length < 2) return;
    state.playing = true;
    el.playBtn.textContent = "pause";
    scheduleNextFrame();
  }

  function pausePlayback() {
    state.playing = false;
    window.clearTimeout(state.playTimer);
    if (el.playBtn) el.playBtn.textContent = "play";
  }

  function togglePlayback() {
    if (state.frames.length < 2) return;
    if (state.playing) pausePlayback();
    else startPlayback();
  }

  function scheduleNextFrame() {
    window.clearTimeout(state.playTimer);
    if (!state.playing) return;

    const delay = Math.max(30, state.delays[state.frameIndex] || 100);
    state.playTimer = window.setTimeout(() => {
      if (!state.playing) return;
      state.frameIndex = (state.frameIndex + 1) % state.frames.length;
      showFrame();
      scheduleNextFrame();
    }, delay);
  }

  function motionFps() {
    if (state.delays.length < 2) return 0;
    const average = state.delays.reduce((sum, delay) => sum + delay, 0) / state.delays.length;
    return Math.round(1000 / average);
  }

  function fitPreview() {
    if (!state.columns || !state.rows) return;
    const rect = el.stage.getBoundingClientRect();
    if (rect.width < 20 || rect.height < 20) return;

    const availableWidth = Math.max(80, rect.width - 50);
    const availableHeight = Math.max(80, rect.height - 50);
    const byWidth = availableWidth / (state.columns * 0.61);
    const byHeight = availableHeight / state.rows;
    const raw = Math.min(16, byWidth, byHeight);
    const size = Math.max(3, Math.floor(raw * 2) / 2);

    el.ascii.style.fontSize = `${size}px`;
    el.ascii.style.lineHeight = `${size}px`;
  }

  function queueFit() {
    window.cancelAnimationFrame(fitFrame);
    fitFrame = window.requestAnimationFrame(fitPreview);
  }

  function downloadCurrent() {
    if (!state.frames.length) return;

    const base = safeBaseName(state.source ? state.source.name : "ascii");
    if (state.frames.length === 1) {
      const blob = new Blob([`${state.frames[0]}\n`], {
        type: "text/plain;charset=utf-8",
      });
      triggerDownload(blob, `${base}-ascii.txt`);
      showToast("Text file saved.");
      return;
    }

    const blob = new Blob([buildStandaloneHtml(base)], {
      type: "text/html;charset=utf-8",
    });
    triggerDownload(blob, `${base}-ascii.html`);
    showToast("Self-contained HTML saved.");
  }

  async function copyCurrentFrame() {
    if (!state.frames.length) return;
    const text = state.frames[state.frameIndex];
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      fallbackCopy(text);
    }
    const prefix = state.frames.length > 1 ? `Frame ${state.frameIndex + 1} copied.` : "ASCII copied.";
    showToast(prefix);
  }

  function fallbackCopy(text) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  function buildStandaloneHtml(base) {
    const safeTitle = escapeHtml(base);
    const payload = JSON.stringify({
      frames: state.frames,
      delays: state.delays,
      columns: state.columns,
      rows: state.rows,
    })
      .replace(/</g, "\\u003c")
      .replace(/\u2028/g, "\\u2028")
      .replace(/\u2029/g, "\\u2029");

    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${safeTitle} — ASCII</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { min-height: 100%; }
  body {
    margin: 0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: #0a0a0a;
    color: #e9e5dc;
    font-family: "Cascadia Mono", "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  }
  main {
    flex: 1;
    min-height: 0;
    padding: 20px;
    overflow: auto;
    display: flex;
    align-items: flex-start;
    justify-content: center;
  }
  pre {
    flex: none;
    margin: auto;
    font: inherit;
    font-size: 10px;
    line-height: 1;
    white-space: pre;
  }
  footer {
    flex: none;
    min-height: 52px;
    padding: 12px 20px;
    border-top: 1px solid #242321;
    display: flex;
    align-items: center;
    gap: 14px;
    color: #827e76;
    font-size: 10px;
    letter-spacing: 0.06em;
  }
  button {
    padding: 7px 10px;
    border: 1px solid #3b3935;
    border-radius: 2px;
    background: transparent;
    color: #e9e5dc;
    font: inherit;
    font-size: 9px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
  }
  button:hover { border-color: #e9e5dc; }
  .name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .spacer { flex: 1; }
  .brand { color: #4d4a45; }
</style>
</head>
<body>
<main><pre id="art" aria-label="ASCII animation"></pre></main>
<footer>
  <button id="toggle" type="button">pause</button>
  <span id="counter"></span>
  <span class="spacer"></span>
  <span class="name">${safeTitle}</span>
  <span class="brand">glyph</span>
</footer>
<script>
(function () {
  var data = ${payload};
  var pre = document.getElementById("art");
  var toggle = document.getElementById("toggle");
  var counter = document.getElementById("counter");
  var index = 0;
  var playing = false;
  var timer = null;

  function fit() {
    var width = Math.max(320, window.innerWidth) - 40;
    var height = Math.max(320, window.innerHeight) - 100;
    var size = Math.min(16, Math.max(3, Math.min(width / (data.columns * 0.61), height / data.rows)));
    pre.style.fontSize = size + "px";
    pre.style.lineHeight = size + "px";
  }

  function draw() {
    pre.textContent = data.frames[index];
    counter.textContent = (index + 1) + " / " + data.frames.length;
  }

  function schedule() {
    window.clearTimeout(timer);
    if (!playing) return;
    var delay = Math.max(30, data.delays[index] || 100);
    timer = window.setTimeout(function () {
      index = (index + 1) % data.frames.length;
      draw();
      schedule();
    }, delay);
  }

  function play() {
    playing = true;
    toggle.textContent = "pause";
    schedule();
  }

  function pause() {
    playing = false;
    window.clearTimeout(timer);
    toggle.textContent = "play";
  }

  toggle.addEventListener("click", function () {
    if (playing) pause();
    else play();
  });

  document.addEventListener("keydown", function (event) {
    if (event.code === "Space") {
      event.preventDefault();
      if (playing) pause();
      else play();
    }
  });

  window.addEventListener("resize", fit);
  fit();
  draw();
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) pause();
  else play();
})();
</script>
</body>
</html>`;
  }

  function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function safeBaseName(name) {
    return (
      (name || "ascii")
        .replace(/\.[^.]+$/, "")
        .replace(/[^\p{L}\p{N}._-]+/gu, "-")
        .replace(/^[-.]+|[-.]+$/g, "")
        .slice(0, 60) || "ascii"
    );
  }

  function showUploadError(message) {
    el.uploadError.textContent = message;
    el.uploadError.hidden = false;
  }

  function hideUploadError() {
    el.uploadError.hidden = true;
    el.uploadError.textContent = "";
  }

  function setStatus(message) {
    el.statusText.textContent = message;
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    el.toast.textContent = message;
    el.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      el.toast.hidden = true;
    }, 2600);
  }

  function friendlyError(error) {
    if (error instanceof TypeError) {
      return "Can't reach the local server. Is app.py still running?";
    }
    return error.message || "Something went wrong.";
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatDuration(milliseconds) {
    if (!Number.isFinite(milliseconds)) return "a moment";
    if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
    return `${(milliseconds / 1000).toFixed(2)} s`;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  el.themeToggle.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme;
    setTheme(current === "paper" ? "ink" : "paper");
  });

  el.browseBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    el.fileInput.click();
  });

  el.replaceBtn.addEventListener("click", () => el.fileInput.click());

  el.dropzone.addEventListener("click", (event) => {
    if (event.target.closest("button, a, input")) return;
    el.fileInput.click();
  });

  el.fileInput.addEventListener("change", () => {
    const file = el.fileInput.files && el.fileInput.files[0];
    el.fileInput.value = "";
    if (file) handleFile(file);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    el.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      el.dropzone.classList.add("is-dragging");
    });
  });

  el.dropzone.addEventListener("dragleave", (event) => {
    if (!el.dropzone.contains(event.relatedTarget)) {
      el.dropzone.classList.remove("is-dragging");
    }
  });

  el.dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    el.dropzone.classList.remove("is-dragging");
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) handleFile(file);
  });

  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => {
    event.preventDefault();
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) handleFile(file);
  });

  document.addEventListener("paste", (event) => {
    if (!event.clipboardData) return;
    const item = Array.from(event.clipboardData.items).find((entry) =>
      entry.type.startsWith("image/")
    );
    if (!item) return;
    const file = item.getAsFile();
    if (!file) return;
    event.preventDefault();
    handleFile(new File([file], "clipboard.png", { type: file.type || "image/png" }));
  });

  el.width.addEventListener("input", () => {
    syncLabels();
    scheduleRender();
  });
  el.brightness.addEventListener("input", () => {
    syncLabels();
    scheduleRender();
  });
  el.contrast.addEventListener("input", () => {
    syncLabels();
    scheduleRender();
  });
  el.fps.addEventListener("input", () => {
    syncLabels();
    scheduleRender();
  });
  el.ramp.addEventListener("change", () => scheduleRender(0));
  el.invert.addEventListener("click", () => {
    const next = el.invert.getAttribute("aria-checked") !== "true";
    el.invert.setAttribute("aria-checked", String(next));
    scheduleRender(0);
  });

  el.resetBtn.addEventListener("click", () => {
    el.width.value = DEFAULT_OPTIONS.width;
    el.ramp.value = DEFAULT_OPTIONS.ramp;
    el.brightness.value = DEFAULT_OPTIONS.brightness;
    el.contrast.value = DEFAULT_OPTIONS.contrast;
    el.fps.value = DEFAULT_OPTIONS.fps;
    el.invert.setAttribute("aria-checked", "false");
    syncLabels();
    scheduleRender(0);
  });

  el.playBtn.addEventListener("click", togglePlayback);
  el.copyBtn.addEventListener("click", copyCurrentFrame);
  el.downloadBtn.addEventListener("click", downloadCurrent);

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (
      target instanceof HTMLElement &&
      (target.matches("input, select, textarea, button") || target.isContentEditable)
    ) {
      return;
    }
    if (event.code === "Space" && state.frames.length > 1) {
      event.preventDefault();
      togglePlayback();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pausePlayback();
  });

  if ("ResizeObserver" in window) {
    new ResizeObserver(queueFit).observe(el.stage);
  }
  window.addEventListener("resize", queueFit);

  el.width.value = DEFAULT_OPTIONS.width;
  initTheme();
  syncLabels();
})();
