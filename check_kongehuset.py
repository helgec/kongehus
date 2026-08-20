import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

URL = "https://www.kongehuset.no/for-pressen"
STATE_FILE = "seen_press_releases.json"

# Henter to potensielle webhooks fra miljøvariablene
SLACK_WEBHOOK_URL_1 = os.environ.get("SLACK_WEBHOOK_URL_1")
SLACK_WEBHOOK_URL_2 = os.environ.get("SLACK_WEBHOOK_URL_2")

# ... (behold get_latest_pressemeldinger() som den er, husk timeout=30) ...

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

    payload = {
        "text": "\n".join(message_lines)
    }
    
    # Send til første webhook hvis den er definert
    if SLACK_WEBHOOK_URL_1:
        try:
            requests.post(SLACK_WEBHOOK_URL_1, json=payload, timeout=20)
        except Exception as e:
            print(f"Feil ved sending til webhook 1: {e}")
            
    # Send til andre webhook hvis den er definert
    if SLACK_WEBHOOK_URL_2:
        try:
            requests.post(SLACK_WEBHOOK_URL_2, json=payload, timeout=20)
        except Exception as e:
            print(f"Feil ved sending til webhook 2: {e}")

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
            # Kaller send_to_slack så lenge minst én webhook er definert
            if SLACK_WEBHOOK_URL_1 or SLACK_WEBHOOK_URL_2:
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
        
        print("Venter 45 sekunder før neste sjekk...")
        time.sleep(45)
