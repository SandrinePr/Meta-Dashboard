from playwright.sync_api import sync_playwright
import json

JS = """
() => {
  const totals = document.querySelector('.rro-results-totals');
  const totalsEC = totals && totals.closest('[data-testid="stElementContainer"]');
  const firstMarker = document.querySelector('.rro-result-card-marker');
  const firstCardEC = firstMarker && firstMarker.closest('[data-testid="stElementContainer"]');
  // walk up to find a bordered/card-looking parent
  let wrap = firstMarker;
  const ancestors = [];
  for (let i = 0; i < 8 && wrap; i++) {
    ancestors.push({
      testid: wrap.getAttribute && wrap.getAttribute('data-testid'),
      className: (wrap.className || '').toString().slice(0, 80),
      tag: wrap.tagName,
    });
    wrap = wrap.parentElement;
  }
  const tec = totalsEC ? totalsEC.getBoundingClientRect() : null;
  // first element container that contains a marker (card root in streamlit)
  const cardRoots = [...document.querySelectorAll('[data-testid="stElementContainer"]')]
    .filter(el => el.querySelector('.rro-result-card-marker'));
  // Actually marker is inside nested containers - find outermost vertical block per card
  const cardVBs = [...document.querySelectorAll('[data-testid="stVerticalBlock"]')]
    .filter(el => {
      const markers = el.querySelectorAll('.rro-result-card-marker');
      if (!markers.length) return false;
      // leaf-ish: does not contain another VB that also has marker? 
      return true;
    });

  let gap = null;
  if (tec && cardRoots[0]) {
    // find the stElementContainer that is a direct child of main VB and contains first marker
    const mainVB = document.querySelector('[data-testid="stMain"] [data-testid="stVerticalBlock"]');
    const topLevel = mainVB ? [...mainVB.children] : [];
    const totalsTop = topLevel.find(el => el.querySelector && el.querySelector('.rro-results-totals'));
    const firstCardTop = topLevel.find(el => el.querySelector && el.querySelector('.rro-result-card-marker'));
    if (totalsTop && firstCardTop) {
      gap = Math.round(firstCardTop.getBoundingClientRect().top - totalsTop.getBoundingClientRect().bottom);
    }
  }

  return {
    hasV5: [...document.querySelectorAll('style')].some(s => (s.textContent||'').includes('rro-css-v5-totals-gap')),
    totalsDisplay: totals ? getComputedStyle(totals).display : null,
    ecMarginBottom: totalsEC ? getComputedStyle(totalsEC).marginBottom : null,
    gapPx: gap,
    markerAncestors: ancestors,
    borders: document.querySelectorAll('[data-testid="stVerticalBlockBorderWrapper"]').length,
    borderLike: document.querySelectorAll('[class*="Border"]').length,
  };
}
"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto("http://localhost:8501", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)
    page.locator('[data-testid="stTextInput"] input').first.fill("borek")
    page.get_by_role("button", name="Zoeken").first.click()
    page.wait_for_timeout(4500)
    print(json.dumps(page.evaluate(JS), indent=2))
    browser.close()
