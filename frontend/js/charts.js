/**
 * Chart.js visualizations
 * Fraud Detection Pipeline Dashboard
 */
'use strict';

// Chart instances (for cleanup on re-render)
let fraudChart = null;
let categoryChart = null;
let rocChart = null;
let featureChart = null;

// ==================== FRAUD DISTRIBUTION (Doughnut) ====================

function renderFraudChart(data) {
    const ctx = document.getElementById('fraud-chart')?.getContext('2d');
    if (!ctx) return;

    if (fraudChart) fraudChart.destroy();

    fraudChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Legítimo', 'Fraude'],
            datasets: [{
                data: [data.legit, data.fraud],
                backgroundColor: ['#22c55e', '#ef4444'],
                borderWidth: 0,
                hoverOffset: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: '60%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#e2e8f0',
                        padding: 20,
                        usePointStyle: true,
                        pointStyleWidth: 12
                    }
                },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#e2e8f0',
                    bodyColor: '#94a3b8',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: {
                        label: function(context) {
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const pct = ((context.parsed / total) * 100).toFixed(2);
                            return `${context.label}: ${context.parsed.toLocaleString()} (${pct}%)`;
                        }
                    }
                }
            }
        }
    });
}

// ==================== CATEGORY DISTRIBUTION (Bar) ====================

function renderCategoryChart(categories) {
    const ctx = document.getElementById('category-chart')?.getContext('2d');
    if (!ctx) return;

    if (categoryChart) categoryChart.destroy();

    categoryChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: categories.map(c => formatCategoryLabel(c.category)),
            datasets: [{
                label: 'Transacciones',
                data: categories.map(c => c.count),
                backgroundColor: '#3b82f6',
                borderRadius: 4,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            indexAxis: 'y',
            scales: {
                y: {
                    ticks: {
                        color: '#94a3b8',
                        font: { size: 11 }
                    },
                    grid: {
                        color: 'rgba(51, 65, 85, 0.5)'
                    }
                },
                x: {
                    ticks: {
                        color: '#94a3b8',
                        callback: function(value) {
                            return value.toLocaleString();
                        }
                    },
                    grid: {
                        color: 'rgba(51, 65, 85, 0.5)'
                    }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#e2e8f0',
                    bodyColor: '#94a3b8',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: {
                        label: function(context) {
                            return `${context.parsed.x.toLocaleString()} transacciones`;
                        }
                    }
                }
            }
        }
    });
}

function formatCategoryLabel(cat) {
    return cat.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
}

// ==================== ROC CURVE (Line) ====================

function renderROCCurve(data) {
    const ctx = document.getElementById('roc-chart')?.getContext('2d');
    if (!ctx) return;

    if (rocChart) rocChart.destroy();

    rocChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.fpr,
            datasets: [
                {
                    label: `ROC (AUC: ${data.auc})`,
                    data: data.tpr,
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2
                },
                {
                    label: 'Random (baseline)',
                    data: data.fpr,
                    borderColor: '#64748b',
                    borderDash: [5, 5],
                    fill: false,
                    pointRadius: 0,
                    borderWidth: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            scales: {
                x: {
                    title: {
                        display: true,
                        text: 'Tasa de Falsos Positivos',
                        color: '#94a3b8'
                    },
                    ticks: { color: '#94a3b8' },
                    grid: { color: 'rgba(51, 65, 85, 0.5)' },
                    min: 0,
                    max: 1
                },
                y: {
                    title: {
                        display: true,
                        text: 'Tasa de Verdaderos Positivos',
                        color: '#94a3b8'
                    },
                    ticks: { color: '#94a3b8' },
                    grid: { color: 'rgba(51, 65, 85, 0.5)' },
                    min: 0,
                    max: 1
                }
            },
            plugins: {
                legend: {
                    labels: {
                        color: '#e2e8f0',
                        usePointStyle: true
                    }
                },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#e2e8f0',
                    bodyColor: '#94a3b8',
                    borderColor: '#334155',
                    borderWidth: 1
                }
            }
        }
    });
}

// ==================== FEATURE IMPORTANCE (Horizontal Bar) ====================

function renderFeatureImportance(features) {
    const ctx = document.getElementById('feature-chart')?.getContext('2d');
    if (!ctx) return;

    if (featureChart) featureChart.destroy();

    // Sort by importance descending
    const sorted = [...features].sort((a, b) => b.importance - a.importance);

    featureChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: sorted.map(f => formatFeatureName(f.name)),
            datasets: [{
                label: 'Importancia',
                data: sorted.map(f => f.importance),
                backgroundColor: sorted.map((_, i) => {
                    const colors = ['#8b5cf6', '#7c3aed', '#6d28d9', '#5b21b6', '#4c1d95',
                                    '#3b82f6', '#2563eb', '#1d4ed8', '#1e40af', '#1e3a8a'];
                    return colors[i % colors.length];
                }),
                borderRadius: 4,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            indexAxis: 'y',
            scales: {
                x: {
                    ticks: {
                        color: '#94a3b8',
                        callback: function(value) {
                            return (value * 100).toFixed(0) + '%';
                        }
                    },
                    grid: { color: 'rgba(51, 65, 85, 0.5)' },
                    min: 0
                },
                y: {
                    ticks: {
                        color: '#94a3b8',
                        font: { size: 11 }
                    },
                    grid: { display: false }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#e2e8f0',
                    bodyColor: '#94a3b8',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: {
                        label: function(context) {
                            return `Importancia: ${(context.parsed.x * 100).toFixed(1)}%`;
                        }
                    }
                }
            }
        }
    });
}

function formatFeatureName(name) {
    return name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
}
