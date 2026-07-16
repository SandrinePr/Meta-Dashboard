"""RRO / Red Rock theme CSS for the Streamlit dashboard."""

RRO_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap');

:root {
    --rro-bg: #101C2C;
    --rro-bg-deep: #0B1826;
    --rro-sidebar: #07111D;
    --rro-card: #141B27;
    --rro-card-light: #1A2434;
    --rro-cta: #B87844;
    --rro-cta-hover: #A45E2B;
    --rro-text: #E8E2D8;
    --rro-text-muted: rgba(232, 226, 216, 0.68);
    --rro-border: rgba(232, 226, 216, 0.14);
    --rro-border-accent: rgba(184, 120, 68, 0.45);
    --rro-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    --rro-highlight: #F7D154;
    --rro-cyan: #57C7E3;
    --rro-badge: #303945;
}

html, body, [class*="css"] {
    font-family: "Source Sans 3", sans-serif;
}

.stApp {
    background: linear-gradient(180deg, #101C2C 0%, #0F1B2A 45%, #101C2C 100%);
    color: var(--rro-text);
}

.block-container {
    padding-top: 2rem;
    max-width: 1200px;
}

h1 {
    font-size: 2.15rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--rro-text) !important;
}

h2, h3, h4, h5, h6, p, label, span, div {
    color: var(--rro-text);
}

.stCaption, small, .muted {
    color: var(--rro-text-muted) !important;
}

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0B1826 0%, #07111D 55%, #101C2C 100%) !important;
    border-right: 1px solid var(--rro-border-accent);
    box-shadow: 4px 0 24px rgba(0, 0, 0, 0.25);
}

/* Desktop (>800px): sidebar always open, no collapse UI */
@media (min-width: 801px) {
    [data-testid="stSidebarCollapseButton"],
    [data-testid="collapsedControl"],
    button[kind="header"] {
        display: none !important;
    }

    section[data-testid="stSidebar"] {
        min-width: 300px !important;
        width: 300px !important;
        transform: translateX(0) !important;
        visibility: visible !important;
    }
}

/* Mobile (≤800px): collapsible sidebar with close (X) control */
@media (max-width: 800px) {
    [data-testid="stSidebarCollapseButton"] {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        position: absolute !important;
        top: 10px !important;
        right: 10px !important;
        z-index: 1000002 !important;
        width: 40px !important;
        height: 40px !important;
        border-radius: 8px !important;
        border: 1px solid var(--rro-border-accent) !important;
        background: rgba(26, 36, 52, 0.95) !important;
        color: var(--rro-text) !important;
        cursor: pointer !important;
    }

    [data-testid="stSidebarCollapseButton"] svg {
        display: none !important;
    }

    [data-testid="stSidebarCollapseButton"]::after {
        content: "×";
        font-size: 1.7rem;
        line-height: 1;
        color: var(--rro-text);
        font-weight: 400;
    }

    [data-testid="collapsedControl"] {
        display: flex !important;
        z-index: 1000001 !important;
    }

    section[data-testid="stSidebar"] {
        min-width: min(300px, 88vw) !important;
        width: min(300px, 88vw) !important;
    }

    /* Space for the close button above sidebar content */
    section[data-testid="stSidebar"] > div:first-child {
        padding-top: 48px !important;
    }
}

section[data-testid="stSidebar"] > div {
    background: transparent !important;
}

section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span {
    color: var(--rro-text) !important;
}

section[data-testid="stSidebar"] .stButton > button {
    background: var(--rro-cta) !important;
    color: #FFFFFF !important;
    border: 1px solid var(--rro-cta) !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    box-shadow: 0 4px 14px rgba(184, 120, 68, 0.28);
}

section[data-testid="stSidebar"] .stButton > button:hover {
    background: var(--rro-cta-hover) !important;
    border-color: var(--rro-cta-hover) !important;
    color: #FFFFFF !important;
}

