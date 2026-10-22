"""
Google Maps DOM helpers (Playwright API) — COLLECTION ONLY.

Selectors captured from a live Browser Use run on current Maps. Camoufox's
page object is a Playwright page, so these work unchanged.

This version adds, for the Daryaft Labs pipeline:
  - sort reviews by "Newest"
  - parse each review's relative date ("4 months ago" -> approx date)
  - scroll the reviews pane only until reviews pass a cutoff date
  - return raw reviews (author, rating, date, review_id, text). NO categorization.
"""

import random
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

# --- Selectors (from your Browser Use log on current Maps) ---------------
FEED = "div[role='feed']"
RESULT_LINK = "a.hfpxzc"
REVIEW_TAB = "button[role='tab'][aria-label*='Reviews']"
SCROLL_PANE = "div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde[tabindex='-1']"
REVIEW_CARD = "div.jftiEf"
SEE_MORE = "button.w8nwRe"
DATE_SEL = "span.rsqaWe"          # relative date inside a review card
# -------------------------------------------------------------------------


def pause(a=0.8, b=1.8):
    time.sleep(random.uniform(a, b))


def parse_relative_date(text, now=None):
    """'4 months ago' / 'a year ago' / 'yesterday' -> approximate datetime."""
    now = now or datetime.now(timezone.utc)
    if not text:
        return None
    t = text.lower().strip()
    if "moment" in t or "just now" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(a|an|\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", t)
    if not m:
        return None
    n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
    per = {"second": 0, "minute": 0, "hour": 0, "day": 1,
           "week": 7, "month": 30, "year": 365}[m.group(2)]
    return now - timedelta(days=n * per)


def dismiss_consent(page):
    for label in ("Accept all", "Reject all", "I agree", "Alle akzeptieren"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                pause()
                return
        except Exception:
            continue


def _scroll_feed(page, target, max_rounds=15):
    last, stable = 0, 0
    js = """([pane, item]) => {
        const p = document.querySelector(pane);
        if (!p) return -1;
        p.scrollTop = p.scrollHeight;
        return document.querySelectorAll(item).length;
    }"""
    for _ in range(max_rounds):
        n = page.evaluate(js, [FEED, RESULT_LINK])
        if n == -1:
            return last
        if n >= target:
            return n
        if n == last:
            stable += 1
            if stable >= 3:
                return n
        else:
            stable = 0
        last = n
        pause(1.0, 2.2)
    return last


def discover_clinics(page, term, limit):
    """Open a category search; return up to `limit` {name, google_maps_url}."""
    page.goto("https://www.google.com/maps/search/" + urllib.parse.quote(term),
              wait_until="domcontentloaded")
    pause(2, 4)
    dismiss_consent(page)
    try:
        page.wait_for_selector(FEED, timeout=15000)
    except Exception:
        pass
    _scroll_feed(page, target=limit + 5)
    items = page.evaluate(
        """(sel) => Array.from(document.querySelectorAll(sel)).map(a => ({
            name: a.getAttribute('aria-label') || '', url: a.href }))""",
        RESULT_LINK)
    seen, out = set(), []
    for it in items:
        if not it["url"] or it["url"] in seen or not it["name"]:
            continue
        seen.add(it["url"])
        out.append({"name": it["name"].strip(), "google_maps_url": it["url"]})
        if len(out) >= limit:
            break
    return out


def goto_place(page, name_or_url):
    if name_or_url.startswith("http"):
        page.goto(name_or_url, wait_until="domcontentloaded")
    else:
        q = urllib.parse.quote(f"{name_or_url} Tampa FL")
        page.goto("https://www.google.com/maps/search/" + q,
                  wait_until="domcontentloaded")
        pause(2, 4)
        if page.locator("h1.DUwDvf").count() == 0 and page.locator(RESULT_LINK).count() > 0:
            page.locator(RESULT_LINK).first.click()
    pause(3, 5)
    dismiss_consent(page)


# --- Overview tab details (website lives here) ---------------------------
HEADER_JS = """() => {
    const t = s => { const e = document.querySelector(s); return e ? e.innerText.trim() : null; };
    const aria = s => { const e = document.querySelector(s); return e ? (e.getAttribute('aria-label') || '').trim() : null; };
    let rating = null, count = null;
    const f = document.querySelector('div.F7nice');
    if (f) {
        const m = f.innerText.match(/([0-9.]+)/);
        rating = m ? parseFloat(m[1]) : null;
        const c = f.innerText.match(/([\\d,]+)\\s*review/i);
        count = c ? parseInt(c[1].replace(/,/g, '')) : null;
    }
    const web = document.querySelector("a[data-item-id='authority']");

    // Per-day opening hours: rendered (even if visually collapsed) as
    // buttons with aria-label "Monday, 8 AM to 5 PM, Copy open hours" or
    // "Saturday, Closed, Copy open hours" — no click needed to read them.
    const hours = {};
    document.querySelectorAll('button.mWUh3d[aria-label]').forEach(b => {
        const label = (b.getAttribute('aria-label') || '').replace(/,\\s*Copy open hours\\s*$/i, '');
        const idx = label.indexOf(',');
        if (idx === -1) return;
        const day = label.slice(0, idx).trim();
        const hoursText = label.slice(idx + 1).trim();
        if (day) hours[day] = hoursText;
    });

    return {
        name: t('h1.DUwDvf'),
        address: (aria("button[data-item-id='address']") || '').replace(/^Address:\\s*/, ''),
        phone: (aria("button[data-item-id^='phone']") || '').replace(/^Phone:\\s*/, ''),
        website: web ? web.href : null,
        rating: rating, review_count: count,
        hours: hours
    };
}"""

REVIEWS_JS = """() => {
    const cards = document.querySelectorAll('div.jftiEf');
    const out = [];
    cards.forEach(c => {
        const textEl = c.querySelector('span.wiI7pd, .MyEned');
        const nameEl = c.querySelector('.d4r55');
        const rateEl = c.querySelector('span.kvMYJc[aria-label]');
        const dateEl = c.querySelector('span.rsqaWe, .rsqaWe');
        const idEl = c.querySelector('[data-review-id]');
        let txt = textEl ? textEl.innerText.trim() : '';
        txt = txt.replace(/\\s*\\u2026?\\s*More$/i, '').trim();
        out.push({
            author: nameEl ? nameEl.innerText.trim() : null,
            rating: rateEl ? (rateEl.getAttribute('aria-label') || '').trim() : null,
            date_text: dateEl ? dateEl.innerText.trim() : null,
            review_id: idEl ? idEl.getAttribute('data-review-id') : null,
            text: txt
        });
    });
    return out;
}"""


def _open_reviews_tab(page):
    for sel in (REVIEW_TAB, "button[aria-label*='Reviews']", "button:has-text('Reviews')"):
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def _sort_newest(page):
    """Best-effort: click Sort -> Newest so the scroll-to-cutoff works."""
    try:
        sort_btn = page.get_by_role("button", name=re.compile("Sort", re.I))
        if sort_btn.count() == 0:
            sort_btn = page.locator("button[aria-label*='Sort']")
        if sort_btn.count() == 0:
            return False
        sort_btn.first.click(timeout=4000)
        pause(0.8, 1.5)
        newest = page.get_by_role("menuitemradio", name=re.compile("Newest", re.I))
        if newest.count() == 0:
            newest = page.locator("div[role='menuitemradio']:has-text('Newest')")
        if newest.count() > 0:
            newest.first.click(timeout=4000)
            pause(1.5, 2.5)
            return True
    except Exception:
        pass
    return False


def scrape_place_reviews(page, name_or_url, cutoff_date, max_rounds=60, hard_cap=600):
    """
    Visit a place (Overview details), open Reviews, sort Newest, scroll until
    reviews pass `cutoff_date`, and return raw reviews. No categorization.
    """
    goto_place(page, name_or_url)
    clinic = page.evaluate(HEADER_JS)

    if not _open_reviews_tab(page):
        clinic["reviews"] = []
        clinic["sorted_newest"] = False
        return clinic
    pause(2, 4)

    clinic["sorted_newest"] = _sort_newest(page)

    # Scroll the reviews pane until the oldest loaded review is past the cutoff.
    scroll_js = """(pane) => {
        const p = document.querySelector(pane);
        if (!p) return -1;
        p.scrollTop = p.scrollHeight;
        return document.querySelectorAll('div.jftiEf').length;
    }"""
    dates_js = """() => Array.from(document.querySelectorAll('div.jftiEf span.rsqaWe, div.jftiEf .rsqaWe')).map(e => e.innerText)"""

    last, stable = 0, 0
    for _ in range(max_rounds):
        n = page.evaluate(scroll_js, SCROLL_PANE)
        if n == -1:
            break
        if n >= hard_cap:
            break
        # stop once we've loaded reviews older than the cutoff
        dts = [parse_relative_date(d) for d in page.evaluate(dates_js)]
        dts = [d for d in dts if d]
        if dts and min(dts) < cutoff_date:
            break
        if n == last:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last = n
        pause(1.2, 2.4)

    # Expand every truncated review, then read all of them.
    page.evaluate("""() => document.querySelectorAll('button.w8nwRe')
                       .forEach(b => { try { b.click(); } catch(e){} })""")
    pause(1, 2)

    raw = page.evaluate(REVIEWS_JS)
    reviews = []
    for r in raw:
        if not r.get("text"):
            continue
        approx = parse_relative_date(r.get("date_text"))
        r["date_approx"] = approx.date().isoformat() if approx else None
        # keep reviews within the window (or undated, to be safe)
        if approx is None or approx >= cutoff_date:
            reviews.append(r)

    clinic["reviews"] = reviews
    clinic["reviews_total_loaded"] = len(raw)
    return clinic


def discover_clinics_at_coords(
    page,
    search_term: str,
    lat: float,
    lng: float,
    zoom: int,
    limit: int,
) -> tuple[list[dict], int]:
    """
    Navigate to coordinates and return (results[:limit], raw_deduped_count).
    Raw count is used by the campaign service for saturation detection.
    URL format: /maps/search/{term}/@{lat},{lng},{zoom}z
    """
    encoded = urllib.parse.quote(search_term)
    url = f"https://www.google.com/maps/search/{encoded}/@{lat},{lng},{zoom}z"
    page.goto(url, wait_until="domcontentloaded")
    pause(2, 4)
    dismiss_consent(page)
    try:
        page.wait_for_selector(FEED, timeout=15000)
    except Exception:
        pass
    # Single scroll call to load results (not the looping _scroll_feed)
    page.evaluate("""([pane, item]) => {
        const p = document.querySelector(pane);
        if (p) p.scrollTop = p.scrollHeight;
        return document.querySelectorAll(item).length;
    }""", [FEED, RESULT_LINK])
    items = page.evaluate(
        """(sel) => Array.from(document.querySelectorAll(sel)).map(a => ({
            name: a.getAttribute('aria-label') || '', url: a.href }))""",
        RESULT_LINK,
    )
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        if not it["url"] or it["url"] in seen or not it["name"]:
            continue
        seen.add(it["url"])
        out.append({"name": it["name"].strip(), "google_maps_url": it["url"]})
    raw_count = len(out)
    return out[:limit], raw_count
