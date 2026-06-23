/**
 * Core application logic — Tab navigation, API helper, data loading
 * Fraud Detection Pipeline Dashboard
 */

// ==================== TAB NAVIGATION ====================

// Global AbortController — one per tab, cancelled on switch
let activeTabController = null;

document.querySelectorAll(".tab-btn").forEach((btn) => {
	btn.addEventListener("click", () => {
		switchTab(btn.dataset.tab);
	});
});

function switchTab(tabId) {
	// Abort any in-flight request from the previous tab
	if (activeTabController) {
		activeTabController.abort();
		activeTabController = null;
	}

	// Remove active from all tabs
	document
		.querySelectorAll(".tab-btn")
		.forEach((b) => b.classList.remove("active"));
	document
		.querySelectorAll(".tab-content")
		.forEach((c) => c.classList.remove("active"));

	// Activate clicked tab
	const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
	const section = document.getElementById(tabId);
	if (btn) btn.classList.add("active");
	if (section) section.classList.add("active");

	// Load data for tab
	loadTabData(tabId);
}

// ==================== DATA LOADING ====================

async function loadTabData(tab) {
	// Create a new tab-level AbortController
	activeTabController = new AbortController();
	const signal = activeTabController.signal;

	switch (tab) {
		case "overview":
			await loadOverview(signal);
			break;
		case "dataset":
			await loadDataset(signal);
			break;
		case "pipeline":
			await loadPipelineStatus(signal);
			break;
		case "model":
			await loadModelMetrics(signal);
			break;
		case "demo":
			await loadDemoModelStatus(signal);
			break;
		// architecture, pmbok, security, cicd are static
	}
}

// ==================== API HELPER ====================

const ADMIN_TOKEN_STORAGE_KEY = "fraud-dashboard-admin-token";

function getStoredAdminApiToken() {
	return window.sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || "";
}

function setAdminTokenInput(token) {
	const input = document.getElementById("admin-api-token");
	if (input) input.value = token;
}

function getAdminApiToken() {
	const input = document.getElementById("admin-api-token");
	const currentToken = input?.value?.trim() || getStoredAdminApiToken();
	if (currentToken) return currentToken;

	const enteredToken = window.prompt(
		"Ingrese ADMIN_API_TOKEN para ejecutar esta acción protegida:",
	);
	if (!enteredToken?.trim()) return "";

	const token = enteredToken.trim();
	window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token);
	setAdminTokenInput(token);
	return token;
}

function saveAdminApiToken() {
	const input = document.getElementById("admin-api-token");
	const token = input?.value?.trim();
	if (!token) {
		alert("Ingrese un ADMIN_API_TOKEN antes de guardar.");
		return;
	}
	window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token);
	alert("ADMIN_API_TOKEN guardado para esta sesión del navegador.");
}

function clearAdminApiToken() {
	window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
	setAdminTokenInput("");
	alert("ADMIN_API_TOKEN eliminado de esta sesión del navegador.");
}

