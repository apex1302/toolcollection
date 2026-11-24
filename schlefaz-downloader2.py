import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests


IDENTIFIER = "die.schlechtesten.filme.aller.zeiten"
METADATA_URL = f"https://archive.org/metadata/{IDENTIFIER}"
DOWNLOAD_BASE_URL = f"https://archive.org/download/{IDENTIFIER}"

OUTPUT_DIR = Path("./Plex_Media")
CSV_LOG_PATH = OUTPUT_DIR / "downloaded_files.csv"
SHOW_NAME = "SchleFaZ"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".mpg", ".mpeg"}
SKIP_SUFFIXES = (".ia.mp4", ".thumbs.mp4")

# Format-Priorität: 0 = mkv (höchste), 1 = mp4, 2 = beliebig sonst
FORMAT_PRIORITY = {
    ".mkv": 0,
    ".mp4": 1,
}

HEADERS = {
    "User-Agent": "SchleFaZ-Plex-Downloader/1.3 (personal media organizer)"
}


def sanitize_filename(name: str) -> str:
    """Entfernt fuer Dateisysteme und Plex problematische Zeichen."""
    cleaned = re.sub(r'[\\/:*?"<>|]+', " - ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "Unbekannt"


def parse_episode_info(filename: str) -> tuple[int | None, int | None, str]:
    """
    Versucht Staffel, Episode und sauberen Titel aus dem Dateinamen zu extrahieren.
    """
    stem = Path(filename).stem
    normalized = stem.replace("_", " ").replace(".", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()

    match = re.search(r"\b[sS](\d{1,2})[eE](\d{1,2})\b", normalized)
    if match:
        season = int(match.group(1))
        episode = int(match.group(2))
        title = re.sub(r"\b[sS]\d{1,2}[eE]\d{1,2}\b", "", normalized)
        title = re.sub(r"^[\s\-–—:]+|[\s\-–—:]+$", "", title).strip()
        return season, episode, title or normalized

    match = re.search(
        r"\b(?:staffel|s)\s*(\d{1,2})\s*(?:folge|episode|f|e)\s*(\d{1,2})\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if match:
        season = int(match.group(1))
        episode = int(match.group(2))
        title = re.sub(
            r"\b(?:staffel|s)\s*\d{1,2}\s*(?:folge|episode|f|e)\s*\d{1,2}\b",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        title = re.sub(r"^[\s\-–—:]+|[\s\-–—:]+$", "", title).strip()
        return season, episode, title or normalized

    return None, None, normalized


def build_dedup_key(season: int | None, episode: int | None, title: str) -> tuple:
    """
    Erzeugt einen Schlüssel zum Erkennen doppelter Folgen derselben Episode.
    Bei bekannter Staffel + Episode wird (season, episode) genutzt,
    sonst ein normalisierter Titel-Schlüssel.
    """
    if season is not None and episode is not None:
        return ("ep", season, episode)

    norm_title = re.sub(r"[^a-z0-9äöüß]+", " ", title.lower()).strip()
    norm_title = re.sub(r"\s+", " ", norm_title)
    return ("title", norm_title)


def get_format_rank(filename: str) -> int:
    """Gibt die Priorität des Videoformats zurück (0 = mkv, 1 = mp4, 2 = sonst)."""
    ext = Path(filename).suffix.lower()
    return FORMAT_PRIORITY.get(ext, 2)


def build_plex_path(base_dir: Path, show_name: str, filename: str) -> Path:
    """
    Erzeugt einen Plex-konformen Pfad:
      Mit Staffel/Episode: <base>/<Show>/Season XX/<Show> - sXXeYY - <Titel>.<ext>
      Ohne Staffel/Episode: <base>/<Show>/Season 00/<Show> - <Titel>.<ext>
    """
    ext = Path(filename).suffix.lower()
    season, episode, title = parse_episode_info(filename)

    safe_show = sanitize_filename(show_name)
    safe_title = sanitize_filename(title)

    if season is not None and episode is not None:
        season_folder = f"Season {season:02d}"
        plex_name = f"{safe_show} - s{season:02d}e{episode:02d} - {safe_title}{ext}"
    else:
        season_folder = "Season 00"
        plex_name = f"{safe_show} - {safe_title}{ext}"

    return base_dir / safe_show / season_folder / plex_name


def format_bytes(num_bytes: float) -> str:
    """Formatiert Bytes lesbar (KB, MB, GB)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:6.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"


def load_completed_from_csv(csv_path: Path) -> tuple[set[str], set[tuple]]:
    """
    Liest bereits erfolgreich heruntergeladene Eintraege aus der CSV.
    Gibt (completed_remote_names, completed_dedup_keys) zurueck.
    """
    completed_names: set[str] = set()
    completed_keys: set[tuple] = set()

    if not csv_path.exists():
        return completed_names, completed_keys

    with open(csv_path, mode="r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            remote_name = (row.get("remote_name") or "").strip()
            if remote_name:
                completed_names.add(remote_name)
                season, episode, title = parse_episode_info(remote_name)
                completed_keys.add(build_dedup_key(season, episode, title))

    return completed_names, completed_keys


def append_to_csv(csv_path: Path, remote_name: str, dest_path: Path, size_bytes: int) -> None:
    """Fuegt einen erfolgreich heruntergeladenen Film der CSV-Datei hinzu."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    with open(csv_path, mode="a", encoding="utf-8", newline="") as f:
        fieldnames = ["timestamp", "remote_name", "plex_path", "size_bytes", "size_readable"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "remote_name": remote_name,
                "plex_path": str(dest_path),
                "size_bytes": size_bytes,
                "size_readable": format_bytes(size_bytes).strip(),
            }
        )


def fetch_video_files() -> list[dict]:
    """Ruft die Dateiliste des Archive.org-Items ab und filtert Videodateien."""
    resp = requests.get(METADATA_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    files = data.get("files", [])
    videos = []

    for f in files:
        name = f.get("name", "")
        lower_name = name.lower()
        ext = Path(lower_name).suffix

        if ext not in VIDEO_EXTENSIONS:
            continue
        if any(lower_name.endswith(suf) for suf in SKIP_SUFFIXES):
            continue

        season, episode, title = parse_episode_info(name)
        dedup_key = build_dedup_key(season, episode, title)
        fmt_rank = get_format_rank(name)

        videos.append(
            {
                "name": name,
                "size": int(f.get("size") or 0),
                "season": season,
                "episode": episode,
                "title": title,
                "dedup_key": dedup_key,
                "fmt_rank": fmt_rank,
            }
        )

    return videos


def select_best_unique_videos(videos: list[dict]) -> tuple[list[dict], int]:
    """
    Waehlt pro Episode/Film nur die beste Datei nach Format-Prioritaet:
      1. .mkv (fmt_rank 0)
      2. .mp4 (fmt_rank 1)
      3. beliebige andere Formate (fmt_rank 2)
    Bei gleichem Format-Rang wird die groessere Datei bevorzugt.
    Gibt (ausgewaehlte_videos, anzahl_verworfenener_dubletten) zurueck.
    """
    best_by_key: dict[tuple, dict] = {}

    for vid in videos:
        key = vid["dedup_key"]
        current_best = best_by_key.get(key)

        if current_best is None:
            best_by_key[key] = vid
            continue

        candidate_rank = (vid["fmt_rank"], -vid["size"], vid["name"].lower())
        best_rank = (current_best["fmt_rank"], -current_best["size"], current_best["name"].lower())

        if candidate_rank < best_rank:
            best_by_key[key] = vid

    selected = list(best_by_key.values())
    duplicates_removed = len(videos) - len(selected)

    selected.sort(
        key=lambda item: (
            item["season"] if item["season"] is not None else 999,
            item["episode"] if item["episode"] is not None else 999,
            item["title"].lower(),
            item["name"].lower(),
        )
    )
    return selected, duplicates_removed


def print_live_progress(downloaded: int, total: int | None, speed_bps: float) -> None:
    """Gibt eine einzeilige Live-Statuszeile mit Geschwindigkeit, Fortschritt und ETA aus."""
    speed_str = f"{format_bytes(speed_bps).strip()}/s"
    dl_str = format_bytes(downloaded).strip()

    if total and total > 0:
        pct = min(100.0, (downloaded / total) * 100.0)
        tot_str = format_bytes(total).strip()
        if speed_bps > 0 and downloaded < total:
            eta_sec = int((total - downloaded) / speed_bps)
            mins, secs = divmod(eta_sec, 60)
            hrs, mins = divmod(mins, 60)
            eta_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs else f"{mins:02d}:{secs:02d}"
        else:
            eta_str = "--:--"
        line = f"\r  Fortschritt: {pct:5.1f}% | {dl_str} / {tot_str} | Speed: {speed_str} | Restzeit: {eta_str}   "
    else:
        line = f"\r  Geladen: {dl_str} | Speed: {speed_str}   "

    sys.stdout.write(line)
    sys.stdout.flush()


def download_single_file(
    remote_name: str, dest_path: Path, expected_size: int = 0, max_retries: int = 3
) -> tuple[bool, int]:
    """
    Laedt eine einzelne Datei sequenziell mit Resume (.part) und Live-Geschwindigkeitsanzeige herunter.
    Gibt (erfolg, finale_dateigroesse) zurueck.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_name(dest_path.name + ".part")

    if dest_path.exists() and dest_path.stat().st_size > 0:
        final_size = dest_path.stat().st_size
        print(f"  [Bereits auf Platte] {dest_path.name} ({format_bytes(final_size).strip()})")
        return True, final_size

    url = f"{DOWNLOAD_BASE_URL}/{quote(remote_name)}"

    for attempt in range(1, max_retries + 1):
        existing_bytes = part_path.stat().st_size if part_path.exists() else 0
        req_headers = dict(HEADERS)
        if existing_bytes > 0:
            req_headers["Range"] = f"bytes={existing_bytes}-"
            print(f"  Setze fort ab {format_bytes(existing_bytes).strip()}...")

        try:
            with requests.get(url, headers=req_headers, stream=True, timeout=60) as r:
                if r.status_code == 200 and existing_bytes > 0:
                    existing_bytes = 0
                    part_path.unlink(missing_ok=True)

                r.raise_for_status()

                content_length = r.headers.get("Content-Length")
                total_bytes = (
                    existing_bytes + int(content_length)
                    if content_length and content_length.isdigit()
                    else (expected_size or None)
                )

                mode = "ab" if existing_bytes > 0 else "wb"
                downloaded = existing_bytes

                last_ui_time = time.monotonic()
                window_start_time = last_ui_time
                window_bytes = 0
                current_speed = 0.0

                with open(part_path, mode) as out:
                    for chunk in r.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        out.write(chunk)
                        chunk_len = len(chunk)
                        downloaded += chunk_len
                        window_bytes += chunk_len

                        now = time.monotonic()
                        elapsed_window = now - window_start_time
                        if elapsed_window >= 1.0:
                            current_speed = window_bytes / elapsed_window
                            window_start_time = now
                            window_bytes = 0

                        if now - last_ui_time >= 0.4:
                            print_live_progress(downloaded, total_bytes, current_speed)
                            last_ui_time = now

                print_live_progress(downloaded, total_bytes, current_speed)
                print()

            part_path.replace(dest_path)
            final_size = dest_path.stat().st_size
            print(f"  [Fertig] {dest_path} ({format_bytes(final_size).strip()})")
            return True, final_size

        except Exception as exc:
            print()
            print(f"  [Fehler Versuch {attempt}/{max_retries}] {remote_name}: {exc}")
            if attempt < max_retries:
                sleep_sec = 5 * attempt
                print(f"  Warte {sleep_sec}s vor erneutem Versuch...")
                time.sleep(sleep_sec)

    print(f"  [Fehlgeschlagen] Konnte {remote_name} nicht vollstaendig laden.")
    return False, 0


def main():
    print(f"Lade Dateiliste von {METADATA_URL} ...")
    raw_videos = fetch_video_files()

    if not raw_videos:
        print("Keine Videodateien gefunden (oder Item nicht erreichbar).")
        return

    unique_videos, duplicates_removed = select_best_unique_videos(raw_videos)
    completed_names, completed_keys = load_completed_from_csv(CSV_LOG_PATH)

    missing_videos = [
        v
        for v in unique_videos
        if v["name"] not in completed_names and v["dedup_key"] not in completed_keys
    ]

    total_size_gb = sum(v["size"] for v in missing_videos) / (1024**3)
    print(f"Videodateien gesamt auf Archive.org: {len(raw_videos)}")
    print(f"Mehrfachversionen herausgefiltert:    {duplicates_removed}")
    print(f"Eindeutige Episoden/Filme:           {len(unique_videos)}")
    print(f"Bereits laut CSV geladen:            {len(completed_names)} Eintraege ({CSV_LOG_PATH})")
    print(f"Noch herunterzuladen:                {len(missing_videos)} Videos (ca. {total_size_gb:.2f} GB)\n")

    if not missing_videos:
        print("Alle eindeutigen Videos wurden laut CSV bereits vollstaendig heruntergeladen.")
        return

    for idx, vid in enumerate(missing_videos, start=1):
        dest_file = build_plex_path(OUTPUT_DIR, SHOW_NAME, vid["name"])
        fmt_label = Path(vid["name"]).suffix.lower().lstrip(".") or "video"
        print(f"[{idx}/{len(missing_videos)}] Quelle ({fmt_label}): {vid['name']}")
        success, final_size = download_single_file(
            vid["name"], dest_file, expected_size=vid["size"]
        )
        if success:
            append_to_csv(CSV_LOG_PATH, vid["name"], dest_file, final_size)

    print("\nDurchlauf abgeschlossen.")


if __name__ == "__main__":
    main()
