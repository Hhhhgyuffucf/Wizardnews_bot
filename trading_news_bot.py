"""
Trading News Bot - Gold, Silver, Bitcoin, Ethereum  (v3)
=========================================================
GitHub Actions har 5 minute mein ye script chalata hai. Har run ~4.5 minute
andar hi loop mein rehta hai aur har 45 second mein check karta hai, taaki
news aur alerts lagbhag turant nikal jayein.

Kya karta hai:
1) NEWS   : Gold/Silver/Bitcoin/Ethereum ki nayi news milte hi bhejta hai -
            chhoti summary + market impact (achha/bura) + image (agar mile)
2) PRICE  : Har 30 minute mein price update
3) EVENTS : Bade US economic events (CPI, jobs report, Fed waghera) ke liye
            5 ghante, 1 ghanta, 15 min, 5 min, 1 min pehle alert, aur event
            aate hi "kya aaya + market pe asar" bhejta hai

Note: Impact (achha/bura) rule-based andaaza hai, financial advice nahi.
"""

import calendar
import html
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import feedparser
import requests

# ---------------- CONFIG ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1" or not BOT_TOKEN or not CHAT_ID

STATE_FILE = os.environ.get("STATE_FILE", "bot_state.json")
LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "270"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "45"))

PRICE_EVERY_MIN = 30
EVENT_STAGES = [300, 60, 15, 5, 1]  # minute pehle
STAGE_LABEL = {300: "5 hours", 60: "1 hour", 15: "15 minutes", 5: "5 minutes", 1: "1 minute"}

IST = ZoneInfo("Asia/Kolkata")
UA = "Mozilla/5.0 (compatible; TradingNewsBot/3.0)"

TICKERS = {
    "🥇 Gold": "GC=F",
    "🥈 Silver": "SI=F",
    "₿ Bitcoin": "BTC-USD",
    "Ξ Ethereum": "ETH-USD",
}

NEWS_FEEDS = {
    "Kitco": "https://www.kitco.com/rss/KitcoNews.xml",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "Investing": "https://www.investing.com/rss/news_11.rss",
    "Investing Economy": "https://www.investing.com/rss/news_14.rss",
    "Investing Markets": "https://www.investing.com/rss/news_25.rss",
    "Investing Forex": "https://www.investing.com/rss/news_1.rss",
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "CNBC Economy": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
}

# Bade US macro events ki news (Gold/Silver/Crypto sab hilte hain) - asset ka naam na ho tab bhi
MACRO_RE = re.compile(
    r"\b(fomc|federal reserve|the fed|fed (?:holds?|cuts?|hikes?|raises?|keeps?|leaves?|signals?|decision|chair)|"
    r"powell|interest rates? (?:decision|cut|hike)|rate (?:decision|cut|hike)|"
    r"cpi|consumer price|inflation (?:data|report|rate|rises|falls|cools|eases|accelerates)|"
    r"pce|ppi|producer price|nonfarm|non-farm|payrolls|jobs report|unemployment rate|jobless claims|"
    r"gdp|retail sales|ism (?:manufacturing|services)|treasury yields?|dollar index|"
    # dollar / yields / central banks
    r"u\.?s\.? dollar|dollar (?:rises|falls|weakens|strengthens|slides|jumps|index)|bond yields?|10-year yield|"
    r"central banks?|ecb|bank of japan|boj|bank of england|rate (?:cuts?|hikes?)|recession|stagflation|"
    # geopolitics / trade / oil
    r"war|ceasefire|missile|attack|escalat\w*|geopolitic\w*|sanctions?|tariffs?|trade (?:war|deal|talks)|"
    r"opec|oil prices?|crude|middle east|iran|israel|russia|ukraine|china|taiwan|trump|"
    # gold / commodity demand
    r"central bank (?:gold|buying)|gold reserves?|safe[- ]haven|commodit\w*|"
    # crypto / regulation / institutions
    r"crypto|stablecoin|spot etf|etf (?:inflows?|outflows?|approval)|sec |cftc|regulat\w*|"
    r"blockchain|altcoin|defi|binance|coinbase|tether|"
    # markets / risk
    r"stocks? (?:fall|plunge|tumble|rally|surge|rebound)|wall street|nasdaq|s&p 500|dow jones|"
    r"bank (?:collapse|failure|crisis)|debt ceiling|government shutdown|default)\b",
    re.I,
)
ALL_ASSETS = ["Gold", "Silver", "Bitcoin", "Ethereum"]
MAX_NEWS_AGE_HOURS = 6

