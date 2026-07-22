import re
import time
import requests
import mysql.connector
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# Konfiguration
BASE_URL = "https://www.transfermarkt.de"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"}
SAISONS = {"2022_2023": "2022", "2023_2024": "2023", "2024_2025": "2024", "2025_2026": "2025"}

def get_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "football_game"),
    )

def get_soup(url):
    while True:
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            return BeautifulSoup(response.text, "lxml")
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            print("  Timeout... warte 60s")
            time.sleep(60)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [429, 502, 503, 504]:
                print("  Blockade/Fehler... warte 120s")
                time.sleep(120)
            else:
                raise

def get_player_details(player_id):
    url = f"{BASE_URL}/-/profil/spieler/{player_id}"
    soup = get_soup(url)
    
    # 1. POSITION: 
    position = "Unknown"
    labels = soup.find_all(lambda tag: tag.name in ['span', 'td', 'th'] and "Position:" in tag.get_text())
    
    for label in labels:
        content = label.find_next_sibling(['span', 'td'])
        if content and len(content.get_text(strip=True)) > 2:
            position = content.get_text(strip=True)
            break
            
    # 2. NAME & SHIRT NUMBER
    raw_name = soup.find("h1").get_text(strip=True) if soup.find("h1") else "Unknown"
    match = re.match(r"^#?(\d+)", raw_name)
    shirt_number = match.group(1) if match else None
    name = re.sub(r"^#?\d+\s*", "", raw_name)

    # 3. GEBURTSDATUM & ALTER
    date_of_birth = None
    age = None
    dob_tag = soup.find(attrs={"itemprop": "birthDate"})
    if dob_tag:
        dob_text = dob_tag.get_text(strip=True)
        date_text = dob_text.split('(')[0].strip()
        
        # Regex-Check: Sichert ab, dass es wirklich ein Datum aus Zahlen ist (verhindert den "k.A." Fehler)
        date_match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', date_text)
        if date_match:
            date_of_birth = f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}"
        
        # Alter extrahieren (steht in Klammern)
        age_match = re.search(r'\((\d+)\)', dob_text)
        if age_match:
            age = int(age_match.group(1))
    
    print(f"DEBUG: Spieler-ID {player_id} | Name: {name} | Position: {position} | DOB: {date_of_birth} | Age: {age}")
    
    return {
        "name": name, 
        "position": position, 
        "shirt_number": shirt_number,
        "date_of_birth": date_of_birth,
        "age": age
    }

def save_to_db(p_id, details, saison_name, club_name):
    conn = get_connection()
    cursor = conn.cursor()
    
    print(f"DEBUG DB: Speichere {p_id} in Saison {saison_name}")
    
    cursor.execute("""
        INSERT INTO bl_players (tm_id, full_name_tm, position, saison, club_name, shirt_number, date_of_birth, age)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE 
            full_name_tm = VALUES(full_name_tm),
            position = VALUES(position),
            club_name = VALUES(club_name),
            shirt_number = VALUES(shirt_number),
            saison = VALUES(saison),
            date_of_birth = VALUES(date_of_birth),
            age = VALUES(age)
    """, (p_id, details['name'], details['position'], saison_name, club_name, details['shirt_number'], details['date_of_birth'], details['age']))
    
    conn.commit()
    cursor.close()
    conn.close()

def main():
    print("Sammle Bundesliga-Vereine...")
    url = f"{BASE_URL}/bundesliga/startseite/wettbewerb/L1"
    soup = get_soup(url)
    
    clubs_data = []
    for link in soup.select("a[href*='/startseite/verein/']"):
        if "/startseite/verein/" in link.get("href"):
            name = link.get("title", "Unknown Club")
            club_url = BASE_URL + link.get("href")
            clubs_data.append({"name": name, "url": club_url})
    
    clubs_data = {c['name']: c for c in clubs_data}.values()

    for saison_name, saison_id in SAISONS.items():
        print(f"=== STARTE SAISON {saison_name} ===")
        
        for club in clubs_data: 
            squad_url = club['url'].replace("/startseite/verein/", "/kader/verein/").split("/saison_id/")[0] + f"/saison_id/{saison_id}"
            
            squad_soup = get_soup(squad_url)
            table = squad_soup.find("table", class_="items")
            if not table:
                continue
                
            for row in table.find_all("tr", class_=["odd", "even"]):
                num_cell = row.find("td", class_="zentriert")
                raw_number = num_cell.get_text(strip=True) if num_cell else None
                shirt_number = int(raw_number) if raw_number and raw_number.isdigit() else None
                
                link = row.find("a", href=re.compile(r"/profil/spieler/"))
                if link:
                    p_id = re.search(r"spieler/(\d+)", link['href']).group(1)
                    
                    details = get_player_details(p_id)
                    details['shirt_number'] = shirt_number
                    
                    save_to_db(p_id, details, saison_name, club['name'])
                    print(f"  Gespeichert: {details['name']} (Nr. {shirt_number}) bei {club['name']}")
                    
                    time.sleep(2)

if __name__ == "__main__":
    main()