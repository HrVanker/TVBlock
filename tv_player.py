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
                CHANNEL_BUG_GIF = os.path.join(BASE_DIR, "assets", "my_logo.gif")
                print("DEBUG: Video started. Loading GUI...", flush=True)
                # This is likely where your code was freezing before!
                bug_gui = BugOverlay(parent_gui, CHANNEL_BUG_GIF)
                print("DEBUG: GUI loaded. Entering playback loop.", flush=True)

                while player.get_state() != vlc.State.Ended:
                    current_time = time.time()
                    elapsed = current_time - last_bug_time
                    
                    # 2. Use thread-safe parent_gui.after(0, ...) to trigger the GUI changes
                    if elapsed >= 5 and not bug_active:
                        print(">> Displaying Floating GIF Bug")
                        parent_gui.after(0, bug_gui.show)
                        bug_active = True
                        last_bug_time = current_time 
                    
                    if bug_active and elapsed >= 15:
                        print(">> Hiding Floating GIF Bug")
                        parent_gui.after(0, bug_gui.hide)
                        bug_active = False
                        last_bug_time = current_time 

                    # --- NEW: Keep the Tkinter GUI alive and animating ---
                    bug_gui.root.update()

                    # Drop sleep to 0.02 so the loop runs fast enough for 30fps GIF playback
                    time.sleep(0.02) 

                    # --- NEW: Clean up GUI when the TV show ends ---
                    bug_gui.destroy()

                    time.sleep(0.5)

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
    # We now pass the 'parent' (the Station Manager GUI) into the bug
    def __init__(self, parent, gif_path):
        self.parent = parent
        
        # Create a Toplevel window (a child of the main manager)
        self.top = tk.Toplevel(parent)
        self.top.overrideredirect(True) # Remove borders
        self.top.attributes("-topmost", True) # Keep above VLC
        self.top.config(bg='black')
        self.top.attributes('-transparentcolor', 'black')

        screen_width = self.top.winfo_screenwidth()
        screen_height = self.top.winfo_screenheight()
        self.top.geometry(f"+{screen_width - 250}+{screen_height - 250}")

        self.gif = Image.open(gif_path)
        self.frames = [ImageTk.PhotoImage(self.gif.copy().convert("RGBA"))]
        self.total_frames = getattr(self.gif, 'n_frames', 1)
        
        self.lbl = tk.Label(self.top, image=self.frames[0], bg='black', borderwidth=0)
        self.lbl.pack()
        self.current_frame = 0

        self.top.withdraw() # Start hidden
        self.is_visible = False
        self.animate()

    def animate(self):
        if self.is_visible:
            self.current_frame = (self.current_frame + 1) % self.total_frames
            if self.current_frame >= len(self.frames):
                self.gif.seek(self.current_frame)
                self.frames.append(ImageTk.PhotoImage(self.gif.copy().convert("RGBA")))
            self.lbl.config(image=self.frames[self.current_frame])
        
        # The main Station Manager loop will handle this timer automatically
        self.top.after(33, self.animate)

    def show(self):
        self.is_visible = True
        self.top.deiconify()

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