EVENTS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

ASSET_PATTERNS = {
    "Gold": r"\bgold\b",
    "Silver": r"\bsilver\b",
    "Bitcoin": r"\b(bitcoin|btc)\b",
    "Ethereum": r"\b(ethereum|ether|eth)\b",
}

BULL_RE = re.compile(
    r"\b(surg\w*|soar\w*|rall(?:y|ies|ied)|jump\w*|climb\w*|gain\w*|rises?|rose|rising|"
    r"record high|all-time high|breakout|inflows?|approv\w*|adopt\w*|bullish|rebound\w*|"
    r"recover\w*|rate cuts?|dovish|safe[- ]haven|boost\w*|higher|buying|accumulat\w*)\b",
    re.I,
)
BEAR_RE = re.compile(
    r"\b(plung\w*|crash\w*|drops?|dropped|falls?|fell|falling|slump\w*|tumbl\w*|sell-?offs?|"
    r"declin\w*|outflows?|hack\w*|exploit\w*|stolen|bans?|banned|lawsuit|bearish|"
    r"rate hikes?|hawkish|liquidat\w*|dump\w*|fraud|reject\w*|slid\w*|sinks?|lower|"
    r"loss(?:es)?|warns?|warning)\b",
    re.I,
)

# In events mein actual > forecast ka matlab dollar kamzor / economy kamzor hota hai
INVERSE_EVENT_WORDS = ("unemployment", "jobless", "claims")


# ---------------- STATE ----------------
def load_state():
    default = {"seen_links": [], "sent_alerts": [], "last_price_slot": None}
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        for k, v in default.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return default