async function api(endpoint, options = {}) {
	const {
		admin = false,
		headers = {},
		timeout = 30000,
		signal = null,
		...fetchOptions
	} = options;
	const requestOptions = {
		...fetchOptions,
		headers: { ...headers },
	};

	if (admin) {
		const token = getAdminApiToken();
		if (!token) {
			alert("ADMIN_API_TOKEN requerido para esta acción protegida.");
			return null;
		}
		requestOptions.headers.Authorization = `Bearer ${token}`;
	}

	// Use external signal (tab-level) or create per-request controller
	const controller = new AbortController();
	requestOptions.signal = controller.signal;

	// If an external signal is provided, propagate its abort to this controller
	if (signal) {
		if (signal.aborted) {
			controller.abort();
		} else {
			signal.addEventListener("abort", () => controller.abort(), { once: true });
		}
	}

	const timeoutId = setTimeout(() => controller.abort(), timeout);

	try {
		const response = await fetch(endpoint, requestOptions);
		clearTimeout(timeoutId);
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}: ${response.statusText}`);
		}
		return await response.json();
	} catch (error) {
		clearTimeout(timeoutId);
		if (error.name === "AbortError") {
			// Don't show alert if the tab-level controller aborted (user switched tabs)
			if (signal && signal.aborted) return null;
			const seconds = Math.round(timeout / 1000);
			alert(`⏱️ Timeout: la petición a ${endpoint} tardó más de ${seconds}s y fue cancelada.`);
			return null;
		}
		console.error(`API error: ${endpoint}`, error);
		alert(`❌ Error en ${endpoint}: ${error.message}`);
		return null;
	}
}

// ==================== OVERVIEW ====================

async function loadOverview(signal) {
	const kpis = await api("/api/kpis", { signal });
	if (!kpis) return;

	setText("kpi-total", kpis.total_records?.toLocaleString() || "N/A");
	setText(
		"kpi-fraud-pct",
		kpis.fraud_pct !== undefined ? `${kpis.fraud_pct}%` : "N/A",
	);
	setText("kpi-rejected", kpis.rejected_records?.toLocaleString() || "0");
	setText(
		"kpi-completeness",
		kpis.completeness_pct !== undefined ? `${kpis.completeness_pct}%` : "N/A",
	);
}

// ==================== DATASET ====================

async function loadDataset(signal) {
	const [transactions, stats] = await Promise.all([
		api("/api/sql/transactions?limit=10&offset=0", { signal }),
		api("/api/sql/stats", { signal }),
	]);

	if (stats) {
		updateDatasetStats({
			rows: stats.total_transactions || 0,
			cols: "—",
			fraud_count: stats.fraud_count || 0,
			amt_mean: stats.avg_amt ? "$" + stats.avg_amt.toLocaleString() : "—",
		});

		// Render charts from SQL stats
		const total = stats.total_transactions || 0;
		const fraud = stats.fraud_count || 0;
		if (total > 0) {
			renderFraudChart({
				legit: total - fraud,
				fraud: fraud,
			});
		}
		if (stats.by_category && stats.by_category.length > 0) {
			renderCategoryChart(stats.by_category);
		}
	}

	if (transactions && transactions.transactions) {
		updateSampleTable(transactions.transactions);
	}
}

function updateDatasetStats(stats) {
	setText(
		"ds-total-rows",
		stats.rows?.toLocaleString() || stats.total_rows?.toLocaleString() || "—",
	);
	setText("ds-total-cols", stats.cols || stats.columns?.length || "—");

	// Handle nested fraud_distribution from Gold stats
	const fraudCount = stats.fraud_distribution?.fraud ?? stats.fraud_count;
	setText(
		"ds-fraud-count",
		fraudCount !== undefined ? fraudCount.toLocaleString() : "—",
	);

	// Handle nested amt_stats from Gold stats
	const avgAmt = stats.amt_stats?.mean ?? stats.amt_mean;
	setText("ds-avg-amt", avgAmt !== undefined ? `$${avgAmt}` : "—");
}

function updateSampleTable(rows) {
	if (!rows || rows.length === 0) return;

	const table = document.getElementById("sample-table");
	if (!table) return;

	const columns = Object.keys(rows[0]);
	const displayCols = columns.slice(0, 8); // Show first 8 columns

	table.innerHTML = `
        <thead>
            <tr>${displayCols.map((c) => `<th>${formatColumnName(c)}</th>`).join("")}</tr>
        </thead>
        <tbody>
            ${rows
							.map(
								(row) => `
                <tr>${displayCols.map((c) => `<td>${formatCellValue(row[c])}</td>`).join("")}</tr>
            `,
							)
							.join("")}
        </tbody>
    `;
}

function formatColumnName(name) {
	return name.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
}

function formatCellValue(val) {
	if (val === null || val === undefined) return "—";
	if (typeof val === "number") {
		return val % 1 === 0 ? val.toLocaleString() : val.toFixed(2);
	}
	const str = String(val);
	return str.length > 30 ? str.substring(0, 27) + "..." : str;
}

async function loadDataDictionary() {
	const dict = await api("/api/dataset/dictionary");
	if (!dict) return;

	const el = document.getElementById("data-dictionary");
	if (!el) return;

	if (el.style.display === "none") {
		el.style.display = "block";
		el.innerHTML = `
            <h4>Diccionario de Datos</h4>
            <div class="table-wrapper">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Columna</th>
                            <th>Tipo</th>
                            <th>Descripción</th>
                            <th>Sensible</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${dict.columns
													.map(
														(col) => `
                            <tr>
                                <td><code>${col.name}</code></td>
                                <td>${col.type}</td>
                                <td>${col.description}</td>
                                <td>${col.sensitive ? "🔒 Sí" : "No"}</td>
                            </tr>
                        `,
													)
													.join("")}
                    </tbody>
                </table>
            </div>
        `;
	} else {
		el.style.display = "none";
	}
}

// ==================== UTILITY ====================

function setText(id, value) {
	const el = document.getElementById(id);
	if (el) el.textContent = value;
}

// ==================== ETHICS POPUP ====================

function toggleEthicsPopup() {
	const popup = document.getElementById("ethics-popup");
	if (popup) {
		popup.classList.toggle("collapsed");
	}
}

// Collapse by default after a short delay so user notices it
setTimeout(() => {
	const popup = document.getElementById("ethics-popup");
	if (popup && !popup.classList.contains("collapsed")) {
		popup.classList.add("collapsed");
	}
}, 4000);

// ==================== INIT ====================

document.addEventListener("DOMContentLoaded", () => {
	setAdminTokenInput(getStoredAdminApiToken());
	loadTabData("overview");
});