section[data-testid="stSidebar"] .stButton > button p {
    color: #FFFFFF !important;
}

.rro-sidebar-title {
    font-size: 1.1rem;
    font-weight: 700;
    color: var(--rro-text);
    margin-bottom: 0.75rem;
}

.rro-stat-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid var(--rro-border);
}

.rro-stat-icon {
    width: 28px;
    height: 28px;
    border-radius: 6px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--rro-cta);
    background: rgba(184, 120, 68, 0.14);
    border: 1px solid rgba(184, 120, 68, 0.35);
    flex-shrink: 0;
}

.rro-svg-icon {
    width: 14px;
    height: 14px;
    display: block;
}

.rro-svg-icon-sm {
    width: 12px;
    height: 12px;
}

.rro-stat-label {
    flex: 1;
    color: var(--rro-text-muted);
    font-size: 0.92rem;
}

.rro-stat-value {
    color: var(--rro-cta);
    font-weight: 700;
    font-size: 1rem;
}

/* Search panel card (wraps everything under the Zoeken header) */
[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(.rro-search-form-header) {
    background: linear-gradient(
        145deg,
        rgba(43, 29, 21, 0.92) 0%,
        rgba(26, 36, 52, 0.95) 35%,
        rgba(20, 27, 39, 1) 100%
    );
    border: 1px solid var(--rro-border-accent);
    border-radius: 18px;
    box-shadow:
        0 10px 30px rgba(0, 0, 0, 0.35),
        inset 0 1px 0 rgba(255, 255, 255, 0.03);
    padding: 28px 24px 20px;
    margin-bottom: 1.5rem;
}

.rro-search-form-header h3 {
    margin: 0 0 20px 0;
    font-size: 1.25rem;
    font-weight: 700;
    color: var(--rro-text);
}

.rro-filter-label {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--rro-text-muted);
    margin: 0.25rem 0 0.35rem 0;
}

div[data-testid="stForm"] {
    background: linear-gradient(
        145deg,
        rgba(43, 29, 21, 0.92) 0%,
        rgba(26, 36, 52, 0.95) 35%,
        rgba(20, 27, 39, 1) 100%
    );
    border: 1px solid var(--rro-border-accent);
    border-radius: 18px;
    box-shadow:
        0 10px 30px rgba(0, 0, 0, 0.35),
        inset 0 1px 0 rgba(255, 255, 255, 0.03);
    padding: 28px 24px 32px;
    margin-bottom: 1.5rem;
}

div[data-testid="stForm"] [data-testid="stFormSubmitHint"],
div[data-testid="stForm"] [data-testid="InputInstructions"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
}

/* Inputs */
.stTextInput input,
div[data-testid="stTextInput"] input,
div[data-testid="stForm"] input[type="text"],
div[data-testid="stDateInput"] input {
    background: var(--rro-card) !important;
    border: 1px solid var(--rro-border) !important;
    color: var(--rro-text) !important;
    border-radius: 8px !important;
}

.stTextInput input::placeholder,
div[data-testid="stDateInput"] input::placeholder {
    color: rgba(232, 226, 216, 0.45) !important;
}

.stTextInput input:focus,
div[data-testid="stTextInput"] input:focus,
div[data-testid="stDateInput"] input:focus {
    border-color: var(--rro-cta) !important;
    box-shadow: 0 0 0 1px rgba(184, 120, 68, 0.55) !important;
}

div[data-testid="stDateInput"] input,
div[data-testid="stDateInput"] button {
    pointer-events: auto !important;
    cursor: pointer !important;
}

div[data-testid="stDateInput"] svg {
    color: var(--rro-text-muted) !important;
    fill: var(--rro-text-muted) !important;
}

/* Primary buttons */
.stButton > button,
div[data-testid="stForm"] .stButton > button {
    background: var(--rro-cta) !important;
    color: #FFFFFF !important;
    border: 1px solid var(--rro-cta) !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    box-shadow: 0 4px 14px rgba(184, 120, 68, 0.22);
}

