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
    BUMPER_BG_VIDEO = os.path.join(BASE_DIR, "assets", "up_next_bg.mp4")
    TEMP_OVERLAY_IMG = os.path.join(BASE_DIR, "assets", "temp_overlay.png")
    BUG_FRAMES_DIR = os.path.join(BASE_DIR, "assets", "bug_frames")


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

                # Channel Bug Timer Variables
                start_time = time.time()
                last_bug_time = start_time
                bug_active = False
                CHANNEL_BUG_GIF = os.path.join(BASE_DIR, "assets", "output.gif")
                print("DEBUG: Video started. Loading GUI...", flush=True)
                # This is likely where your code was freezing before!
                bug_gui = BugOverlay(parent_gui, CHANNEL_BUG_GIF)
                print("DEBUG: GUI loaded. Entering playback loop.", flush=True)

                

                # Save history when show finishes normally
                update_history(current_video_state["show"], current_video_state["path"], "watched", 100)

            elif content['type'] == 'break':
                print("--- GENERATING UP NEXT BUMPER ---")
                comm_duration = random.randint(content['min'], content['max'])
                
                # Get upcoming shows
                upcoming_shows = schedule.get_upcoming_durations(limit=3)

                # Generate the text overlay
                gfx_engine.generate_transparent_bumper(
                    upcoming_shows, 
                    comm_duration, 
                    output_path=TEMP_OVERLAY_IMG
                )

                # Play the Animated Background
                print("--- PLAYING BUMPER ---")
                media = vlc_instance.media_new(BUMPER_BG_VIDEO)
                player.set_media(media)
                player.play()
                time.sleep(0.5) # Wait for video to initialize

                # Apply the Text Overlay
                player.video_set_logo_int(vlc.VideoLogoOption.enable, 1)
                player.video_set_logo_string(vlc.VideoLogoOption.file, TEMP_OVERLAY_IMG)
                player.video_set_logo_int(vlc.VideoLogoOption.x, 0)
                player.video_set_logo_int(vlc.VideoLogoOption.y, 0)
                player.video_set_logo_int(vlc.VideoLogoOption.opacity, 255)

                while player.get_state() != vlc.State.Ended:
                    time.sleep(0.5)

                # CLEAR THE OVERLAY
                player.video_set_logo_int(vlc.VideoLogoOption.enable, 0)

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

class BugOverlay:
    def __init__(self, parent, gif_path):
        self.parent = parent
        self.top = tk.Toplevel(parent)
        self.top.overrideredirect(True) 
        
        # --- NEW: Bind to the video window so it minimizes with it ---
        self.top.transient(parent) 
        self.top.attributes("-topmost", True) 
        self.top.config(bg='black')
        self.top.attributes('-transparentcolor', 'black')

        self.gif = Image.open(gif_path)
        gif_width, gif_height = self.gif.size

        # 2. CALCULATE EXACT CORNER POSITION
        screen_width = self.top.winfo_screenwidth()
        screen_height = self.top.winfo_screenheight()

        PAD_X = 50 
        PAD_Y = 50 

        pos_x = screen_width - gif_width - PAD_X
        pos_y = screen_height - gif_height - PAD_Y

        self.top.geometry(f"+{pos_x}+{pos_y}")

        # --- NEW: Pre-load the GIF and extract its actual timing ---
        self.frames = []
        self.durations = []
        try:
            while True:
                self.frames.append(ImageTk.PhotoImage(self.gif.copy().convert("RGBA")))
                # Read the delay specific to this frame (default to 33ms if missing)
                self.durations.append(self.gif.info.get('duration', 33)) 
                self.gif.seek(len(self.frames))
        except EOFError:
            pass

        self.total_frames = len(self.frames)
        self.lbl = tk.Label(self.top, image=self.frames[0], bg='black', borderwidth=0)
        self.lbl.pack()
        self.current_frame = 0

        self.top.withdraw()
        self.is_visible = False

    def animate(self):
        if not self.is_visible: return

        self.lbl.config(image=self.frames[self.current_frame])
        
        # --- NEW: Play once and stop ---
        if self.current_frame < self.total_frames - 1:
            delay = self.durations[self.current_frame]
            self.current_frame += 1
            self.top.after(delay, self.animate)
        else:
            # Reached the end of the GIF!
            self.hide()

    def show(self):
        self.current_frame = 0 # Reset to beginning
        self.is_visible = True
        self.top.deiconify()
        self.animate() # Start playing

    def hide(self):
        self.is_visible = False
        self.top.withdraw()

    def destroy(self):
        self.top.destroy()

# Global variable to hold our window
current_bug_window = None

def toggle_channel_bug(gif_path, enable=True):
    """
    Spawns or destroys the floating Tkinter GIF window.
    """
    global current_bug_window
    
    if enable and current_bug_window is None:
        # Run the GUI in a separate thread so VLC doesn't freeze
        def run_gui():
            global current_bug_window
            current_bug_window = BugOverlay(gif_path)
            current_bug_window.root.mainloop()
            
        threading.Thread(target=run_gui, daemon=True).start()
        
    elif not enable and current_bug_window is not None:
        current_bug_window.close()
        current_bug_window = None

#if __name__ == "__main__":
 #   main()