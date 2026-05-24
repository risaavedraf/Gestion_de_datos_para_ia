/**
 * Pipeline stage management — Run pipeline, update status, log output
 * Fraud Detection Pipeline Dashboard
 */


// ==================== RUN FULL PIPELINE ====================

async function runPipeline(sampleSize) {
    const logEl = document.getElementById('pipeline-log');
    const statusEls = {
        bronze: document.getElementById('status-bronze'),
        silver: document.getElementById('status-silver'),
        gold: document.getElementById('status-gold'),
        load: document.getElementById('status-load')
    };

    // Reset statuses
    Object.values(statusEls).forEach(el => {
        if (el) {
            el.textContent = '⏳ Pending';
            el.className = 'status-badge pending';
        }
    });
    if (logEl) logEl.textContent = '';

    const url = sampleSize
        ? `/api/pipeline/run?sample_size=${sampleSize}`
        : '/api/pipeline/run';

    log('🚀 Starting pipeline...');

    try {
        const result = await api(url, { method: 'POST', admin: true });

        if (result?.status === 'success') {
            const stages = result.stages;

            // Bronze
            if (stages.bronze?.status === 'success') {
                updateStatus(statusEls.bronze, '✅ Complete');
                log(`✅ Bronze: ${stages.bronze.rows} rows ingested, ${stages.bronze.cols} columns`);
            } else {
                updateStatus(statusEls.bronze, '❌ Failed');
                log(`❌ Bronze: ${stages.bronze?.error || 'Unknown error'}`);
            }

            // Silver
            if (stages.silver?.status === 'success') {
                updateStatus(statusEls.silver, '✅ Complete');
                log(`✅ Silver: ${stages.silver.rows_out} rows cleaned, PII removed`);
            } else {
                updateStatus(statusEls.silver, '❌ Failed');
                log(`❌ Silver: ${stages.silver?.error || 'Unknown error'}`);
            }

            // Gold
            if (stages.gold?.status === 'success') {
                updateStatus(statusEls.gold, '✅ Complete');
                log(`✅ Gold: ${stages.gold.valid} valid, ${stages.gold.rejected} rejected`);
            } else {
                updateStatus(statusEls.gold, '❌ Failed');
                log(`❌ Gold: ${stages.gold?.error || 'Unknown error'}`);
            }

            // Load
            if (stages.load?.status === 'success') {
                updateStatus(statusEls.load, '✅ Complete');
                log(`✅ Load: ${stages.load.transactions_attempted || 'N/A'} transactions loaded`);
            } else {
                updateStatus(statusEls.load, '⚠️ Skipped');
                log(`⚠️ Load: ${stages.load?.error || 'No PostgreSQL configured'}`);
            }

            const duration = stages.gold?.duration_seconds || stages.silver?.duration_seconds || 'N/A';
            log(`\n⏱️ Pipeline complete in ${duration}s`);

        } else {
            log(`❌ Pipeline failed: ${result?.error || result?.detail || 'Unknown error'}`);
            Object.values(statusEls).forEach(el => {
                if (el && el.textContent === '⏳ Pending') {
                    updateStatus(el, '❌ Failed');
                }
            });
        }
    } catch (error) {
        log(`❌ Error: ${error.message}`);
    }
}

// ==================== UI BRIDGE ====================

function runPipelineFromUI() {
    const input = document.getElementById('sample-size');
    const sampleSize = input?.value ? parseInt(input.value) : null;
    runPipeline(sampleSize);
}

// ==================== RUN INDIVIDUAL STAGE ====================

async function runStage(stage) {
    const statusEl = document.getElementById(`status-${stage}`);
    const logEl = document.getElementById('pipeline-log');

    if (statusEl) {
        statusEl.textContent = '⏳ Running...';
        statusEl.className = 'status-badge warning';
    }

    const sampleInput = document.getElementById('sample-size');
    const sampleSize = sampleInput?.value ? parseInt(sampleInput.value) : null;
    const url = sampleSize
        ? `/api/pipeline/run/${stage}?sample_size=${sampleSize}`
        : `/api/pipeline/run/${stage}`;

    log(`🚀 Running ${stage}...`);

    try {
        const result = await api(url, { method: 'POST', admin: true });

        if (result?.status === 'success' || result?.status === 'available') {
            updateStatus(statusEl, '✅ Complete');

            switch (stage) {
                case 'bronze':
                    log(`✅ Bronze: ${result.rows} rows, ${result.cols} columns`);
                    break;
                case 'silver':
                    log(`✅ Silver: ${result.rows_out} rows cleaned`);
                    break;
                case 'gold':
                    log(`✅ Gold: ${result.valid} valid, ${result.rejected} rejected`);
                    break;
                case 'load':
                    log(`✅ Load: ${result.transactions_attempted || 'N/A'} transactions`);
                    break;
            }
        } else {
            updateStatus(statusEl, '❌ Failed');
            log(`❌ ${stage} failed: ${result?.error || result?.detail || 'Unknown'}`);
        }
    } catch (error) {
        updateStatus(statusEl, '❌ Failed');
        log(`❌ ${stage} error: ${error.message}`);
    }
}

// ==================== HELPERS ====================

function updateStatus(el, text) {
    if (!el) return;
    el.textContent = text;
    if (text.includes('✅')) {
        el.className = 'status-badge success';
    } else if (text.includes('⚠️')) {
        el.className = 'status-badge warning';
    } else if (text.includes('❌')) {
        el.className = 'status-badge danger';
    } else {
        el.className = 'status-badge pending';
    }
}

function log(message) {
    const logEl = document.getElementById('pipeline-log');
    if (logEl) {
        logEl.textContent += message + '\n';
        logEl.scrollTop = logEl.scrollHeight;
    }
}