.stButton > button:hover,
div[data-testid="stForm"] .stButton > button:hover {
    background: var(--rro-cta-hover) !important;
    border-color: var(--rro-cta-hover) !important;
    color: #FFFFFF !important;
}

.stButton > button p,
div[data-testid="stForm"] .stButton > button p {
    color: #FFFFFF !important;
}

/* Multiselect filters */
[data-testid="stMultiSelect"] label,
[data-testid="stMultiSelect"] label p {
    color: var(--rro-text-muted) !important;
    font-size: 0.85rem !important;
    font-weight: 600 !important;
}

[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
    background-color: var(--rro-card) !important;
    color: var(--rro-text) !important;
    border: 1px solid var(--rro-border) !important;
}

[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
    background-color: var(--rro-cta) !important;
    color: #FFFFFF !important;
    border-radius: 999px !important;
}

[data-testid="stMultiSelect"] span[data-baseweb="tag"] span,
[data-testid="stMultiSelect"] span[data-baseweb="tag"] svg {
    color: #FFFFFF !important;
    fill: #FFFFFF !important;
}

div[data-baseweb="popover"] ul[role="listbox"],
div[data-baseweb="menu"] ul[role="listbox"] {
    background-color: var(--rro-card-light) !important;
    color: var(--rro-text) !important;
    border: 1px solid var(--rro-border-accent) !important;
    box-shadow: var(--rro-shadow);
}

div[data-baseweb="popover"] li[role="option"],
div[data-baseweb="menu"] li[role="option"] {
    background-color: var(--rro-card-light) !important;
    color: var(--rro-text) !important;
}

div[data-baseweb="popover"] li[role="option"]:hover,
div[data-baseweb="popover"] li[aria-selected="true"],
div[data-baseweb="menu"] li[role="option"]:hover,
div[data-baseweb="menu"] li[aria-selected="true"] {
    background-color: rgba(184, 120, 68, 0.18) !important;
    color: var(--rro-text) !important;
}

/* Datepicker popup */
[data-baseweb="calendar"],
[data-baseweb="datepicker"] {
    background-color: var(--rro-card-light) !important;
    color: var(--rro-text) !important;
    border: 1px solid var(--rro-border-accent) !important;
    box-shadow: var(--rro-shadow);
}

[data-baseweb="calendar"] [role="gridcell"],
[data-baseweb="calendar"] [role="columnheader"],
[data-baseweb="calendar"] button,
[data-baseweb="calendar"] div {
    color: var(--rro-text) !important;
}

[data-baseweb="calendar"] [aria-selected="true"] {
    background-color: var(--rro-cta) !important;
    color: #FFFFFF !important;
}

[data-baseweb="calendar"] [aria-selected="true"] div {
    color: #FFFFFF !important;
}

[data-baseweb="calendar"] [role="gridcell"][aria-label*="between"],
[data-baseweb="calendar"] [data-range="true"] {
    background-color: rgba(184, 120, 68, 0.22) !important;
    color: var(--rro-text) !important;
}

/* Alerts / info */
[data-testid="stAlert"],
[data-baseweb="notification"],
[data-testid="stNotification"] {
    background-color: var(--rro-card) !important;
    color: var(--rro-text) !important;
    border-left: 4px solid var(--rro-cta) !important;
    border-radius: 8px !important;
}

[data-testid="stAlert"] *,
[data-baseweb="notification"] * {
    color: var(--rro-text) !important;
}

