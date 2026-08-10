import json
import os
import requests
from bs4 import BeautifulSoup

URL = "https://www.kongehuset.no/for-pressen"
STATE_FILE = "seen_press_releases.json"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

def get_latest_pressemeldinger():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    response = requests.get(URL, headers=headers)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    
    # Finn alle lenker hvor teksten inneholder "Gå til pressemelding"
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True).lower()
        
        if "gå til pressemelding" in link_text:
            href = a["href"]
            full_url = href if href.startswith("http") else f"https://www.kongehuset.no{href}"
            
            # Finn boksen/kortet som denne lenken ligger i
            card = a.find_parent(["article", "div", "li"])
            title = None
            
            if card:
                # Hent overskriften som ligger i samme boks
                heading = card.find(["h2", "h3", "h4", "h5", "strong"])
                if heading:
                    title = heading.get_text(strip=True)
            
            # Fallback dersom overskriften ikke ble funnet i kortet
            if not title:
                title = "Pressemelding fra Kongehuset"
            
            # Unngå duplikater
            if not any(item["url"] == full_url for item in results):
                results.append({"title": title, "url": full_url})
                
    return results

def send_to_slack(title, url):
    payload = {
        "text": f"👑 *Ny pressemelding fra Kongehuset!*\n*<{url}|{title}>*"
    }
    response = requests.post(SLACK_WEBHOOK_URL, json=payload)
    response.raise_for_status()

def main():
    seen_urls = []
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            seen_urls = json.load(f)
            
    current_items = get_latest_pressemeldinger()
    new_seen = list(seen_urls)
    
    for item in current_items:
        if item["url"] not in seen_urls:
            print(f"Ny pressemelding funnet: {item['title']}")
            if SLACK_WEBHOOK_URL:
                send_to_slack(item["title"], item["url"])
            new_seen.append(item["url"])
            
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(new_seen, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