def save_state(state):
    state["seen_links"] = state["seen_links"][-600:]
    state["sent_alerts"] = state["sent_alerts"][-400:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ---------------- TELEGRAM ----------------
def esc(text):
    return html.escape(text or "", quote=False)


def send(text, image_url=None, card_bytes=None):
    """
    Message bhejta hai. Image ka order:
    1) news ki asli image (URL)  2) bot ka banaya card  3) sirf text
    """
    if DRY_RUN:
        print("----- DRY RUN MESSAGE -----")
        if image_url:
            print(f"[IMAGE] {image_url}")
        elif card_bytes:
            print(f"[CARD IMAGE {len(card_bytes)} bytes]")
        print(text)
        return True

    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    data = {"chat_id": CHAT_ID, "caption": text[:1024], "parse_mode": "HTML"}
    if image_url:
        try:
            r = requests.post(f"{base}/sendPhoto", data={**data, "photo": image_url}, timeout=25)
            if r.status_code == 200:
                return True
        except requests.exceptions.Timeout:
            # Telegram ne shayad bhej diya hoga; dobara bhejne se duplicate banta hai
            print("sendPhoto timeout - assuming delivered")
            return True
        except Exception:
            pass  # asli image na chali to card try karenge
    if card_bytes:
        try:
            r = requests.post(f"{base}/sendPhoto", data=data,
                              files={"photo": ("news.png", card_bytes, "image/png")}, timeout=10)
            if r.status_code == 200:
                return True
        except Exception:
            pass

    try:
        r = requests.post(
            f"{base}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text[:4000], "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code != 200:
            print("Telegram error:", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        print("Telegram send failed:", type(e).__name__)
        return False


# ---------------- NEWS HELPERS ----------------
def detect_assets(text):
    return [name for name, pat in ASSET_PATTERNS.items() if re.search(pat, text or "", re.I)]


def impact_label(score):
    if score > 0:
        return "🟢 Positive (bullish)"
    if score < 0:
        return "🔴 Negative (bearish)"
    return "🟡 Neutral / unclear"


def score_text(text):
    return len(BULL_RE.findall(text or "")) - len(BEAR_RE.findall(text or ""))


def clean_text(raw):
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*The post .*? appeared first on .*$", "", text, flags=re.I)
    text = re.sub(r"\s*(Read more|Continue reading|Click here).*$", "", text, flags=re.I)
    return text.strip()


def make_summary(entry, title, limit=260):
    raw = entry.get("summary") or entry.get("description") or ""
    if not raw and entry.get("content"):
        raw = entry["content"][0].get("value", "")
    return trim_summary(clean_text(raw), title, limit)


def trim_summary(text, title, limit=260):
    """Text ko chhoti, poore-vakya wali summary mein badalta hai ('' agar kaam ka na ho)."""
    text = (text or "").strip()
    if text.lower().startswith(title.lower()):
        text = text[len(title):].lstrip(" -:.")
    if len(text) < 40 or text.lower() == title.lower():
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if out and len(out) + len(s) + 1 > limit:
            break
        out = f"{out} {s}".strip()
        if len(out) >= 120:
            break
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def extract_image(entry):
    candidates = []
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key, []) or []:
            candidates.append(m.get("url"))
    for enc in entry.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image"):
            candidates.append(enc.get("href") or enc.get("url"))
    for lnk in entry.get("links", []) or []:
        if lnk.get("rel") == "enclosure" and str(lnk.get("type", "")).startswith("image"):
            candidates.append(lnk.get("href"))
    blobs = [entry.get("summary", "")]
    if entry.get("content"):
        blobs.append(entry["content"][0].get("value", ""))
    for blob in blobs:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', blob or "", re.I)
        if m:
            candidates.append(m.group(1))
    for url in candidates:
        if url and url.startswith("http"):
            return url
    return None


def _fetch_page(link):
    """Article page ka HTML (shuruaati hissa). Fail ho to None."""
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for _ in range(2):  # ek baar retry
        try:
            r = requests.get(link, timeout=7, headers=headers, stream=True)
            if r.status_code == 200:
                return r.raw.read(300_000, decode_content=True).decode("utf-8", "ignore")
            print(f"page fetch status {r.status_code}")
            if r.status_code in (401, 403, 404):
                return None
        except Exception as e:
            print("page fetch error:", type(e).__name__)
    return None


def reader_summary(link):
    """Site bot ko block kare to free 'reader' service (r.jina.ai) se article text lo."""
    try:
        r = requests.get("https://r.jina.ai/" + link, timeout=12,
                         headers={"Accept": "text/plain", "X-Return-Format": "markdown"})
        if r.status_code != 200:
            print(f"reader status {r.status_code}")
            return ""
        body = r.text.split("Markdown Content:", 1)[-1]
        for para in re.split(r"\n\s*\n", body):
            text = clean_text(re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", para))  # links/images hatao
            text = re.sub(r"^[#>*\-\s]+", "", text)
            if len(text) >= 90 and not re.search(
                    r"cookie|subscribe|newsletter|sign up|privacy|advertis|copyright|all rights", text, re.I):
                return text
    except Exception as e:
        print("reader error:", type(e).__name__)
    return ""


def page_summary_from(page):
    """Page se summary: og/meta description -> JSON-LD description -> pehla lamba paragraph."""
    d = og_description_from(page)
    if d and len(d) >= 40:
        return d
    m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){40,500})"', page or "")
    if m:
        try:
            return json.loads('"' + m.group(1) + '"')
        except Exception:
            return m.group(1)
    for para in re.findall(r"<p[^>]*>(.*?)</p>", page or "", re.S | re.I):
        text = clean_text(para)
        if len(text) >= 80 and not re.search(r"cookie|subscribe|newsletter|sign up|privacy", text, re.I):
            return text
    return d or ""


def _meta(page, names):
    """Page ke <meta> tag se content nikalta hai (property ya name dono)."""
    for n in names:
        for pat in (
            rf'<meta[^>]+(?:property|name)=["\']{n}["\'][^>]+content=["\']([^"\']*)["\']',
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{n}["\']',
        ):
            m = re.search(pat, page or "", re.I)
            if m and m.group(1).strip():
                return html.unescape(m.group(1)).strip()
    return None


def og_image_from(page):
    url = _meta(page, ["og:image:secure_url", "og:image", "twitter:image"])
    if url and url.startswith("//"):
        url = "https:" + url
    return url if url and url.startswith("http") else None


def og_description_from(page):
    return _meta(page, ["og:description", "description", "twitter:description"])


def fetch_og_image(link):
    """Article page se main image (og:image)."""
    return og_image_from(_fetch_page(link))


def _font(size, bold=True):
    from PIL import ImageFont

    names = (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"] if bold
             else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_card(title, assets, score, source):
    """Image na mile to bot khud ek clean news-card image banata hai (PNG bytes)."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    try:
        accent = (34, 197, 94) if score > 0 else (239, 68, 68) if score < 0 else (234, 179, 8)
        label = "POSITIVE" if score > 0 else "NEGATIVE" if score < 0 else "NEUTRAL"
        W, H = 1080, 760
        img = Image.new("RGB", (W, H), (17, 20, 28))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 14], fill=accent)
        d.text((60, 55), "  /  ".join(a.upper() for a in assets) or "MARKET NEWS",
               font=_font(54), fill=(255, 255, 255))
        d.text((60, 130), f"{source}  •  Market impact: {label}",
               font=_font(30, False), fill=accent)
        d.line([60, 190, W - 60, 190], fill=(60, 66, 80), width=2)

        font, y, lines, line = _font(52), 225, [], ""
        for word in title.split():
            test = f"{line} {word}".strip()
            if d.textlength(test, font=font) <= W - 120:
                line = test
            else:
                lines.append(line)
                line = word
        lines.append(line)
        for ln in lines[:6]:
            d.text((60, y), ln, font=font, fill=(240, 244, 250))
            y += 74
        if len(lines) > 6:
            d.text((60, y), "…", font=font, fill=(240, 244, 250))
        img = img.crop((0, 0, W, min(H, max(420, y + 50))))  # khali jagah hatao
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception as e:
        print("Card error:", type(e).__name__)
        return None


_QUOTE_CACHE = {}


def quote_line(assets):
    """Summary na mile to related asset ka live price dikhao (kaam ki jaankari)."""
    try:
        import yfinance as yf

        parts = []
        by_name = {k.split()[-1]: v for k, v in TICKERS.items()}  # "🥇 Gold" -> "Gold"
        for name in assets[:4]:
            ticker = by_name.get(name)
            if not ticker:
                continue
            cached = _QUOTE_CACHE.get(name)
            if cached and time.time() - cached[0] < 120:
                parts.append(cached[1])
                continue
            data = yf.Ticker(ticker).history(period="1d", interval="5m")
            if data.empty:
                continue
            last, first = float(data["Close"].iloc[-1]), float(data["Close"].iloc[0])
            text = f"{name} ${last:,.0f} ({(last - first) / first * 100:+.1f}%)"
            _QUOTE_CACHE[name] = (time.time(), text)
            parts.append(text)
        return "Live prices: " + " | ".join(parts) if parts else ""
    except Exception:
        return ""


def build_news_message(source, title, summary, assets, score):
    lines = [f"🚨 <b>{esc(title)}</b>"]
    if summary:
        lines += ["", esc(summary)]
    lines += ["", f"📊 <b>Market impact:</b> {impact_label(score)}"]
    if assets:
        lines.append(f"🎯 {esc(', '.join(assets))}")
    lines.append(f"📰 {esc(source)}")
    return "\n".join(lines)


_STOP = set("the a an of to in on for and or as at by with from is are be will after before over amid new says say "
            "us u s its it this that has have had not but into about up down out more than".split())
_COMMON = {"bitcoin", "btc", "gold", "silver", "ethereum", "ether", "eth", "price", "prices", "market", "markets"}


def title_stems(title):
    words = re.findall(r"[a-z]+", (title or "").lower())
    return {w[:5] for w in words if len(w) > 2 and w not in _STOP and w not in _COMMON}


def is_duplicate_story(state, title):
    """Ek hi khabar alag headline se dobara aaye to skip (e.g. Lagarde resigns / to resign)."""
    mine = title_stems(title)
    if len(mine) < 2:
        return False
    for old in state.get("recent_titles", []):
        theirs = set(old)
        shared = len(mine & theirs)
        if shared >= 2 and shared / min(len(mine), len(theirs)) >= 0.5:
            return True
    return False


def remember_title(state, title):
    rt = state.setdefault("recent_titles", [])
    rt.append(sorted(title_stems(title)))
    state["recent_titles"] = rt[-60:]


def is_old(entry):
    """Feed mein purani news (naye feed add hone par) spam na bane."""
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return False
    try:
        age = time.time() - calendar.timegm(t)
        return age > MAX_NEWS_AGE_HOURS * 3600
    except Exception:
        return False


def check_news(state):
    seen = set(state["seen_links"])
    bootstrap = not seen  # pehli baar: purani news spam na ho
    sent = 0
    for source, url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(url, agent=UA)
        except Exception as e:
            print(f"Feed error {source}: {type(e).__name__}")
            continue
        for entry in list(feed.entries)[:20]:
            link, title = entry.get("link"), (entry.get("title") or "").strip()
            if not link or not title or link in seen:
                continue
            seen.add(link)
            state["seen_links"].append(link)
            if bootstrap or is_old(entry):
                continue
            assets = detect_assets(title)
            macro = bool(MACRO_RE.search(title))
            if (not assets and not macro) or sent >= 6:
                continue
            if is_duplicate_story(state, title):
                print("duplicate story skipped:", title[:60])
                continue
            if not assets:
                assets = list(ALL_ASSETS)
            summary = make_summary(entry, title)
            image = extract_image(entry)
            if not summary or not image:
                # Feed mein summary/image nahi hai to article page se lo (max ~4 sec)
                page = _fetch_page(link)
                if page:
                    image = image or og_image_from(page)
                    if not summary:
                        summary = trim_summary(clean_text(page_summary_from(page)), title)
            if not summary:
                summary = trim_summary(clean_text(reader_summary(link)), title)
            if not summary:
                summary = quote_line(assets)
            score = score_text(f"{title}. {summary}")
            card = make_card(title, assets, score, source)  # backup image
            send(build_news_message(source, title, summary, assets, score), image, card)
            remember_title(state, title)
            sent += 1


# ---------------- PRICE ----------------
def get_prices():
    import yfinance as yf

    lines = ["📊 <b>Price Update</b>", ""]
    for name, ticker in TICKERS.items():
        try:
            data = yf.Ticker(ticker).history(period="1d", interval="5m")
            if data.empty:
                lines.append(f"{name}: no data")
                continue
            last, first = data["Close"].iloc[-1], data["Close"].iloc[0]
            pct = (last - first) / first * 100
            arrow = "🟢" if last >= first else "🔴"
            lines.append(f"{name}: ${last:,.2f}  {arrow} {pct:+.2f}%")
        except Exception as e:
            lines.append(f"{name}: error ({type(e).__name__})")
    return "\n".join(lines)


def check_price(state, now):
    slot = now.strftime("%Y-%m-%d-%H-") + str(now.minute // PRICE_EVERY_MIN)
    if state.get("last_price_slot") == slot:
        return
    send(get_prices())
    state["last_price_slot"] = slot


# ---------------- EVENTS ----------------
def parse_num(value):
    if value is None:
        return None
    s = str(value).replace(",", "").strip().upper()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    v = float(m.group())
    for suffix, mult in (("K", 1e3), ("M", 1e6), ("B", 1e9), ("T", 1e12)):
        if s.endswith(suffix):
            return v * mult
    return v


def event_impact(title, actual, forecast):
    """(emoji, text) ya None. Aam rule: USD mazboot => Gold/Crypto pe dabaav."""
    a, f = parse_num(actual), parse_num(forecast)
    if a is None or f is None:
        return None
    if a == f:
        return "🟡", "Neutral - result in line with forecast"
    inverse = any(w in title.lower() for w in INVERSE_EVENT_WORDS)
    strong_usd = (a > f) != inverse
    if strong_usd:
        return "🔴", "Negative - stronger dollar, may pressure Gold/Silver/Crypto"
    return "🟢", "Positive - weaker dollar, may lift Gold/Silver/Crypto"


_events_cache = {"t": 0, "data": []}


def fetch_events(now_ts):
    if now_ts - _events_cache["t"] < 180:
        return _events_cache["data"]
    try:
        r = requests.get(EVENTS_URL, timeout=15, headers={"User-Agent": UA})
        r.raise_for_status()
        _events_cache.update(t=now_ts, data=r.json())
    except Exception as e:
        print("Events calendar unavailable:", type(e).__name__)
        _events_cache["t"] = now_ts  # baar-baar retry na ho
    return _events_cache["data"]


def ist_str(dt):
    return dt.astimezone(IST).strftime("%d %b, %I:%M %p IST")


def event_details(ev):
    parts = []
    if ev.get("forecast"):
        parts.append(f"Forecast: {esc(str(ev['forecast']))}")
    if ev.get("previous"):
        parts.append(f"Previous: {esc(str(ev['previous']))}")
    return " | ".join(parts)


def check_events(state, now, events=None):
    events = fetch_events(now.timestamp()) if events is None else events
    sent = set(state["sent_alerts"])

    def mark(key):
        sent.add(key)
        state["sent_alerts"].append(key)

    for ev in events:
        try:
            if ev.get("impact") != "High" or ev.get("country") != "USD":
                continue
            title = ev["title"]
            when = datetime.fromisoformat(ev["date"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            eid = f"{title}|{ev['date']}"
            mins = (when - now).total_seconds() / 60
            details = event_details(ev)
            actual = ev.get("actual")
            impact = event_impact(title, actual, ev.get("forecast"))

            # --- pehle se alerts ---
            for i, st in enumerate(EVENT_STAGES):
                lower = EVENT_STAGES[i + 1] if i + 1 < len(EVENT_STAGES) else 0
                key = f"{eid}|{st}"
                if lower < mins <= st and key not in sent:
                    mark(key)
                    left = f"~{int(mins // 60)}h {int(mins % 60)}m" if mins >= 60 else f"~{max(1, round(mins))} min"
                    msg = (f"⏰ <b>Event Alert - starts in {STAGE_LABEL[st]}</b>\n\n"
                           f"<b>{esc(title)}</b> (USD, High Impact)\n"
                           f"🕒 {ist_str(when)} (in {left})\n")
                    if details:
                        msg += f"{details}\n"
                    msg += "\n⚠️ Gold, Silver, Bitcoin and Ethereum may see sharp moves."
                    send(msg)

            # --- event aate hi ---
            key0 = f"{eid}|0"
            if -20 < mins <= 0 and key0 not in sent:
                mark(key0)
                if impact:
                    mark(f"{eid}|result")
                    send(result_message(title, ev, impact))
                else:
                    inv = any(w in title.lower() for w in INVERSE_EVENT_WORDS)
                    up, down = ("lower", "higher") if inv else ("higher", "lower")
                    if ev.get("forecast"):
                        msg = (f"🔔 <b>Event out now: {esc(title)}</b>\n"
                               f"{details}\n\n"
                               f"I'll send the result as soon as it is available. General rule:\n"
                               f"• Actual {up} than forecast → 🔴 pressure on Gold/Crypto\n"
                               f"• Actual {down} than forecast → 🟢 support for Gold/Crypto")
                    else:
                        msg = (f"🔔 <b>Event out now: {esc(title)}</b>\n"
                               f"{details}\n\n"
                               f"No number to compare. I'm watching the news feeds and will post "
                               f"what was said and the market impact as soon as it is published.")
                    send(msg)

            # --- baad mein result aaye to ---
            keyr = f"{eid}|result"
            if impact and -180 < mins <= 0 and keyr not in sent:
                mark(keyr)
                send(result_message(title, ev, impact))
        except Exception as e:
            print("Event skip:", type(e).__name__, e)
    state["sent_alerts"] = list(dict.fromkeys(state["sent_alerts"]))


def result_message(title, ev, impact):
    emoji, text = impact
    return (f"📢 <b>{esc(title)} - Result is out</b>\n\n"
            f"Actual: <b>{esc(str(ev.get('actual')))}</b> | "
            f"Forecast: {esc(str(ev.get('forecast')))} | Previous: {esc(str(ev.get('previous')))}\n\n"
            f"📊 <b>Market impact:</b> {emoji} {esc(text)}")


# ---------------- MAIN ----------------
def run_once(state):
    now = datetime.now(timezone.utc)
    for step in (lambda: check_news(state),
                 lambda: check_events(state, now),
                 lambda: check_price(state, now)):
        try:
            step()
        except Exception as e:
            print("Step error:", type(e).__name__, e)


def main():
    state = load_state()
    end = time.time() + LOOP_SECONDS
    while True:
        run_once(state)
        save_state(state)
        if time.time() + POLL_SECONDS > end:
            break
        time.sleep(POLL_SECONDS)
    print("Run complete.")


if __name__ == "__main__":
    main()
