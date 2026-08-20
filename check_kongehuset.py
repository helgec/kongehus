import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

URL = "https://www.kongehuset.no/for-pressen"
STATE_FILE = "seen_press_releases.json"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

def get_latest_pressemeldinger():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    response = requests.get(URL, headers=headers, timeout=10)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    
    date_pattern = re.compile(r'\b\d{1,2}\.\s+[a-zæøåA-ZÆØÅ]+\s+\d{4}\b')
    
    for a in soup.find_all("a", href=True):
        if "gå til pressemelding" in a.get_text(strip=True).lower():
            href = a["href"]
            full_url = href if href.startswith("http") else f"https://www.kongehuset.no{href}"
            
            card = a.parent
            while card and card.name not in ["body", "html"]:
                if card.find(["h1", "h2", "h3", "h4", "h5"]):
                    break
                card = card.parent
                
            if not card:
                card = a.parent

            heading_el = card.find(["h1", "h2", "h3", "h4", "h5"])
            title = heading_el.get_text(strip=True) if heading_el else "Pressemelding"

            date_str = ""
            ingress_parts = []
            
            for elem in card.find_all(["p", "div", "span", "time"]):
                text = elem.get_text(strip=True)
                
                if not text or text == title or "gå til pressemelding" in text.lower():
                    continue
                    
                date_match = date_pattern.search(text)
                if date_match:
                    date_str = date_match.group(0)
                    clean_text = date_pattern.sub('', text).strip()
                    if clean_text and clean_text not in ingress_parts:
                        ingress_parts.append(clean_text)
                else:
                    if text not in ingress_parts and not any(text in p for p in ingress_parts):
                        ingress_parts.append(text)

            ingress = " ".join(ingress_parts).strip()

            if not any(item["url"] == full_url for item in results):
                results.append({
                    "title": title,
                    "url": full_url,
                    "date": date_str,
                    "ingress": ingress
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

    payload = {
        "text": "\n".join(message_lines)
    }
    
    response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    response.raise_for_status()

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
            if SLACK_WEBHOOK_URL:
                try:
                    send_to_slack(item)
                    new_seen.append(item["url"])
                except Exception as e:
                    print(f"❌ Kunne ikke sende til Slack: {e}")
            else:
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
        
        print("Venter 15 minutter før neste sjekk...")
        time.sleep(900)  # 900 sekunder = 15 minutter
