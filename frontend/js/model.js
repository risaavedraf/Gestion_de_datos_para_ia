/**
 * Model metrics, prediction, and retraining UI
 * Fraud Detection Pipeline Dashboard
 */


// ==================== LOAD MODEL METRICS ====================

async function loadModelMetrics() {
	const metrics = await api("/api/model/metrics");
	if (!metrics) return;

	// Update metric cards
	setMetric("accuracy", metrics.accuracy);
	setMetric("precision", metrics.precision);
	setMetric("recall", metrics.recall);
	setMetric("f1", metrics.f1_score);
	setMetric("roc-auc", metrics.roc_auc);

	// Render charts
	if (metrics.confusion_matrix) renderConfusionMatrix(metrics.confusion_matrix);
	if (metrics.roc_curve) renderROCCurve(metrics.roc_curve);
	if (metrics.feature_importance)
		renderFeatureImportance(metrics.feature_importance);
}

function setMetric(id, value) {
	const el = document.getElementById(`metric-${id}`);
	if (el && value !== undefined && value !== null) {
		el.textContent = typeof value === "number" ? value.toFixed(3) : value;
	}
}

// ==================== CONFUSION MATRIX ====================

function renderConfusionMatrix(cm) {
	const el = document.getElementById("confusion-matrix");
	if (!el) return;

	el.innerHTML = `
        <table class="cm-table">
            <tr>
                <td></td>
                <td><strong>Predicted 0</strong></td>
                <td><strong>Predicted 1</strong></td>
            </tr>
            <tr>
                <td><strong>Actual 0</strong></td>
                <td class="tn">${formatNumber(cm.tn)}</td>
                <td class="fp">${formatNumber(cm.fp)}</td>
            </tr>
            <tr>
                <td><strong>Actual 1</strong></td>
                <td class="fn">${formatNumber(cm.fn)}</td>
                <td class="tp">${formatNumber(cm.tp)}</td>
            </tr>
        </table>
    `;
}

function formatNumber(val) {
	if (val === undefined || val === null) return "—";
	return typeof val === "number" ? val.toLocaleString() : val;
}

// ==================== LIVE PREDICTION ====================

async function predictTransaction() {
	const amt = document.getElementById("predict-amt")?.value;
	const hour = document.getElementById("predict-hour")?.value;
	const dayOfWeek = document.getElementById("predict-day-of-week")?.value;
	const month = document.getElementById("predict-month")?.value;
	const category = document.getElementById("predict-category")?.value;
	const gender = document.getElementById("predict-gender")?.value;
	const distance = document.getElementById("predict-distance")?.value;
	const cityPop = document.getElementById("predict-city-pop")?.value;
	const age = document.getElementById("predict-age")?.value;

	if (!amt) {
		alert("Ingrese un monto");
		return;
	}

	const resultEl = document.getElementById("predict-result");
	const placeholderEl = document.getElementById("predict-result-placeholder");
	if (resultEl) {
		resultEl.style.display = "block";
		resultEl.innerHTML = '<div class="loading">Analizando...</div>';
	}
	if (placeholderEl) {
		placeholderEl.style.display = "none";
	}

	const result = await api("/api/model/predict", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			amt: parseFloat(amt),
			trans_hour: parseInt(hour) || 12,
			trans_day_of_week: parseInt(dayOfWeek) || 0,
			trans_month: parseInt(month) || 6,
			category: category || "shopping_pos",
			gender: gender || "M",
			distance_km: parseFloat(distance) || 0,
			city_pop: parseInt(cityPop) || 0,
			age_at_transaction: parseInt(age) || 30,
		}),
	});

	if (result && resultEl) {
		const riskClass =
			result.risk_level === "high"
				? "danger"
				: result.risk_level === "medium"
					? "warning"
					: "success";

		const predLabel =
			result.prediction === 1
				? "⚠️ FRAUDE DETECTADO"
				: "✅ TRANSACCIÓN LEGÍTIMA";
		const probPct =
			result.probability !== undefined
				? (result.probability * 100).toFixed(1) + "%"
				: "N/A";
		const riskText = result.risk_level
			? result.risk_level.toUpperCase()
			: "DESCONOCIDO";

		resultEl.innerHTML = `
            <div class="predict-card ${riskClass}">
                <div class="predict-label">${predLabel}</div>
                <div class="predict-prob">Probabilidad: ${probPct}</div>
                <div class="predict-risk">Nivel de Riesgo: ${riskText}</div>
            </div>
        `;
	} else if (resultEl) {
		resultEl.innerHTML = `
            <div class="predict-card warning">
                <div class="predict-label">⚠️ Modelo no disponible</div>
                <div class="predict-prob">El modelo no está entrenado. Ejecute el entrenamiento primero:</div>
                <div class="predict-prob"><code>docker-compose exec app python main.py --train</code></div>
            </div>
        `;
	}
}

