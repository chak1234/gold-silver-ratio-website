import { fetchJson } from "./api.js";

const TABLE_ROWS = 60;

const byId = (id) => document.getElementById(id);
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const dateLabel = (iso, options) =>
  new Intl.DateTimeFormat(undefined, { ...options, timeZone: "UTC" }).format(new Date(iso));

let points = [];
let recorded = false;
let rangeDays = 90;
let referenceRatio = null;
let chart = null;

const crosshair = {
  id: "crosshair",
  afterDatasetsDraw({ ctx, tooltip, chartArea }) {
    const active = tooltip?.getActiveElements?.() ?? [];
    if (!active.length) return;
    ctx.save();
    ctx.strokeStyle = cssVar("--muted");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(active[0].element.x, chartArea.top);
    ctx.lineTo(active[0].element.x, chartArea.bottom);
    ctx.stroke();
    ctx.restore();
  },
};

/** Directly label the reference line so the chart needs no legend. */
const referenceLabel = {
  id: "referenceLabel",
  afterDatasetsDraw({ ctx, scales, chartArea }) {
    if (referenceRatio === null) return;
    ctx.save();
    ctx.fillStyle = cssVar("--muted");
    ctx.font = "12px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.fillText(
      `Long-run average, about ${referenceRatio}:1`,
      chartArea.left + 8,
      scales.y.getPixelForValue(referenceRatio) - 4,
    );
    ctx.restore();
  },
};

function visiblePoints() {
  const cutoff = new Date();
  cutoff.setUTCDate(cutoff.getUTCDate() - rangeDays);
  const earliest = cutoff.toISOString().slice(0, 10);
  return points.filter((point) => point.date >= earliest);
}

function chartConfig(visible) {
  const ink = cssVar("--ink");
  const muted = cssVar("--muted");
  const line = cssVar("--line");
  const series = cssVar("--chart-line");
  const surface = cssVar("--surface");
  const datasets = [
    {
      label: "Ratio",
      data: visible.map((point) => point.ratio),
      borderColor: series,
      borderWidth: 2,
      pointRadius: 0,
      pointHitRadius: 24,
      pointHoverRadius: 5,
      pointHoverBackgroundColor: series,
      pointHoverBorderColor: surface,
      pointHoverBorderWidth: 2,
      tension: 0,
    },
  ];
  if (referenceRatio !== null) {
    datasets.push({
      label: "Long-run average",
      data: visible.map(() => referenceRatio),
      borderColor: muted,
      borderWidth: 1,
      pointRadius: 0,
      pointHoverRadius: 0,
    });
  }

  return {
    type: "line",
    data: {
      labels: visible.map((point) => dateLabel(point.date, { month: "short", day: "numeric" })),
      datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: reducedMotion.matches ? false : { duration: 300 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: surface,
          borderColor: line,
          borderWidth: 1,
          titleColor: muted,
          bodyColor: ink,
          displayColors: false,
          padding: 10,
          filter: (item) => item.datasetIndex === 0,
          callbacks: {
            title: (items) =>
              dateLabel(visible[items[0].dataIndex].date, { dateStyle: "medium" }),
            label: (item) => `Ratio ${item.parsed.y.toFixed(2)}:1`,
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: line },
          ticks: { color: muted, autoSkip: true, maxTicksLimit: 7, maxRotation: 0 },
        },
        y: {
          grid: { color: line },
          border: { display: false },
          ticks: { color: muted, maxTicksLimit: 6 },
        },
      },
    },
    plugins: [crosshair, referenceLabel],
  };
}

function renderTable(visible) {
  const rows = visible
    .slice(-TABLE_ROWS)
    .reverse()
    .map((point) => {
      const row = document.createElement("tr");
      const cells = [
        dateLabel(point.date, { dateStyle: "medium" }),
        point.ratio.toFixed(2),
        point.gold.toFixed(2),
        point.silver.toFixed(2),
      ];
      cells.forEach((text, index) => {
        const cell = document.createElement(index === 0 ? "th" : "td");
        if (index === 0) cell.scope = "row";
        cell.textContent = text;
        row.append(cell);
      });
      return row;
    });
  byId("history-rows").replaceChildren(...rows);
  byId("chart-table").hidden = rows.length === 0;
}

function showMessage(text) {
  chart?.destroy();
  chart = null;
  byId("chart-message").textContent = text;
  byId("chart-message").hidden = false;
  byId("ratio-chart").closest(".chart-frame").classList.add("is-empty");
}

function draw() {
  const visible = visiblePoints();
  renderTable(visible);
  if (visible.length < 2) {
    showMessage(
      recorded
        ? "This site records one reading a day. The chart appears after two days of readings."
        : "Not enough history for this range yet.",
    );
    return;
  }
  if (typeof window.Chart === "undefined") {
    showMessage("The chart library could not be loaded. The data is available in the table below.");
    return;
  }
  byId("chart-message").hidden = true;
  byId("ratio-chart").closest(".chart-frame").classList.remove("is-empty");
  chart?.destroy();
  chart = new window.Chart(byId("ratio-chart"), chartConfig(visible));
}

/** Load the history once. ``longRunAverage`` draws the reference line when known. */
export async function initHistoryChart(longRunAverage) {
  referenceRatio = longRunAverage ?? null;

  const buttons = document.querySelectorAll("[data-range]");
  for (const button of buttons) {
    button.addEventListener("click", () => {
      rangeDays = Number(button.dataset.range);
      for (const other of buttons) {
        other.setAttribute("aria-pressed", String(other === button));
      }
      draw();
    });
  }
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (points.length) draw();
  });

  try {
    const history = await fetchJson("/api/history");
    ({ points, recorded } = history);
    if (recorded && points.length) {
      byId("history-lead").textContent =
        `Daily readings recorded by this site since ${dateLabel(points[0].date, { dateStyle: "medium" })}.`;
    } else {
      byId("history-lead").textContent = `Daily ratio from ${history.source}.`;
    }
    draw();
  } catch {
    document.querySelector(".segmented").hidden = true;
    showMessage("Historical data is unavailable right now.");
  }
}
