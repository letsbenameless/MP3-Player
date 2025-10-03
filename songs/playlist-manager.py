import os
import sys

TOOLS = {
    "1": ("Download Lyric Videos", "songs\download-lyric-videos.py"),
    "2": ("MP3 Metadata Renamer/Tagger", "songs\mp3-metadata.py"),
    "3": ("Song Length Checker", "songs\song-length-checker.py"),
    "4": ("Playlist Player", "songs\media-player.py"),
}

def show_menu():
    os.system("cls" if os.name == "nt" else "clear")
    print("\n=== Playlist Manager ===")
    for key, (desc, _) in TOOLS.items():
        print(f"{key}. {desc}")
    print("Q. Quit")

def main():
    while True:
        show_menu()
        choice = input("\nSelect an option: ").strip().upper()

        if choice == "Q":
            print("Exiting Playlist Manager.")
            break
        elif choice in TOOLS:
            desc, script = TOOLS[choice]
            print(f"\n▶ Running {desc}...\n")
            os.system(f"python {script}")
            input("\nPress Enter to return to the Playlist Manager menu...")
        else:
            print("\nInvalid selection. Please choose a valid option.")
            input("Press Enter to continue...")

if __name__ == "__main__":
    main()