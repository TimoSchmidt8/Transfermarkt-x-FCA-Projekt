import os
import re
import time
import requests
import mysql.connector
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

BASE_URL = "https://www.transfermarkt.de"
CURRENT_SEASON = "2023/2024" # Anpassen, falls nötig

def get_robust_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

http_session = get_robust_session()

def get_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "football_game"),
    )

def get_soup(url):
    response = http_session.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")

def fetch_current_market_value(player_id):
    """Holt nur den aktuellsten Marktwert aus der API und konvertiert das Datum für MySQL."""
    url = f"{BASE_URL}/ceapi/marketValueDevelopment/graph/{player_id}"
    try:
        response = http_session.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            entries = response.json().get("list", [])
            if entries:
                last_entry = entries[-1] # Der letzte/aktuellste Eintrag
                raw_date = last_entry.get("datum_mw")
                
                # Konvertiere '27.05.2026' zu '2026-05-27'
                if raw_date and "." in raw_date:
                    parts = raw_date.split(".")
                    if len(parts) == 3:
                        raw_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
                        
                return {
                    "value": last_entry.get("y"),
                    "date": raw_date
                }
    except Exception:
        pass
    return {"value": None, "date": None}

def get_bundesliga_clubs():
    """Schritt 1: Sammelt die 18 aktuellen Bundesliga-Vereine."""
    print("Rufe Bundesliga-Clubs ab...")
    url = f"{BASE_URL}/bundesliga/startseite/wettbewerb/L1"
    soup = get_soup(url)
    
    clubs = []
    # Sucht die Links zu den Vereinen in der Ligatabelle
    table = soup.select_one("#yw1 table.items tbody")
    if not table:
        return clubs
        
    for row in table.select("tr"):
        link_tag = row.select_one("td.hauptlink a")
        if link_tag:
            club_name = link_tag.get("title", link_tag.get_text(strip=True))
            club_url = link_tag.get("href")
            clubs.append({"name": club_name, "url": club_url})
            
    # Duplikate filtern, da Transfermarkt Links manchmal doppelt hat
    seen = set()
    unique_clubs = []
    for c in clubs:
        if c["name"] not in seen:
            seen.add(c["name"])
            unique_clubs.append(c)
            
    print(f"{len(unique_clubs)} Vereine gefunden.")
    return unique_clubs

def get_players_from_club(club_url):
    """Schritt 2: Sammelt alle Spieler-IDs aus dem aktuellen Kader des Vereins."""
    full_url = f"{BASE_URL}{club_url}"
    soup = get_soup(full_url)
    
    player_ids = set()
    for link in soup.select("table.items td.hauptlink a"):
        href = link.get("href", "")
        if "/profil/spieler/" in href:
            # ID aus der URL extrahieren (die Zahlen am Ende)
            match = re.search(r'/spieler/(\d+)', href)
            if match:
                player_ids.add(int(match.group(1)))
                
    return list(player_ids)

def scrape_player_details(player_id, club_name):
    """Schritt 3: Holt die exakten Matching-Daten vom Spielerprofil."""
    profile_url = f"{BASE_URL}/-/profil/spieler/{player_id}"
    profile_soup = get_soup(profile_url)

    # Name und Trikotnummer
    name_tag = profile_soup.find("h1")
    raw_name = name_tag.get_text(" ", strip=True) if name_tag else f"Unknown {player_id}"

    shirt_number = None
    number_match = re.match(r"^#(\d+)\s+", raw_name)
    if number_match:
        shirt_number = int(number_match.group(1))

    full_name_tm = re.sub(r"^#\d+\s+", "", raw_name).strip()

    # Geburtsdatum (Der robuste SEO-Trick)
    date_of_birth = None
    dob_tag = profile_soup.find(attrs={"itemprop": "birthDate"})
    if dob_tag:
        date_text = dob_tag.get_text(strip=True).split('(')[0].strip()
        parts = date_text.split('.')
        if len(parts) == 3:
            date_of_birth = f"{parts[2]}-{parts[1]}-{parts[0]}"
            
    if not date_of_birth:
        match = re.search(r'"birthDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"', profile_soup.text)
        if match:
            date_of_birth = match.group(1)

    # Aktueller Marktwert
    mv_data = fetch_current_market_value(player_id)

    return {
        "tm_id": player_id,
        "club_name": club_name,
        "shirt_number": shirt_number,
        "full_name_tm": full_name_tm,
        "date_of_birth": date_of_birth,
        "market_value_eur": mv_data["value"],
        "last_updated": mv_data["date"]
    }

def save_to_db(data):
    """Schritt 4: Speichert die Daten sicher in die neuen Tabellen."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Spieler in bl_players
    cursor.execute("""
        INSERT INTO bl_players (tm_id, club_name, shirt_number, full_name_tm, date_of_birth)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            club_name = VALUES(club_name),
            shirt_number = VALUES(shirt_number),
            full_name_tm = VALUES(full_name_tm),
            date_of_birth = VALUES(date_of_birth)
    """, (
        data["tm_id"], data["club_name"], data["shirt_number"], 
        data["full_name_tm"], data["date_of_birth"]
    ))

    # 2. Marktwert in bl_market_values
    if data["market_value_eur"]:
        cursor.execute("""
            INSERT INTO bl_market_values (tm_id, season, market_value_eur, last_updated)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                market_value_eur = VALUES(market_value_eur),
                last_updated = VALUES(last_updated)
        """, (
            data["tm_id"], CURRENT_SEASON, data["market_value_eur"], data["last_updated"]
        ))

    conn.commit()
    cursor.close()
    conn.close()

def main():
    clubs = get_bundesliga_clubs()
    
    for club in clubs:
        print(f"\n--- Scrape Kader von: {club['name']} ---")
        player_ids = get_players_from_club(club['url'])
        
        for idx, p_id in enumerate(player_ids, start=1):
            try:
                data = scrape_player_details(p_id, club['name'])
                save_to_db(data)
                
                mv_str = f"€{data['market_value_eur']}" if data['market_value_eur'] else "Kein MW"
                print(f"  [{idx}/{len(player_ids)}] Gespeichert: {data['full_name_tm']} (#{data['shirt_number']}) | {mv_str}")
                
                time.sleep(2.5) # Respektvolle Pause für Transfermarkt
                
            except Exception as e:
                print(f"  Fehler bei Spieler {p_id}: {e}")
                time.sleep(5)

if __name__ == "__main__":
    main()