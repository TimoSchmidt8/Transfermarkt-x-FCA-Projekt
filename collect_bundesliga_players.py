import re
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

BASE_URL = "https://www.transfermarkt.de"
BUNDESLIGA_URL = "https://www.transfermarkt.de/bundesliga/startseite/wettbewerb/L1"

def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")

def get_player_details(player_id):
    """Holt Name, Position und Rückennummer von der Profilseite."""
    url = f"{BASE_URL}/-/profil/spieler/{player_id}"
    soup = get_soup(url)
    
    # Position finden
    position = "Unknown"
    pos_label = soup.find("th", string="Position:")
    if pos_label:
        position = pos_label.find_next_sibling("td").get_text(strip=True)
    
    # Name
    name = soup.find("h1").get_text(strip=True) if soup.find("h1") else "Unknown"
    
    return {"name": name, "position": position}

def collect_club_urls():
    soup = get_soup(BUNDESLIGA_URL)
    return sorted(list(set([BASE_URL + link.get("href").split("?")[0] for link in soup.select("a[href*='/startseite/verein/']") if "/startseite/verein/" in link.get("href")])))

def main():
    club_urls = collect_club_urls()
    
    with open("bundesliga_players_data.csv", "w", encoding="utf-8") as f:
        f.write("player_id,name,position,club_url\n")
        
       for index, club_url in enumerate(club_urls, start=1):
        squad_url = club_url.replace("/startseite/verein/", "/kader/verein/")
        print(f"[{index}/{len(club_urls)}] Scrape Kader: {squad_url}")

        # Warte-Logik für den gesamten Kader-Abruf
        success = False
        while not success:
            try:
                player_ids = collect_player_ids_from_squad(squad_url)
                print(f"  Spieler gefunden: {len(player_ids)}")
                all_player_ids.update(player_ids)
                success = True # Erfolg, weiter zum nächsten Club
            except Exception as e:
                error_msg = str(e)
                if "502" in error_msg or "504" in error_msg:
                    print("  Server-Fehler. Warte 60 Sekunden und versuche erneut...")
                    time.sleep(60) # Pause bei Server-Überlastung
                else:
                    print(f"  Kritischer Fehler bei {squad_url}: {e}")
                    break # Bei anderen Fehlern abbrechen, um Hänger zu vermeiden

        time.sleep(10)

if __name__ == "__main__":
    main()
