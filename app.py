"""Streamlit frontend for the Analyst + Auditor research system."""
from __future__ import annotations
import json
import os
import re
import streamlit as st
from urllib.parse import urlparse

from questions import QUESTIONS
from research_api import (
    research,
    extract_trace_timeline,
    extract_sources,
    extract_provider_info,
    load_evaluation_summary,
    load_question_trace,
    INR_RATE,
)

st.set_page_config(
    page_title="Research Analyst & Auditor",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ──────────────────────────────────────────────────────────────────────

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
    --sidebar-bg: #0f1729;
    --sidebar-hover: #1a2540;
    --sidebar-text: #8b9ec2;
    --sidebar-text-active: #f1f5f9;
    --sidebar-accent: #3b82f6;
    --sidebar-divider: #1e2d4a;

    --bg-page: #f8fafc;
    --bg-card: #ffffff;
    --bg-subtle: #f1f5f9;
    --bg-input: #f8fafc;

    --border: #e2e8f0;
    --border-light: #f1f5f9;

    --text-primary: #0f172a;
    --text-secondary: #475569;
    --text-muted: #94a3b8;
    --text-caption: #64748b;

    --accent: #3b82f6;
    --accent-dark: #2563eb;
    --accent-soft: #dbeafe;
    --green: #16a34a;
    --green-soft: #dcfce7;
    --green-bg: #f0fdf4;
    --yellow: #d97706;
    --yellow-soft: #fef3c7;
    --red: #dc2626;
    --red-soft: #fee2e2;
    --orange: #ea580c;
    --orange-soft: #ffedd5;
    --orange-bg: #fff7ed;
    --purple: #7c3aed;
    --purple-soft: #ede9fe;
    --cyan: #0891b2;
    --cyan-soft: #cffafe;

    --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.06), 0 2px 4px -2px rgba(0,0,0,0.04);
    --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.07), 0 4px 6px -4px rgba(0,0,0,0.03);
    --shadow-card: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    --radius: 12px;
    --radius-sm: 8px;
    --radius-lg: 16px;
}

/* ── Global ─────────────────────────────── */
.main .block-container { padding-top: 1.5rem; }

/* ── Sidebar ────────────────────────────── */
section[data-testid="stSidebar"] {
    background: var(--sidebar-bg) !important;
    border-right: 1px solid var(--sidebar-divider) !important;
    min-width: 260px !important;
}
section[data-testid="stSidebar"] * {
    color: var(--sidebar-text) !important;
}
section[data-testid="stSidebar"] .stRadio label[data-checked="true"] span,
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] strong {
    color: var(--sidebar-text-active) !important;
}
section[data-testid="stSidebar"] hr {
    border-color: var(--sidebar-divider) !important;
    margin: 8px 0 !important;
}
section[data-testid="stSidebar"] .stRadio > div {
    gap: 0 !important;
}
section[data-testid="stSidebar"] .stRadio [data-testid="stWidgetLabel"] {
    display: none !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label {
    padding: 8px 12px !important;
    border-radius: 8px !important;
    margin-bottom: 2px !important;
    cursor: pointer !important;
    transition: background 0.15s !important;
    display: block !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label:hover {
    background: var(--sidebar-hover) !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) {
    background: var(--sidebar-hover) !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label span,
section[data-testid="stSidebar"] [role="radiogroup"] > label p {
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    color: var(--sidebar-text-active) !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label > div:first-child,
section[data-testid="stSidebar"] [role="radiogroup"] > label > input {
    display: none !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label::after {
    display: block;
    font-size: 0.68rem !important;
    color: #8b9ec2 !important;
    font-weight: 400 !important;
    margin-top: 1px;
    line-height: 1.3;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label:first-child::after {
    content: "Ask a question and get verified answers with sources";
}
section[data-testid="stSidebar"] [role="radiogroup"] > label:last-child::after {
    content: "Run benchmark and view results";
}

.sidebar-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: #f1f5f9 !important;
    margin-bottom: 2px;
    line-height: 1.2;
}
.sidebar-subtitle {
    font-size: 0.7rem;
    color: #8b9ec2 !important;
    line-height: 1.3;
    margin-bottom: 0;
}
.sidebar-nav-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px 12px;
    border-radius: 8px;
    margin-bottom: 2px;
    cursor: pointer;
    transition: background 0.15s;
}
.sidebar-nav-item:hover { background: var(--sidebar-hover); }
.sidebar-nav-icon {
    font-size: 1rem;
    margin-top: 1px;
    flex-shrink: 0;
}
.sidebar-nav-label {
    font-size: 0.82rem;
    font-weight: 600;
    color: #f1f5f9 !important;
    line-height: 1.3;
}
.sidebar-nav-desc {
    font-size: 0.68rem;
    color: #8b9ec2 !important;
    line-height: 1.3;
    margin-top: 1px;
}
.sidebar-feature {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 0;
    font-size: 0.75rem;
    color: #8b9ec2 !important;
}
.sidebar-feature-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
}
.sidebar-step {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 4px 0;
}
.sidebar-step-num {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.65rem;
    font-weight: 700;
    color: #fff;
    flex-shrink: 0;
}
.sidebar-step-label {
    font-size: 0.75rem;
    font-weight: 600;
    color: #cbd5e8 !important;
    line-height: 1.3;
}
.sidebar-step-desc {
    font-size: 0.65rem;
    color: #8b9ec2 !important;
    line-height: 1.3;
}

/* ── Page header ────────────────────────── */
.page-header {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 4px;
}
.page-header-icon {
    width: 44px;
    height: 44px;
    border-radius: 12px;
    background: var(--accent);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.3rem;
    flex-shrink: 0;
}
.page-header-title {
    font-size: 1.5rem;
    font-weight: 800;
    color: var(--text-primary);
    line-height: 1.2;
}
.page-header-subtitle {
    font-size: 0.85rem;
    color: var(--text-secondary);
    margin-top: 2px;
}

/* ── Feature cards (header row) ─────────── */
.feature-cards {
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
    flex-wrap: wrap;
}
.feature-card {
    flex: 1;
    min-width: 140px;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 14px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-sm);
}
.feature-card-icon {
    width: 34px;
    height: 34px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1rem;
    flex-shrink: 0;
}
.feature-card-icon.fc-blue { background: var(--accent-soft); }
.feature-card-icon.fc-purple { background: var(--purple-soft); }
.feature-card-icon.fc-green { background: var(--green-soft); }
.feature-card-icon.fc-red { background: var(--red-soft); }
.feature-card-icon.fc-pink { background: #fce7f3; }
.feature-card-title {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.3;
}
.feature-card-desc {
    font-size: 0.7rem;
    color: var(--text-muted);
    line-height: 1.3;
}

/* ── Card ───────────────────────────────── */
.card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-card);
}
.card-header {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    margin-bottom: 12px;
}
.card-title {
    font-size: 1.1rem;
    font-weight: 700;
    color: var(--text-primary);
    margin-bottom: 4px;
}

/* ── Section header with icon ───────────── */
.section-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 6px;
}
.section-icon {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.95rem;
    flex-shrink: 0;
}
.section-icon.si-blue { background: var(--accent-soft); }
.section-icon.si-purple { background: var(--purple-soft); }
.section-icon.si-green { background: var(--green-soft); }
.section-icon.si-orange { background: var(--orange-soft); }
.section-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text-primary);
}
.section-subtitle {
    font-size: 0.78rem;
    color: var(--text-secondary);
}

