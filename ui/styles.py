"""RRO / Red Rock theme CSS for the Streamlit dashboard."""

RRO_CSS = """
<style>
/* rro-css-v5-totals-gap */
@import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap');

:root {
    --rro-bg: #101C2C;
    --rro-bg-deep: #0B1826;
    --rro-sidebar: #07111D;
    --rro-card: #151C2B;
    --rro-card-light: #1C2433;
    --rro-panel: #1A2230;
    --rro-input: #252E3D;
    --rro-cta: #B87844;
    --rro-cta-hover: #A45E2B;
    --rro-text: #F2F0EC;
    --rro-text-muted: rgba(242, 240, 236, 0.62);
    --rro-border: rgba(242, 240, 236, 0.12);
    --rro-border-accent: rgba(184, 120, 68, 0.45);
    /* Hover/focus: warme stroke, geen lichtblauw */
    --rro-stroke: var(--rro-cta);
    --rro-shadow: 0 4px 18px rgba(0, 0, 0, 0.28);
    --rro-highlight: #B87844;
    --rro-cyan: #57C7E3;
    --rro-badge: #303945;
    --rro-radius: 12px;
    --rro-radius-sm: 8px;
    --rro-control-h: 38px;
}

html, body, [class*="css"] {
    font-family: "Source Sans 3", sans-serif;
}

.stApp {
    background: linear-gradient(165deg, #0B1524 0%, #101C2C 42%, #0E1A2A 100%);
    color: var(--rro-text);
    overflow-x: hidden;
}

/* Streamlit topbar: transparant, geen toolbar — ruimte komt via padding hieronder */
[data-testid="stHeader"] {
    background: transparent !important;
}

[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"] {
    display: none !important;
}

/* 5rem ruimte boven de hoofdkaart (padding, niet margin — margin wordt door Streamlit weggedrukt) */
section.main[data-testid="stMain"],
[data-testid="stMain"] {
    padding-top: 5rem !important;
}

div.stMainBlockContainer.block-container,
[data-testid="stMain"] div.block-container,
[data-testid="stMain"] .stMainBlockContainer.block-container,
section.main .block-container {
    max-width: 800px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    padding: 0.65rem 1.1rem 1rem !important;
    background: var(--rro-panel) !important;
    border: 1px solid var(--rro-border) !important;
    border-radius: 16px !important;
    box-shadow: var(--rro-shadow) !important;
}

/* Compacte verticale ritme in de hoofdkaart */
[data-testid="stMain"] .block-container > div {
    gap: 0.35rem !important;
}

[data-testid="stMain"] [data-testid="stVerticalBlock"] {
    gap: 0.35rem !important;
}

[data-testid="stMain"] [data-testid="stElementContainer"] {
    margin-bottom: 0 !important;
}

h1,
.rro-page-title {
    font-size: 34px !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em;
    line-height: 1.15 !important;
    color: var(--rro-text) !important;
    margin: 0 0 8px 0 !important;
    padding: 0 !important;
    background: transparent !important;
}

/* Title and caption are separate Streamlit widgets — space between their containers */
[data-testid="stMain"] [data-testid="stElementContainer"]:has(.rro-page-title) {
    margin-bottom: 8px !important;
    padding-bottom: 0 !important;
}

.rro-page-title:hover,
h1:hover {
    background: transparent !important;
    color: var(--rro-text) !important;
}

[data-testid="stMain"] .stCaption {
    margin: 0 0 0.35rem 0 !important;
    padding: 0 !important;
}

/* Geen Streamlit/Baseweb lichtblauwe focus/hover glows */
.stApp *:focus,
.stApp *:focus-visible,
.stApp input:focus,
.stApp button:focus,
.stApp [data-baseweb="input"]:focus-within,
.stApp [data-baseweb="select"]:focus-within {
    outline: none !important;
    box-shadow: none !important;
}

.stApp a:hover,
.stApp a:focus {
    background: transparent !important;
}

h2, h3, h4, h5, h6, p, label, span, div {
    color: var(--rro-text);
}

.stCaption, small, .muted {
    color: var(--rro-text-muted) !important;
}

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0B1826 0%, #07111D 55%, #0C1724 100%) !important;
    border-right: 1px solid var(--rro-border);
    box-shadow: none;
}

/* Desktop: compacte sidebar ~230px */
@media (min-width: 801px) {
    [data-testid="stSidebarCollapseButton"],
    [data-testid="collapsedControl"],
    button[kind="header"] {
        display: none !important;
    }

    section[data-testid="stSidebar"] {
        min-width: 230px !important;
        width: 230px !important;
        transform: translateX(0) !important;
        visibility: visible !important;
    }

    section[data-testid="stSidebar"] > div {
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
    }
}

@media (max-width: 800px) {
    [data-testid="stSidebarCollapseButton"] {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        position: absolute !important;
        top: 10px !important;
        right: 10px !important;
        z-index: 1000002 !important;
        width: 36px !important;
        height: 36px !important;
        border-radius: 8px !important;
        border: 1px solid var(--rro-border) !important;
        background: rgba(26, 36, 52, 0.95) !important;
        color: var(--rro-text) !important;
        cursor: pointer !important;
    }

    [data-testid="stSidebarCollapseButton"] svg {
        display: none !important;
    }

    [data-testid="stSidebarCollapseButton"]::after {
        content: "×";
        font-size: 1.5rem;
        line-height: 1;
        color: var(--rro-text);
        font-weight: 400;
    }

    [data-testid="collapsedControl"] {
        display: flex !important;
        z-index: 1000001 !important;
    }

    section[data-testid="stSidebar"] {
        min-width: min(230px, 88vw) !important;
        width: min(230px, 88vw) !important;
    }

    section[data-testid="stSidebar"] > div:first-child {
        padding-top: 48px !important;
    }

    [data-testid="stMain"] .block-container,
    [data-testid="stMain"] .stMainBlockContainer.block-container,
    section.main .block-container {
        max-width: calc(100% - 1rem) !important;
        margin-top: 0 !important;
        margin-left: auto !important;
        margin-right: auto !important;
        margin-bottom: 0.25rem !important;
        padding: 0.55rem 0.85rem 0.85rem !important;
    }

    section.main[data-testid="stMain"],
    [data-testid="stMain"] {
        padding-top: 5rem !important;
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
    border-radius: var(--rro-radius-sm) !important;
    font-weight: 700 !important;
    box-shadow: none;
    min-height: 38px !important;
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
    font-size: 1rem;
    font-weight: 700;
    color: var(--rro-text);
    margin-bottom: 0.5rem;
}

.rro-stat-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid var(--rro-border);
}

.rro-stat-icon {
    width: 24px;
    height: 24px;
    border-radius: 6px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--rro-cta);
    background: rgba(184, 120, 68, 0.12);
    border: 1px solid rgba(184, 120, 68, 0.3);
    flex-shrink: 0;
}

.rro-svg-icon {
    width: 13px;
    height: 13px;
    display: block;
}

.rro-svg-icon-sm {
    width: 11px;
    height: 11px;
}

.rro-stat-label {
    flex: 1;
    color: var(--rro-text-muted);
    font-size: 0.82rem;
}

.rro-stat-value {
    color: var(--rro-cta);
    font-weight: 700;
    font-size: 0.9rem;
}

.rro-sync-metrics-note {
    margin: 0.65rem 0 0.85rem 0;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--rro-border) !important;
    /* Zelfde bg als de Social Media Search Dashboard-hoofdkaart */
    background: #1A2230 !important;
    background-color: #1A2230 !important;
    color: var(--rro-text-muted) !important;
    font-size: 0.74rem;
    line-height: 1.4;
}

.rro-sync-metrics-note strong {
    color: var(--rro-text) !important;
    font-weight: 700;
}

.rro-sync-metrics-note code {
    color: var(--rro-text) !important;
    font-size: 0.72rem;
    word-break: break-all;
}

/* Zoeken-header (geen aparte nested card; zit in hoofdkaart) */
[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(.rro-search-form-header) {
    background: transparent;
    border: none;
    border-radius: 0;
    box-shadow: none;
    padding: 0;
    margin-bottom: 0.35rem;
}

.rro-search-form-header h3 {
    margin: 0 0 0.3rem 0;
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--rro-text);
}

.rro-filter-label {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--rro-text);
    margin: 0.2rem 0 0.25rem 0;
}

div[data-testid="stForm"] {
    background: transparent;
    border: none;
    box-shadow: none;
    padding: 0;
    margin: 0;
}

div[data-testid="stForm"] [data-testid="stFormSubmitHint"] {
    display: none !important;
}

/* Zoekrij: input + knop uitlijnen */
[data-testid="stHorizontalBlock"]:has(.rro-search-submit) {
    align-items: flex-end !important;
    gap: 0.55rem !important;
    margin-bottom: 0.55rem !important;
}

[data-testid="stHorizontalBlock"]:has(.rro-search-submit) > div {
    overflow: visible !important;
}

.rro-search-submit {
    display: none !important;
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
}

[data-testid="stHorizontalBlock"]:has(.rro-search-submit) [data-testid="stButton"] {
    margin-top: 0 !important;
}

[data-testid="stHorizontalBlock"]:has(.rro-search-submit) [data-testid="column"] {
    display: flex !important;
    flex-direction: column !important;
    justify-content: flex-end !important;
}

/* Inputs — verticaal gecentreerde tekst */
.stTextInput input,
div[data-testid="stTextInput"] input,
div[data-testid="stForm"] input[type="text"] {
    background: var(--rro-input) !important;
    border: 1px solid var(--rro-border) !important;
    color: var(--rro-text) !important;
    border-radius: var(--rro-radius-sm) !important;
    height: var(--rro-control-h) !important;
    min-height: var(--rro-control-h) !important;
    max-height: var(--rro-control-h) !important;
    line-height: var(--rro-control-h) !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    padding-left: 0.85rem !important;
    padding-right: 6.5rem !important;
    box-sizing: border-box !important;
    transition: border-color 0.15s ease;
}

.stTextInput input::placeholder {
    color: rgba(242, 240, 236, 0.42) !important;
    line-height: var(--rro-control-h) !important;
}

/* Celine: geen extra balk — alleen warme stroke als hover/focus */
.stTextInput input:hover,
div[data-testid="stTextInput"] input:hover,
.stTextInput input:focus,
div[data-testid="stTextInput"] input:focus {
    border-color: var(--rro-cta) !important;
    box-shadow: none !important;
    outline: none !important;
    background: var(--rro-input) !important;
}

div[data-testid="stTextInput"] [data-testid="stTextInputRootElement"],
div[data-testid="stTextInput"] > div,
div[data-testid="stTextInput"] [data-baseweb="input"],
div[data-testid="stTextInput"] [data-baseweb="base-input"] {
    position: relative !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    min-height: var(--rro-control-h) !important;
    height: var(--rro-control-h) !important;
}

/* Celine: Press enter rechts in balk, verticaal gecentreerd */
div[data-testid="stTextInput"] [data-testid="InputInstructions"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    position: absolute !important;
    right: 12px !important;
    top: 50% !important;
    bottom: auto !important;
    left: auto !important;
    transform: translateY(-50%) !important;
    align-items: center !important;
    justify-content: flex-end !important;
    height: auto !important;
    width: auto !important;
    max-width: 6.25rem !important;
    margin: 0 !important;
    padding: 0 !important;
    z-index: 3;
    pointer-events: none !important;
    color: var(--rro-text-muted) !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    line-height: 1 !important;
    white-space: nowrap !important;
}

div[data-testid="stTextInput"] [data-testid="InputInstructions"] *,
div[data-testid="stTextInput"] [data-testid="InputInstructions"] kbd,
div[data-testid="stTextInput"] [data-testid="InputInstructions"] span {
    display: inline !important;
    visibility: visible !important;
    opacity: 1 !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: var(--rro-text-muted) !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    line-height: 1 !important;
    padding: 0 !important;
    margin: 0 !important;
}

/* Datums: geen donkere schaduwranden; gelijk aan andere filters */
div[data-testid="stDateInput"] input,
div[data-testid="stDateInput"] button {
    pointer-events: auto !important;
    cursor: pointer !important;
}

div[data-testid="stDateInput"],
div[data-testid="stDateInput"] > div,
div[data-testid="stDateInput"] [data-baseweb="input"],
div[data-testid="stDateInput"] [data-baseweb="base-input"],
div[data-testid="stDateInput"] [data-baseweb="popover"] > div,
div[data-testid="stDateInput"] [data-baseweb="popover"] > div > div {
    background: transparent !important;
    box-shadow: none !important;
    filter: none !important;
    outline: none !important;
}

div[data-testid="stDateInput"] [data-baseweb="input"] {
    background: var(--rro-input) !important;
    border: 1px solid var(--rro-border) !important;
    border-radius: var(--rro-radius-sm) !important;
    min-height: var(--rro-control-h) !important;
    height: var(--rro-control-h) !important;
    transition: border-color 0.15s ease !important;
    overflow: hidden !important;
}

div[data-testid="stDateInput"] [data-baseweb="input"]:hover,
div[data-testid="stDateInput"] [data-baseweb="input"]:focus-within,
div[data-testid="stDateInput"]:hover [data-baseweb="input"] {
    border-color: var(--rro-cta) !important;
    box-shadow: none !important;
    background: var(--rro-input) !important;
}

div[data-testid="stDateInput"] input {
    height: var(--rro-control-h) !important;
    min-height: var(--rro-control-h) !important;
    line-height: var(--rro-control-h) !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    padding-right: 2.25rem !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
    color: var(--rro-text) !important;
}

div[data-testid="stDateInput"] button,
div[data-testid="stDateInput"] [role="button"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

div[data-testid="stDateInput"] svg {
    color: var(--rro-text-muted) !important;
    fill: var(--rro-text-muted) !important;
}

/* Zoeken-knop naast zoekveld: oranje, hover = iets donkerder oranje */
.rro-search-submit + div .stButton > button,
[data-testid="stHorizontalBlock"]:has(.rro-search-submit) .stButton > button,
.stButton > button {
    background: var(--rro-cta) !important;
    color: #FFFFFF !important;
    border: 1px solid var(--rro-cta) !important;
    border-radius: var(--rro-radius-sm) !important;
    font-weight: 700 !important;
    height: var(--rro-control-h) !important;
    min-height: var(--rro-control-h) !important;
    padding: 0 0.9rem !important;
    box-shadow: none !important;
    transition: border-color 0.15s ease, background 0.15s ease;
}

.stButton > button:hover,
[data-testid="stHorizontalBlock"]:has(.rro-search-submit) .stButton > button:hover,
.stButton > button:focus,
.stButton > button:focus-visible {
    background: var(--rro-cta-hover) !important;
    border-color: var(--rro-cta-hover) !important;
    color: #FFFFFF !important;
    box-shadow: none !important;
    outline: none !important;
}

.stButton > button p {
    color: #FFFFFF !important;
}

/* Filters: lager, compact, één rij */
[data-testid="stMultiSelect"] label,
[data-testid="stMultiSelect"] label p,
[data-testid="stDateInput"] label,
[data-testid="stDateInput"] label p {
    color: var(--rro-text) !important;
    font-size: 0.82rem !important;
    font-weight: 700 !important;
    margin-bottom: 0.2rem !important;
}

[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
    background-color: var(--rro-input) !important;
    color: var(--rro-text) !important;
    border: 1px solid var(--rro-border) !important;
    border-radius: var(--rro-radius-sm) !important;
    min-height: var(--rro-control-h) !important;
    height: auto !important;
    max-height: none !important;
    box-shadow: none !important;
    transition: border-color 0.15s ease;
    padding-top: 2px !important;
    padding-bottom: 2px !important;
}

[data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover,
[data-testid="stMultiSelect"] div[data-baseweb="select"]:hover > div,
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div[aria-expanded="true"] {
    border-color: var(--rro-cta) !important;
    box-shadow: none !important;
    background-color: var(--rro-input) !important;
}

[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
    background-color: var(--rro-cta) !important;
    color: #FFFFFF !important;
    border-radius: 999px !important;
    font-size: 0.72rem !important;
    min-height: 22px !important;
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
    border: 1px solid var(--rro-border) !important;
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

[data-baseweb="calendar"],
[data-baseweb="datepicker"] {
    background-color: var(--rro-card-light) !important;
    color: var(--rro-text) !important;
    border: 1px solid var(--rro-border) !important;
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

[data-testid="stAlert"],
[data-baseweb="notification"],
[data-testid="stNotification"] {
    background-color: var(--rro-card) !important;
    color: var(--rro-text) !important;
    border-left: 3px solid var(--rro-cta) !important;
    border-radius: 8px !important;
}

[data-testid="stAlert"] *,
[data-baseweb="notification"] * {
    color: var(--rro-text) !important;
}

/* Resultaten in dezelfde hoofdkaart (geen aparte bordered card) */
[data-testid="stVerticalBlockBorderWrapper"]:has(.rro-results-section) {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
    margin: 0.35rem 0 0 0 !important;
}

.rro-results-section {
    padding: 0.25rem 0 0;
    border-top: 1px solid var(--rro-border);
    margin-top: 0.4rem;
}

.rro-results-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 0.35rem 0 0.35rem 0;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid var(--rro-border);
}

.rro-results-header h2 {
    margin: 0;
    font-size: 1.2rem;
    font-weight: 700;
    color: var(--rro-text);
}

div.rro-results-totals,
.rro-results-totals {
    display: block !important;
    color: var(--rro-text-muted) !important;
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    line-height: 1.45 !important;
    margin: 0 !important;
    padding: 0 !important;
    background: transparent !important;
    background-color: transparent !important;
    border: 0 !important;
    border-width: 0 !important;
    border-style: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
}

.rro-results-totals .rro-stat {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-right: 4px;
}

/* Totals sit inside a Streamlit element container; margin there creates real
   space before the next sibling (first result card). */
[data-testid="stMain"] [data-testid="stElementContainer"]:has(.rro-results-totals) {
    margin-bottom: 20px !important;
}

.rro-comment-group {
    margin: -4px 0 14px 0;
    padding: 10px 12px 2px 12px;
    border-left: 2px solid var(--rro-cta);
    background: rgba(20, 27, 39, 0.55);
    border-radius: 0 8px 8px 0;
}

.rro-comment-group-title {
    color: var(--rro-text-muted);
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    margin-bottom: 8px;
}

.rro-comment-item {
    padding: 6px 0;
    border-top: 1px solid var(--rro-border);
}

.rro-comment-item:first-of-type {
    border-top: none;
}

.rro-comment-item-head {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    margin-bottom: 3px;
}

.rro-comment-card {
    max-width: 100%;
    background: var(--rro-card);
    border: 1px solid var(--rro-border);
    border-left: 2px solid var(--rro-cta);
    border-radius: var(--rro-radius-sm);
    padding: 10px 12px 14px;
    margin-bottom: 10px;
    box-shadow: none;
    transition: border-color 0.15s ease;
    overflow: visible;
}

.rro-comment-card:hover {
    border-color: var(--rro-cta);
}

.rro-comment-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--rro-text-muted);
    font-size: 0.74rem;
    font-weight: 700;
    margin-bottom: 6px;
}

.rro-comment-label .rro-svg-icon-sm {
    width: 11px;
    height: 11px;
}

.rro-comment-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    margin-bottom: 4px;
}

.rro-comment-date {
    color: var(--rro-text-muted);
    font-size: 0.74rem;
}

.rro-comment-text {
    color: var(--rro-text);
    font-size: 0.86rem;
    line-height: 1.4;
    word-break: break-word;
}

.rro-comment-parent {
    color: var(--rro-text-muted);
    font-size: 0.74rem;
    margin-top: 5px;
    font-style: italic;
}

.rro-comment-link {
    display: inline-block;
    margin-top: 6px;
    color: var(--rro-cta) !important;
    font-size: 0.78rem;
    text-decoration: none;
}

.rro-comment-link:hover {
    text-decoration: underline;
}

/* Resultaatkaarten: style ONLY the card's own BorderWrapper.
   NEVER use :has(.rro-result-card-marker) on stVerticalBlock — after search the
   main column also :has markers and then inherits card padding + height:auto,
   which expands leftover component iframes above the title (~159px each). */
.rro-result-card-marker {
    display: none !important;
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:has(> div .rro-result-card-marker),
[data-testid="stVerticalBlockBorderWrapper"]:has(.rro-result-card-marker) {
    background: var(--rro-card) !important;
    border: 1px solid var(--rro-border) !important;
    border-radius: var(--rro-radius) !important;
    padding: 12px 12px 18px !important;
    margin: 0 0 12px 0 !important;
    overflow: visible !important;
    box-shadow: none !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:has(.rro-result-card-marker):hover {
    border-color: var(--rro-border) !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:has(.rro-result-card-marker)
    [data-testid="stHorizontalBlock"] {
    align-items: flex-start !important;
    gap: 0.75rem !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:has(.rro-result-card-marker)
    [data-testid="stImage"] {
    margin: 0 !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:has(.rro-result-card-marker)
    [data-testid="stImage"] img {
    width: 88px !important;
    height: 88px !important;
    max-width: 88px !important;
    object-fit: cover !important;
    border-radius: var(--rro-radius-sm) !important;
    border: 1px solid var(--rro-border) !important;
    background: var(--rro-input);
}

.rro-result-card {
    display: grid;
    grid-template-columns: 88px 1fr 120px;
    gap: 12px;
    align-items: start;
    background: var(--rro-card);
    border: 1px solid var(--rro-border);
    border-radius: var(--rro-radius);
    padding: 12px 12px 18px;
    margin-bottom: 12px;
    box-shadow: none;
    transition: border-color 0.15s ease;
    overflow: visible;
}

.rro-result-card:hover {
    border-color: var(--rro-border);
    box-shadow: none;
}

.rro-thumb {
    width: 88px;
    height: 88px;
    border-radius: var(--rro-radius-sm);
    object-fit: cover;
    border: 1px solid var(--rro-border);
    background: var(--rro-input);
    transition: border-color 0.15s ease;
}

.rro-result-card:hover .rro-thumb,
.rro-result-card:hover .rro-thumb-placeholder {
    border-color: var(--rro-border);
}

.rro-thumb-placeholder {
    width: 88px;
    height: 88px;
    border-radius: var(--rro-radius-sm);
    border: 1px dashed var(--rro-border);
    background: var(--rro-input);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--rro-text-muted);
    font-size: 0.68rem;
    text-align: center;
    padding: 6px;
    transition: border-color 0.15s ease;
}

.rro-card-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    margin-bottom: 6px;
}

.badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.02em;
}

.badge-platform .rro-svg-icon {
    width: 12px;
    height: 12px;
}

.badge-type .rro-svg-icon-sm {
    width: 10px;
    height: 10px;
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
    font-size: 0.66rem;
    padding: 2px 7px;
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
    font-size: 0.76rem;
    margin-bottom: 4px;
}

.rro-card-text {
    color: var(--rro-text);
    font-size: 0.88rem;
    line-height: 1.4;
    word-break: break-word;
}

.rro-card-stats-wrap {
    display: block !important;
    width: 100% !important;
    padding: 0 0 2px 0 !important;
    margin: 0 !important;
    overflow: visible !important;
}

.rro-card-stats {
    display: flex !important;
    flex-wrap: wrap !important;
    align-items: center !important;
    gap: 8px 14px !important;
    margin: 10px 0 0 0 !important;
    padding: 10px 12px 12px !important;
    color: var(--rro-text-muted) !important;
    font-size: 0.78rem !important;
    line-height: 1.45 !important;
    background: rgba(255, 255, 255, 0.03) !important;
    border: 1px solid var(--rro-border) !important;
    border-radius: var(--rro-radius-sm) !important;
    overflow: visible !important;
    min-height: auto !important;
    height: auto !important;
    max-height: none !important;
    box-sizing: border-box !important;
}

.rro-card-stats-spacer {
    display: block !important;
    height: 10px !important;
    width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    flex-shrink: 0 !important;
}

.rro-stat {
    display: inline-flex !important;
    align-items: center !important;
    gap: 4px !important;
    white-space: nowrap !important;
    line-height: 1.35 !important;
}

.rro-stat-svg {
    width: 13px;
    height: 13px;
    flex-shrink: 0;
    display: block;
    color: var(--rro-cta);
}

.rro-stat-label-inline {
    color: var(--rro-text-muted);
    font-weight: 500;
    line-height: 1;
}

.rro-stat-value-inline {
    color: var(--rro-text);
    font-weight: 700;
    line-height: 1;
}

.rro-stat-missing {
    color: var(--rro-text-muted) !important;
    font-weight: 500 !important;
    font-style: italic;
    font-size: 0.82em;
}

.rro-results-views-note {
    margin: -0.2rem 0 0.75rem 0;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--rro-border);
    background: var(--rro-input);
    color: var(--rro-text-muted);
    font-size: 0.8rem;
    line-height: 1.4;
}

.rro-results-views-note code {
    color: var(--rro-text);
    font-size: 0.78rem;
}

.rro-totals-sep {
    color: var(--rro-text-muted);
}

.rro-results-totals .rro-stat-label-inline {
    font-weight: 600;
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
    color: #FFFFFF;
    border-radius: 4px;
    padding: 0 4px;
    font-weight: 700;
}

.hashtag-match,
.mention-match {
    text-decoration: underline;
    text-decoration-color: var(--rro-highlight);
    text-decoration-thickness: 2px;
    text-underline-offset: 3px;
    background: transparent;
}

.rro-btn-link {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    border: 1px solid rgba(242, 240, 236, 0.45);
    color: var(--rro-text) !important;
    background: transparent;
    border-radius: var(--rro-radius-sm);
    padding: 8px 10px;
    text-decoration: none !important;
    font-weight: 700;
    font-size: 0.82rem;
    text-align: center;
    transition: border-color 0.15s ease;
}

.rro-btn-link:hover {
    background: transparent;
    border-color: var(--rro-cta);
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
    border-radius: var(--rro-radius-sm);
    padding: 8px 10px;
    font-weight: 700;
    font-size: 0.82rem;
    text-align: center;
}

@media (max-width: 700px) {
    .rro-result-card {
        grid-template-columns: 72px 1fr;
    }
    .rro-result-card > div:last-child {
        grid-column: 1 / -1;
    }
    .rro-thumb, .rro-thumb-placeholder {
        width: 72px;
        height: 72px;
    }
    .rro-page-title,
    h1 {
        font-size: 26px !important;
    }
}
</style>
"""
