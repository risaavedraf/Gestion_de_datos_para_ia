let performanceChart = null;

async function loadPerformanceMetrics(signal) {
	const data = await api("/api/metrics/performance?limit=100", { signal });
	if (!data) return;

	renderPerformanceSummary(data.summary);
	renderPerformanceChart(data.history || []);
	renderPerformanceHistory(data.history || []);
}

function renderPerformanceSummary(summary) {
	const latest = summary.latest;
	setText(
		"perf-last-duration",
		latest ? formatDuration(latest.duration_ms) : "—",
	);
	setText(
		"perf-peak-memory",
		latest ? `${formatNumber(latest.memory_rss_mb_peak)} MB` : "—",
	);
	setText("perf-total-operations", summary.total_operations ?? 0);
}

function renderPerformanceChart(history) {
	const canvas = document.getElementById("performance-chart");
	if (!canvas || window.Chart === undefined) return;

	if (performanceChart) performanceChart.destroy();
	const records = history.slice().reverse();
	performanceChart = new Chart(canvas.getContext("2d"), {
		type: "line",
		data: {
			labels: records.map((record) => formatTimestamp(record.timestamp)),
			datasets: [
				{
					label: "Duración (ms)",
					data: records.map((record) => record.duration_ms),
					borderColor: "#3b82f6",
					backgroundColor: "rgba(59, 130, 246, 0.12)",
					fill: true,
					tension: 0.25,
					yAxisID: "duration",
				},
				{
					label: "Memoria pico (MB)",
					data: records.map((record) => record.memory_rss_mb_peak),
					borderColor: "#8b5cf6",
					backgroundColor: "rgba(139, 92, 246, 0.08)",
					fill: true,
					tension: 0.25,
					yAxisID: "memory",
				},
			],
		},
		options: {
			responsive: true,
			maintainAspectRatio: false,
			interaction: { mode: "index", intersect: false },
			scales: {
				x: {
					ticks: { color: "#94a3b8", maxRotation: 45 },
					grid: { color: "rgba(51, 65, 85, 0.5)" },
				},
				duration: {
					position: "left",
					ticks: { color: "#94a3b8" },
					grid: { color: "rgba(51, 65, 85, 0.5)" },
				},
				memory: {
					position: "right",
					ticks: { color: "#c4b5fd" },
					grid: { drawOnChartArea: false },
				},
			},
			plugins: {
				legend: { labels: { color: "#e2e8f0", usePointStyle: true } },
				tooltip: {
					backgroundColor: "#1e293b",
					titleColor: "#e2e8f0",
					bodyColor: "#94a3b8",
					borderColor: "#334155",
					borderWidth: 1,
				},
			},
		},
	});
}

function renderPerformanceHistory(history) {
	const body = document.querySelector("#performance-history tbody");
	const empty = document.getElementById("performance-empty");
	if (!body || !empty) return;

	body.replaceChildren();
	empty.hidden = history.length > 0;
	if (!history.length) return;

	for (const record of history) {
		const row = document.createElement("tr");
		const values = [
			formatTimestamp(record.timestamp),
			record.operation,
			record.stage || "—",
			formatDuration(record.duration_ms),
			`${formatNumber(record.memory_rss_mb_peak)} MB`,
			record.rows_processed ?? record.batch_size ?? record.sample_size ?? "—",
		];
		for (const value of values) {
			const cell = document.createElement("td");
			cell.textContent = String(value);
			row.appendChild(cell);
		}
		const status = document.createElement("td");
		const badge = document.createElement("span");
		badge.className = `badge ${record.status === "success" ? "success" : "danger"}`;
		badge.textContent = record.status === "success" ? "Éxito" : "Error";
		status.appendChild(badge);
		row.appendChild(status);
		body.appendChild(row);
	}
}

function formatDuration(durationMs) {
	if (durationMs === null || durationMs === undefined) return "—";
	return durationMs >= 1000
		? `${formatNumber(durationMs / 1000)} s`
		: `${formatNumber(durationMs)} ms`;
}

function formatNumber(value) {
	return Number(value).toLocaleString("es-CL", { maximumFractionDigits: 2 });
}

function formatTimestamp(value) {
	return new Date(value).toLocaleString("es-CL", {
		dateStyle: "short",
		timeStyle: "medium",
	});
}
