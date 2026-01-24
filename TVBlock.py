import os
from inventory_manager import InventoryManager
from schedule_engine import ScheduleEngine
from commercial_manager import CommercialManager

def save_to_m3u(playlist, output_path):
    """
    Writes the list to .m3u using #EXTINF for clean titles.
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n") # Header
        
        for path in playlist:
            # 1. Get the filename without the full path
            filename = os.path.basename(path)
            # 2. Remove the extension (optional, looks cleaner)
            display_name = os.path.splitext(filename)[0]
            
            # 3. Write the Metadata line (-1 is default duration)
            f.write(f"#EXTINF:-1,{display_name}\n")
            # 4. Write the Path line
            f.write(f"{path}\n")
            
    print(f"\nSaved Playlist to: {output_path}")

def inject_commercials(playlist, comm_manager, frequency=4, min_time=120, max_time=240):
    """
    Inserts commercial blocks into the playlist using a time range.
    """
    new_playlist = []
    counter = 0

    for item in playlist:
        new_playlist.append(item)
        counter += 1

        # If we hit the frequency count, insert a break
        if counter % frequency == 0:
            # Generate a break with a random length between min and max
            break_files = comm_manager.generate_break(min_duration=min_time, max_duration=max_time)
            new_playlist.extend(break_files)
    
    return new_playlist

def main():
    # --- CONFIGURATION ---
    tv_path = r"D:\Media\Shows" 
    movie_path = r"D:\Media\Movies"
    commercials_path = r"D:\Media\Commercials" # <--- CREATE THIS FOLDER if you haven't!
    output_file = r"D:\Media\MyTVChannel.m3u"
    
    # 1. SETUP & SCAN
    scanner = InventoryManager()
    tv_library = scanner.scan_series(tv_path)
    movie_library = scanner.scan_movies(movie_path)
    
    # 2. LOAD COMMERCIALS
    # Ensure you have some video files in the commercials path!
    comm_manager = CommercialManager(commercials_path)

    # 3. BUILD SCHEDULE
    scheduler = ScheduleEngine(tv_library)

    # --- QUEUES (Your existing setup) ---
    scheduler.create_show_queue("Star Trek The Next Generation")
    scheduler.create_show_queue("Star Trek Deep Space Nine", seasons=[2, 3, 4])
    scheduler.create_show_queue("Batman - The Animated Series 1992")
    scheduler.create_show_queue("The Simpsons", seasons=[3, 4, 5, 6, 7, 8])
    scheduler.create_show_queue("Pete & Pete")
    scheduler.create_show_queue("Rocko's Modern Life")
    scheduler.create_show_queue("The Kids in the Hall", seasons=[1, 2])

    scheduler.create_rotation_group("Star Trek", ["Star Trek Deep Space Nine", "Star Trek The Next Generation"])
    scheduler.create_rotation_group("90s_Kids", ["Pete & Pete", "Rocko's Modern Life"])

    # --- BLOCK DEFINITION ---
    schedule_block = [
        {"type": "rotate", "group": "Star Trek"},
        {"type": "anchor", "show": "The Kids in the Hall"},
        {"type": "rotate", "group": "90s_Kids"},
        {"type": "anchor", "show": "Batman - The Animated Series 1992"},
        {"type": "anchor", "show": "The Simpsons", "count": 2},
    ]

    # Generate raw list (let's go for 24 hours worth, approx 50 items for testing)
    raw_playlist = scheduler.generate_schedule(schedule_block, max_episodes=50)

    # 4. INJECT COMMERCIALS (Updated Logic)
    # "Insert a break every 3 shows, lasting between 2 and 4 minutes"
    playlist_with_ads = inject_commercials(
        raw_playlist, 
        comm_manager, 
        frequency=3, 
        min_time=120, # 2 minutes
        max_time=240  # 4 minutes
    )

    INCLUDE_MOVIES = True  # Set to False to disable movies entirely
    MOVIE_START_INDEX = 10 # Don't play a movie in the first 10 items
    
    # 5. SMART MOVIE INJECTION
    if INCLUDE_MOVIES and movie_library:
        import random
        
        # A. Pick a random movie
        movie_to_play = random.choice(movie_library)
        movie_name = os.path.basename(movie_to_play)
        
        # B. Pick a safe random spot to insert it
        # We ensure it doesn't try to insert past the end of the playlist
        if len(playlist_with_ads) > MOVIE_START_INDEX:
            # Pick a random index between start_index and the end of the list
            insert_index = random.randint(MOVIE_START_INDEX, len(playlist_with_ads))
            
            playlist_with_ads.insert(insert_index, movie_to_play)
            print(f"--> Scheduled Feature Presentation: '{movie_name}' at item #{insert_index}")
        else:
            print("Playlist too short to insert movie safely.")

    # 6. EXPORT (Uses the new save_to_m3u function)
    save_to_m3u(playlist_with_ads, output_file)

if __name__ == "__main__":
    main()