// ==================== RETRAIN MODEL ====================

async function retrainModel() {
	const btn = document.getElementById("retrain-btn");
	if (btn) {
		btn.disabled = true;
		btn.textContent = "⏳ Training... (2-3 min)";
	}

	try {
		const result = await api("/api/model/train", {
			method: "POST",
			admin: true,
		});

		if (result) {
			alert(
				`Training complete!\nBest model: ${result.best_model}\nF1: ${result.best_f1}`,
			);
			await loadModelMetrics();
		} else {
			alert("Training failed. Please check the server logs.");
		}
	} catch (error) {
		alert(`Training error: ${error.message}`);
	} finally {
		if (btn) {
			btn.disabled = false;
			btn.textContent = "🔄 Re-entrenar modelo";
		}
	}
}

// ==================== DEMO SCENARIOS ====================

function loadDemoScenario(scenario) {
	const scenarios = {
		normal: {
			amt: 47.0,
			hour: 13,
			category: "grocery_pos",
			gender: "F",
			day_of_week: 2,
			month: 6,
			distance_km: 10.0,
			city_pop: 88589,
			age: 46,
			label: "Transacción Normal",
		},
		suspicious: {
			amt: 950.0,
			hour: 15,
			category: "shopping_pos",
			gender: "M",
			day_of_week: 5,
			month: 11,
			distance_km: 85.0,
			city_pop: 45000,
			age: 50,
			label: "Transacción Sospechosa",
		},
		night: {
			amt: 800.0,
			hour: 23,
			category: "entertainment",
			gender: "M",
			day_of_week: 6,
			month: 12,
			distance_km: 90.0,
			city_pop: 35000,
			age: 48,
			label: "Compra Nocturna",
		},
		high: {
			amt: 900.0,
			hour: 2,
			category: "misc_net",
			gender: "M",
			day_of_week: 0,
			month: 11,
			distance_km: 100.0,
			city_pop: 30000,
			age: 45,
			label: "Compra Online Madrugada",
		},
	};

	const s = scenarios[scenario];
	if (!s) return;

	document.getElementById("demo-amt").value = s.amt;
	document.getElementById("demo-hour").value = s.hour;
	document.getElementById("demo-day-of-week").value = s.day_of_week;
	document.getElementById("demo-month").value = s.month;
	document.getElementById("demo-category").value = s.category;
	document.getElementById("demo-gender").value = s.gender;
	document.getElementById("demo-distance").value = s.distance_km;
	document.getElementById("demo-city-pop").value = s.city_pop;
	document.getElementById("demo-age").value = s.age;

	// Auto-predict after loading scenario
	runDemoPrediction();
}

