const state = { channels: [], category: "全部", query: "" };

const list = document.querySelector("#channel-list");
const empty = document.querySelector("#empty-state");
const visibleCount = document.querySelector("#visible-count");

function formatPercent(value) {
  return Number.isFinite(value) ? `${value.toFixed(1)}%` : "—";
}

function formatDelay(value) {
  return Number.isFinite(value) ? `${Math.round(value * 1000)} ms` : "—";
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { hour12: false });
}

function cell(row, value, className = "") {
  const item = document.createElement("td");
  item.textContent = value ?? "—";
  if (className) item.className = className;
  row.append(item);
}

function render() {
  const query = state.query.trim().toLocaleLowerCase("zh-CN");
  const channels = state.channels.filter((channel) => {
    const categoryMatches = state.category === "全部" || channel.category === state.category;
    const queryMatches = !query || String(channel.name ?? "").toLocaleLowerCase("zh-CN").includes(query);
    return categoryMatches && queryMatches;
  });
  list.replaceChildren();
  channels.forEach((channel) => {
    const row = document.createElement("tr");
    cell(row, channel.name);
    cell(row, channel.category, "muted");
    cell(row, channel.source_type === "official" ? "官方" : "社区", "muted");
    cell(row, channel.height ? `${channel.height}p` : "—");
    cell(row, formatDelay(channel.latency_seconds));
    cell(row, formatPercent(channel.availability_percent));
    cell(row, Number.isFinite(channel.score) ? channel.score.toFixed(1) : "—", "score");
    cell(row, formatTime(channel.last_checked_at), "muted");
    list.append(row);
  });
  empty.hidden = channels.length > 0;
  visibleCount.textContent = `显示 ${channels.length} 个`;
}

document.querySelectorAll("[data-category]").forEach((button) => {
  button.addEventListener("click", () => {
    state.category = button.dataset.category;
    document.querySelectorAll("[data-category]").forEach((item) => item.classList.toggle("active", item === button));
    render();
  });
});

document.querySelector("#search").addEventListener("input", (event) => {
  state.query = event.target.value;
  render();
});

fetch("status.json", { cache: "no-store" })
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then((data) => {
    state.channels = Array.isArray(data.channels) ? data.channels : [];
    document.querySelector("#summary").textContent = `当前 ${state.channels.length} 个可用频道 · 最近更新 ${formatTime(data.checked_at)}`;
    if (data.measurement_notice) document.querySelector("#measurement-notice").textContent = data.measurement_notice;
    render();
  })
  .catch(() => {
    document.querySelector("#summary").textContent = "暂时无法读取状态数据，订阅链接仍可直接使用。";
    empty.hidden = false;
    empty.textContent = "状态数据加载失败，请稍后刷新。";
  });

fetch('report.json', { cache: 'no-store' })
  .then((response) => response.ok ? response.json() : null)
  .then((report) => {
    if (!report) return;
    const issueCount = Array.isArray(report.issues) ? report.issues.length : 0;
    const message = `最近任务：发现 ${report.discovered_sources ?? 0} 条线路，识别 ${report.matched_sources ?? 0} 条，检测成功 ${report.successful_sources ?? 0} 条。`;
    const detail = document.createElement('p');
    detail.className = 'summary';
    detail.textContent = message + (issueCount ? ` 来源抓取有 ${issueCount} 项问题，详情见运行报告。` : '');
    document.querySelector('.hero > div').append(detail);
  })
  .catch(() => {});