/* Results section */
[data-testid="stVerticalBlockBorderWrapper"]:has(.rro-results-section) {
    background: linear-gradient(
        145deg,
        rgba(26, 36, 52, 0.98) 0%,
        rgba(20, 27, 39, 1) 55%,
        rgba(16, 28, 44, 1) 100%
    ) !important;
    border: 1px solid var(--rro-border-accent) !important;
    border-radius: 18px !important;
    box-shadow:
        0 10px 30px rgba(0, 0, 0, 0.35),
        inset 0 1px 0 rgba(255, 255, 255, 0.03) !important;
    padding: 8px 8px 12px !important;
    margin: 1.25rem 0 1.75rem 0 !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:has(.rro-results-section) > div {
    background: transparent !important;
}

.rro-results-section {
    padding: 12px 12px 4px;
}

.rro-results-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 0.35rem 0 0.65rem 0;
    padding-bottom: 0.65rem;
    border-bottom: 1px solid var(--rro-border);
}

.rro-results-header h2 {
    margin: 0;
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--rro-text);
}

.rro-results-subcount {
    color: var(--rro-text-muted);
    font-size: 0.88rem;
    margin: 0 0 0.55rem 0;
}

.rro-results-totals {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 14px;
    color: var(--rro-text-muted);
    font-size: 0.9rem;
    font-weight: 600;
    margin: 0 0 1rem 0;
    padding: 10px 12px;
    background: rgba(184, 120, 68, 0.08);
    border: 1px solid rgba(184, 120, 68, 0.28);
    border-radius: 10px;
}

.rro-results-totals .rro-stat {
    display: inline-flex;
    align-items: center;
    gap: 4px;
}

/* Comment groups */
.rro-comment-group {
    margin: -6px 0 20px 0;
    padding: 12px 16px 4px 16px;
    border-left: 2px solid var(--rro-cta);
    background: rgba(20, 27, 39, 0.72);
    border-radius: 0 10px 10px 0;
}

.rro-comment-group-title {
    color: var(--rro-text-muted);
    font-size: 0.82rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    margin-bottom: 10px;
}

.rro-comment-item {
    padding: 8px 0;
    border-top: 1px solid var(--rro-border);
}

.rro-comment-item:first-of-type {
    border-top: none;
}

.rro-comment-item-head {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-bottom: 4px;
}

.rro-comment-card {
    max-width: 640px;
    background: linear-gradient(145deg, #141B27, #1A2434);
    border: 1px solid var(--rro-border);
    border-left: 2px solid var(--rro-cta);
    border-radius: 10px;
    padding: 12px 16px;
    margin-bottom: 12px;
    box-shadow: var(--rro-shadow);
}

.rro-comment-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--rro-text-muted);
    font-size: 0.78rem;
    font-weight: 700;
    margin-bottom: 8px;
}

.rro-comment-label .rro-svg-icon-sm {
    width: 12px;
    height: 12px;
}

.rro-comment-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-bottom: 6px;
}

.rro-comment-date {
    color: var(--rro-text-muted);
    font-size: 0.78rem;
}

.rro-comment-text {
    color: var(--rro-text);
    font-size: 0.9rem;
    line-height: 1.45;
    word-break: break-word;
}

.rro-comment-parent {
    color: var(--rro-text-muted);
    font-size: 0.78rem;
    margin-top: 6px;
    font-style: italic;
}

.rro-comment-link {
    display: inline-block;
    margin-top: 8px;
    color: var(--rro-cta) !important;
    font-size: 0.82rem;
    text-decoration: none;
}

.rro-comment-link:hover {
    text-decoration: underline;
}

/* Result cards */
.rro-result-card {
    display: grid;
    grid-template-columns: 140px 1fr 180px;
    gap: 20px;
    align-items: center;
    background: linear-gradient(145deg, #141B27, #1A2434);
    border: 1px solid var(--rro-border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 16px;
    box-shadow: var(--rro-shadow);
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.rro-result-card:hover {
    border-color: var(--rro-border-accent);
    box-shadow:
        0 10px 28px rgba(0, 0, 0, 0.4),
        0 0 0 1px rgba(184, 120, 68, 0.12);
}

.rro-thumb {
    width: 128px;
    height: 128px;
    border-radius: 10px;
    object-fit: cover;
    border: 1px solid var(--rro-border);
    background: var(--rro-card);
}

.rro-thumb-placeholder {
    width: 128px;
    height: 128px;
    border-radius: 10px;
    border: 1px dashed var(--rro-border);
    background: rgba(20, 27, 39, 0.65);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--rro-text-muted);
    font-size: 0.75rem;
    text-align: center;
    padding: 8px;
}

.rro-card-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-bottom: 10px;
}

.badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.02em;
}

