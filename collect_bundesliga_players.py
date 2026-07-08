import re
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

BASE_URL = "https://www.transfermarkt.de"
BUNDESLIGA_URL = "https://www.transfermarkt.de/bundesliga/startseite/wettbewerb/L1"


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=20)
    print("URL:", url)
    print("Status:", response.status_code)
    if response.status_code != 200:
        print(response.text[:500])
    response.raise_for_status()

    return BeautifulSoup(response.text, "lxml")


def collect_club_urls():
    soup = get_soup(BUNDESLIGA_URL)

    club_urls = set()

    for link in soup.select("a[href*='/startseite/verein/']"):
        href = link.get("href")
        if not href:
            continue

        # Beispiel:
        # /fc-bayern-munchen/startseite/verein/27
        if "/startseite/verein/" in href:
            full_url = BASE_URL + href.split("?")[0]
            club_urls.add(full_url)

    return sorted(club_urls)


def squad_url_from_club_url(club_url):
    # aus /startseite/verein/27 wird /kader/verein/27
    return club_url.replace("/startseite/verein/", "/kader/verein/")


def collect_player_ids_from_squad(squad_url):
    soup = get_soup(squad_url)

    player_ids = set()

    for link in soup.select("a[href*='/profil/spieler/']"):
        href = link.get("href")
        if not href:
            continue

        match = re.search(r"/profil/spieler/(\d+)", href)
        if match:
            player_ids.add(match.group(1))

    return player_ids


def main():
    print("Sammle Bundesliga-Clubs...")
    club_urls = collect_club_urls()

    print(f"Gefundene Clubs: {len(club_urls)}")

    all_player_ids = set()

    for index, club_url in enumerate(club_urls, start=1):
        squad_url = squad_url_from_club_url(club_url)
        print(f"[{index}/{len(club_urls)}] {squad_url}")

        try:
            player_ids = collect_player_ids_from_squad(squad_url)
            print(f"  Spieler gefunden: {len(player_ids)}")
            all_player_ids.update(player_ids)
        except Exception as e:
            print(f"  Fehler: {e}")

        time.sleep(2)

    with open("player_ids.txt", "w", encoding="utf-8") as file:
        for player_id in sorted(all_player_ids, key=int):
            file.write(player_id + "\n")

    print(f"\nFertig. Insgesamt eindeutige Spieler-IDs: {len(all_player_ids)}")
    print("Gespeichert in player_ids.txt")


if __name__ == "__main__":
    main()