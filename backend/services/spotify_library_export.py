import os, re, requests, csv
from backend.services.spotify_export import export_spotify


def sanitize_filename(name: str) -> str:
    """Return a filesystem-safe version of a playlist/album name, avoiding reserved words."""
    # Replace illegal characters
    safe = re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(". ")

    # Reserved internal filenames (case-insensitive)
    reserved = {"__LIKED_SONGS__", "__FAILED_EXPORTS__", "__ALL_TRACKS__"}
    if safe.upper() in (r.upper() for r in reserved):
        safe = f"{safe}_USER"  # prevent overwriting internal exports

    return safe[:240]


def export_full_library(token: str, output_dir: str):
    """Export playlists, albums, and liked songs for the authorized user."""
    headers = {"Authorization": f"Bearer {token}"}
    os.makedirs(output_dir, exist_ok=True)

    failed = []  # collect (item_type, name, error_message)

    # ─── Export Playlists ───────────────────────────────────────────
    print("🎵 Fetching playlists...")
    url = "https://api.spotify.com/v1/me/playlists"
    while url:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        for p in data["items"]:
            name = sanitize_filename(p["name"])
            print(f"Exporting playlist: {name}")
            try:
                export_spotify(
                    p["external_urls"]["spotify"],
                    os.path.join(output_dir, f"{name}.csv"),
                    token=token
                )
            except requests.HTTPError as e:
                msg = f"❌ Failed to export playlist '{name}': {e}"
                print(msg)
                failed.append(("playlist", name, str(e)))
            except Exception as e:
                msg = f"⚠️ Unexpected error on playlist '{name}': {e}"
                print(msg)
                failed.append(("playlist", name, str(e)))
        url = data.get("next")

    # ─── Export Albums ───────────────────────────────────────────────
    print("\n💿 Fetching saved albums...")
    url = "https://api.spotify.com/v1/me/albums"
    while url:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        for item in data["items"]:
            album = item["album"]
            name = sanitize_filename(album["name"])
            print(f"Exporting album: {name}")
            try:
                export_spotify(
                    album["external_urls"]["spotify"],
                    os.path.join(output_dir, f"{name}.csv"),
                    token=token
                )
            except requests.HTTPError as e:
                msg = f"❌ Failed to export album '{name}': {e}"
                print(msg)
                failed.append(("album", name, str(e)))
            except Exception as e:
                msg = f"⚠️ Unexpected error on album '{name}': {e}"
                print(msg)
                failed.append(("album", name, str(e)))
        url = data.get("next")

    # ─── Export Liked Songs ─────────────────────────────────────────
    print("\n❤️ Fetching liked songs...")
    liked_tracks = []
    url = "https://api.spotify.com/v1/me/tracks"
    while url:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        for item in data["items"]:
            track = item["track"]
            if not track:
                continue
            liked_tracks.append({
                "name": track.get("name", ""),
                "artists": ", ".join([a["name"] for a in track.get("artists", [])]),
                "album": track.get("album", {}).get("name", ""),
                "album_release_year": (track.get("album", {}).get("release_date") or "")[:4],
                "duration_ms": track.get("duration_ms", ""),
                "spotify_track_url": track.get("external_urls", {}).get("spotify", ""),
            })
        url = data.get("next")

    if liked_tracks:
        liked_path = os.path.join(output_dir, "__LIKED_SONGS__.csv")
        print(f"\nExporting {len(liked_tracks)} liked songs → {liked_path}")
        try:
            with open(liked_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "name",
                        "artists",
                        "album",
                        "album_release_year",
                        "duration_ms",
                        "spotify_track_url",
                    ],
                )
                writer.writeheader()
                for row in liked_tracks:
                    writer.writerow(row)
        except Exception as e:
            msg = f"⚠️ Failed to write liked songs file: {e}"
            print(msg)
            failed.append(("liked_songs", "__LIKED_SONGS__.csv", str(e)))
    else:
        print("No liked songs found.")

    # ─── Summary ────────────────────────────────────────────────────
    print("\n✅ Full library export complete.")

    if failed:
        log_path = os.path.join(output_dir, "failed_exports.txt")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("Failed Exports Log\n")
            f.write("==================\n\n")
            for kind, name, err in failed:
                f.write(f"[{kind}] {name}\n{err}\n\n")

        print(f"\n⚠️ {len(failed)} exports failed. Details written to:")
        print(f"   {log_path}")
        for kind, name, err in failed:
            print(f" - ({kind}) {name}: {err}")
    else:
        print("\n✅ All items exported successfully.")
