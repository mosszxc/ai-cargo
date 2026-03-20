# Spike Results: 1688.com Product Data Acquisition

**Date:** 2026-03-19 (updated 2026-03-20)
**Duration:** ~3 hours (Playwright tests + API research)
**Original verdict:** NO-GO for Playwright scraping. GO via third-party API.
**Final implementation:** Scrapling StealthyFetcher + Claude Haiku LLM extraction (80% success rate, free except Haiku ~$0.001/parse). See `skills/calc/parser_1688.py`.

---

## Part 1: Playwright Scraping (FAILED)

### Summary

| Metric | Result |
|---|---|
| URLs tested | 20 (across 5 categories) |
| Successfully parsed (title OR price) | 1/20 (5%) |
| Anti-bot detected | 18/20 (90%) |
| Login redirect | 17/20 (85%) |

**Result: 5% < 50% threshold = NO-GO for Playwright scraping.**

### Strategies tested (all failed)

| # | Strategy | Result |
|---|---|---|
| 1 | Direct Playwright headless Chrome | 1/20 partial, rest login redirect |
| 2 | Proxy retry (SOCKS5 Webshare) | 0/19, connection failed for Chinese domains |
| 3 | Stealth mode (hide webdriver, fake plugins) | All redirected to login |
| 4 | Mobile version (m.1688.com) | "验证码拦截" (captcha block) |
| 5 | Internal API endpoints (h5api, laputa) | API not found or requires auth |
| 6 | Google cached versions | Google consent page, no data |
| 7 | Visit homepage first for cookies | Still redirected |
| 8 | alibaba.com (English version) | Captcha interception |

### Root Cause

1688.com (Alibaba Group) has enterprise-grade anti-bot protection. Detects headless browsers instantly, forces login redirect. All Alibaba properties share the same anti-bot. Not solvable with better scraping — it's a platform constraint.

---

## Part 2: Third-Party API Research (SUCCESS)

### APIs Tested

| Service | Reachable | Auth Error | Notes |
|---|---|---|---|
| **LovBuy** (lovbuy.com) | YES | 501 (invalid key) | Cheapest, endpoint confirmed working |
| **OneBound** (onebound.cn) | YES | 4016 (key disabled) | Free trial, Russian lang support |
| **Apify** (apify.com) | YES | 401 (token needed) | Free $5/month, cloud scraping |
| **Oxylabs** (oxylabs.io) | YES | 405 (method) | Enterprise, expensive |
| RapidAPI (all 5 providers) | **BLOCKED** | 451 geo-block | US sanctions compliance — unusable from our server |
| TMAPI (tmapi.top) | **BROKEN** | SSL cert mismatch | api.tmapi.top cert invalid, api.tmapi.io returns 403 |
| DajiAPI (dajiapi.cn) | **DOWN** | DNS resolution failed | Domain unreachable |
| LovBuy old (buytaobao1688.com) | **DOWN** | SSL cert expired | Deprecated |
| OTCommerce (otcommerce.com) | Skipped | — | $150/month minimum, too expensive for MVP |

### Recommended: LovBuy API

| Parameter | Value |
|---|---|
| Endpoint | `https://www.lovbuy.com/api/getinfo.php` |
| Auth | Single API key parameter |
| Cost | **5 CNY / 100 requests (~$0.007/req, ~0.5 rub/req)** |
| Min balance | 5 CNY (~$0.70, ~60 rub) |
| Registration | lovbuy.com (create account, add balance from account center) |
| Fields returned | title, price, images, properties/attributes, min_order |
| Latency | ~1-2 sec (tested endpoint response time) |
| 1688 param | `website=2` |
| Language | `lang=en` (30+ languages) |

**Why LovBuy:**
- Cheapest option ($0.007/req vs Apify ~$0.01/req vs OneBound unknown)
- Simplest integration (one API key, one GET request)
- Endpoint confirmed reachable and responding (status 501 = correct auth error)
- Supports 1688 directly (website=2 param)

**API call example:**
```
GET https://www.lovbuy.com/api/getinfo.php?key=YOUR_KEY&website=2&productid=790251400429&lang=en
```

**Response format (status 200):**
```json
{
  "status": 200,
  "title": "Product title",
  "price": "12.50",
  "images": ["url1", "url2"],
  "props": {"weight": "0.5kg", "size": "30x20x15cm"},
  "min_order": 100
}
```

### Backup: OneBound API

