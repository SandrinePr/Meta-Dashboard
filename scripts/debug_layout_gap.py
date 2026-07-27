"""Diagnose empty space above title after Streamlit search."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"C:\RRO\Projects\AutomationDashboardMETA\debug_layout_report.json")

PROBE = r"""
() => {
  const report = {
    body: null,
    main: null,
    blockContainer: null,
    title: null,
    titleOffsetInBlock: null,
    gapAboveTitlePx: null,
    childrenOfBlock: [],
    childrenOfMainVertical: [],
    elementsBetweenBlockTopAndTitle: [],
    matchedCardParents: [],
    iframesInMain: [],
    computedOnMainVB: null,
  };

  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      tag: el.tagName,
      id: el.id || null,
      testid: el.getAttribute("data-testid"),
      className: (el.className || "").toString().slice(0, 180),
      top: Math.round(r.top),
      left: Math.round(r.left),
      width: Math.round(r.width),
      height: Math.round(r.height),
      marginTop: cs.marginTop,
      marginBottom: cs.marginBottom,
      paddingTop: cs.paddingTop,
      paddingBottom: cs.paddingBottom,
      display: cs.display,
      position: cs.position,
      overflow: cs.overflow,
      justifyContent: cs.justifyContent,
      alignItems: cs.alignItems,
      gap: cs.gap,
      minHeight: cs.minHeight,
      heightCss: cs.height,
    };
  };

  report.body = box(document.body);
  const main = document.querySelector('[data-testid="stMain"]') || document.querySelector("section.main");
  report.main = box(main);
  const block = document.querySelector('[data-testid="stMain"] .block-container')
    || document.querySelector(".stMainBlockContainer")
    || document.querySelector(".block-container");
  report.blockContainer = box(block);
  const title = document.querySelector(".rro-page-title") || document.querySelector("h1");
  report.title = box(title);

  if (block && title) {
    const br = block.getBoundingClientRect();
    const tr = title.getBoundingClientRect();
    report.titleOffsetInBlock = Math.round(tr.top - br.top);
    report.gapAboveTitlePx = Math.round(tr.top - br.top - (parseFloat(getComputedStyle(block).paddingTop) || 0));
  }

  if (block) {
    const root = block.firstElementChild || block;
    [...root.children].forEach((el, i) => {
      const b = box(el);
      b.index = i;
      b.innerTextPreview = (el.innerText || "").replace(/\s+/g, " ").slice(0, 80);
      b.hasStyleTag = !!el.querySelector("style");
      b.hasIframe = !!el.querySelector("iframe");
      b.hasMarker = !!el.querySelector(".rro-result-card-marker");
      b.hasResults = !!el.querySelector(".rro-results-section");
      report.childrenOfBlock.push(b);
    });

    // Prefer the main vertical block children if present
    const vb = block.querySelector('[data-testid="stVerticalBlock"]');
    if (vb) {
      report.computedOnMainVB = box(vb);
      [...vb.children].forEach((el, i) => {
        const b = box(el);
        b.index = i;
        b.innerTextPreview = (el.innerText || "").replace(/\s+/g, " ").slice(0, 80);
        b.hasStyleTag = !!el.querySelector("style");
        b.hasIframe = !!el.querySelector("iframe");
        b.hasMarker = !!el.querySelector(".rro-result-card-marker");
        b.childTestIds = [...el.querySelectorAll("[data-testid]")].slice(0, 8).map(n => n.getAttribute("data-testid"));
        report.childrenOfMainVertical.push(b);
      });
    }
  }

  if (block && title) {
    const br = block.getBoundingClientRect();
    const tr = title.getBoundingClientRect();
    const all = [...block.querySelectorAll("*")];
    for (const el of all) {
      const r = el.getBoundingClientRect();
      if (r.height < 8) continue;
      // element whose box sits in the empty band above the title, inside the card
      if (r.top >= br.top - 1 && r.bottom <= tr.top + 1 && r.top < tr.top - 5) {
        report.elementsBetweenBlockTopAndTitle.push({
          ...box(el),
          path: (() => {
            const parts = [];
            let n = el;
            for (let i = 0; i < 6 && n; i++) {
              parts.push(
                (n.getAttribute && n.getAttribute("data-testid"))
                  || (n.className && n.className.toString && n.className.toString().split(" ")[0])
                  || n.tagName
              );
              n = n.parentElement;
            }
            return parts.join(" < ");
          })(),
        });
      }
    }
  }

  // Which vertical blocks match the OLD broad card selector?
  document.querySelectorAll('[data-testid="stVerticalBlock"]').forEach((el) => {
    const hasMarker = !!el.querySelector(".rro-result-card-marker");
    const hasNestedMarked = !!el.querySelector('[data-testid="stVerticalBlock"] .rro-result-card-marker');
    if (hasMarker) {
      report.matchedCardParents.push({
        ...box(el),
        hasMarker,
        hasNestedMarked,
        matchesOldBroad: hasMarker, // :has(.marker) via descendant in child div
        matchesNewLeaf: hasMarker && !hasNestedMarked,
        textPreview: (el.innerText || "").replace(/\s+/g, " ").slice(0, 60),
      });
    }
  });

  document.querySelectorAll('[data-testid="stMain"] iframe').forEach((el) => {
    report.iframesInMain.push(box(el));
  });

  return report;
}
"""


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto("http://localhost:8501", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)

        before = page.evaluate(PROBE)

        # Clear and type search
        inp = page.locator('[data-testid="stTextInput"] input').first
        inp.click()
        inp.fill("")
        inp.fill("borek")
        # Click Zoeken if present
        btn = page.get_by_role("button", name="Zoeken")
        if btn.count():
            btn.first.click()
        page.wait_for_timeout(4000)

        after = page.evaluate(PROBE)
        browser.close()

    payload = {"before": before, "after": after}
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")

    def summarize(label: str, data: dict) -> None:
        print(f"\n=== {label} ===")
        print("titleOffsetInBlock:", data.get("titleOffsetInBlock"))
        print("gapAboveTitlePx:", data.get("gapAboveTitlePx"))
        print("block:", data.get("blockContainer"))
        print("title:", data.get("title"))
        print("mainVB:", data.get("computedOnMainVB"))
        print("childrenOfMainVertical count:", len(data.get("childrenOfMainVertical") or []))
        for c in (data.get("childrenOfMainVertical") or [])[:12]:
            print(
                f"  [{c.get('index')}] h={c.get('height')} top={c.get('top')} "
                f"style={c.get('hasStyleTag')} iframe={c.get('hasIframe')} "
                f"marker={c.get('hasMarker')} text={c.get('innerTextPreview')!r}"
            )
        print("elementsBetweenBlockTopAndTitle:", len(data.get("elementsBetweenBlockTopAndTitle") or []))
        for e in (data.get("elementsBetweenBlockTopAndTitle") or [])[:15]:
            print(
                f"  h={e.get('height')} top={e.get('top')} path={e.get('path')} "
                f"class={e.get('className')!r}"
            )
        print("matchedCardParents:")
        for m in data.get("matchedCardParents") or []:
            print(
                f"  h={m.get('height')} nestedMarked={m.get('hasNestedMarked')} "
                f"leaf={m.get('matchesNewLeaf')} text={m.get('textPreview')!r}"
            )
        print("iframesInMain:", data.get("iframesInMain"))

    summarize("BEFORE search", before)
    summarize("AFTER search", after)


if __name__ == "__main__":
    main()
