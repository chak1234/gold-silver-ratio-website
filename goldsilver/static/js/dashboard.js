import { fetchJson } from "./api.js";

const GRAMS_PER_OUNCE = 31.1035;
const REFRESH_MS = 60 * 1000; // Served from the backend cache, so visitors add no provider requests.
const CURRENCIES = ["USD", "CAD", "INR"];

const byId = (id) => document.getElementById(id);

const currency = (value, code) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(value);

const number = (value, digits) =>
  new Intl.NumberFormat(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);

const timestamp = (iso) =>
  new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(iso),
  );

let latest = null;

function renderPriceTable(prices) {
  const rows = CURRENCIES.map((code) => {
    const { gold, silver, rate } = prices[code];
    const cells = [
      currency(gold.gram, code),
      currency(gold.gram * 10, code),
      currency(gold.ounce, code),
      currency(silver.gram, code),
      currency(silver.gram * 100, code),
      currency(silver.ounce, code),
      number(rate, code === "USD" ? 2 : 4),
    ];
    const row = document.createElement("tr");
    const header = document.createElement("th");
    header.scope = "row";
    header.textContent = code;
    row.append(header);
    for (const text of cells) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.append(cell);
    }
    return row;
  });
  byId("price-rows").replaceChildren(...rows);
}

function renderCalculator() {
  const amount = Number.parseFloat(byId("investment-amount").value);
  const valid = latest && Number.isFinite(amount) && amount >= 0;
  for (const metal of ["gold", "silver"]) {
    const ounces = valid ? amount / latest.prices.USD[metal].ounce : null;
    byId(`${metal}-ounces`).textContent = valid ? number(ounces, metal === "gold" ? 4 : 2) : "—";
    byId(`${metal}-grams`).textContent = valid ? number(ounces * GRAMS_PER_OUNCE, 2) : "—";
  }
}

function render(data) {
  latest = data;
  const average = data.reference.long_run_average;
  const difference = ((data.ratio - average) / average) * 100;
  const position = difference >= 0 ? "above" : "below";

  byId("ratio-value").textContent = number(data.ratio, 2);
  byId("ratio-summary").textContent =
    `One ounce of gold buys ${number(data.ratio, 2)} ounces of silver, ` +
    `${number(Math.abs(difference), 0)}% ${position} the long-run average of about ${average}:1.`;
  byId("gold-spot").textContent = currency(data.prices.USD.gold.ounce, "USD");
  byId("silver-spot").textContent = currency(data.prices.USD.silver.ounce, "USD");

  byId("reference-average").textContent = `~${average}:1`;
  byId("reference-low").textContent = `~${data.reference.modern_low}:1`;
  byId("reference-high").textContent = `~${data.reference.modern_high}:1`;
  byId("versus-average").textContent = `${difference >= 0 ? "+" : "−"}${number(Math.abs(difference), 1)}%`;

  const status = byId("data-status");
  status.classList.toggle("status--warning", data.stale);
  status.textContent = data.stale
    ? `The data provider is unreachable. Showing the last available prices from ${timestamp(data.as_of)}.`
    : `Prices as of ${timestamp(data.as_of)} · Source: ${data.source}`;

  renderPriceTable(data.prices);
  renderCalculator();
}

function renderUnavailable() {
  const status = byId("data-status");
  status.classList.add("status--warning");
  status.textContent = latest
    ? "Could not refresh prices. The figures shown may be out of date."
    : "Prices are unavailable right now. Please try again shortly.";
  if (!latest) {
    byId("ratio-summary").textContent = "";
  }
}

async function refresh() {
  try {
    const data = await fetchJson("/api/prices");
    render(data);
    return data;
  } catch {
    renderUnavailable();
    return null;
  }
}

/** Start the dashboard. Resolves with the first price payload, or null. */
export function initDashboard() {
  byId("investment-amount").addEventListener("input", renderCalculator);
  setInterval(() => {
    if (!document.hidden) {
      refresh();
    }
  }, REFRESH_MS);
  return refresh();
}
