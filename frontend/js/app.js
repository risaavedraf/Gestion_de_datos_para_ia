/**
 * Core application logic — Tab navigation, API helper, data loading
 * Fraud Detection Pipeline Dashboard
 */


// ==================== TAB NAVIGATION ====================

document.querySelectorAll(".tab-btn").forEach((btn) => {
	btn.addEventListener("click", () => {
		switchTab(btn.dataset.tab);
	});
});

function switchTab(tabId) {
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
	switch (tab) {
		case "overview":
			await loadOverview();
			break;
		case "dataset":
			await loadDataset();
			break;
		case "pipeline":
			await loadPipelineStatus();
			break;
		case "model":
			await loadModelMetrics();
			break;
		case "demo":
			await loadDemoModelStatus();
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
	const { admin = false, headers = {}, ...fetchOptions } = options;
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

	try {
		const response = await fetch(endpoint, requestOptions);
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}: ${response.statusText}`);
		}
		return await response.json();
	} catch (error) {
		console.error(`API error: ${endpoint}`, error);
		return null;
	}
}

// ==================== OVERVIEW ====================

async function loadOverview() {
	const kpis = await api("/api/kpis");
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

const DATASET_SOURCE_KEY = "fraud-dashboard-dataset-source";

function getDatasetSource() {
    return window.localStorage.getItem(DATASET_SOURCE_KEY) || "parquet";
}

function setDatasetSource(source) {
    window.localStorage.setItem(DATASET_SOURCE_KEY, source);
}

function toggleDatasetSource() {
    const current = getDatasetSource();
    const next = current === "parquet" ? "sql" : "parquet";
    setDatasetSource(next);
    updateSourceToggleUI();
    loadDataset();
}

function updateSourceToggleUI() {
    const source = getDatasetSource();
    const btn = document.getElementById("dataset-source-toggle");
    if (btn) {
        btn.textContent = source === "parquet"
            ? "🗄️ Cambiar a SQL"
            : "📁 Cambiar a Parquet";
        btn.className = "btn btn-secondary toggle-btn toggle-" + source;
    }
    const badge = document.getElementById("dataset-source-badge");
    if (badge) {
        badge.textContent = source === "parquet" ? "📁 Parquet" : "🗄️ PostgreSQL";
    }
}

async function loadDatasetFromSQL() {
    const [transactions, stats, _kpis] = await Promise.all([
        api("/api/sql/transactions?limit=10&offset=0"),
        api("/api/sql/stats"),
        api("/api/sql/kpis"),
    ]);

    if (stats) {
        updateDatasetStats({
            rows: stats.total_transactions || 0,
            cols: "—",
            fraud_count: stats.fraud_count || 0,
            amt_mean: stats.avg_amt
                ? "$" + stats.avg_amt.toLocaleString()
                : "—",
        });
    }

    if (transactions && transactions.transactions) {
        updateSampleTable(transactions.transactions);
    }

    // Charts not available from SQL — show placeholder message
    const fraudChart = document.getElementById("fraud-chart");
    const categoryChart = document.getElementById("category-chart");
    if (fraudChart) {
        const ctx = fraudChart.getContext("2d");
        ctx.clearRect(0, 0, fraudChart.width, fraudChart.height);
        ctx.font = "14px sans-serif";
        ctx.fillStyle = "#888";
        ctx.textAlign = "center";
        ctx.fillText(
            "Charts only available in Parquet mode",
            fraudChart.width / 2,
            fraudChart.height / 2,
        );
    }
    if (categoryChart) {
        const ctx = categoryChart.getContext("2d");
        ctx.clearRect(0, 0, categoryChart.width, categoryChart.height);
        ctx.font = "14px sans-serif";
        ctx.fillStyle = "#888";
        ctx.textAlign = "center";
        ctx.fillText(
            "Charts only available in Parquet mode",
            categoryChart.width / 2,
            categoryChart.height / 2,
        );
    }
}

async function loadDataset() {
    const source = getDatasetSource();
    updateSourceToggleUI();

    if (source === "sql") {
        return loadDatasetFromSQL();
    }

	const [stats, sample, fraudDist, catDist] = await Promise.all([
		api("/api/dataset/stats"),
		api("/api/dataset/sample?n=10"),
		api("/api/dataset/fraud-dist"),
		api("/api/dataset/category-dist"),
	]);

	if (stats) updateDatasetStats(stats);
	if (sample) updateSampleTable(sample.sample);
	if (fraudDist) renderFraudChart(fraudDist);
	if (catDist) renderCategoryChart(catDist.categories);
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
