"""Inspect live computed styles for totals + title spacing."""
from playwright.sync_api import sync_playwright
import json

JS = r"""
() => {
  const totals = document.querySelector('.rro-results-totals');
  const title = document.querySelector('.rro-page-title');
  const caption = document.querySelector('[data-testid="stCaptionContainer"], .stCaption, [data-testid="stCaption"]');
  const styles = [...document.querySelectorAll('style')].map((s, i) => {
    const t = s.textContent || '';
    return {
      i,
      id: s.id,
      hasOldTotals: /rro-results-totals[\s\S]{0,120}padding:\s*8px/.test(t)
        || /rro-results-totals[\s\S]{0,120}background:\s*rgba\(255/.test(t),
      hasNewTotals: /rro-results-totals[\s\S]{0,220}background:\s*transparent\s*!important/.test(t),
      hasTitle8: /rro-page-title[\s\S]{0,200}margin:\s*0\s+0\s+8px/.test(t),
      len: t.length,
    };
  });

  const box = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
      className: (el.className || '').toString().slice(0, 120),
      bg: cs.backgroundColor,
      borderTop: cs.borderTop,
      borderRadius: cs.borderRadius,
      padding: cs.padding,
      margin: cs.margin,
      top: Math.round(r.top),
      height: Math.round(r.height),
    };
  };

  const matching = [];
  if (totals) {
    for (const sheet of document.styleSheets) {
      let rules;
      try { rules = [...sheet.cssRules]; } catch { continue; }
      for (const rule of rules) {
        if (!rule.selectorText || !rule.selectorText.includes('rro-results-totals')) continue;
        matching.push({
          selector: rule.selectorText.slice(0, 160),
          bg: rule.style.background || rule.style.backgroundColor,
          padding: rule.style.padding,
          border: rule.style.border,
          borderRadius: rule.style.borderRadius,
          cssText: (rule.cssText || '').slice(0, 280),
        });
      }
    }
  }

  let titleCaptionGap = null;
  if (title) {
    const titleBox = title.getBoundingClientRect();
    // next visible text sibling container
    const capEl = document.querySelector('[data-testid="stCaption"]')
      || [...document.querySelectorAll('[data-testid="stElementContainer"]')]
        .find(el => (el.innerText || '').includes('Zoek lokaal'));
    if (capEl) {
      titleCaptionGap = Math.round(capEl.getBoundingClientRect().top - titleBox.bottom);
    }
  }

  return {
    totalsExists: !!totals,
    totals: box(totals),
    title: box(title),
    titleCaptionGap,
    styleTags: styles.filter(s => s.hasOldTotals || s.hasNewTotals || s.hasTitle8 || s.len > 5000),
    matchingTotalsRules: matching,
  };
}
"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto("http://localhost:8501", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)
    page.locator('[data-testid="stTextInput"] input').first.fill("borek")
    page.get_by_role("button", name="Zoeken").first.click()
    page.wait_for_timeout(3500)
    data = page.evaluate(JS)
    browser.close()

print(json.dumps(data, indent=2))
