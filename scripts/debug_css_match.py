"""Confirm which CSS rules style the main vertical block after search."""
from playwright.sync_api import sync_playwright

PROBE = r"""
() => {
  const vbs = [...document.querySelectorAll('[data-testid="stMain"] [data-testid="stVerticalBlock"]')];
  const main = vbs[0];
  if (!main) return {error: "no main vb"};

  const sheets = [];
  for (const sheet of [...document.styleSheets]) {
    let rules;
    try { rules = [...sheet.cssRules]; } catch (e) { continue; }
    for (const rule of rules) {
      if (!rule.selectorText || !rule.style) continue;
      const sel = rule.selectorText;
      if (!sel.includes("stVerticalBlock") && !sel.includes("rro-result-card")) continue;
      let matches = false;
      try { matches = main.matches(sel); } catch (e) { continue; }
      if (!matches) continue;
      sheets.push({
        selector: sel.slice(0, 300),
        padding: rule.style.padding || rule.style.paddingTop,
        margin: rule.style.margin || rule.style.marginBottom,
        background: rule.style.background || rule.style.backgroundColor,
      });
    }
  }

  const iframes = [...document.querySelectorAll('[data-testid="stMain"] iframe')].map((f) => {
    const r = f.getBoundingClientRect();
    const parent = f.closest('[data-testid="stElementContainer"]');
    const pr = parent ? parent.getBoundingClientRect() : null;
    return {
      iframeH: Math.round(r.height),
      parentH: pr ? Math.round(pr.height) : null,
      src: (f.getAttribute("src") || "").slice(0, 80),
      title: f.getAttribute("title"),
      outerHTML: f.outerHTML.slice(0, 200),
    };
  });

  // Where do the iframes come from in the React tree? previous/next sibling text
  const iframeParents = [...document.querySelectorAll('[data-testid="stMain"] [data-testid="stElementContainer"]:has(iframe)')].map((el, i) => {
    const prev = el.previousElementSibling;
    const next = el.nextElementSibling;
    return {
      i,
      h: Math.round(el.getBoundingClientRect().height),
      prevText: prev ? (prev.innerText || "").replace(/\s+/g," ").slice(0,40) : null,
      nextText: next ? (next.innerText || "").replace(/\s+/g," ").slice(0,40) : null,
      prevHasStyle: !!(prev && prev.querySelector("style")),
      html: el.innerHTML.slice(0, 250),
    };
  });

  return {
    mainMatches: sheets,
    mainClass: main.className,
    iframes,
    iframeParents,
    leafSelectorWorks: main.matches(
      'div[data-testid="stVerticalBlock"]:has(.rro-result-card-marker):not(:has([data-testid="stVerticalBlock"] .rro-result-card-marker))'
    ),
    oldSelectorWorks: main.matches(
      'div[data-testid="stVerticalBlock"]:has(> div .rro-result-card-marker)'
    ),
    hasMarkerOnly: main.matches(
      'div[data-testid="stVerticalBlock"]:has(.rro-result-card-marker)'
    ),
  };
}
"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto("http://localhost:8501", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)
    inp = page.locator('[data-testid="stTextInput"] input').first
    inp.fill("borek")
    page.get_by_role("button", name="Zoeken").first.click()
    page.wait_for_timeout(4000)
    data = page.evaluate(PROBE)
    browser.close()

import json
print(json.dumps(data, indent=2))
