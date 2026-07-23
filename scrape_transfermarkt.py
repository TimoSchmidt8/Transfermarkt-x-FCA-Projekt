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

# NEU: Generiert automatisch ALLE Saisons seit der Einführung der Marktwerte (2004) bis heute
SAISONS = {f"{year}_{year+1}": str(year) for year in range(2004, 2026)}

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
        database="football_game",
    )

def setup_database_automatically():
    """Reißt die kaputte Tabelle ab und baut sie mit Duplikat-Schutz neu auf."""
    conn = get_connection()
    cursor = conn.cursor()
    print("-> Bereinige und strukturiere Datenbank automatisch...")
    
    cursor.execute("DROP TABLE IF EXISTS bl_market_values")
    
    cursor.execute("""
        CREATE TABLE bl_market_values (
            tm_id INT,
            market_value_eur BIGINT,
            last_updated DATE,
            saison VARCHAR(20),
            UNIQUE KEY unique_player_date (tm_id, last_updated)
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()
    print("-> Datenbank-Setup abgeschlossen. Tabelle ist jetzt perfekt.")

def get_soup(url):
    while True:
        try:
            response = http_session.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            return BeautifulSoup(response.text, "lxml")
        except (requests.exceptions.RetryError, requests.exceptions.HTTPError) as e:
            if "502" in str(e) or "504" in str(e):
                print("  Transfermarkt blockt (502/504). Warte 120 Sekunden...")
                time.sleep(120)
            else:
                raise e

def fetch_all_market_values(player_id):
    """Holt die Historie und berechnet die Saison direkt aus dem Datum."""
    url = f"{BASE_URL}/ceapi/marketValueDevelopment/graph/{player_id}"
    try:
        response = http_session.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            entries = response.json().get("list", [])
            history = []
            for entry in entries:
                raw_date = entry.get("datum_mw")
                if raw_date and "." in raw_date:
                    parts = raw_date.split(".")
                    if len(parts) == 3:
                        day = int(parts[0])
                        month = int(parts[1])
                        year = int(parts[2])
                        
                        formatted_date = f"{year}-{month:02d}-{day:02d}"
                        
                        if month >= 7:
                            saison_calc = f"{year}_{year+1}"
                        else:
                            saison_calc = f"{year-1}_{year}"

                        history.append({
                            "value": entry.get("y"), 
                            "date": formatted_date,
                            "saison": saison_calc
                        })
            return history
    except Exception:
        pass
    return []

def get_bundesliga_clubs():
    url = f"{BASE_URL}/bundesliga/startseite/wettbewerb/L1"
    soup = get_soup(url)
    clubs = []
    table = soup.select_one("#yw1 table.items tbody")
    if not table:
        return clubs
    for row in table.select("tr"):
        link_tag = row.select_one("td.hauptlink a")
        if link_tag:
            club_url = link_tag.get("href")
            clubs.append(club_url)
    return list(set(clubs))

def save_market_values(player_id, history):
    """Speichert Marktwert, Datum und Saison fehlerfrei ab."""
    if not history:
        return
    conn = get_connection()
    cursor = conn.cursor()
    for entry in history:
        cursor.execute("""
            INSERT INTO bl_market_values (tm_id, market_value_eur, last_updated, saison)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                market_value_eur=VALUES(market_value_eur),
                saison=VALUES(saison)
        """, (player_id, entry['value'], entry['date'], entry['saison']))
    conn.commit()
    cursor.close()
    conn.close()

def main():
    print("Starte Marktwert-Scraper (Alle Spieler seit 2004)...")
    
    setup_database_automatically()
    
    club_urls = get_bundesliga_clubs()
    processed_players = set()
    
    for saison_name, saison_id in SAISONS.items():
        print(f"\n=== SUCHE SPIELER IN SAISON {saison_name} ===")
        for club_url in club_urls:
            squad_url = f"{BASE_URL}{club_url.replace('/startseite/verein/', '/kader/verein/')}/saison_id/{saison_id}"
            squad_soup = get_soup(squad_url)
            table = squad_soup.find("table", class_="items")
            if not table:
                continue
                
            for row in table.find_all("tr", class_=["odd", "even"]):
                link = row.find("a", href=re.compile(r"/profil/spieler/"))
                if not link:
                    continue
                p_id = re.search(r"spieler/(\d+)", link['href']).group(1)
                
                if p_id in processed_players:
                    continue
                    
                history = fetch_all_market_values(p_id)
                try:
                    save_market_values(p_id, history)
                    processed_players.add(p_id)
                    print(f"  -> Spieler ID {p_id} | {len(history)} Marktwerte (inkl. Saison) perfekt gespeichert")
                    time.sleep(1) # Etwas schneller gemacht
                except Exception as e:
                    print(f"  Fehler beim Speichern von Spieler {p_id}: {e}")
                    time.sleep(5)

if __name__ == "__main__":
    main()