// ==================== PIPELINE STATUS ====================

async function loadPipelineStatus() {
    const status = await api('/api/pipeline/status');
    if (!status) return;

    updateLayerCard('bronze', status.bronze);
    updateLayerCard('silver', status.silver);
    updateLayerCard('gold', status.gold);
    updateSqlCounts(status.sql_counts);
}

function updateSqlCounts(sqlCounts) {
    const el = document.getElementById('sql-counts-info');
    if (!el) return;

    if (!sqlCounts) {
        el.innerHTML = '<div class="stat sql-unavailable">🗄️ SQL no disponible</div>';
        return;
    }

    el.innerHTML = `
        <div class="sql-counts-grid">
            ${[
                ['customers', 'Clientes'],
                ['merchants', 'Comercios'],
                ['transactions', 'Transacciones'],
                ['pipeline_logs', 'Logs'],
                ['pipeline_load_state', 'Estado Carga']
            ].map(([key, label]) => `
                <div class="sql-count-item">
                    <span class="sql-count">${(sqlCounts[key] || 0).toLocaleString()}</span>
                    <span class="sql-label">${label}</span>
                </div>
            `).join('')}
        </div>
    `;
}

function updateLayerCard(layer, data) {
    const el = document.getElementById(`${layer}-info`);
    if (!el || !data) return;

    if (data.status === 'available' || data.rows !== undefined) {
        el.innerHTML = `
            <div class="stat">Rows: <strong>${(data.rows || 0).toLocaleString()}</strong></div>
            <div class="stat">Columns: <strong>${data.cols || '—'}</strong></div>
            ${data.fraud_pct !== undefined ? `<div class="stat">Fraud: <strong>${data.fraud_pct}%</strong></div>` : ''}
            ${data.amt_mean !== undefined ? `<div class="stat">Avg Amt: <strong>$${data.amt_mean}</strong></div>` : ''}
        `;
    } else {
        el.innerHTML = '<div class="stat">No data available</div>';
    }
}

// ==================== DEMO ====================

async function runDemo() {
    const sizeSelect = document.getElementById('demo-sample-size');
    const sampleSize = sizeSelect?.value ? parseInt(sizeSelect.value) : 1000;
    const logEl = document.getElementById('demo-log');
    const resultsEl = document.getElementById('demo-results');

    if (logEl) logEl.textContent = '';
    if (resultsEl) resultsEl.style.display = 'none';

    demoLog(`🚀 Ejecutando pipeline con ${sampleSize} transacciones...`);
    demoLog('');

    const startTime = Date.now();

    try {
        const url = `/api/pipeline/run?sample_size=${sampleSize}`;
        const result = await api(url, { method: 'POST', admin: true });
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

        if (result?.status === 'success') {
            const s = result.stages;

            demoLog(`✅ Bronze: ${s.bronze?.rows || 0} rows ingested`);
            demoLog(`✅ Silver: ${s.silver?.rows_out || 0} rows cleaned`);
            demoLog(`✅ Gold: ${s.gold?.valid || 0} valid, ${s.gold?.rejected || 0} rejected`);

            if (s.load?.status === 'success') {
                demoLog(`✅ Load: ${s.load.transactions_attempted || 0} transactions to PostgreSQL`);
            } else {
                demoLog(`⚠️ Load: ${s.load?.error || 'PostgreSQL not configured'}`);
            }

            demoLog(`\n⏱️ Completado en ${elapsed}s`);

            // Show results
            if (resultsEl) {
                resultsEl.style.display = 'block';
                setText('demo-bronze-rows', s.bronze?.rows?.toLocaleString() || '—');
                setText('demo-silver-rows', s.silver?.rows_out?.toLocaleString() || '—');
                setText('demo-gold-valid', s.gold?.valid?.toLocaleString() || '—');
                setText('demo-gold-rejected', s.gold?.rejected?.toLocaleString() || '—');
                setText('demo-duration', `${elapsed}s`);
            }

            // After pipeline results, try model prediction
            demoLog('\n🤖 Modelo de Predicción:');
            try {
                const predResult = await api('/api/model/predict', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        amt: 150.00,
                        trans_hour: 23,
                        category: 'shopping_pos',
                        gender: 'M'
                    })
                });

                if (predResult && predResult.prediction !== undefined) {
                    const label = predResult.prediction === 1 ? '⚠️ FRAUDE' : '✅ LEGÍTIMO';
                    const prob = (predResult.probability * 100).toFixed(1);
                    const risk = predResult.risk_level.toUpperCase();
                    demoLog(`  Transacción: $150.00 a las 23hrs, categoría shopping`);
                    demoLog(`  Resultado: ${label} (${prob}% probabilidad, riesgo ${risk})`);

                    // Show prediction card in results
                    const predCard = document.getElementById('demo-prediction-card');
                    if (predCard) {
                        predCard.style.display = 'block';
                        setText('demo-prediction', label);
                        setText('demo-prediction-detail', `${prob}% prob. — Riesgo: ${risk}`);
                    }
                } else {
                    demoLog('  ⚠️ Modelo no entrenado. Ejecute: docker-compose exec app python main.py --train');
                }
            } catch (e) {
                demoLog('  ⚠️ Modelo no disponible. Entrene el modelo primero.');
            }
        } else {
            demoLog(`❌ Pipeline failed: ${result?.error || result?.detail || 'Unknown error'}`);
        }
    } catch (error) {
        demoLog(`❌ Error: ${error.message}`);
    }
}

function demoLog(message) {
    const logEl = document.getElementById('demo-log');
    if (logEl) {
        logEl.textContent += message + '\n';
        logEl.scrollTop = logEl.scrollHeight;
    }
}
