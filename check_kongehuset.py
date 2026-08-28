import json
import os
import re
import time
import requests
import feedparser
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RSS_URL = "https://www.kongehuset.no/for-pressen/rss"
STATE_FILE = "seen_press_releases.json"

# Støtter opptil tre webhooks
SLACK_WEBHOOK_URL_1 = os.environ.get("SLACK_WEBHOOK_URL_1") or os.environ.get("SLACK_WEBHOOK_URL")
SLACK_WEBHOOK_URL_2 = os.environ.get("SLACK_WEBHOOK_URL_2")
SLACK_WEBHOOK_URL_3 = os.environ.get("SLACK_WEBHOOK_URL_3")
SLACK_WEBHOOK_URL_4 = os.environ.get("SLACK_WEBHOOK_URL_4")

def get_latest_pressemeldinger():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    response = requests.get(RSS_URL, headers=headers, timeout=30, verify=False)
    response.raise_for_status()
    
    feed = feedparser.parse(response.content)
    results = []
    
    for entry in feed.entries:
        results.append({
            "title": entry.get("title", "Pressemelding"),
            "url": entry.get("link", ""),
            "date": entry.get("published", ""),
            "ingress": entry.get("summary", "")
        })
        
    return results[:3]

def send_to_slack(item):
    title = item["title"]
    url = item["url"]
    date = item.get("date", "")
    ingress = item.get("ingress", "")

    message_lines = ["👑 *Ny pressemelding fra Kongehuset!*"]
    
    if date:
        message_lines.append(f"🗓️ _{date}_")
        
    message_lines.append(f"📌 *<{url}|{title}>*")
    
    if ingress:
        message_lines.append(f">{ingress}")

    payload = {"text": "\n".join(message_lines)}
    
    # Henter alle definerte webhooks
    webhooks = [w for w in [SLACK_WEBHOOK_URL_1, SLACK_WEBHOOK_URL_2, SLACK_WEBHOOK_URL_3, SLACK_WEBHOOK_URL_4] if w]

    if not webhooks:
        print("⚠️ Ingen Slack-webhooks er konfigurert!")
        return

    for webhook in webhooks:
        try:
            res = requests.post(webhook, json=payload, timeout=10)
            res.raise_for_status()
            print(f"✅ Sendt til Slack ({webhook[:35]}...)")
        except Exception as e:
            print(f"❌ Feil ved sending til Slack ({webhook[:35]}...): {e}")

def main():
    seen_urls = []
    
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                seen_urls = json.load(f)
        except json.JSONDecodeError:
            print("⚠️ Filen var tom eller korrupt. Starter med blanke ark.")
            seen_urls = []
            
    current_items = get_latest_pressemeldinger()
    new_seen = list(seen_urls)
    
    for item in current_items:
        if item["url"] not in seen_urls:
            print(f"Ny pressemelding funnet: {item['title']}")
            send_to_slack(item)
            new_seen.append(item["url"])
            
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(new_seen, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    while True:
        print("Sjekker Kongehuset for nye pressemeldinger...")
        try:
            main()
        except Exception as e:
            print(f"Feil under kjøring: {e}")
        
        print("Venter 15 sekunder før neste sjekk...")
        time.sleep(15)
