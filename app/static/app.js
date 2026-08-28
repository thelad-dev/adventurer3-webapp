const $ = (id) => document.getElementById(id);

const logEl = $("log");
const camera = $("camera");
const cameraFallback = $("camera-fallback");
let pendingFile = "";

function log(text) {
  const stamp = new Date().toLocaleTimeString("de-DE");
  logEl.textContent = `[${stamp}]\n${text}\n\n${logEl.textContent}`.slice(0, 8000);
}

function fmt(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "–";
  return Number(value).toLocaleString("de-DE", { maximumFractionDigits: digits });
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function stepMm() {
  const selected = document.querySelector('input[name="step"]:checked');
  return selected ? Number(selected.value) : 1;
}

function busy(printing) {
  document.querySelectorAll("[data-axis], #btn-home").forEach((btn) => {
    btn.disabled = printing;
  });
}

function render(status) {
  const name = status.machine_name || "Adventurer 3";
  setText("machine-name", name);
  const bits = [
    status.machine_type,
    status.firmware,
    status.online ? status.machine_status || "online" : "offline",
  ].filter(Boolean);
  setText("machine-meta", bits.join(" · "));

  const pill = $("status-pill");
  let state = "off";
  let label = "offline";
  if (status.online && status.paused) {
    state = "pause";
    label = "pause";
  } else if (status.online && status.printing) {
    state = "print";
    label = status.machine_status || "druckt";
  } else if (status.online) {
    state = "ok";
    label = status.machine_status || "bereit";
  }
  pill.dataset.state = state;
  pill.textContent = label;

  setText("nozzle-now", fmt(status.nozzle, 0));
  setText("nozzle-want", fmt(status.nozzle_target, 0));
  setText("bed-now", fmt(status.bed, 0));
  setText("bed-want", fmt(status.bed_target, 0));
  setText("current-file", status.current_file || "–");
  setText("pos-x", fmt(status.x, 2));
  setText("pos-y", fmt(status.y, 2));
  setText("pos-z", fmt(status.z, 2));

  const bar = $("print-progress");
  if (status.progress_pct === null || status.progress_pct === undefined) {
    bar.removeAttribute("value");
    setText("progress-text", "unbekannt");
  } else {
    bar.value = status.progress_pct;
    setText("progress-text", `${status.progress_pct} %`);
  }
  busy(Boolean(status.printing));
}

async function post(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  if (data.status) render(data.status);
  if (data.reply) log(data.reply);
  return data;
}

function bindPost(id, path, payload) {
  $(id).addEventListener("click", async () => {
    try {
      await post(path, typeof payload === "function" ? payload() : payload);
    } catch (err) {
      log(String(err.message || err));
    }
  });
}

async function loadFiles() {
  const list = $("file-list");
  list.replaceChildren();
  const item = document.createElement("li");
  item.textContent = "Lade …";
  list.append(item);
  try {
    const response = await fetch("/api/files");
    const data = await response.json();
    list.replaceChildren();
    if (!data.files || data.files.length === 0) {
      const empty = document.createElement("li");
      empty.textContent = "Keine Druckdateien gefunden.";
      list.append(empty);
      return;
    }
    for (const name of data.files) {
      const row = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = name;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Start";
      button.addEventListener("click", () => {
        pendingFile = name;
        $("confirm-print-name").textContent = name;
        $("confirm-print").showModal();
      });
      row.append(label, button);
      list.append(row);
    }
  } catch (err) {
    list.replaceChildren();
    const fail = document.createElement("li");
    fail.textContent = String(err.message || err);
    list.append(fail);
  }
}

function connectEvents() {
  const source = new EventSource("/api/events");
  source.onmessage = (event) => {
    try {
      render(JSON.parse(event.data));
    } catch {
      /* ignorieren */
    }
  };
  source.onerror = () => {
    source.close();
    setTimeout(pollOnce, 2000);
  };
}

async function pollOnce() {
  try {
    const response = await fetch("/api/status");
    render(await response.json());
  } catch {
    setText("machine-meta", "Server nicht erreichbar");
  }
  setTimeout(pollOnce, 2000);
}

function startCamera() {
  camera.addEventListener("error", () => {
    camera.hidden = true;
    cameraFallback.hidden = false;
  });
  camera.addEventListener("load", () => {
    camera.hidden = false;
    cameraFallback.hidden = true;
  });
  camera.src = "/api/camera";
}

function boot() {
  bindPost("btn-pause", "/api/pause");
  bindPost("btn-resume", "/api/resume");
  bindPost("btn-led-on", "/api/led", { on: true });
  bindPost("btn-led-off", "/api/led", { on: false });
  bindPost("btn-fan-on", "/api/fan", { on: true });
  bindPost("btn-fan-off", "/api/fan", { on: false });
  bindPost("btn-motors-on", "/api/motors", { on: true });
  bindPost("btn-motors-off", "/api/motors", { on: false });
  bindPost("btn-home", "/api/home");

  $("btn-stop").addEventListener("click", () => $("confirm-stop").showModal());
  $("confirm-stop").addEventListener("close", async () => {
    if ($("confirm-stop").returnValue !== "ok") return;
    try {
      await post("/api/stop");
    } catch (err) {
      log(String(err.message || err));
    }
  });
  $("confirm-print").addEventListener("close", async () => {
    if ($("confirm-print").returnValue !== "ok" || !pendingFile) return;
    try {
      await post("/api/print", { filename: pendingFile });
    } catch (err) {
      log(String(err.message || err));
    }
  });

  document.querySelectorAll("[data-axis]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mm = stepMm() * Number(btn.dataset.dir);
      try {
        await post("/api/move", { axis: btn.dataset.axis, mm });
      } catch (err) {
        log(String(err.message || err));
      }
    });
  });

  $("form-nozzle").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await post("/api/temps", { nozzle: Number($("nozzle-input").value) });
    } catch (err) {
      log(String(err.message || err));
    }
  });
  $("form-bed").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await post("/api/temps", { bed: Number($("bed-input").value) });
    } catch (err) {
      log(String(err.message || err));
    }
  });
  $("form-raw").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await post("/api/command", { command: $("raw-input").value });
    } catch (err) {
      log(String(err.message || err));
    }
  });
  $("btn-refresh-files").addEventListener("click", loadFiles);

  if ("EventSource" in window) connectEvents();
  else pollOnce();
  startCamera();
  pollOnce();
}

boot();
