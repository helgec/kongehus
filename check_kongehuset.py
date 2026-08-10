import json
import os
import re
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
    
    # Regex for å kjenne igjen datoer som "10. august 2026"
    date_pattern = re.compile(r'\b\d{1,2}\.\s+[a-zæøåA-ZÆØÅ]+\s+\d{4}\b')
    
    # Finn alle lenker med teksten "Gå til pressemelding"
    for a in soup.find_all("a", href=True):
        if "gå til pressemelding" in a.get_text(strip=True).lower():
            href = a["href"]
            full_url = href if href.startswith("http") else f"https://www.kongehuset.no{href}"
            
            # Gå oppover i HTML-treet til vi finner hele raden/kortet
            card = a.parent
            while card and card.name not in ["body", "html"]:
                if card.find(["h1", "h2", "h3", "h4", "h5"]):
                    break
                card = card.parent
                
            if not card:
                card = a.parent

            # 1. Hent tittel
            heading_el = card.find(["h1", "h2", "h3", "h4", "h5"])
            title = heading_el.get_text(strip=True) if heading_el else "Pressemelding"

            # 2. Hent dato og ingress fra tekstene i kortet
            date_str = ""
            ingress_parts = []
            
            for elem in card.find_all(["p", "div", "span", "time"]):
                text = elem.get_text(strip=True)
                
                # Hopp over tittelen, lenketeksten og tomme felter
                if not text or text == title or "gå til pressemelding" in text.lower():
                    continue
                    
                # Sjekk om teksten inneholder en dato
                date_match = date_pattern.search(text)
                if date_match:
                    date_str = date_match.group(0)
                    # Fjern datoen hvis den var del av en lengre tekst
                    clean_text = date_pattern.sub('', text).strip()
                    if clean_text and clean_text not in ingress_parts:
                        ingress_parts.append(clean_text)
                else:
                    # Legg til som ingress/brødtekst hvis den ikke allerede er lagt til
                    if text not in ingress_parts and not any(text in p for p in ingress_parts):
                        ingress_parts.append(text)

            ingress = " ".join(ingress_parts).strip()

            # Unngå duplikater
            if not any(item["url"] == full_url for item in results):
                results.append({
                    "title": title,
                    "url": full_url,
                    "date": date_str,
                    "ingress": ingress
                })
                
    return results

def send_to_slack(item):
    title = item["title"]
    url = item["url"]
    date = item.get("date", "")
    ingress = item.get("ingress", "")

    # Bygger en pen Slack-melding
    message_lines = ["👑 *Ny pressemelding fra Kongehuset!*"]
    
    if date:
        message_lines.append(f"🗓️ _{date}_")
        
    message_lines.append(f"📌 *<{url}|{title}>*")
    
    if ingress:
        message_lines.append(f">{ingress}")

    payload = {
        "text": "\n".join(message_lines)
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
                send_to_slack(item)
            new_seen.append(item["url"])
            
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(new_seen, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
