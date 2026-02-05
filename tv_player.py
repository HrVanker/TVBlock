import vlc
import time
import os
import json
import random
import datetime
import glob
import tkinter as tk
from PIL import Image, ImageTk
import threading
from inventory_manager import InventoryManager
from schedule_engine import ScheduleEngine
from commercial_manager import CommercialManager
from graphics_engine import GraphicsEngine
import sys

# Determine the absolute path of the application
if getattr(sys, 'frozen', False):
    # Running as a compiled PyInstaller EXE
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Running as a normal Python script
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HISTORY_FILE = "station_history.json"
gfx_engine = GraphicsEngine()

# Global tracker for the current video state
current_video_state = {
    "show": None,
    "path": None,
    "start_time": 0,
    "duration": 0
}

def update_history(show_name, episode_path, status, percent):
    """
    Updates the JSON history file with detailed playback stats.
    """
    print(f"DEBUG: Attempting to save history for {os.path.basename(episode_path)}...") # Debug line
    
    history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                history = json.load(f)
        except:
            print("DEBUG: Could not read existing history, starting new.")
            pass

    if "playback_log" not in history:
        history["playback_log"] = {}

    filename = os.path.basename(episode_path)
    
    entry = {
        "show": show_name,
        "path": episode_path,
        "status": status,
        "percent_watched": round(percent, 2),
        "last_played": str(datetime.datetime.now())
    }
    
    history["playback_log"][filename] = entry
    
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=4)
        print(f"DEBUG: History saved successfully to {os.path.abspath(HISTORY_FILE)}")
    except Exception as e:
        print(f"DEBUG: Error writing file: {e}")

def main(parent_gui):
    print("\n[ TV Station is LIVE ]")
    print("Press Ctrl+C to stop the station safely.\n")

    # --- 1. INITIALIZATION & LOADING ---
    # Load configuration
    with open("station_config.json", 'r') as f:
        config = json.load(f)

    # Scan the hard drive for media
    inventory = InventoryManager()
    library = inventory.scan_series(config["paths"]["tv"])
    
    # Handle movies (optional based on your config)
    movie_map = {}
    if config["settings"].get("enable_movies", False):
        movie_list = inventory.scan_movies(config["paths"]["movies"])
        # ScheduleEngine expects a map/list for movies depending on your version
        movie_map = {os.path.basename(m): m for m in movie_list} 

    # --- 2. SETUP ENGINES ---
    vlc_instance = vlc.Instance('--no-xlib', '--fullscreen', '--sub-filter=logo')
    player = vlc_instance.media_player_new()

    schedule = ScheduleEngine(library, list(movie_map.values()))
    comm_manager = CommercialManager(config["paths"]["commercials"])

    # Define your asset paths here
    


    # --- 3. MAIN PLAYBACK LOOP ---
    try:
        while True:
            content = schedule.get_next_item()

            if content['type'] == 'video':
                print(f"NOW PLAYING: {content['show']} - {content['display']}")
                
                # Update Tracker
                current_video_state["show"] = content['show']
                current_video_state["path"] = content['path']
                current_video_state["start_time"] = time.time()
                
                # Get duration from TinyTag (used for history percent)
                from tinytag import TinyTag
                try:
                    tag = TinyTag.get(content['path'])
                    current_video_state["duration"] = (tag.duration * 1000) if tag.duration else 1320000
                except:
                    current_video_state["duration"] = 1320000 # Default to 22 mins

                media = vlc_instance.media_new(content['path'])
                player.set_media(media)
                player.play()
                time.sleep(1) # Give VLC time to start

                # Save history when show finishes normally
                update_history(current_video_state["show"], current_video_state["path"], "watched", 100)

            elif content['type'] == 'break':
                # Play the Commercials
                print("--- COMMERCIAL BREAK ---")
                current_video_state["path"] = None 
                clips = comm_manager.generate_break(content['min'], content['max'])
                for clip in clips:
                    media = vlc_instance.media_new(clip)
                    player.set_media(media)
                    player.play()
                    time.sleep(1)
                    while player.get_state() != vlc.State.Ended:
                        time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nStation shutting down via User Interrupt...")
        
        # SAVE PROGRESS ON EXIT
        if current_video_state["path"] and current_video_state["duration"] > 0:
            elapsed_ms = (time.time() - current_video_state["start_time"]) * 1000
            percent = (elapsed_ms / current_video_state["duration"]) * 100
            if percent > 100: percent = 100
            status = "partial" if percent <= 90 else "watched"
            print(f"Saving final progress for {os.path.basename(current_video_state['path'])} ({int(percent)}%)...")
            update_history(current_video_state["show"], current_video_state["path"], status, percent)
        
        player.stop()

#if __name__ == "__main__":
 #   main()