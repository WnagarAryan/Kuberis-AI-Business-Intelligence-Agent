const stageUpload = document.getElementById("stage-upload");
const stageDashboard = document.getElementById("stage-dashboard");
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const btnSample = document.getElementById("btn-sample");
const uploadStatus = document.getElementById("upload-status");
const btnReset = document.getElementById("btn-reset");

const metricsGrid = document.getElementById("metrics-grid");
const datasetName = document.getElementById("dataset-name");
const datasetTypeLabel = document.getElementById("dataset-type-label");
const datasetSummary = document.getElementById("dataset-summary");
const qualityNotes = document.getElementById("quality-notes");
const previewTable = document.getElementById("preview-table");
const statsLine = document.getElementById("stats-line");
const suggestions = document.getElementById("suggestions");
const askForm = document.getElementById("ask-form");
const questionInput = document.getElementById("question-input");
const askSubmit = document.getElementById("ask-submit");
const chatLog = document.getElementById("chat-log");

let sessionId = null;

const METRIC_LABELS = {
  total_sales: "Total Sales",
  total_revenue: "Total Revenue",
  total_profit: "Total Profit",
  total_expenses: "Total Expenses",
  average_order_value: "Avg. Order Value",
  total_customers: "Total Customers",
  total_products: "Total Products",
};

function fmtNumber(n) {
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function showStatus(msg, isError) {
  uploadStatus.hidden = false;
  uploadStatus.textContent = msg;
  uploadStatus.classList.toggle("error", !!isError);
}

// An unhandled server error comes back as plain text, not JSON, so parsing the
// body as JSON first threw away the only description of what went wrong and
// left every failure looking like a bad file.
async function readError(res) {
  const text = await res.text();
  try {
    const detail = JSON.parse(text).detail;
    if (detail) return detail;
  } catch (_) {
    /* not JSON - fall through to the raw text */
  }
  const snippet = text.trim().slice(0, 200);
  return snippet ? `${snippet} (HTTP ${res.status})` : `Request failed (HTTP ${res.status})`;
}

async function handleUploadResponse(res) {
  if (!res.ok) {
    throw new Error(await readError(res));
  }
  return res.json();
}

async function uploadFile(file) {
  showStatus(`Reading ${file.name}…`, false);
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: formData });
  const data = await handleUploadResponse(res);
  renderDashboard(data, file.name);
}

async function loadSample() {
  showStatus("Reading the sample dataset…", false);
  const res = await fetch("/api/sample", { method: "POST" });
  const data = await handleUploadResponse(res);
  renderDashboard(data, "filtered_sales.csv");
}

function renderDashboard(data, sourceName) {
  sessionId = data.session_id;

  datasetName.textContent = sourceName;
  datasetTypeLabel.textContent = `02 — ${data.dataset_type} dataset`;
  datasetSummary.textContent = data.summary;

  qualityNotes.innerHTML = "";
  (data.data_quality_notes || []).forEach((note) => {
    const div = document.createElement("div");
    div.className = "quality-note";
    div.textContent = note;
    qualityNotes.appendChild(div);
  });

  metricsGrid.innerHTML = "";
  let anyMetric = false;
  Object.entries(METRIC_LABELS).forEach(([key, label]) => {
    const value = data.metrics[key];
    if (value !== null && value !== undefined) {
      anyMetric = true;
      const el = document.createElement("div");
      el.className = "metric";
      el.innerHTML = `<div class="metric-label">${label}</div><div class="metric-value">${fmtNumber(value)}</div>`;
      metricsGrid.appendChild(el);
    }
  });
  if (!anyMetric) {
    metricsGrid.innerHTML = '<div class="metrics-empty">No standard business metric columns were detected.</div>';
  }

  renderPreviewTable(data.preview);

  const stats = data.stats;
  const missingCount = Object.keys(stats.missing_values || {}).length;
  statsLine.textContent = `${stats.row_count.toLocaleString()} rows · ${stats.column_count} columns · ${stats.duplicate_rows} duplicate rows${missingCount ? ` · missing values in ${missingCount} column(s)` : ""}`;

  suggestions.innerHTML = "";
  (data.suggested_questions || []).forEach((q) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "suggestion-chip";
    chip.textContent = q;
    chip.addEventListener("click", () => {
      questionInput.value = q;
      askForm.requestSubmit();
    });
    suggestions.appendChild(chip);
  });

  chatLog.innerHTML = "";

  stageUpload.hidden = true;
  stageDashboard.hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderPreviewTable(preview) {
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  preview.columns.forEach((col) => {
    const th = document.createElement("th");
    th.textContent = col;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");
  preview.rows.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((cell) => {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  previewTable.innerHTML = "";
  previewTable.appendChild(thead);
  previewTable.appendChild(tbody);
}

// ---- Upload interactions ----
dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file).catch((err) => showStatus(err.message, true));
});
fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (file) uploadFile(file).catch((err) => showStatus(err.message, true));
});
btnSample.addEventListener("click", () => {
  loadSample().catch((err) => showStatus(err.message, true));
});
btnReset.addEventListener("click", () => {
  sessionId = null;
  stageDashboard.hidden = true;
  stageUpload.hidden = false;
  uploadStatus.hidden = true;
  fileInput.value = "";
});

// ---- Ask flow ----
askForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = questionInput.value.trim();
  if (!question || !sessionId) return;

  askSubmit.disabled = true;
  const loadingEntry = document.createElement("div");
  loadingEntry.className = "chat-entry";
  loadingEntry.innerHTML = `<p class="chat-question">${escapeHtml(question)}</p><p class="chat-loading">Working through the numbers…</p>`;
  chatLog.prepend(loadingEntry);
  questionInput.value = "";

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, question }),
    });
    if (!res.ok) {
      throw new Error(await readError(res));
    }
    const data = await res.json();
    loadingEntry.innerHTML = `
      <p class="chat-question">${escapeHtml(data.question)}</p>
      <p class="chat-answer"><strong>Answer:</strong> ${escapeHtml(data.answer)}</p>
      <p class="chat-explanation">${escapeHtml(data.explanation)}</p>
      <p class="chat-recommendation">${escapeHtml(data.recommendation)}</p>
    `;
  } catch (err) {
    loadingEntry.innerHTML = `<p class="chat-question">${escapeHtml(question)}</p><p class="chat-error">${escapeHtml(err.message)}</p>`;
  } finally {
    askSubmit.disabled = false;
  }
});

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
