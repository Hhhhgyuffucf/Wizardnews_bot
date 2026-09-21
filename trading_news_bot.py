"""
Trading News Bot - Gold, Silver, Bitcoin, Ethereum
====================================================

Kya karta hai:
1) Har 5 minute mein news feeds check karta hai — jo bhi NEW headline
   Gold/Silver/Bitcoin/Ethereum se related ho, TURANT Telegram par bhej deta hai
   (duplicate dobara nahi bhejega, sirf naya jo abhi aaya)
2) Har 1 ghante mein price update bhejta hai
3) Scheduled important economic events (Fed meeting, US jobs report, CPI
   waghera jo Gold/USD ko hilate hain) ka pehle se pata hone par,
   event se 30 min PEHLE alert bhej deta hai "itne baje X event aane wala hai"

   NOTE: Ye sirf SCHEDULED events ke liye kaam karta hai (jinka time pehle se
   pata hota hai). Achanak/breaking news (jaise crash, hack, tweet) ko pehle
   se predict karna possible nahi — uske liye #1 wala turant-alert system hai.

SETUP (ek baar karna hai):
---------------------------
1) Telegram par jaake "BotFather" ko search kar, /newbot bhejo, naam do.
   Wo tujhe ek BOT_TOKEN dega (jaise: 123456789:ABCdefGhIJKlmNoPQRstuVwxyZ)

2) Apna chat_id nikalne ke liye:
   - Apne naye bot ko Telegram par ek message bhejo (jaise "Hi")
   - Browser mein ye link kholo (BOT_TOKEN apna daal ke):
     https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   - Usme "chat":{"id": XXXXXXX} milega, wahi tera CHAT_ID hai

3) Neeche BOT_TOKEN aur CHAT_ID fill kar de.

4) Terminal mein ye install kar:
   pip install requests yfinance feedparser schedule

5) Run kar:
   python trading_news_bot.py

Bas! Bot chalte rehna chahiye (laptop on ya kisi server/VPS pe deploy kar
sakta hai taaki 24x7 chale - e.g. Railway, Render, PythonAnywhere free tier).
"""

import time
import json
import os
import requests
import yfinance as yf
import feedparser
import schedule

# ---------------- CONFIG (yeh fill kar) ----------------
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
CHAT_ID = "YOUR_CHAT_ID_HERE"

# File jisme already-bheji hui news ke links save honge (duplicate rokne ke liye)
SEEN_FILE = "seen_news.json"

# Keywords jinse filter karega ki news related hai ya nahi
KEYWORDS = ["gold", "silver", "bitcoin", "btc", "ethereum", "eth", "crypto"]

# ---------------- ASSETS ----------------
TICKERS = {
    "🥇 Gold": "GC=F",
    "🥈 Silver": "SI=F",
    "₿ Bitcoin": "BTC-USD",
    "Ξ Ethereum": "ETH-USD",
}

# Jitne zyada feeds utni behtar coverage - inme se koi feed down ho to bot
# baaki feeds se kaam karta rahega
NEWS_FEEDS = {
    "Gold/Silver": "https://www.kitco.com/rss/KitcoNews.xml",
    "Crypto (CoinDesk)": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Crypto (CoinTelegraph)": "https://cointelegraph.com/rss",
    "Commodities (Investing.com)": "https://www.investing.com/rss/news_11.rss",
}


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


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    # Zyada bada na ho jaye isliye sirf last 500 rakho
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)


def check_breaking_news():
    """Har baar naya, related news milte hi turant Telegram par bhej deta hai."""
    seen = load_seen()
    new_seen = set(seen)

    for category, url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                link = entry.link
                title = entry.title

                if link in seen:
                    continue  # pehle se bheja hua hai, skip

                new_seen.add(link)

                # sirf wahi news bhejo jo gold/silver/btc/eth se related ho
                if any(kw in title.lower() for kw in KEYWORDS):
                    msg = f"🚨 *Breaking News* ({category})\n\n{title}\n\n{link}"
                    send_telegram_message(msg)
        except Exception as e:
            print(f"Error checking {category}: {e}")

    save_seen(new_seen)


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, data=payload)
    if resp.status_code != 200:
        print("Error sending message:", resp.text)
    else:
        print("Update sent successfully!")


def price_job():
    print("Sending hourly price update...")
    send_telegram_message(get_prices())


def check_economic_events():
    """
    Best-effort scheduled-event alerts (Fed meeting, jobs report, CPI, etc.)
    jo Gold/USD/Crypto ko hilate hain. Forex Factory ka unofficial calendar
    feed use karta hai - agar ye kabhi band ho jaye to sirf ye function
    fail hoga, baaki bot (news + prices) chalta rahega.
    """
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        data = requests.get(url, timeout=10).json()
    except Exception as e:
        print("Economic calendar unavailable:", e)
        return

    seen = load_seen()
    new_seen = set(seen)
    now = time.time()

    for event in data:
        try:
            if event.get("impact") != "High":
                continue
            if event.get("country") not in ("USD",):
                continue

            event_id = f"event_{event.get('title')}_{event.get('date')}"
            event_time = time.mktime(time.strptime(event["date"], "%Y-%m-%dT%H:%M:%S%z")) \
                if "T" in event.get("date", "") else None

            if not event_time or event_id in seen:
                continue

            minutes_away = (event_time - now) / 60
            if 0 < minutes_away <= 300:
                new_seen.add(event_id)
                hours = int(minutes_away // 60)
                mins = int(minutes_away % 60)
                time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} min"
                msg = (f"⏰ *Event Alert*\n\n{event['title']} (USD, High Impact)\n"
                       f"~{time_str} mein aane wala hai\n"
                       f"Gold/Crypto pe asar ho sakta hai, dhyan rakhna!")
                send_telegram_message(msg)
        except Exception:
            continue

    save_seen(new_seen)


if __name__ == "__main__":
    print("Trading News Bot started. Sending first update now...")
    price_job()
    check_breaking_news()  # pehli baar purani news ko 'seen' mark karega, spam nahi karega

    # News: har 1 minute check karega, jaise hi naya milta hai turant bhej dega
    schedule.every(1).minutes.do(check_breaking_news)

    # Price: har ghante ek update
    schedule.every(1).hours.do(price_job)

    # Economic events: har 15 min check karega ki koi bada event 30 min mein to nahi
    schedule.every(15).minutes.do(check_economic_events)

    while True:
        schedule.run_pending()
        time.sleep(30)