/* ── Metric card ────────────────────────── */
.metric-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 16px 20px;
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.15s ease;
}
.metric-card:hover { box-shadow: var(--shadow-md); }
.metric-card-row {
    display: flex;
    align-items: center;
    gap: 12px;
}
.metric-icon {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1rem;
    flex-shrink: 0;
}
.mi-green { background: var(--green-soft); }
.mi-blue { background: var(--accent-soft); }
.mi-purple { background: var(--purple-soft); }
.mi-orange { background: var(--orange-soft); }
.mi-cyan { background: var(--cyan-soft); }
.mi-yellow { background: var(--yellow-soft); }
.metric-value {
    font-size: 1.4rem;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1.2;
}
.metric-label {
    font-size: 0.68rem;
    font-weight: 500;
    color: var(--text-muted);
    margin-top: 1px;
    line-height: 1.3;
}
.metric-card-simple {
    text-align: center;
}
.metric-card-simple .metric-value {
    font-size: 1.5rem;
    color: var(--accent);
}
.metric-card-simple .metric-label {
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
}
.metric-subtitle {
    font-size: 0.65rem;
    color: var(--text-muted);
    margin-top: 1px;
    line-height: 1.3;
}

/* ── Verdict badges ─────────────────────── */
.verdict-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
    white-space: nowrap;
}
.verdict-supported { background: var(--green-soft); color: var(--green); }
.verdict-unsupported { background: var(--yellow-soft); color: var(--yellow); }
.verdict-contradicted { background: var(--red-soft); color: var(--red); }
.verdict-no_citation { background: var(--bg-subtle); color: var(--text-muted); }
.verdict-source_error { background: var(--orange-soft); color: var(--orange); }

/* ── Claim card ─────────────────────────── */
.claim-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 16px 20px;
    margin-bottom: 12px;
    box-shadow: var(--shadow-sm);
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
}
.claim-text {
    font-size: 0.88rem;
    color: var(--text-primary);
    line-height: 1.55;
    flex: 1;
}
.claim-source {
    font-size: 0.73rem;
    color: var(--text-caption);
    margin-top: 4px;
}

/* ── Timeline ───────────────────────────── */
.timeline-container {
    position: relative;
    padding-left: 32px;
    margin-left: 12px;
}
.timeline-container::before {
    content: '';
    position: absolute;
    left: 12px;
    top: 0;
    bottom: 0;
    width: 2px;
    background: linear-gradient(to bottom, var(--accent), var(--border));
    border-radius: 1px;
}
.timeline-step {
    position: relative;
    padding: 12px 0 12px 8px;
}
.timeline-step::before {
    content: '';
    position: absolute;
    left: -25px;
    top: 18px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--accent);
    border: 2px solid var(--bg-card);
    box-shadow: 0 0 0 2px var(--accent);
    z-index: 1;
}
.timeline-step.step-search::before { background: var(--cyan); box-shadow: 0 0 0 2px var(--cyan); }
.timeline-step.step-evidence::before { background: var(--purple); box-shadow: 0 0 0 2px var(--purple); }
.timeline-step.step-fetch::before { background: var(--green); box-shadow: 0 0 0 2px var(--green); }
.timeline-step.step-fallback::before { background: var(--orange); box-shadow: 0 0 0 2px var(--orange); }
.timeline-step.step-complete::before { background: var(--green); box-shadow: 0 0 0 2px var(--green); }
.timeline-label {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-primary);
}
.timeline-time {
    font-size: 0.72rem;
    color: var(--text-muted);
    margin-left: 8px;
}
.timeline-detail {
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin-top: 4px;
    line-height: 1.4;
}

/* ── Source cards ────────────────────────── */
.source-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 14px 18px;
    margin-bottom: 10px;
    box-shadow: var(--shadow-sm);
    border-left: 3px solid var(--border);
}
.source-card.source-fetched { border-left-color: var(--green); }
.source-card.source-selected { border-left-color: var(--accent); }
.source-title { font-size: 0.85rem; font-weight: 600; color: var(--text-primary); }
.source-url { font-size: 0.75rem; color: var(--accent); text-decoration: none; }
.source-meta { font-size: 0.72rem; color: var(--text-muted); margin-top: 4px; }

/* ── Provider info ──────────────────────── */
.provider-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    background: var(--bg-subtle);
    border: 1px solid var(--border);
    border-radius: 20px;
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--text-primary);
    margin-right: 8px;
    margin-bottom: 6px;
}
.provider-arrow { color: var(--text-muted); font-size: 0.9rem; margin: 0 4px; }

/* ── Status pills ───────────────────────── */
.status-success {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 12px; background: var(--green-soft); color: var(--green);
    border-radius: 12px; font-size: 0.75rem; font-weight: 600;
}
.status-fail {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 12px; background: var(--red-soft); color: var(--red);
    border-radius: 12px; font-size: 0.75rem; font-weight: 600;
}

/* ── Difficulty labels ──────────────────── */
.diff-easy { color: var(--green); font-weight: 600; }
.diff-easy-medium { color: var(--green); font-weight: 600; }
.diff-medium { color: var(--accent); font-weight: 600; }
.diff-medium-hard { color: var(--orange); font-weight: 600; }
.diff-hard { color: var(--red); font-weight: 600; }

/* ── Difficulty pill badges ────────────── */
.diff-pill {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    white-space: nowrap;
}
.diff-pill-easy { background: var(--green-soft); color: var(--green); }
.diff-pill-easy-medium { background: var(--green-soft); color: var(--green); }
.diff-pill-medium { background: var(--accent-soft); color: var(--accent); }
.diff-pill-medium-hard { background: var(--orange-soft); color: var(--orange); }
.diff-pill-hard { background: var(--red-soft); color: var(--red); }

/* ── Eval result rows ──────────────────── */
.eval-row {
    display: flex;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border-light);
    gap: 16px;
    font-size: 0.85rem;
    transition: background 0.1s;
}
.eval-row:hover { background: var(--bg-subtle); }
.eval-row:last-child { border-bottom: none; }
.eval-row-num {
    font-weight: 700;
    color: var(--text-primary);
    min-width: 32px;
    flex-shrink: 0;
}
.eval-row-question {
    flex: 1;
    color: var(--text-secondary);
    line-height: 1.4;
    min-width: 0;
}
.eval-row-diff { flex-shrink: 0; }
.eval-row-status { flex-shrink: 0; }
.eval-row-chevron {
    color: var(--text-muted);
    font-size: 1.1rem;
    flex-shrink: 0;
    margin-left: auto;
}