async function runDemoPrediction() {
	const amt = document.getElementById("demo-amt")?.value;
	const hour = document.getElementById("demo-hour")?.value;
	const dayOfWeek = document.getElementById("demo-day-of-week")?.value;
	const month = document.getElementById("demo-month")?.value;
	const category = document.getElementById("demo-category")?.value;
	const gender = document.getElementById("demo-gender")?.value;
	const distance = document.getElementById("demo-distance")?.value;
	const cityPop = document.getElementById("demo-city-pop")?.value;
	const age = document.getElementById("demo-age")?.value;

	if (!amt) {
		alert("Ingrese un monto");
		return;
	}

	const resultSection = document.getElementById("demo-result-section");
	const resultEl = document.getElementById("demo-prediction-result");
	const explanationEl = document.getElementById("demo-explanation");
	const placeholderEl = document.getElementById("demo-predict-placeholder");

	if (resultSection) resultSection.style.display = "block";
	if (resultEl)
		resultEl.innerHTML = '<div class="loading">Analizando transacción...</div>';
	if (placeholderEl) placeholderEl.style.display = "none";

	const result = await api("/api/model/predict", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			amt: parseFloat(amt),
			trans_hour: parseInt(hour) || 12,
			trans_day_of_week: parseInt(dayOfWeek) || 0,
			trans_month: parseInt(month) || 6,
			category: category || "shopping_pos",
			gender: gender || "M",
			distance_km: parseFloat(distance) || 0,
			city_pop: parseInt(cityPop) || 0,
			age_at_transaction: parseInt(age) || 30,
		}),
	});

	if (result && result.prediction !== undefined) {
		const isFraud = result.prediction === 1;
		const probPct = (result.probability * 100).toFixed(1);
		const riskClass =
			result.risk_level === "high"
				? "danger"
				: result.risk_level === "medium"
					? "warning"
					: "success";

		if (resultEl) {
			resultEl.innerHTML = `
                <div class="predict-card ${riskClass} demo-predict-card">
                    <div class="predict-icon">${isFraud ? "⚠️" : "✅"}</div>
                    <div class="predict-label">${isFraud ? "FRAUDE DETECTADO" : "TRANSACCIÓN LEGÍTIMA"}</div>
                    <div class="predict-prob">Probabilidad: ${probPct}%</div>
                    <div class="predict-risk">Nivel de Riesgo: ${result.risk_level.toUpperCase()}</div>
                </div>
            `;
		}

		if (explanationEl) {
			const reasons = [];
			try {
				const metrics = await api("/api/model/metrics");
				if (metrics?.feature_importance?.length) {
					const top = metrics.feature_importance.slice(0, 3);
					reasons.push(
						...top.map(
							(f) =>
								f.name + " (peso: " + (f.importance * 100).toFixed(1) + "%)",
						),
					);
				}
			} catch (_) {
				/* fallback to heuristics */
			}

			if (reasons.length === 0) {
				if (parseFloat(amt) > 500) reasons.push("monto elevado");
				if (parseInt(hour) >= 22 || parseInt(hour) <= 5)
					reasons.push("horario nocturno");
				if (parseFloat(distance) > 100) reasons.push("distancia inusual");
				if (reasons.length === 0) reasons.push("características normales");
			}

			explanationEl.innerHTML = `
                <div class="explanation-card">
                    <h4>📊 Explicación del Modelo</h4>
                    <p>Factores considerados: <strong>${reasons.join(", ")}</strong></p>
                    <p>El modelo ${isFraud ? "detectó patrones asociados a transacciones fraudulentas" : "no encontró señales de riesgo"} en esta transacción.</p>
                </div>
            `;
		}
	} else {
		if (resultEl) {
			resultEl.innerHTML = `
                <div class="predict-card warning">
                    <div class="predict-label">⚠️ Modelo no disponible</div>
                    <div class="predict-prob">El modelo no está entrenado. Ejecute:</div>
                    <div class="predict-prob"><code>docker-compose exec app python main.py --train</code></div>
                </div>
            `;
		}
	}
}

async function loadDemoModelStatus() {
	const el = document.getElementById("demo-model-info");
	if (!el) return;

	try {
		const metrics = await api("/api/model/metrics");
		if (metrics) {
			el.innerHTML = `
                <div class="model-status-loaded">
                    <div class="model-status-header">
                        <span class="status-dot green"></span>
                        <strong>Modelo cargado:</strong> RandomForest
                    </div>
                    <div class="model-metrics-grid">
                        <div class="model-metric-card">
                            <div class="model-metric-label">Accuracy</div>
                            <div class="model-metric-value">${metrics.accuracy?.toFixed(4) || "N/A"}</div>
                        </div>
                        <div class="model-metric-card">
                            <div class="model-metric-label">Precisión</div>
                            <div class="model-metric-value">${metrics.precision?.toFixed(4) || "N/A"}</div>
                        </div>
                        <div class="model-metric-card">
                            <div class="model-metric-label">Recall</div>
                            <div class="model-metric-value">${metrics.recall?.toFixed(4) || "N/A"}</div>
                        </div>
                        <div class="model-metric-card">
                            <div class="model-metric-label">F1-Score</div>
                            <div class="model-metric-value">${metrics.f1_score?.toFixed(4) || "N/A"}</div>
                        </div>
                        <div class="model-metric-card">
                            <div class="model-metric-label">ROC-AUC</div>
                            <div class="model-metric-value">${metrics.roc_auc?.toFixed(4) || "N/A"}</div>
                        </div>
                    </div>
                </div>
            `;
		} else {
			el.innerHTML = `
                <div class="model-status-none">
                    <span class="status-dot yellow"></span>
                    <strong>Modelo no entrenado.</strong> Ejecute: <code>docker-compose exec app python main.py --train</code>
                </div>
            `;
		}
	} catch (e) {
		el.innerHTML = `
            <div class="model-status-error">
                <span class="status-dot red"></span>
                <strong>Error al cargar estado del modelo</strong>
            </div>
        `;
	}
}
