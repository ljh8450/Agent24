const feed = document.getElementById("event-feed");
const emptyState = document.getElementById("empty-state");
const eventCount = document.getElementById("event-count");
const runFilter = document.getElementById("run-filter");
const autoScroll = document.getElementById("auto-scroll");
const connection = document.getElementById("connection");
let source = null;
let lastEventId = 0;
let visibleCount = 0;
const seenEventIds = new Set();

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
}[character]));

function eventAlias(type) {
  if (type === "tool.started") return "tool_call";
  if (type === "tool.completed" || type === "tool.failed") return "tool_result";
  return "event";
}

function setConnection(state, label) {
  connection.className = `connection is-${state}`;
  connection.querySelector("span").textContent = label;
}

function renderRecord(record) {
  if (seenEventIds.has(record.id)) return;
  seenEventIds.add(record.id);
  lastEventId = Math.max(lastEventId, Number(record.id) || 0);
  emptyState.hidden = true;
  const alias = eventAlias(record.type);
  const node = document.createElement("article");
  node.className = `event-record is-${alias}`;
  node.dataset.eventId = record.id;
  node.innerHTML = `<header><time>${escapeHtml(record.created_at)}</time><strong>${escapeHtml(alias)}</strong><span>${escapeHtml(record.type)}</span><code>${escapeHtml(record.run_id)}</code></header><pre>${escapeHtml(JSON.stringify(record, null, 2))}</pre>`;
  feed.append(node);
  visibleCount += 1;
  eventCount.textContent = `${visibleCount} EVENT${visibleCount === 1 ? "" : "S"}`;
  while (feed.querySelectorAll(".event-record").length > 500) feed.querySelector(".event-record")?.remove();
  if (autoScroll.checked) feed.scrollTop = feed.scrollHeight;
}

function streamUrl() {
  const query = new URLSearchParams({ after: String(lastEventId) });
  if (runFilter.value) query.set("run_id", runFilter.value);
  return `/api/events/stream?${query}`;
}

function connect() {
  source?.close();
  setConnection("connecting", "CONNECTING");
  source = new EventSource(streamUrl());
  source.addEventListener("open", () => setConnection("live", "LIVE"));
  source.addEventListener("raw.event", (event) => {
    renderRecord(JSON.parse(event.data));
    setConnection("live", "LIVE");
  });
  source.addEventListener("error", () => setConnection("retry", "RECONNECTING"));
}

async function loadRuns() {
  const response = await fetch("/api/runs");
  const data = await response.json();
  runFilter.innerHTML = `<option value="">모든 실행</option>${(data.runs || []).map((run) => `<option value="${escapeHtml(run.id)}">${escapeHtml(`${run.question} · ${run.status}`)}</option>`).join("")}`;
}

async function loadSnapshot() {
  const query = new URLSearchParams({ limit: "200" });
  if (runFilter.value) query.set("run_id", runFilter.value);
  const response = await fetch(`/api/events?${query}`);
  const data = await response.json();
  (data.events || []).forEach(renderRecord);
}

function clearFeed(resetCursor = false) {
  feed.querySelectorAll(".event-record").forEach((node) => node.remove());
  emptyState.hidden = false;
  visibleCount = 0;
  eventCount.textContent = "0 EVENTS";
  if (resetCursor) {
    lastEventId = 0;
    seenEventIds.clear();
  }
}

runFilter.addEventListener("change", async () => {
  source?.close();
  clearFeed(true);
  await loadSnapshot();
  connect();
});
document.getElementById("clear-events").addEventListener("click", () => clearFeed(false));
window.addEventListener("beforeunload", () => source?.close());

(async () => {
  try {
    await loadRuns();
    await loadSnapshot();
    connect();
  } catch {
    setConnection("retry", "CONNECTION FAILED");
  }
})();