/* ── Evaluation table ───────────────────── */
.eval-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    overflow: hidden;
    font-size: 0.82rem;
}
.eval-table thead th {
    background: var(--bg-subtle);
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
}
.eval-table tbody td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-light);
    color: var(--text-primary);
    vertical-align: middle;
}
.eval-table tbody tr:last-child td { border-bottom: none; }
.eval-table tbody tr:hover { background: var(--bg-subtle); }
.eval-table .q-text {
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.8rem;
    color: var(--text-secondary);
}

/* ── Quick tips ─────────────────────────── */
.quick-tips {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    box-shadow: var(--shadow-sm);
}
.quick-tip-header {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.9rem;
    font-weight: 700;
    color: var(--text-primary);
    margin-bottom: 14px;
}
.quick-tip-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 5px 0;
    font-size: 0.8rem;
    color: var(--text-secondary);
    line-height: 1.4;
}
.quick-tip-num {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.68rem;
    font-weight: 700;
    color: #fff;
    flex-shrink: 0;
}

/* ── Aggregate metrics list ─────────────── */
.agg-metric-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid var(--border-light);
    font-size: 0.82rem;
}
.agg-metric-row:last-child { border-bottom: none; }
.agg-metric-label {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--text-secondary);
}
.agg-metric-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}
.agg-metric-value {
    font-weight: 700;
    color: var(--text-primary);
}

/* ── Input card ─────────────────────────── */
.input-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-card);
}
.input-card .stTextArea textarea {
    background: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
}
.input-label {
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 6px;
}
.char-counter {
    font-size: 0.72rem;
    color: var(--text-muted);
    text-align: right;
    margin-top: -10px;
    margin-bottom: 12px;
}

/* ── Empty state ────────────────────────── */
.empty-state {
    text-align: center;
    padding: 48px 24px;
    color: var(--text-muted);
    background: var(--bg-card);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    box-shadow: var(--shadow-card);
}
.empty-state-illustration {
    display: flex;
    justify-content: center;
    align-items: center;
    margin-bottom: 16px;
}
.empty-state-illustration svg {
    width: 120px;
    height: 120px;
}
.empty-state-icon {
    font-size: 2.5rem;
    margin-bottom: 12px;
    opacity: 0.4;
}
.empty-state-title {
    font-size: 1.1rem;
    font-weight: 700;
    color: var(--text-primary);
    margin-bottom: 6px;
}
.empty-state-text {
    font-size: 0.85rem;
    color: var(--text-secondary);
    max-width: 480px;
    margin: 0 auto;
    line-height: 1.6;
}

/* ── Historical baseline banner ─────────── */
.baseline-banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 18px;
    background: var(--orange-bg);
    border: 1px solid #fed7aa;
    border-radius: var(--radius-sm);
    margin-bottom: 16px;
}
.baseline-icon {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: var(--orange-soft);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1rem;
    flex-shrink: 0;
}
.baseline-title {
    font-size: 0.85rem;
    font-weight: 700;
    color: var(--orange);
}
.baseline-text {
    font-size: 0.78rem;
    color: #9a3412;
    line-height: 1.4;
}

/* ── Legend dots ─────────────────────────── */
.legend {
    display: flex;
    gap: 16px;
    font-size: 0.75rem;
    color: var(--text-muted);
    margin-bottom: 10px;
}
.legend-item {
    display: flex;
    align-items: center;
    gap: 5px;
}
.legend-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
}