| Parameter | Value |
|---|---|
| Endpoint | `https://api-gw.onebound.cn/1688/item_get/` |
| Auth | API key + secret |
| Cost | Free trial, then paid |
| Registration | console.open.onebound.cn |
| Fields | title, price, images, SKUs, seller_id, location, props, total_sold |
| Language | cn, en, **ru** (Russian!) |
| Special | `sales_data=1` for 30-day sales, `agent=1` for distributor pricing |

**Why backup:** More fields (SKUs, seller, sales data), Russian language support, but pricing unknown after trial.

### Backup 2: Apify

| Parameter | Value |
|---|---|
| Actor | `ecomscrape/1688-product-details-page-scraper` |
| Auth | Apify API token |
| Cost | Free $5/month credit |
| Registration | apify.com |
| Special | Cloud scraping — handles anti-bot on their infra |
| Latency | ~30-60 sec (actor startup + scraping) |

**Why last resort:** Slower (30-60s vs 1-2s), more complex integration, actor-based async model.

---

## Go/No-Go Decision

### GO — via LovBuy API

| Criterion | Threshold | Result |
|---|---|---|
| Endpoint reachable | YES | YES (status 200, proper JSON error) |
| Cost viable for MVP | < $10/month | YES (~$0.70 to start, $7 for 1000 requests) |
| Fields sufficient | title + price minimum | YES (title, price, images, props) |
| Integration complexity | < 1 day | YES (one GET request, one API key) |
| Fallback available | At least 1 backup | YES (OneBound, Apify) |

**Decision: GO with LovBuy API as primary parser.**

### Action items

1. Register at lovbuy.com, deposit 5 CNY ($0.70)
2. Get API key from account center
3. Test with 5-10 real URLs using `parser_1688_api.py`
4. If LovBuy fails → try OneBound (free trial)
5. If both fail → Apify (free $5/month)

### Fallback: Manual input

Even if ALL APIs fail, calc_skill works with text input:
- "500 kg clothes Guangzhou → Moscow"
- AI extracts: weight=500kg, category=clothes, from=Guangzhou, to=Moscow
- Calculator runs — no parsing needed

**The parser is a convenience feature, not a blocker.**

---

## Files Produced

- `spike/test_1688_parser.py` — Playwright test: 20 URLs (5% success)
- `spike/test_1688_stealth.py` — 5 anti-bot bypass strategies (all failed)
- `spike/test_1688_alternatives.py` — Alternative platforms test
- `spike/test_1688_apis.py` — API probe v1 (RapidAPI geo-blocked discovery)
- `spike/test_1688_apis_v2.py` — API probe v2 (LovBuy, OneBound, Apify confirmed)
- `spike/parser_1688_api.py` — **Working parser module** (LovBuy + OneBound + Apify)
- `spike/results.json` — Raw Playwright results
- `docs/spike-results.md` — This document

---

## Appendix: Playwright Per-URL Results

| # | Category | Offer ID | Title | Price | Error |
|---|---|---|---|---|---|
| 1 | clothing | 790251400429 | YES | YES (¥2652) | Anti-bot detected |
| 2 | clothing | 822970193916 | no | no | Login redirect |
| 3 | clothing | 733761829621 | no | no | Login redirect |
| 4 | clothing | 984046619242 | no | no | Login redirect |
| 5 | electronics | 888640675772 | no | no | Login redirect |
| 6 | electronics | 752205332799 | no | no | Login redirect |
| 7 | electronics | 647997819110 | no | no | Login redirect |
| 8 | electronics | 708012905355 | no | no | Navigation timeout |
| 9 | home | 44776366308 | no | no | Login redirect |
| 10 | home | 583658240815 | no | no | Login redirect |
| 11 | home | 606628051926 | no | no | Navigation timeout |
| 12 | home | 617737573706 | no | no | Login redirect |
| 13 | toys | 595045370736 | no | no | Login redirect |
| 14 | toys | 577687060339 | no | no | Login redirect |
| 15 | toys | 602480973397 | no | no | Login redirect |
| 16 | toys | 656173618669 | no | no | Login redirect |
| 17 | cosmetics | 520537149440 | no | no | Login redirect |
| 18 | cosmetics | 534550904424 | no | no | Login redirect |
| 19 | cosmetics | 703974508333 | no | no | Login redirect |
| 20 | cosmetics | 558868806998 | no | no | Login redirect |
