"""
Trading News Bot - Gold, Silver, Bitcoin, Ethereum
====================================================
GitHub Actions version - is version ko GitHub Actions har 5 minute mein
khud-ba-khud chalata hai (24x7, laptop/phone ki zaroorat nahi).

Har run mein ye karta hai:
1) News feeds check karta hai - jo NAYA Gold/Silver/Bitcoin/Ethereum
   related headline mile, turant Telegram bhej deta hai
2) Har ghante mein ek baar price update bhejta hai
3) Bade scheduled economic events (Fed, jobs report, CPI) ka
   5 ghante pehle alert bhejta hai

State (kaunsi news pehle bhej chuke hain, last price kab bheja) ek
JSON file (bot_state.json) mein save hota hai jo GitHub repo mein
hi wapas commit ho jaati hai, taaki agli baar duplicate na bheje.
"""

import time
import json
import os
import requests
import yfinance as yf
import feedparser

# ---------------- CONFIG ----------------
# GitHub Actions mein ye secrets se (environment variables) aate hain
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID_HERE")

STATE_FILE = "bot_state.json"
KEYWORDS = ["gold", "silver", "bitcoin", "btc", "ethereum", "eth", "crypto"]

TICKERS = {
    "🥇 Gold": "GC=F",
    "🥈 Silver": "SI=F",
    "₿ Bitcoin": "BTC-USD",
    "Ξ Ethereum": "ETH-USD",
}

NEWS_FEEDS = {
    "Gold/Silver": "https://www.kitco.com/rss/KitcoNews.xml",
    "Crypto (CoinDesk)": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Crypto (CoinTelegraph)": "https://cointelegraph.com/rss",
    "Commodities (Investing.com)": "https://www.investing.com/rss/news_11.rss",
}


def load_state():
    default = {"seen_links": [], "seen_events": [], "last_price_hour": None}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                for k, v in default.items():
                    data.setdefault(k, v)
                return data
        except Exception:
            return default
    return default


def save_state(state):
    state["seen_links"] = state["seen_links"][-500:]
    state["seen_events"] = state["seen_events"][-200:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, data=payload, timeout=15)
    if resp.status_code != 200:
        print("Error sending message:", resp.text)
    else:
        print("Update sent successfully!")


def get_prices():
    lines = ["📊 *Price Update*\n"]
    for name, ticker in TICKERS.items():
        try:
            data = yf.Ticker(ticker).history(period="1d", interval="5m")
            if data.empty:
                lines.append(f"{name}: data unavailable")
                continue
            last = data["Close"].iloc[-1]
            first = data["Close"].iloc[0]
            change = last - first
            pct = (change / first) * 100
            arrow = "🟢" if change >= 0 else "🔴"
            lines.append(f"{name}: ${last:,.2f}  {arrow} {pct:+.2f}%")
        except Exception as e:
            lines.append(f"{name}: error fetching ({e})")
    return "\n".join(lines)


def check_price(state):
    """Har ghante mein sirf ek baar price bhejta hai."""
    current_hour = time.strftime("%Y-%m-%d-%H", time.gmtime())
    if state.get("last_price_hour") == current_hour:
        return  # is ghante mein already bhej chuke
    send_telegram_message(get_prices())
    state["last_price_hour"] = current_hour


def check_breaking_news(state):
    seen = set(state["seen_links"])
    for category, url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                link = entry.link
                title = entry.title
                if link in seen:
                    continue
                seen.add(link)
                state["seen_links"].append(link)
                if any(kw in title.lower() for kw in KEYWORDS):
                    msg = f"🚨 *Breaking News* ({category})\n\n{title}\n\n{link}"
                    send_telegram_message(msg)
        except Exception as e:
            print(f"Error checking {category}: {e}")


def check_economic_events(state):
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        data = requests.get(url, timeout=10).json()
    except Exception as e:
        print("Economic calendar unavailable:", e)
        return

    seen = set(state["seen_events"])
    now = time.time()

    for event in data:
        try:
            if event.get("impact") != "High" or event.get("country") != "USD":
                continue

            event_id = f"event_{event.get('title')}_{event.get('date')}"
            if event_id in seen or "T" not in event.get("date", ""):
                continue

            event_time = time.mktime(time.strptime(event["date"], "%Y-%m-%dT%H:%M:%S%z"))
            minutes_away = (event_time - now) / 60

            if 0 < minutes_away <= 300:
                seen.add(event_id)
                state["seen_events"].append(event_id)
                hours = int(minutes_away // 60)
                mins = int(minutes_away % 60)
                time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} min"
                msg = (f"⏰ *Event Alert*\n\n{event['title']} (USD, High Impact)\n"
                       f"~{time_str} mein aane wala hai\n"
                       f"Gold/Crypto pe asar ho sakta hai, dhyan rakhna!")
                send_telegram_message(msg)
        except Exception:
            continue


if __name__ == "__main__":
    state = load_state()
    check_breaking_news(state)
    check_economic_events(state)
    check_price(state)
    save_state(state)
    print("Run complete.")