/* ── Overrides ──────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 2px;
    background: var(--bg-subtle);
    padding: 4px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
}
.stTabs [data-baseweb="tab"] {
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 0.82rem;
    font-weight: 500;
}
div[data-testid="stExpander"] {
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-sm);
}
.stButton > button[kind="primary"] {
    border-radius: var(--radius-sm);
    font-weight: 600;
    padding: 10px 24px;
    background: #ef4444 !important;
    border-color: #ef4444 !important;
    color: #fff !important;
    font-size: 0.95rem;
}
.stButton > button[kind="primary"]:hover {
    background: #dc2626 !important;
    border-color: #dc2626 !important;
}
.stTextArea textarea {
    border-radius: var(--radius-sm) !important;
    border: 1px solid var(--border) !important;
    background: var(--bg-input) !important;
}
.stToggle label { font-size: 0.85rem !important; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

VERDICT_ICONS = {
    "supported": ("✓", "Supported"),
    "unsupported": ("⚠", "Unsupported"),
    "contradicted": ("✕", "Contradicted"),
    "no_citation": ("○", "No Citation"),
    "source_error": ("⚠", "Source Error"),
}

DIFFICULTY_CLASS = {
    "easy": "diff-easy",
    "easy-medium": "diff-easy-medium",
    "medium": "diff-medium",
    "medium-hard": "diff-medium-hard",
    "hard": "diff-hard",
}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


def _short_model(model: str) -> str:
    if "/" in model:
        return model.split("/")[-1].replace(":free", "")
    return model


def _render_metric_card(value: str, label: str, icon: str = "", icon_class: str = "mi-blue",
                        subtitle: str = ""):
    sub_html = f'<div class="metric-subtitle">{subtitle}</div>' if subtitle else ""
    if icon:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-card-row">'
            f'<div class="metric-icon {icon_class}">{icon}</div>'
            f'<div>'
            f'<div class="metric-value">{value}</div>'
            f'<div class="metric-label">{label}</div>'
            f'{sub_html}'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="metric-card metric-card-simple">'
            f'<div class="metric-value">{value}</div>'
            f'<div class="metric-label">{label}</div>'
            f'{sub_html}'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_verdict_badge(verdict: str):
    icon, label = VERDICT_ICONS.get(verdict, ("?", verdict))
    return (
        f'<span class="verdict-badge verdict-{verdict}">'
        f'{icon} {label}</span>'
    )


def _step_class(event: str) -> str:
    if event in ("search", "parsed_queries", "follow_up_search"):
        return "step-search"
    if event == "evidence_evaluation":
        return "step-evidence"
    if event in ("fetch_page",):
        return "step-fetch"
    if event in ("model_fallback", "ollama_fallback", "degraded_answer"):
        return "step-fallback"
    if event in ("research_complete", "final_answer"):
        return "step-complete"
    return ""


# ── Sidebar ──────────────────────────────────────────────────────────────────

def render_sidebar():
    with st.sidebar:
        st.markdown(
            '<div style="padding:2px 0 0;">'
            '<div style="font-size:1.3rem; margin-bottom:4px;">✦</div>'
            '<div class="sidebar-title">Research<br>Analyst & Auditor</div>'
            '<div class="sidebar-subtitle">Evidence-aware research agent with independent verification</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        st.divider()

        nav_labels = {
            "Research": "🔍  Research",
            "Evaluation": "📊  Evaluation",
        }
        page = st.radio(
            "Navigation",
            ["Research", "Evaluation"],
            index=0,
            label_visibility="collapsed",
            format_func=lambda x: nav_labels.get(x, x),
        )

        st.divider()

        st.markdown(
            '<div class="sidebar-feature"><div class="sidebar-feature-dot" style="background:#3b82f6;"></div> Evidence-aware research</div>'
            '<div class="sidebar-feature"><div class="sidebar-feature-dot" style="background:#16a34a;"></div> Independent claim verification</div>'
            '<div class="sidebar-feature"><div class="sidebar-feature-dot" style="background:#7c3aed;"></div> Reusable research memory</div>'
            '<div class="sidebar-feature"><div class="sidebar-feature-dot" style="background:#d97706;"></div> Source-level citations</div>',
            unsafe_allow_html=True,
        )

        st.divider()

        st.markdown(
            '<div style="font-size:0.72rem; font-weight:600; color:#cbd5e8 !important; margin-bottom:6px;">How it works</div>'
            '<div class="sidebar-step">'
            '<div class="sidebar-step-num" style="background:#3b82f6;">1</div>'
            '<div><div class="sidebar-step-label">Plan</div>'
            '<div class="sidebar-step-desc">Analyze the question and create search strategy</div></div></div>'
            '<div class="sidebar-step">'
            '<div class="sidebar-step-num" style="background:#0891b2;">2</div>'
            '<div><div class="sidebar-step-label">Research</div>'
            '<div class="sidebar-step-desc">Search, fetch, and evaluate evidence</div></div></div>'
            '<div class="sidebar-step">'
            '<div class="sidebar-step-num" style="background:#16a34a;">3</div>'
            '<div><div class="sidebar-step-label">Answer</div>'
            '<div class="sidebar-step-desc">Generate answer with citations</div></div></div>'
            '<div class="sidebar-step">'
            '<div class="sidebar-step-num" style="background:#7c3aed;">4</div>'
            '<div><div class="sidebar-step-label">Verify</div>'
            '<div class="sidebar-step-desc">Auditor independently checks claims</div></div></div>',
            unsafe_allow_html=True,
        )
    return page


# ── Answer panel ─────────────────────────────────────────────────────────────

def render_answer_panel(answer_data: dict):
    st.markdown(
        '<div class="section-header">'
        '<div class="section-icon si-blue">📝</div>'
        '<div><div class="section-title">Answer</div>'
        '<div class="section-subtitle">Evidence-based answer with citations</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    summary = answer_data.get("summary", "")
    if not summary or len(summary.strip()) < 10:
        st.markdown(
            '<div class="empty-state">'
            '<div class="empty-state-icon">📋</div>'
            '<div class="empty-state-title">Ask a question to get started</div>'
            '<div class="empty-state-text">'
            'Your research answer will appear here with citations and verification results.'
            '</div></div>',
            unsafe_allow_html=True,
        )
    else:
        display = re.sub(
            r'\[(https?://[^\]]+)\]',
            r'[\1](\1)',
            summary,
        )
        st.markdown(display)

    sources = answer_data.get("sources_used", [])
    if sources:
        st.caption(f"{len(sources)} source(s) cited")


# ── Claims / Auditor panel ──────────────────────────────────────────────────

def render_claims_panel(audit_data: dict):
    st.markdown(
        '<div class="section-header">'
        '<div class="section-icon si-purple">🛡️</div>'
        '<div><div class="section-title">Claim Verification</div>'
        '<div class="section-subtitle">Each claim independently verified by the Auditor against original sources</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    verdicts = audit_data.get("verdicts", [])
    if not verdicts:
        st.info("No claims to verify.")
        return

    supported = sum(1 for v in verdicts if v.get("verdict") == "supported")
    total = len(verdicts)
    if total:
        st.markdown(
            f'<div style="font-size:0.82rem; color:var(--text-secondary); margin-bottom:16px;">'
            f'<strong>{supported}</strong> of <strong>{total}</strong> claims supported by evidence'
            f'</div>',
            unsafe_allow_html=True,
        )

    for i, v in enumerate(verdicts):
        claim = v.get("claim", {})
        verdict = v.get("verdict", "unknown")
        text = claim.get("text", "")
        citation = claim.get("citation", "")
        from_mem = claim.get("from_memory", False)
        badge = _render_verdict_badge(verdict)

        source_line = ""
        if citation:
            source_line = f'<div class="claim-source">Source: <a href="{citation}" target="_blank">{_domain(citation)}</a></div>'
        if from_mem:
            source_line += '<div class="claim-source">🧠 Informed by prior memory</div>'

        st.markdown(
            f'<div class="claim-card">'
            f'<div class="claim-text"><strong>{i+1}.</strong> {text}{source_line}</div>'
            f'<div>{badge}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        evidence = v.get("evidence", "")
        excerpt = v.get("source_excerpt", "")
        if evidence or excerpt:
            with st.expander("Auditor evidence", expanded=False):
                if evidence:
                    st.markdown(f"**Verdict rationale:** {evidence}")
                if excerpt:
                    st.markdown(f"**Source excerpt:** _{excerpt[:300]}_")


# ── Timeline panel ──────────────────────────────────────────────────────────

def render_timeline_panel(trace: list[dict]):
    st.markdown(
        '<div class="section-header">'
        '<div class="section-icon si-blue">≡</div>'
        '<div><div class="section-title">Research Process</div>'
        '<div class="section-subtitle">Step-by-step research timeline</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    timeline = extract_trace_timeline(trace)

    if not timeline:
        st.info("No research trace available.")
        return

    phase_icons = {
        "plan": "📋", "parsed_queries": "🔍", "search": "🌐",
        "source_selection": "📌", "fetch_page": "📄",
        "evidence_evaluation": "⚖️", "follow_up_search": "🔄",
        "research_complete": "✅", "final_answer": "💡",
        "memory_recall": "🧠", "model_fallback": "🔀",
        "ollama_fallback": "🔀", "degraded_answer": "⚠️",
        "conflicts_detected": "⚡",
    }

    html_parts = ['<div class="timeline-container">']

    for step in timeline:
        event = step["event"]
        icon = phase_icons.get(event, "•")
        ts = step.get("timestamp", 0)
        label = step.get("label", event)
        css_class = _step_class(event)

        detail_lines = []
        if event == "plan" and step.get("detail"):
            detail_lines.append(step["detail"][:200])
        elif event == "search":
            detail_lines.append(f'Query: {step.get("query", "")}')
            detail_lines.append(f'{step.get("result_count", 0)} results')
        elif event == "source_selection":
            sel = step.get("selected", [])
            detail_lines.append(f'{len(sel)} selected from {step.get("total_candidates", 0)} candidates')
        elif event == "fetch_page":
            if step.get("title"):
                detail_lines.append(step["title"][:80])
            if step.get("text_length"):
                detail_lines.append(f'{step["text_length"]:,} chars extracted')
        elif event == "evidence_evaluation":
            suff = step.get("sufficient", False)
            detail_lines.append(f'Evidence {"sufficient" if suff else "insufficient"}')
            if step.get("follow_up"):
                detail_lines.append(f'Follow-up: {step["follow_up"]}')
        elif event in ("model_fallback", "ollama_fallback"):
            detail_lines.append(f'{step.get("from_model", "")} → {step.get("to_model", "")}')
        elif event == "research_complete":
            detail_lines.append(step.get("reason", ""))

        detail_html = "<br>".join(detail_lines) if detail_lines else ""
        detail_block = f'<div class="timeline-detail">{detail_html}</div>' if detail_html else ""

        html_parts.append(
            f'<div class="timeline-step {css_class}">'
            f'<span class="timeline-label">{icon} {label}</span>'
            f'<span class="timeline-time">{ts:.1f}s</span>'
            f'{detail_block}'
            f'</div>'
        )

    html_parts.append('</div>')
    st.markdown("\n".join(html_parts), unsafe_allow_html=True)


# ── Sources panel ────────────────────────────────────────────────────────────

def render_sources_panel(trace: list[dict]):
    st.markdown(
        '<div class="section-header">'
        '<div class="section-icon si-green">🔗</div>'
        '<div><div class="section-title">Evidence & Sources</div>'
        '<div class="section-subtitle">Web sources gathered during research</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    sources = extract_sources(trace)

    if not sources:
        st.info("No sources found in trace.")
        return

    fetched = [s for s in sources if s["was_fetched"]]
    selected = [s for s in sources if s["was_selected"] and not s["was_fetched"]]
    search_only = [s for s in sources if not s["was_selected"]]

    if fetched:
        st.markdown(f"**Fetched evidence** ({len(fetched)} pages)")
        for s in fetched:
            title = s.get("title") or _domain(s["url"])
            score_str = f' · score {s["score"]:.2f}' if s.get("score") else ""
            st.markdown(
                f'<div class="source-card source-fetched">'
                f'<div class="source-title">{title}</div>'
                f'<a class="source-url" href="{s["url"]}" target="_blank">{_domain(s["url"])}</a>'
                f'<div class="source-meta">{score_str}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    if selected:
        st.markdown(f"**Selected but not fetched** ({len(selected)})")
        for s in selected:
            st.markdown(
                f'<div class="source-card source-selected">'
                f'<a class="source-url" href="{s["url"]}" target="_blank">{_domain(s["url"])}</a>'
                f'</div>',
                unsafe_allow_html=True,
            )

    if search_only:
        with st.expander(f"All search results ({len(search_only)} not selected)", expanded=False):
            for s in search_only[:20]:
                st.markdown(f"- [{_domain(s['url'])}]({s['url']})")


# ── Memory panel ─────────────────────────────────────────────────────────────

def render_memory_panel(memory_before: dict, memory_after: dict, trace: list[dict]):
    st.markdown(
        '<div class="section-header">'
        '<div class="section-icon si-purple">🧠</div>'
        '<div><div class="section-title">Memory</div>'
        '<div class="section-subtitle">Entity knowledge base</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    recall_events = [e for e in trace if e.get("event") == "memory_recall"]
    had_recall = len(recall_events) > 0

    if had_recall:
        entities = []
        for ev in recall_events:
            entities.extend(ev.get("entities_found", []))
        st.markdown("**Entities recalled:** " + ", ".join(entities))
        st.info("Previous knowledge was used to guide research. Fresh web evidence was still required.")
    else:
        st.caption("No prior memory was used for this question.")

    new_entities = set(memory_after.keys()) - set(memory_before.keys())
    updated_entities = set()
    for k in set(memory_after.keys()) & set(memory_before.keys()):
        if len(memory_after[k].get("facts", [])) > len(memory_before[k].get("facts", [])):
            updated_entities.add(k)

    if new_entities or updated_entities:
        st.markdown("**New knowledge stored:**")
        for ent_key in new_entities | updated_entities:
            ent = memory_after[ent_key]
            name = ent_key.replace("_", " ").title()
            facts = ent.get("facts", [])
            prefix = "New" if ent_key in new_entities else "Updated"
            st.markdown(f"- **{name}** ({prefix}) — {len(facts)} fact(s)")
    else:
        st.caption("No new entities stored from this question.")


# ── Metrics panel ────────────────────────────────────────────────────────────

def render_metrics_panel(answer_data: dict, audit_data: dict,
                         analyst_latency: float, auditor_latency: float,
                         total_latency: float):
    st.markdown(
        '<div class="section-header">'
        '<div class="section-icon si-blue">📊</div>'
        '<div><div class="section-title">Metrics</div>'
        '<div class="section-subtitle">Research performance and cost</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    a_cost = answer_data.get("cost", {})
    u_cost = audit_data.get("cost", {})
    verdicts = audit_data.get("verdicts", [])

    verdict_counts = {}
    for v in verdicts:
        vtype = v.get("verdict", "unknown")
        verdict_counts[vtype] = verdict_counts.get(vtype, 0) + 1

    trace = answer_data.get("tool_trace", [])
    searches = sum(1 for e in trace if e.get("event") == "search")
    fetches = sum(1 for e in trace if e.get("event") == "fetch_page")
    rounds = max((e.get("round", 0) for e in trace), default=0)
    evals = sum(1 for e in trace if e.get("event") == "evidence_evaluation")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _render_metric_card(str(rounds), "Research Rounds", "🔄", "mi-blue")
    with c2:
        _render_metric_card(str(searches), "Web Searches", "🌐", "mi-cyan")
    with c3:
        _render_metric_card(str(fetches), "Pages Fetched", "📄", "mi-green")
    with c4:
        _render_metric_card(str(evals), "Evidence Evals", "⚖️", "mi-purple")

    st.write("")

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        _render_metric_card(str(len(verdicts)), "Claims", "🛡️", "mi-blue")
    with c6:
        _render_metric_card(str(verdict_counts.get("supported", 0)), "Supported", "✓", "mi-green")
    with c7:
        unsup = verdict_counts.get("unsupported", 0) + verdict_counts.get("contradicted", 0)
        _render_metric_card(str(unsup), "Unsupported", "⚠", "mi-yellow")
    with c8:
        _render_metric_card(str(verdict_counts.get("no_citation", 0)), "No Citation", "○", "mi-orange")

    st.write("")

    a_in = a_cost.get("input_tokens", 0)
    a_out = a_cost.get("output_tokens", 0)
    u_in = u_cost.get("input_tokens", 0)
    u_out = u_cost.get("output_tokens", 0)
    total_cost_inr = (a_cost.get("cost_usd", 0) + u_cost.get("cost_usd", 0)) * INR_RATE

    t1, t2, t3 = st.columns(3)
    with t1:
        _render_metric_card(f"{a_in + a_out:,}", "Analyst Tokens", "📊", "mi-blue")
    with t2:
        _render_metric_card(f"{u_in + u_out:,}", "Auditor Tokens", "🛡️", "mi-purple")
    with t3:
        _render_metric_card(f"₹{total_cost_inr:.2f}", "Total Cost", "💰", "mi-green")

    st.write("")

    l1, l2, l3 = st.columns(3)
    with l1:
        _render_metric_card(f"{analyst_latency:.1f}s", "Analyst Latency", "⏱", "mi-cyan")
    with l2:
        _render_metric_card(f"{auditor_latency:.1f}s", "Auditor Latency", "⏱", "mi-orange")
    with l3:
        _render_metric_card(f"{total_latency:.1f}s", "Total Latency", "⏱", "mi-blue")


# ── Provider panel ───────────────────────────────────────────────────────────

def render_provider_panel(trace: list[dict]):
    st.markdown(
        '<div class="section-header">'
        '<div class="section-icon si-orange">⚙️</div>'
        '<div><div class="section-title">Provider</div>'
        '<div class="section-subtitle">Model and fallback information</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    info = extract_provider_info(trace)

    if info["had_fallback"]:
        chips = [f'<span class="provider-chip">{_short_model(info["primary"])}</span>']
        for fb in info["fallbacks"]:
            chips.append('<span class="provider-arrow">→</span>')
            chips.append(f'<span class="provider-chip">{_short_model(fb["to"])}</span>')

        st.markdown(" ".join(chips), unsafe_allow_html=True)

        for fb in info["fallbacks"]:
            if fb.get("reason"):
                st.caption(f'Fallback reason: {fb["reason"]}')
    else:
        st.markdown(
            f'<span class="provider-chip">{_short_model(info["final"])}</span>',
            unsafe_allow_html=True,
        )


# ── Evaluation page ─────────────────────────────────────────────────────────

def render_evaluation_page():
    # Page header
    st.markdown(
        '<div class="page-header">'
        '<div class="page-header-icon" style="background:var(--purple);">📊</div>'
        '<div>'
        '<div class="page-header-title">Evaluation</div>'
        '<div class="page-header-subtitle">Benchmark the analyst and auditor across research questions</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )

    summary = load_evaluation_summary()

    if summary is None:
        st.markdown(
            '<div class="empty-state">'
            '<div class="empty-state-icon">📊</div>'
            '<div class="empty-state-title">No evaluation results</div>'
            '<div class="empty-state-text">'
            'Run <code>python runner.py --fresh-memory</code> '
            'to generate evaluation traces.'
            '</div></div>',
            unsafe_allow_html=True,
        )
        return

    per_q = summary.get("per_question", [])
    models_used = {_short_model(pq.get("analyst_model", "")) for pq in per_q
                   if pq.get("analyst_model")}
    model_label = ", ".join(sorted(models_used)) if models_used else "unknown"

    is_current = any(
        any(ev.get("event") == "evidence_evaluation"
            for ev in (load_question_trace(pq.get("q", 0)) or {}).get("analyst", {}).get("tool_trace", []))
        for pq in per_q[:1]
    )

    if not is_current:
        st.markdown(
            f'<div class="baseline-banner">'
            f'<div class="baseline-icon">⏱</div>'
            f'<div>'
            f'<div class="baseline-title">Historical baseline</div>'
            f'<div class="baseline-text">This evaluation predates the evidence-aware research loop and represents the {model_label} baseline.</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    # Summary metrics row
    succeeded = summary.get("succeeded", 0)
    failed_count = summary.get("failed", 0)
    total_q = summary.get("total_questions", 0)
    total_lat = summary.get("total_latency_s", 0)
    a_tok = summary.get("total_analyst_tokens", {})
    u_tok = summary.get("total_auditor_tokens", {})
    mem_reuse = summary.get("memory_reuse_questions", [])
    total_a = a_tok.get("input", 0) + a_tok.get("output", 0)
    total_u = u_tok.get("input", 0) + u_tok.get("output", 0)
    total_tokens = total_a + total_u
    success_pct = f"{(succeeded / total_q * 100):.0f}%" if total_q else "0%"
    fail_pct = f"{(failed_count / total_q * 100):.0f}%" if total_q else "0%"
    runtime_min = total_lat / 60
    avg_runtime = runtime_min / total_q if total_q else 0
    avg_tokens = total_tokens / total_q if total_q else 0

    sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
    with sc1:
        _render_metric_card(str(total_q), "Total Questions", "✅", "mi-green",
                            "Across different topics")
    with sc2:
        _render_metric_card(str(succeeded), "Successful", "✅", "mi-green",
                            f"{success_pct} success rate")
    with sc3:
        _render_metric_card(str(failed_count), "Failed", "❌", "mi-orange",
                            f"{fail_pct} failure rate")
    with sc4:
        tok_label = f"{total_tokens / 1000:.1f}K" if total_tokens >= 1000 else str(total_tokens)
        _render_metric_card(tok_label, "Total Tokens", "📄", "mi-blue",
                            f"Avg. {avg_tokens / 1000:.1f}K per question")
    with sc5:
        cost_str = f'₹{summary.get("total_cost_inr", 0):.2f}'
        _render_metric_card(cost_str, "Total Cost", "💰", "mi-green")
    with sc6:
        rt_label = f"{runtime_min:.1f} min" if runtime_min >= 1 else f"{total_lat:.0f}s"
        _render_metric_card(rt_label, "Total Runtime", "⏱", "mi-purple",
                            f"Avg. {avg_runtime:.1f} min per question")

    st.write("")

    # Question Results section — full width
    q_text_map = {q["id"]: q["question"] for q in QUESTIONS}

    st.markdown(
        '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">'
        '<div class="section-header" style="margin-bottom:0;">'
        '<div class="section-icon si-blue">📋</div>'
        '<div><div class="section-title">Question Results</div></div>'
        '</div>'
        '<div class="legend">'
        f'<div class="legend-item"><div class="legend-dot" style="background:var(--green);"></div> Succeeded</div>'
        f'<div class="legend-item"><div class="legend-dot" style="background:var(--red);"></div> Failed</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )

    DIFF_PILL_CLASS = {
        "easy": "diff-pill-easy",
        "easy-medium": "diff-pill-easy-medium",
        "medium": "diff-pill-medium",
        "medium-hard": "diff-pill-medium-hard",
        "hard": "diff-pill-hard",
    }

    rows_html = [
        '<div class="card" style="padding:0; overflow:hidden;">'
        '<table class="eval-table">'
        '<thead><tr>'
        '<th style="width:50px;">#</th><th>Question</th>'
        '<th style="width:120px;">Difficulty</th><th style="width:120px;">Status</th>'
        '</tr></thead>'
        '<tbody>'
    ]

    for pq in per_q:
        qid = pq.get("q", "?")
        status = pq.get("status", "?")
        status_html = (
            '<span class="status-success">✓ Succeeded</span>'
            if status == "success"
            else '<span class="status-fail">✕ Failed</span>'
        )
        difficulty = pq.get("difficulty", "")
        diff_pill_cls = DIFF_PILL_CLASS.get(difficulty, "")
        q_text = q_text_map.get(qid, "")

        rows_html.append(
            f'<tr>'
            f'<td><strong>Q{qid}</strong></td>'
            f'<td style="max-width:none; white-space:normal;">{q_text}</td>'
            f'<td><span class="diff-pill {diff_pill_cls}">{difficulty.replace("-", " ").title()}</span></td>'
            f'<td>{status_html}</td>'
            f'</tr>'
        )

    rows_html.append('</tbody></table></div>')
    st.markdown("\n".join(rows_html), unsafe_allow_html=True)

    st.write("")

    # Question Details section — full width header + selector
    st.markdown(
        '<div class="section-header">'
        '<div class="section-icon si-purple">📋</div>'
        '<div><div class="section-title">Question Details</div>'
        '<div class="section-subtitle">Select a question to view the analyst answer, evidence, auditor verification, and research trace</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    selected_q = st.selectbox(
        "Select question to inspect",
        options=[f"Q{q['id']}: {q['question'][:80]}..." for q in QUESTIONS],
        index=None,
        placeholder="Choose a question...",
    )

    if selected_q:
        qid = int(selected_q.split(":")[0][1:])
        trace_data = load_question_trace(qid)

        if trace_data is None:
            st.warning(f"No trace found for Q{qid}")
        elif trace_data.get("status") == "failed":
            st.error(f"Q{qid} failed: {trace_data.get('error', 'Unknown error')}")
        else:
            col_details, col_agg = st.columns([3, 1])

            with col_details:
                analyst_data = trace_data.get("analyst", {})
                audit_data = trace_data.get("audit", {})

                tab_ans, tab_process, tab_sources, tab_claims, tab_mem = st.tabs([
                    "📝 Answer", "≡ Research Process", "🔗 Sources",
                    "🛡️ Claim Verification", "🧠 Memory",
                ])

                with tab_ans:
                    render_answer_panel(analyst_data)
                with tab_process:
                    render_timeline_panel(analyst_data.get("tool_trace", []))
                with tab_sources:
                    render_sources_panel(analyst_data.get("tool_trace", []))
                with tab_claims:
                    render_claims_panel(audit_data)
                with tab_mem:
                    render_memory_panel(
                        {},
                        trace_data.get("memory_state", {}),
                        analyst_data.get("tool_trace", []),
                    )

            with col_agg:
                st.markdown(
                    '<div class="card" style="padding:16px;">'
                    '<div style="display:flex;align-items:center;gap:8px;font-size:0.85rem;font-weight:700;color:var(--text-primary);margin-bottom:12px;">'
                    '📊 Aggregate Metrics</div>'
                    f'<div class="agg-metric-row"><div class="agg-metric-label">Success Rate</div><div class="agg-metric-value" style="color:var(--green);">{success_pct}</div></div>'
                    f'<div class="agg-metric-row"><div class="agg-metric-label">Total Questions</div><div class="agg-metric-value">{total_q}</div></div>'
                    f'<div class="agg-metric-row"><div class="agg-metric-label">Total Cost</div><div class="agg-metric-value">{cost_str}</div></div>'
                    f'<div class="agg-metric-row"><div class="agg-metric-label">Total Tokens</div><div class="agg-metric-value">{total_tokens:,}</div></div>'
                    f'<div class="agg-metric-row"><div class="agg-metric-label">Total Runtime</div><div class="agg-metric-value">{runtime_min:.1f} min</div></div>'
                    f'<div class="agg-metric-row"><div class="agg-metric-label">Avg Runtime</div><div class="agg-metric-value">{avg_runtime:.1f} min</div></div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

                if mem_reuse:
                    reuse_badges = " ".join(
                        f'<span style="display:inline-block;padding:3px 12px;background:var(--accent-soft);'
                        f'color:var(--accent);border-radius:14px;font-size:0.78rem;font-weight:600;margin:2px;">Q{q}</span>'
                        for q in mem_reuse
                    )
                    st.markdown(
                        f'<div class="card" style="padding:16px; margin-top:10px;">'
                        f'<div style="display:flex;align-items:center;gap:8px;font-size:0.85rem;font-weight:700;color:var(--text-primary);margin-bottom:8px;">🧠 Memory Reuse</div>'
                        f'<div style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:8px;line-height:1.4;">'
                        f'Reused in {len(mem_reuse)} question(s):</div>'
                        f'{reuse_badges}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div class="card" style="padding:16px; margin-top:10px;">'
                        '<div style="display:flex;align-items:center;gap:8px;font-size:0.85rem;font-weight:700;color:var(--text-primary);margin-bottom:8px;">🧠 Memory Reuse</div>'
                        '<div style="font-size:0.78rem;color:var(--text-muted);">No memory reuse detected.</div>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
    else:
        col_empty, col_agg = st.columns([3, 1])
        with col_empty:
            st.markdown(
                '<div style="text-align:center; padding:32px 16px; color:var(--text-muted);'
                'background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);">'
                '<div class="empty-state-illustration">'
                '<svg viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">'
                '<rect x="25" y="15" width="60" height="75" rx="6" fill="#dbeafe" stroke="#93c5fd" stroke-width="1.5"/>'
                '<rect x="35" y="30" width="30" height="3" rx="1.5" fill="#93c5fd"/>'
                '<rect x="35" y="38" width="40" height="3" rx="1.5" fill="#bfdbfe"/>'
                '<rect x="35" y="46" width="25" height="3" rx="1.5" fill="#bfdbfe"/>'
                '<rect x="35" y="54" width="35" height="3" rx="1.5" fill="#bfdbfe"/>'
                '<rect x="35" y="62" width="20" height="3" rx="1.5" fill="#bfdbfe"/>'
                '<circle cx="80" cy="75" r="22" fill="#eff6ff" stroke="#60a5fa" stroke-width="2"/>'
                '<circle cx="80" cy="75" r="12" fill="#dbeafe" stroke="#60a5fa" stroke-width="1.5"/>'
                '<line x1="92" y1="87" x2="102" y2="97" stroke="#60a5fa" stroke-width="3" stroke-linecap="round"/>'
                '</svg>'
                '</div>'
                '<div style="font-size:1rem; font-weight:700; color:var(--text-primary); margin-top:8px;">Select a question</div>'
                '<div class="empty-state-text">'
                'Choose a question from the dropdown above to view detailed results.'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )
        with col_agg:
            st.markdown(
                '<div class="card" style="padding:16px;">'
                '<div style="display:flex;align-items:center;gap:8px;font-size:0.85rem;font-weight:700;color:var(--text-primary);margin-bottom:12px;">'
                '📊 Aggregate Metrics</div>'
                f'<div class="agg-metric-row"><div class="agg-metric-label">Success Rate</div><div class="agg-metric-value" style="color:var(--green);">{success_pct}</div></div>'
                f'<div class="agg-metric-row"><div class="agg-metric-label">Total Questions</div><div class="agg-metric-value">{total_q}</div></div>'
                f'<div class="agg-metric-row"><div class="agg-metric-label">Total Cost</div><div class="agg-metric-value">{cost_str}</div></div>'
                f'<div class="agg-metric-row"><div class="agg-metric-label">Total Tokens</div><div class="agg-metric-value">{total_tokens:,}</div></div>'
                f'<div class="agg-metric-row"><div class="agg-metric-label">Total Runtime</div><div class="agg-metric-value">{runtime_min:.1f} min</div></div>'
                f'<div class="agg-metric-row"><div class="agg-metric-label">Avg Runtime</div><div class="agg-metric-value">{avg_runtime:.1f} min</div></div>'
                '</div>',
                unsafe_allow_html=True,
            )

            if mem_reuse:
                reuse_badges = " ".join(
                    f'<span style="display:inline-block;padding:3px 12px;background:var(--accent-soft);'
                    f'color:var(--accent);border-radius:14px;font-size:0.78rem;font-weight:600;margin:2px;">Q{q}</span>'
                    for q in mem_reuse
                )
                st.markdown(
                    f'<div class="card" style="padding:16px; margin-top:10px;">'
                    f'<div style="display:flex;align-items:center;gap:8px;font-size:0.85rem;font-weight:700;color:var(--text-primary);margin-bottom:8px;">🧠 Memory Reuse</div>'
                    f'<div style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:8px;line-height:1.4;">'
                    f'Reused in {len(mem_reuse)} question(s):</div>'
                    f'{reuse_badges}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="card" style="padding:16px; margin-top:10px;">'
                    '<div style="display:flex;align-items:center;gap:8px;font-size:0.85rem;font-weight:700;color:var(--text-primary);margin-bottom:8px;">🧠 Memory Reuse</div>'
                    '<div style="font-size:0.78rem;color:var(--text-muted);">No memory reuse detected.</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )


# ── Research page ────────────────────────────────────────────────────────────

def render_research_page():
    # Page header
    st.markdown(
        '<div class="page-header">'
        '<div class="page-header-icon">🔍</div>'
        '<div>'
        '<div class="page-header-title">Research Analyst & Auditor</div>'
        '<div class="page-header-subtitle">Get evidence-based answers with independent verification</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )

    # Feature cards row
    st.markdown(
        '<div class="feature-cards">'
        '<div class="feature-card">'
        '<div class="feature-card-icon fc-blue">🔍</div>'
        '<div><div class="feature-card-title">Multi-round Web Research</div>'
        '<div class="feature-card-desc">Search and fetch live information</div></div></div>'
        '<div class="feature-card">'
        '<div class="feature-card-icon fc-red">⚖️</div>'
        '<div><div class="feature-card-title">Evidence Evaluation</div>'
        '<div class="feature-card-desc">Analyze and verify claims</div></div></div>'
        '<div class="feature-card">'
        '<div class="feature-card-icon fc-purple">🛡️</div>'
        '<div><div class="feature-card-title">Claim Verification</div>'
        '<div class="feature-card-desc">Independent auditor checks</div></div></div>'
        '<div class="feature-card">'
        '<div class="feature-card-icon fc-pink">🧠</div>'
        '<div><div class="feature-card-title">Entity Memory</div>'
        '<div class="feature-card-desc">Builds knowledge over time</div></div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Input section — wrapped in a card
    with st.container():
        st.markdown('<div class="input-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="input-label">Ask a research question</div>',
            unsafe_allow_html=True,
        )
        question = st.text_area(
            "Ask a research question",
            placeholder="e.g. Who founded Infosys, and what is the company's current market capitalization?",
            height=100,
            max_chars=2000,
            label_visibility="collapsed",
        )
        char_count = len(question) if question else 0
        st.markdown(
            f'<div class="char-counter">{char_count}/2000</div>',
            unsafe_allow_html=True,
        )

        col_opt1, col_opt2, col_btn = st.columns([2, 2, 2])
        with col_opt1:
            fresh_memory = st.toggle("Fresh memory", value=False,
                                      help="Start with empty memory (default uses saved knowledge)")
        with col_opt2:
            show_process = st.toggle("Show research process", value=True,
                                      help="Display detailed research steps and sources")
        with col_btn:
            run_clicked = st.button("✦ Research", type="primary", use_container_width=True)
            st.markdown(
                '<div style="font-size:0.7rem; color:var(--text-muted); text-align:center; margin-top:-8px;">This may take a few minutes...</div>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    st.write("")

    if run_clicked and question.strip():
        with st.spinner("Researching..."):
            try:
                result = research(question.strip(), fresh_memory=fresh_memory)
            except Exception as e:
                st.error(f"Research failed: {type(e).__name__}: {str(e)[:200]}")
                st.caption("The backend encountered an error. Check your API keys and provider availability.")
                return

        answer_data = result.answer.model_dump()
        audit_data = result.audit.model_dump()
        trace = answer_data.get("tool_trace", [])

        # Results with 7 tabs matching reference
        tab_answer, tab_claims, tab_process, tab_sources, tab_memory, tab_metrics, tab_provider = st.tabs([
            "📝 Answer", "🛡️ Claim Verification", "≡ Research Process",
            "🔗 Evidence & Sources", "🧠 Memory", "📊 Metrics", "⚙️ Provider",
        ])

        with tab_answer:
            render_answer_panel(answer_data)

        with tab_claims:
            render_claims_panel(audit_data)

        with tab_process:
            if show_process:
                render_timeline_panel(trace)
            else:
                st.caption("Research process display is disabled. Enable it above.")

        with tab_sources:
            render_sources_panel(trace)

        with tab_memory:
            render_memory_panel(result.memory_before, result.memory_after, trace)

        with tab_metrics:
            render_metrics_panel(
                answer_data, audit_data,
                result.analyst_latency, result.auditor_latency,
                result.total_latency,
            )

        with tab_provider:
            render_provider_panel(trace)

    elif run_clicked:
        st.warning("Please enter a research question.")

    elif not run_clicked:
        # Empty state — single centered card matching reference
        st.markdown(
            '<div class="empty-state">'
            '<div class="empty-state-illustration">'
            '<svg viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">'
            '<rect x="25" y="15" width="60" height="75" rx="6" fill="#dbeafe" stroke="#93c5fd" stroke-width="1.5"/>'
            '<rect x="35" y="30" width="30" height="3" rx="1.5" fill="#93c5fd"/>'
            '<rect x="35" y="38" width="40" height="3" rx="1.5" fill="#bfdbfe"/>'
            '<rect x="35" y="46" width="25" height="3" rx="1.5" fill="#bfdbfe"/>'
            '<rect x="35" y="54" width="35" height="3" rx="1.5" fill="#bfdbfe"/>'
            '<rect x="35" y="62" width="20" height="3" rx="1.5" fill="#bfdbfe"/>'
            '<circle cx="80" cy="75" r="22" fill="#eff6ff" stroke="#60a5fa" stroke-width="2"/>'
            '<circle cx="80" cy="75" r="12" fill="#dbeafe" stroke="#60a5fa" stroke-width="1.5"/>'
            '<line x1="92" y1="87" x2="102" y2="97" stroke="#60a5fa" stroke-width="3" stroke-linecap="round"/>'
            '<circle cx="25" cy="20" r="6" fill="#e0e7ff" stroke="#a5b4fc" stroke-width="1"/>'
            '<circle cx="25" cy="20" r="2" fill="#818cf8"/>'
            '<circle cx="22" cy="20" r="1" fill="#818cf8" opacity="0.5"/>'
            '<circle cx="28" cy="20" r="1" fill="#818cf8" opacity="0.5"/>'
            '</svg>'
            '</div>'
            '<div class="empty-state-title">Ask a research question</div>'
            '<div class="empty-state-text">'
            'Enter a question above and click Research to start. The system will '
            'search the web, gather evidence, evaluate source quality, and have '
            'an independent auditor verify the claims.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )


# ── Navigation ───────────────────────────────────────────────────────────────

page = render_sidebar()

if page == "Research":
    render_research_page()
else:
    render_evaluation_page()
