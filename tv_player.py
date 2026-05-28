import mpv
import time
import os
import json
import random
import datetime
import glob
import threading
import sys

# Tkinter and PIL imports might not be strictly needed in this file depending on your setup, 
# but we will leave them if you plan to use them later.
import tkinter as tk
from PIL import Image, ImageTk

from inventory_manager import InventoryManager
from schedule_engine import ScheduleEngine
from commercial_manager import CommercialManager
from graphics_engine import GraphicsEngine

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
    print(f"DEBUG: Attempting to save history for {os.path.basename(episode_path)}...")
    
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

def main(parent_gui=None):
    print("\n[ TV Station is LIVE with MPV ]")
    print("Press Ctrl+C in the console to stop the station safely.\n")

    # --- 1. INITIALIZATION & LOADING ---
    # Load configuration
    try:
        with open("station_config.json", 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("ERROR: station_config.json not found. Please ensure it exists.")
        return

    # Scan the hard drive for media
    inventory = InventoryManager()
    library = inventory.scan_series(config["paths"]["tv"])
    
    # Handle movies (optional based on your config)
    movie_map = {}
    if config["settings"].get("enable_movies", False):
        movie_list = inventory.scan_movies(config["paths"]["movies"])
        movie_map = {os.path.basename(m): m for m in movie_list} 

    # --- 2. SETUP ENGINES & MPV PLAYER ---
    schedule = ScheduleEngine(library, list(movie_map.values()))
    comm_manager = CommercialManager(config["paths"]["commercials"])

    # Initialize MPV player
    # keep_open=True prevents the window from closing instantly when a video finishes
    # input_default_bindings=True lets you use standard mpv hotkeys (like 'f' for fullscreen, space to pause)
    player = mpv.MPV(
        fullscreen=True, 
        keep_open=True, 
        input_default_bindings=True, 
        input_vo_keyboard=True,
        log_handler=lambda level, prefix, text: print(f"MPV [{level}] {prefix}: {text}") if level in ['error', 'warning'] else None
    )

    # --- 3. MAIN PLAYBACK LOOP ---
    
    # Define the path to your bug
    bug_path = os.path.join(BASE_DIR, "assets", "bug.webm")
    bug_path_ffmpeg = bug_path.replace("\\", "/").replace(":", "\\:")
    
    bug_filter_graph = "movie=filename='assets/bug.webm':loop=0,setpts=N/FRAME_RATE/TB[logo];[in][logo]overlay=W-w-50:H-h-50"
    

    try:
        while True:
            content = schedule.get_next_item()

            if content['type'] == 'video':
                print(f"NOW PLAYING: {content['show']} - {content['display']}")
                
                # Update Tracker
                current_video_state["show"] = content['show']
                current_video_state["path"] = content['path']
                current_video_state["start_time"] = time.time()
                
                from tinytag import TinyTag
                try:
                    tag = TinyTag.get(content['path'])
                    current_video_state["duration"] = (tag.duration * 1000) if tag.duration else 1320000
                except:
                    current_video_state["duration"] = 1320000

                # --- TURN ON THE BUG ---
                if os.path.exists(bug_path):
                    player.vf = [{"name": "lavfi", "graph": bug_filter_graph}]
                else:
                    print(f"DEBUG: Bug asset not found at {bug_path}, playing without bug.")

                # Play the video
                player.play(content['path'])
                player.wait_for_playback()

                # Save history
                update_history(current_video_state["show"], current_video_state["path"], "watched", 100)

            elif content['type'] == 'break':
                print("--- COMMERCIAL BREAK ---")
                current_video_state["path"] = None 
                
                # --- TURN OFF THE BUG ---
                # Clearing the video filters removes the overlay for commercials
                player.vf = []

                clips = comm_manager.generate_break(content['min'], content['max'])
                
                for clip in clips:
                    print(f"  Playing Ad: {os.path.basename(clip)}")
                    player.play(clip)
                    player.wait_for_playback()

    except KeyboardInterrupt:
        print("\nStation shutting down via User Interrupt...")
        
        # SAVE PROGRESS ON EXIT
        if current_video_state["path"] and current_video_state["duration"] > 0:
            # Note: Later we can grab mpv's exact `player.time_pos` here, 
            # but time.time() works perfectly for now!
            elapsed_ms = (time.time() - current_video_state["start_time"]) * 1000
            percent = (elapsed_ms / current_video_state["duration"]) * 100
            if percent > 100: percent = 100
            status = "partial" if percent <= 90 else "watched"
            print(f"Saving final progress for {os.path.basename(current_video_state['path'])} ({int(percent)}%)...")
            update_history(current_video_state["show"], current_video_state["path"], status, percent)
        
        # Safely terminate the MPV player
        player.terminate()

# If running this standalone for testing
if __name__ == "__main__":
    main()