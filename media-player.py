import os
import random
import vlc
import time

def choose_playlist(base_folder):
    subfolders = [f for f in os.listdir(base_folder) if os.path.isdir(os.path.join(base_folder, f)) and f not in ("duds", "dud-replacements")]
    if not subfolders:
        print("No playlists found.")
        return None

    error_message = None
    while True:
        os.system("cls")  # clear console before showing playlist menu
        print("\nAvailable playlists:")
        for i, folder in enumerate(subfolders, start=1):
            print(f"{i}. {folder}")

        print("\nM. Open Playlist Manager")
        print("Q. Quit")

        if error_message:
            print(f"\n{error_message}")

        choice = input("\nSelect a playlist number or option: ").strip().upper()

        if choice == "Q":
            return None
        elif choice == "M":
            os.system("python songs\playlist-manager.py")
            continue
        try:
            index = int(choice) - 1
            if 0 <= index < len(subfolders):
                return os.path.join(base_folder, subfolders[index])
            else:
                error_message = "Select a valid playlist number."
        except ValueError:
            error_message = "Select a valid playlist number."

def load_songs(folder):
    songs = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".mp3")]
    random.shuffle(songs)
    return songs

def format_media(media):
    """Return a nice string: Title or fallback filename (no artist)."""
    if not media:
        return "Unknown"
    title = media.get_meta(vlc.Meta.Title)
    if title:
        return title
    else:
        return os.path.basename(media.get_mrl()).replace("file:///", "")

def show_queue(media_list, current_index, window=10):
    print("\n--- Upcoming Queue ---")
    count = media_list.count()
    for i in range(current_index, min(current_index + window, count)):
        media = media_list.item_at_index(i)
        label = format_media(media)
        if i == current_index:
            print(f"> {i+1}. {label} (currently playing)")
        else:
            print(f"  {i+1}. {label}")
    print("----------------------")

def play_playlist(base_folder, playlist):
    songs = load_songs(playlist)
    if not songs:
        print("No mp3 files found in this playlist.")
        return False

    # setup VLC
    instance = vlc.Instance()
    player = instance.media_list_player_new()
    media_list = instance.media_list_new(songs)
    media_player = player.get_media_player()
    player.set_media_list(media_list)
    player.play()

    last_index = -1

    while True:
        # figure out which song is playing
        current_media = player.get_media_player().get_media()
        current_index = None
        if current_media:
            current_mrl = current_media.get_mrl()
            for i in range(media_list.count()):
                if media_list.item_at_index(i).get_mrl() == current_mrl:
                    current_index = i
                    break

        # refresh queue only when song changes
        if current_index is not None and current_index != last_index:
            os.system("cls")  # clear console
            show_queue(media_list, current_index, window=10)
            print("Menu: [N]ext  [P]revious  [F]orward 10s  [B]ack 10s  [L]ock PC  [S]top  [Q]uit")
            last_index = current_index

        # detect end of playlist
        if current_index is None and last_index == media_list.count() - 1:
            print("\n🎵 Playlist finished.")
            return True

        # quick check for user input without blocking
        if os.name == "nt":
            import msvcrt
            if msvcrt.kbhit():
                action = msvcrt.getch().decode("utf-8").upper()

                if action == "N":
                    player.next()
                elif action == "P":
                    player.previous()
                elif action == "F":
                    current_time = media_player.get_time()
                    media_player.set_time(current_time + 10000)  # skip ahead 10s
                elif action == "B":
                    current_time = media_player.get_time()
                    media_player.set_time(max(0, current_time - 10000))  # skip back 10s
                elif action == "S":
                    player.stop()
                    print("Playback stopped.")
                    return True
                elif action == "L":  # Lock workstation
                    os.system("rundll32.exe user32.dll,LockWorkStation")
                    os.system("nircmd monitor off")
                elif action == "Q":
                    player.stop()
                    return False

        time.sleep(0.5)  # poll every half second

def main():
    base_folder = r"C:\\Users\\letsbenameless\\Desktop\\Audio Devices\\songs"

    while True:
        os.system("cls")  # clear console before showing playlist menu
        playlist = choose_playlist(base_folder)
        if not playlist:
            return

        continue_playing = play_playlist(base_folder, playlist)
        if not continue_playing:
            break

if __name__ == "__main__":
    main()