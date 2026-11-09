import { initChat } from "./chat.js";
import { initDashboard } from "./dashboard.js";
import { initHistoryChart } from "./history-chart.js";

initChat();
const prices = await initDashboard();
await initHistoryChart(prices?.reference.long_run_average);