.badge-platform .rro-svg-icon {
    width: 13px;
    height: 13px;
}

.badge-type .rro-svg-icon-sm {
    width: 11px;
    height: 11px;
}

.badge-instagram,
.badge-facebook {
    background: var(--rro-badge);
    color: var(--rro-text);
    border: 1px solid var(--rro-border);
}

.badge-post,
.badge-content {
    background: rgba(184, 120, 68, 0.14);
    color: var(--rro-cta);
    border: 1px solid rgba(184, 120, 68, 0.45);
}

.badge-comment {
    background: var(--rro-badge);
    color: var(--rro-text);
    border: 1px solid var(--rro-border);
}

.badge-match {
    font-size: 0.7rem;
    padding: 3px 9px;
}

.badge-caption {
    background: rgba(232, 226, 216, 0.08);
    color: var(--rro-text);
    border: 1px solid rgba(232, 226, 216, 0.22);
}

.badge-hashtag {
    background: rgba(184, 120, 68, 0.14);
    color: var(--rro-cta);
    border: 1px solid rgba(184, 120, 68, 0.55);
}

.badge-tag {
    background: rgba(87, 199, 227, 0.14);
    color: var(--rro-cyan);
    border: 1px solid rgba(87, 199, 227, 0.55);
}

.rro-card-date {
    color: var(--rro-text-muted);
    font-size: 0.85rem;
    margin-bottom: 8px;
}

.rro-card-text {
    color: var(--rro-text);
    font-size: 0.95rem;
    line-height: 1.5;
    word-break: break-word;
}

.rro-card-stats {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    margin-top: 10px;
    color: var(--rro-text-muted);
    font-size: 0.9rem;
}

.rro-stat {
    display: inline-flex;
    align-items: center;
    gap: 5px;
}

.rro-stat-svg {
    width: 15px;
    height: 15px;
    flex-shrink: 0;
    display: block;
}

.rro-stat-value-inline {
    line-height: 1;
}

.rro-totals-sep {
    color: var(--rro-text-muted);
}

.rro-card-text .hashtag,
.hashtag {
    color: var(--rro-cta);
    font-weight: 700;
}

.rro-card-text .mention,
.mention {
    color: var(--rro-cyan);
    font-weight: 700;
}

.text-match {
    background: var(--rro-highlight);
    color: #101C2C;
    border-radius: 4px;
    padding: 0 4px;
    font-weight: 700;
}

.hashtag-match,
.mention-match {
    text-decoration: underline;
    text-decoration-color: var(--rro-highlight);
    text-decoration-thickness: 3px;
    text-underline-offset: 4px;
    background: transparent;
}

.rro-btn-link {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    border: 1px solid var(--rro-cta);
    color: var(--rro-cta) !important;
    background: transparent;
    border-radius: 8px;
    padding: 10px 16px;
    text-decoration: none !important;
    font-weight: 700;
    text-align: center;
    transition: background 0.2s ease;
}

.rro-btn-link:hover {
    background: rgba(184, 120, 68, 0.16);
    color: var(--rro-text) !important;
}

.rro-btn-disabled {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    border: 1px solid var(--rro-border);
    color: var(--rro-text-muted);
    background: transparent;
    border-radius: 8px;
    padding: 10px 16px;
    font-weight: 700;
    text-align: center;
}

@media (max-width: 900px) {
    .rro-result-card {
        grid-template-columns: 1fr;
    }
    .rro-thumb, .rro-thumb-placeholder {
        width: 100%;
        height: 180px;
    }
    h1 {
        font-size: 1.75rem;
    }
}
</style>
"""
