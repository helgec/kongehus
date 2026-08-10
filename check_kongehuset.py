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
    
    # 1. Finn overskriften som heter/inneholder "Pressemeldinger"
    target_heading = None
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        if "pressemeldinger" in heading.get_text().lower():
            target_heading = heading
            break
            
    if not target_heading:
        print("Fant ikke overskriften 'Pressemeldinger'.")
        return results

    # 2. Hent seksjonen/containeren som overskriften ligger i
    search_area = target_heading.find_parent(["section", "article", "div"])
    if not search_area:
        search_area = target_heading.parent

    # 3. Hent KUN lenkene som ligger inne i denne spesifikke seksjonen
    for a in search_area.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(strip=True)
        
        # Filtrer vekk selve overskriften og generiske knapper
        if not title or title.lower() in ["pressemeldinger", "se alle pressemeldinger", "les mer", "se alle"]:
            continue
            
        # Filtrer ut altfor korte tekster (f.eks. piler eller ikoner)
        if len(title) < 5:
            continue

        full_url = href if href.startswith("http") else f"https://www.kongehuset.no{href}"
        
        # Sjekk at vi ikke legger til duplikater i listen
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
