import tkinter as tk
from tkinter import ttk, messagebox, Menu, filedialog
from PIL import Image
from rotation_editor import RotationEditor
from graphics_engine import GraphicsEngine
import random
import json
import os
import sys
import threading
import time
import datetime

# Check if we are running as a bundled exe or a script
if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))

# --- THE MPV DLL FIX ---
if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
    os.add_dll_directory(app_dir)

import mpv

from inventory_manager import InventoryManager
from schedule_engine import ScheduleEngine
from commercial_manager import CommercialManager

CONFIG_FILE = "station_config.json"
HISTORY_FILE = "station_history.json"
DEFAULT_CONFIG = {
    "paths": {
        "tv": "",
        "movies": "",
        "commercials": ""
    },
    "settings": {
        "enable_movies": False,
        "movie_frequency": 20,
        "commercial_frequency": 3,
        "commercial_min_sec": 60,
        "commercial_max_sec": 120
    },
    "rotation_groups": {
        "Example_Group": []
    },
    "schedule_block": [],
    "blacklist": []
}

class TVStationService:
    def __init__(self, gui_app):
        self.gui = gui_app
        self.running = False
        self.skip_flag = False
        self.current_meta = {"title": "Offline", "show": "", "percent": 0}
        self.gfx_engine = GraphicsEngine()

        self.load_components()

    def load_components(self):
        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4)
            except Exception as e:
                print(f"Error creating default config: {e}")

        with open(CONFIG_FILE, 'r') as f:
            self.config = json.load(f)
            
        if "blacklist" not in self.config:
            self.config["blacklist"] = []
            
        scanner = InventoryManager()
        self.library = {}
        tv_path = self.config['paths'].get('tv', '')
        if tv_path and os.path.exists(tv_path):
            self.library = scanner.scan_series(tv_path)
        
        self.movie_library = []
        self.movie_map = {}
        mov_path = self.config['paths'].get('movies', '')
        if mov_path and os.path.exists(mov_path):
            self.movie_library = scanner.scan_movies(mov_path)
            for m in self.movie_library:
                self.movie_map[os.path.basename(m)] = m

        comm_path = self.config['paths'].get('commercials', '')
        if comm_path and os.path.exists(comm_path):
            self.comm_manager = CommercialManager(comm_path)
        else:
            class DummyComm:
                def generate_break(self, a, b): return []
            self.comm_manager = DummyComm()
        
        self.scheduler = ScheduleEngine(
            self.library, 
            movie_library=self.movie_library, 
            config_file=CONFIG_FILE
        )

    def start_broadcast(self, window_id):
        if self.running: return
        self.running = True
        self.window_id = window_id
        self.thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.thread.start()

    def stop_broadcast(self):
        self.running = False

    def skip_current(self):
        if self.running:
            print("USER COMMAND: SKIP")
            self.skip_flag = True

    def save_config(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)
        if hasattr(self, 'scheduler'):
            self.scheduler._init_queues() 

    def _get_random_bug_filter(self):
        """Picks a random GIF from assets/bugs and returns the FFmpeg filter string."""
        bug_dir = os.path.join(app_dir, "assets", "bugs")
        if not os.path.exists(bug_dir):
            return None
        bugs = [f for f in os.listdir(bug_dir) if f.lower().endswith('.gif')]
        if not bugs:
            return None
            
        selected_bug = random.choice(bugs)
        bug_path = os.path.join(bug_dir, selected_bug)
        bug_path_ffmpeg = bug_path.replace("\\", "/").replace(":", "\\:")
        bug_height = 100
        return f"lavfi=[movie=filename='{bug_path_ffmpeg}':loop=0,scale=-1:{bug_height},setsar=1[logo];[in][logo]overlay=W-w-50:H-h-50]"

    def _broadcast_loop(self):
        player = None
        try:
            # --- 1. MPV SETUP ---
            player = mpv.MPV(
                wid=self.window_id,
                input_default_bindings=True,
                input_vo_keyboard=True,
                log_handler=lambda level, prefix, text: print(f"MPV [{level}] {prefix}: {text}") if level in ['error', 'warning'] else None
            )

            while self.running:
                current_content = self.scheduler.get_next_item()
                current_playlist = self._prepare_playlist(current_content)
                
                if current_content['type'] == 'video':
                    show_title = current_content.get('show', 'Unknown Show')
                    ep_title = os.path.basename(current_content.get('path', 'Unknown Episode'))
                    self.current_meta.update({"show": show_title, "title": ep_title})
                elif current_content['type'] == 'break':
                    self.current_meta.update({"show": "Commercial Break", "title": "Messages"})

                # --- BUMPER SEQUENCE ---
                # 1. CALCULATE TRUE COMMERCIAL DURATION
                    # Start with 15 seconds to account for the Bumper itself
                    real_comm_duration = 15 
                    from tinytag import TinyTag
                    
                    for clip in current_playlist:
                        try:
                            tag = TinyTag.get(clip)
                            if tag.duration:
                                real_comm_duration += tag.duration
                        except Exception:
                            # Safe fallback if a commercial file can't be read
                            real_comm_duration += 30 
                            
                    comm_duration = int(real_comm_duration)
                    
                    # 2. SYNC LIMIT WITH COMMERCIAL FREQUENCY
                    comm_freq = self.config.get("settings", {}).get("commercial_frequency", 3)
                    upcoming_shows = self.scheduler.get_upcoming_durations(limit=comm_freq)

                    bg_folder = os.path.join(app_dir, "assets", "bg")
                    bumper_bg = None

                    if os.path.exists(bg_folder):
                        valid_exts = ('.mp4', '.mkv', '.avi', '.mov')
                        files = [f for f in os.listdir(bg_folder) if f.lower().endswith(valid_exts)]
                        if files:
                            bumper_bg = os.path.join(bg_folder, random.choice(files))

                    if not bumper_bg:
                        bumper_bg = os.path.join(app_dir, "assets", "up_next_bg.mp4")

                    player.play(bumper_bg)
                    time.sleep(0.5) 

                    # Actively wait for MPV to initialize the video track and report its 
                    # true physical pixel count, bypassing the 1/4-size scaling issue.
                    timeout = time.time() + 5.0 # 5-second safety timeout
                    while player.dwidth is None and time.time() < timeout and self.running:
                        time.sleep(0.1) 

                    # Now we grab the actual physical screen dimensions
                    bg_width = 1920
                    bg_height = 1080
                    print(f"DEBUG: OSD Canvas mapped at {bg_width}x{bg_height}")

                    temp_overlay = os.path.join(app_dir, "assets", "temp_overlay.png")
                    self.gfx_engine.generate_transparent_bumper(
                        upcoming_shows, 
                        comm_duration, 
                        output_path=temp_overlay,
                        target_width=bg_width, 
                        target_height=bg_height
                    )

                    temp_overlay_ffmpeg = temp_overlay.replace("\\", "/").replace(":", "\\:")
                    bumper_filter = f"lavfi=[movie=filename='{temp_overlay_ffmpeg}'[logo];[in][logo]overlay=0:0]"
                    
                    try:
                        # 1. Open the image with Pillow
                        img = Image.open(temp_overlay)
                        
                        # 2. Convert to RGBA and swap to BGRA order
                        img = img.convert("RGBA")
                        r, g, b, a = img.split()
                        img_bgra = Image.merge("RGBA", (b, g, r, a))
                        img_bytes = img_bgra.tobytes()
                        
                        # 3. Bypass Windows pointer issues by saving the raw bytes to a file
                        bgra_path = os.path.join(app_dir, "assets", "temp_overlay.bgra")
                        with open(bgra_path, "wb") as f:
                            f.write(img_bytes)
                        
                        # 4. Calculate width, height, and stride (bytes per row)
                        w, h = img.size
                        stride = w * 4 
                        
                        # 5. Format path for MPV core (just forward slashes needed here)
                        bgra_path_mpv = bgra_path.replace("\\", "/")
                        
                        # 6. Send the raw command to the MPV core!
                        player.command("overlay-add", 1, 0, 0, bgra_path_mpv, 0, "bgra", w, h, stride)
                    except Exception as e:
                        print(f"DEBUG: Native OSD Bumper Error: {e}")

                    bug_filter = self._get_random_bug_filter()
                    if bug_filter:
                        try: player.command("vf", "add", f"@stationbug:{bug_filter}")
                        except: pass

                    # Wait for Bumper to end, allow skipping
                    bumper_start_time = time.time()
                    bumper_duration = 15  # Set your desired interstitial length in seconds
                    
                    while not getattr(player, 'idle_active', True) and self.running:
                        # 1. Check for manual user skip
                        if self.skip_flag:
                            player.command("stop")
                            self.skip_flag = False
                            break
                            
                        # 2. Check if our 15-second timer has expired
                        if time.time() - bumper_start_time >= bumper_duration:
                            print("DEBUG: Bumper time limit reached. Moving to commercials.")
                            player.command("stop")
                            break
                            
                        time.sleep(0.1)

                    # Remove bumper native OSD graphic
                    try: player.command("overlay-remove", 1) 
                    except: pass
                    
                    # Remove the animated station bug
                    try: player.command("vf", "remove", "@stationbug")
                    except: pass
                    
                    print("--- COMMERCIAL BREAK STARTING ---")

                # --- PLAY CHUNK (Shows or Commercials) ---
                for filepath in current_playlist:
                    if not self.running: break
                    
                    player.play(filepath)
                    time.sleep(0.5)
                    
                    mid_bug_shown = False
                    end_bug_shown = False
                    remove_bug_at_time = 0
                    bug_display_duration = 15 
                    
                    # --- MONITOR LOOP ---
                    while not getattr(player, 'idle_active', True) and self.running:
                        # CHECK FOR SKIP
                        if self.skip_flag:
                            duration = player.duration if player.duration else 0
                            curr_time = player.time_pos if player.time_pos else 0
                            pct = (curr_time / duration) * 100 if duration > 0 else 0
                            
                            if current_content['type'] == 'video' and pct > 5:
                                self.update_history(current_content['show'], current_content['path'], "partial", pct)
                            
                            player.command("stop")
                            self.skip_flag = False
                            break 
                        
                        # TIMED BUG LOGIC
                        duration = player.duration if player.duration else 0
                        if duration > 0:
                            curr_time = player.time_pos if player.time_pos else 0
                            self.current_meta["percent"] = (curr_time / duration) * 100

                            if current_content['type'] == 'video':
                                if remove_bug_at_time > 0 and time.time() >= remove_bug_at_time:
                                    try: player.command("vf", "remove", "@stationbug")
                                    except: pass
                                    remove_bug_at_time = 0

                                if not mid_bug_shown and curr_time >= (duration / 2):
                                    bug_filter = self._get_random_bug_filter()
                                    if bug_filter:
                                        try: player.command("vf", "add", f"@stationbug:{bug_filter}")
                                        except: pass
                                        remove_bug_at_time = time.time() + bug_display_duration
                                    mid_bug_shown = True

                                if not end_bug_shown and curr_time >= (duration - 60):
                                    bug_filter = self._get_random_bug_filter()
                                    if bug_filter:
                                        try: player.command("vf", "add", f"@stationbug:{bug_filter}")
                                        except: pass
                                        remove_bug_at_time = time.time() + bug_display_duration
                                    end_bug_shown = True
                        
                        time.sleep(0.1)

                    if current_content['type'] == 'video' and not self.skip_flag and self.running:
                        self.update_history(current_content['show'], current_content['path'], "watched", 100)

                    try: player.command("vf", "remove", "@stationbug")
                    except: pass

        except Exception as e:
            # We catch the ShutdownError silently so the app doesn't crash visually
            print(f"DEBUG: Broadcast Loop Terminated Safely.")
        finally:
            if player:
                try: player.terminate()
                except: pass
            self.current_meta = {"title": "Offline", "show": "", "percent": 0}

    def _prepare_playlist(self, content):
        playlist = []
        if content['type'] == 'video':
            if content.get('path'): 
                playlist.append(content['path'])
        elif content['type'] == 'break':
            clips = self.comm_manager.generate_break(content['min'], content['max'])
            playlist.extend(clips)
        return playlist

    def update_history(self, show, path, status, percent):
        history = {}
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    history = json.load(f)
            except: pass

        if "playback_log" not in history:
            history["playback_log"] = {}

        filename = os.path.basename(path)
        entry = {
            "show": show,
            "path": path,
            "status": status,
            "percent_watched": round(percent, 2),
            "last_played": str(datetime.datetime.now())
        }
        history["playback_log"][filename] = entry
        
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=4)
            
        self.scheduler.history = self.scheduler._load_history()

class StationManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TV Station Control Panel")
        self.root.geometry("1200x800")
        
        self.station = TVStationService(self)
        self.create_widgets()
        
        if not self.station.config['paths']['tv']:
            self.notebook.select(3) 
            messagebox.showinfo("Welcome!", "Welcome to your TV Station!\n\nIt looks like this is a fresh start. Please select your 'TV Shows Folder' in the settings below to begin.")
        
        self.update_ui_loop()

    def create_widgets(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.tab_dashboard = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_dashboard, text=" 📡 Live Dashboard ")
        self.build_dashboard_tab()
        self.tab_schedule = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_schedule, text=" 📅 Schedule Editor ")
        self.build_schedule_tab() 
        self.tab_library = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_library, text=" 📂 Library Manager ")
        self.build_library_tab()
        self.tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_settings, text=" ⚙ Configuration ")
        self.build_settings_tab()

    def build_dashboard_tab(self):
        control_frame = tk.Frame(self.tab_dashboard, bg="#222", height=80)
        control_frame.pack(fill=tk.X)
        self.btn_start = tk.Button(control_frame, text="▶ START STATION", bg="green", fg="white", font=("Arial", 12, "bold"), command=self.toggle_station)
        self.btn_start.pack(side=tk.LEFT, padx=20, pady=20)
        self.btn_skip = tk.Button(control_frame, text="⏭ SKIP ITEM", bg="#555", fg="white", font=("Arial", 10), command=self.station.skip_current)
        self.btn_skip.pack(side=tk.RIGHT, padx=20, pady=20)
        info_frame = tk.Frame(self.tab_dashboard, bg="#eee", pady=20)
        info_frame.pack(fill=tk.X)
        tk.Label(info_frame, text="NOW PLAYING", font=("Arial", 10, "bold"), fg="#777", bg="#eee").pack()
        self.lbl_show = tk.Label(info_frame, text="---", font=("Arial", 24, "bold"), bg="#eee", fg="#333")
        self.lbl_show.pack()
        self.lbl_episode = tk.Label(info_frame, text="Offline", font=("Arial", 16), bg="#eee", fg="#555")
        self.lbl_episode.pack(pady=5)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(info_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=50, pady=10)
        list_frame = tk.LabelFrame(self.tab_dashboard, text="Coming Up Next", font=("Arial", 12, "bold"), padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        self.up_next_tree = ttk.Treeview(list_frame, columns=("show", "episode"), show="headings", height=10)
        self.up_next_tree.heading("show", text="Show / Type")
        self.up_next_tree.heading("episode", text="Episode / Details")
        self.up_next_tree.column("show", width=300)
        self.up_next_tree.column("episode", width=500)
        self.up_next_tree.pack(fill=tk.BOTH, expand=True)

    def build_library_tab(self):
        paned = ttk.PanedWindow(self.tab_library, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        left = tk.LabelFrame(paned, text="Series")
        paned.add(left, weight=1)
        self.series_list = tk.Listbox(left, font=("Arial", 11), selectmode=tk.SINGLE)
        self.series_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.series_list.bind('<<ListboxSelect>>', self.on_series_select)
        right = tk.LabelFrame(paned, text="Episodes (Right-click to Toggle Disable)")
        paned.add(right, weight=3)
        columns = ("episode", "status", "last_played")
        self.ep_tree = ttk.Treeview(right, columns=columns, show="headings")
        self.ep_tree.heading("episode", text="Episode Title")
        self.ep_tree.heading("status", text="Playback Status")
        self.ep_tree.heading("last_played", text="Last Played")
        self.ep_tree.column("episode", width=400)
        self.ep_tree.column("status", width=120)
        self.ep_tree.column("last_played", width=180)
        scrollbar = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.ep_tree.yview)
        self.ep_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.ep_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Toggle Enable/Disable", command=self.toggle_blacklist)
        self.ep_tree.bind("<Button-3>", self.show_context_menu)
        self.ep_tree.tag_configure("watched", foreground="green")
        self.ep_tree.tag_configure("partial", foreground="#cfb000") 
        self.ep_tree.tag_configure("disabled", foreground="gray")
        self.ep_tree.tag_configure("normal", foreground="black")
        for s in sorted(self.station.library.keys()):
            self.series_list.insert(tk.END, s)

    def on_series_select(self, event):
        sel = self.series_list.curselection()
        if not sel: return
        show_name = self.series_list.get(sel[0])
        series_data = self.station.library[show_name]
        blacklist = self.station.config.get("blacklist", [])
        self.station.scheduler.history = self.station.scheduler._load_history()
        playback_log = self.station.scheduler.history.get("playback_log", {})
        for i in self.ep_tree.get_children(): 
            self.ep_tree.delete(i)
        for season_num in sorted(series_data.keys()):
            season_id = f"SEASON_ID_{season_num}" 
            season_text = f"Season {season_num}"
            all_eps = series_data[season_num]
            disabled_count = sum(1 for ep in all_eps if ep in blacklist)
            season_tag = "normal"
            season_status = ""
            if disabled_count == len(all_eps):
                season_tag = "disabled"
                season_status = "⛔ All Disabled"
            elif disabled_count > 0:
                season_status = f"⚠️ {disabled_count} Disabled"
            self.ep_tree.insert("", tk.END, iid=season_id, text=season_text, open=False, values=(season_text, season_status, ""), tags=(season_tag,))
            for ep_path in all_eps:
                filename = os.path.basename(ep_path)
                if ep_path in blacklist:
                    status = "⛔ DISABLED"
                    last_played = "-"
                    tag = "disabled"
                else:
                    status = "Unwatched"
                    last_played = "-"
                    tag = "normal"
                    if filename in playback_log:
                        data = playback_log[filename]
                        pct = data.get('percent_watched', 0)
                        if data['status'] == 'watched':
                            status = "✅ Watched"
                            tag = "watched"
                        elif data['status'] == 'partial':
                            status = f"⏸ Partial ({int(pct)}%)"
                            tag = "partial"
                        raw_date = data.get('last_played', '')
                        if raw_date: last_played = raw_date.split('.')[0]
                self.ep_tree.insert(season_id, tk.END, iid=ep_path, values=(filename, status, last_played), tags=(tag,))

    def toggle_random(self):
        selected = self.sched_tree.selection()
        if not selected: return
        item_id = selected[0]
        vals = self.sched_tree.item(item_id)['values']
        s_type = vals[0]
        if s_type == "movie": return 
        current_mode = vals[3]
        new_mode = "Random" if current_mode == "Sequential" else "Sequential"
        self.sched_tree.item(item_id, values=(s_type, vals[1], vals[2], new_mode))

    def show_context_menu(self, event):
        item = self.ep_tree.identify_row(event.y)
        if item:
            self.ep_tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def toggle_blacklist(self):
        selected_item = self.ep_tree.selection()
        if not selected_item: return
        item_id = selected_item[0]
        blacklist = self.station.config["blacklist"]
        changed = False
        if item_id.startswith("SEASON_ID_"):
            children = self.ep_tree.get_children(item_id)
            any_enabled = False
            for child_path in children:
                if child_path not in blacklist:
                    any_enabled = True
                    break
            for child_path in children:
                if any_enabled:
                    if child_path not in blacklist:
                        blacklist.append(child_path)
                        changed = True
                else:
                    if child_path in blacklist:
                        blacklist.remove(child_path)
                        changed = True
        else:
            file_path = item_id
            if file_path in blacklist:
                blacklist.remove(file_path)
            else:
                blacklist.append(file_path)
            changed = True
        if changed:
            self.station.save_config()
            open_seasons = []
            for child in self.ep_tree.get_children():
                if self.ep_tree.item(child, "open"):
                    open_seasons.append(child)
            self.on_series_select(None)
            for season_id in open_seasons:
                if self.ep_tree.exists(season_id):
                    self.ep_tree.item(season_id, open=True)
            if self.ep_tree.exists(item_id):
                self.ep_tree.selection_set(item_id)
                self.ep_tree.focus(item_id) 

    def toggle_station(self):
        if not self.station.running:
            self.video_window = tk.Toplevel(self.root)
            self.video_window.title("TV Station Broadcast")
            self.video_window.configure(bg="black")
            self.video_window.attributes("-fullscreen", True)
            self.video_window.bind("<Escape>", lambda e: self.toggle_station())
            self.video_window.protocol("WM_DELETE_WINDOW", self.toggle_station)
            self.video_window.update()
            
            window_id = self.video_window.winfo_id()
            self.station.start_broadcast(window_id)
            self.btn_start.config(text="⏹ STOP STATION", bg="red")
        else:
            self.station.stop_broadcast()
            
            # THE FIX: Wait 0.5s for MPV to process stop_broadcast safely before destroying the window
            self.root.after(500, self._destroy_video_window)
            self.btn_start.config(text="▶ START STATION", bg="green")

    def _destroy_video_window(self):
        if hasattr(self, 'video_window') and self.video_window:
            self.video_window.destroy()
            self.video_window = None

    def update_ui_loop(self):
        meta = self.station.current_meta
        self.lbl_show.config(text=meta['show'] if meta['show'] else "---")
        self.lbl_episode.config(text=meta['title'])
        self.progress_var.set(meta['percent'])
        for item in self.up_next_tree.get_children():
            self.up_next_tree.delete(item)
        upcoming = self.station.scheduler.get_upcoming_list()
        for item in upcoming:
            show = item.get('show', '---')
            title = item.get('display', 'Unknown')
            if item['type'] == 'break':
                show = "COMMERCIALS"
                title = f"{item['min']}s - {item['max']}s Block"
            self.up_next_tree.insert("", tk.END, values=(show, title))
        self.root.after(1000, self.update_ui_loop)
    
    def build_schedule_tab(self):
        paned = ttk.PanedWindow(self.tab_schedule, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        col1 = tk.LabelFrame(paned, text="1. Source Bin")
        paned.add(col1, weight=1)
        tk.Label(col1, text="📺 TV Shows", font=("Arial", 9, "bold")).pack(anchor=tk.W)
        self.lst_source_shows = tk.Listbox(col1, height=6, exportselection=False)
        self.lst_source_shows.pack(fill=tk.X, padx=5)
        for s in sorted(self.station.library.keys()):
            self.lst_source_shows.insert(tk.END, s)
        frame_rot = tk.Frame(col1)
        frame_rot.pack(fill=tk.X, pady=(5,0))
        tk.Label(frame_rot, text="🔄 Rotation Groups", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        tk.Button(frame_rot, text="⚙ Edit", font=("Arial", 8), command=self.open_rotation_editor).pack(side=tk.RIGHT, padx=5)
        self.lst_source_groups = tk.Listbox(col1, height=4, exportselection=False)
        self.lst_source_groups.pack(fill=tk.X, padx=5)
        self.refresh_source_groups()
        tk.Label(col1, text="🎬 Individual Movies", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(5,0))
        self.lst_source_movies = tk.Listbox(col1, height=6, exportselection=False)
        self.lst_source_movies.pack(fill=tk.X, padx=5)
        if hasattr(self.station, 'movie_map'):
            for m_name in sorted(self.station.movie_map.keys()):
                self.lst_source_movies.insert(tk.END, m_name)
        tk.Label(col1, text="⭐ Special Tokens", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(5,0))
        self.lst_tokens = tk.Listbox(col1, height=2, exportselection=False)
        self.lst_tokens.pack(fill=tk.X, padx=5)
        self.lst_tokens.insert(tk.END, "[Random Movie]")
        btn_add = tk.Button(col1, text="ADD TO BLOCK ➡", bg="#ddd", font=("Arial", 10, "bold"), command=self.add_item_to_schedule)
        btn_add.pack(pady=10, fill=tk.X, padx=20)
        col2 = tk.LabelFrame(paned, text="2. Programming Block")
        paned.add(col2, weight=2)
        columns = ("type", "name", "count", "random")
        self.sched_tree = ttk.Treeview(col2, columns=columns, show="headings", selectmode="browse")
        self.sched_tree.heading("type", text="Type")
        self.sched_tree.heading("name", text="Show / Group")
        self.sched_tree.heading("count", text="#")
        self.sched_tree.heading("random", text="Mode") 
        self.sched_tree.column("type", width=50)
        self.sched_tree.column("name", width=200)
        self.sched_tree.column("count", width=30)
        self.sched_tree.column("random", width=60) 
        self.sched_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.sched_tree.bind("<Double-1>", self.on_schedule_double_click)
        ctrl_frame = tk.Frame(col2)
        ctrl_frame.pack(fill=tk.X, pady=5)
        tk.Button(ctrl_frame, text="▲ Up", command=self.move_up).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        tk.Button(ctrl_frame, text="▼ Down", command=self.move_down).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        tk.Button(ctrl_frame, text="🎲 Randomize", command=self.toggle_random).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        tk.Button(ctrl_frame, text="❌ Remove", command=self.remove_item, fg="red").pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        col3 = tk.LabelFrame(paned, text="3. Station Settings")
        paned.add(col3, weight=1)
        settings = self.station.config.get("settings", {})
        tk.Label(col3, text="Commercial Frequency (Items)", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10,0), padx=5)
        self.var_comm_freq = tk.IntVar(value=settings.get("commercial_frequency", 3))
        tk.Scale(col3, variable=self.var_comm_freq, from_=1, to=10, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5)
        tk.Label(col3, text="Min Duration (Seconds)", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10,0), padx=5)
        self.var_comm_min = tk.IntVar(value=settings.get("commercial_min_sec", 60))
        tk.Entry(col3, textvariable=self.var_comm_min).pack(fill=tk.X, padx=5)
        tk.Label(col3, text="Max Duration (Seconds)", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10,0), padx=5)
        self.var_comm_max = tk.IntVar(value=settings.get("commercial_max_sec", 180))
        tk.Entry(col3, textvariable=self.var_comm_max).pack(fill=tk.X, padx=5)
        tk.Button(col3, text="💾 SAVE & RELOAD", bg="green", fg="white", font=("Arial", 12, "bold"), height=2, command=self.save_full_schedule).pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=20)
        self.load_schedule_to_tree()

    def load_schedule_to_tree(self):
        for i in self.sched_tree.get_children():
            self.sched_tree.delete(i)
        block = self.station.config.get("schedule_block", [])
        for slot in block:
            s_type = slot.get("type", "anchor")
            name = ""
            count = slot.get("count", 1)
            is_random = slot.get("random", False)
            random_str = "Random" if is_random else "Sequential"
            if s_type == "anchor": name = slot.get("show", "Unknown")
            elif s_type == "rotate": name = slot.get("group", "Unknown")
            elif s_type == "movie": 
                random_str = "-" 
                if "path" in slot: name = os.path.basename(slot["path"])
                else: name = "[Random Movie]"
            self.sched_tree.insert("", tk.END, values=(s_type, name, count, random_str))

    def add_item_to_schedule(self):
        if self.lst_source_shows.curselection():
            name = self.lst_source_shows.get(self.lst_source_shows.curselection())
            self.sched_tree.insert("", tk.END, values=("anchor", name, 1, "Sequential"))
            self.lst_source_shows.selection_clear(0, tk.END)
        elif self.lst_source_groups.curselection():
            name = self.lst_source_groups.get(self.lst_source_groups.curselection())
            self.sched_tree.insert("", tk.END, values=("rotate", name, 1, "Sequential"))
            self.lst_source_groups.selection_clear(0, tk.END)
        elif self.lst_source_movies.curselection():
            name = self.lst_source_movies.get(self.lst_source_movies.curselection())
            self.sched_tree.insert("", tk.END, values=("movie", name, 1, "-"))
            self.lst_source_movies.selection_clear(0, tk.END)
        elif self.lst_tokens.curselection():
            name = self.lst_tokens.get(self.lst_tokens.curselection())
            self.sched_tree.insert("", tk.END, values=("movie", name, 1, "Random"))
            self.lst_tokens.selection_clear(0, tk.END)

    def remove_item(self):
        selected = self.sched_tree.selection()
        if selected:
            self.sched_tree.delete(selected[0])

    def move_up(self):
        rows = self.sched_tree.selection()
        for row in rows:
            self.sched_tree.move(row, self.sched_tree.parent(row), self.sched_tree.index(row)-1)

    def move_down(self):
        rows = self.sched_tree.selection()
        for row in reversed(rows):
            self.sched_tree.move(row, self.sched_tree.parent(row), self.sched_tree.index(row)+1)

    def on_schedule_double_click(self, event):
        item_id = self.sched_tree.identify_row(event.y)
        if not item_id: return
        current_vals = self.sched_tree.item(item_id)['values']
        if current_vals[0] == "movie": return 
        from tkinter import simpledialog
        new_count = simpledialog.askinteger("Edit Count", f"Play how many episodes of {current_vals[1]}?", initialvalue=current_vals[2], minvalue=1, maxvalue=10)
        if new_count:
            self.sched_tree.item(item_id, values=(current_vals[0], current_vals[1], new_count))

    def save_full_schedule(self):
        new_block = []
        for item_id in self.sched_tree.get_children():
            vals = self.sched_tree.item(item_id)['values']
            s_type = vals[0]
            name = vals[1]
            count = int(vals[2])
            mode = vals[3]
            slot = {"type": s_type, "count": count}
            if mode == "Random":
                slot["random"] = True
            if s_type == "anchor": slot["show"] = name
            elif s_type == "rotate": slot["group"] = name
            elif s_type == "movie":
                if name == "[Random Movie]": pass
                elif name in self.station.movie_map: slot["path"] = self.station.movie_map[name]
            new_block.append(slot)
            
        self.station.config["schedule_block"] = new_block
        self.station.config["settings"]["commercial_frequency"] = self.var_comm_freq.get()
        self.station.config["settings"]["commercial_min_sec"] = self.var_comm_min.get()
        self.station.config["settings"]["commercial_max_sec"] = self.var_comm_max.get()
        self.station.save_config()
        self.station.scheduler = ScheduleEngine(
            self.station.library, 
            movie_library=self.station.movie_library, 
            config_file=CONFIG_FILE
        )
        messagebox.showinfo("Success", "Schedule updated and station reloaded!")

    def refresh_source_groups(self):
        self.lst_source_groups.delete(0, tk.END)
        groups = self.station.config.get('rotation_groups', {})
        for g in sorted(groups.keys()):
            self.lst_source_groups.insert(tk.END, g)

    def open_rotation_editor(self):
        editor = RotationEditor(self.root, self.station.library.keys(), self.refresh_app_data)

    def refresh_app_data(self):
        with open(CONFIG_FILE, 'r') as f:
            self.station.config = json.load(f)
        self.refresh_source_groups()
        self.station.scheduler.rotation_groups = {} 
        self.station.scheduler._init_rotations()    

    def build_settings_tab(self):
        frame = tk.Frame(self.tab_settings, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(frame, text="Library Locations", font=("Arial", 14, "bold")).pack(anchor=tk.W, pady=(0, 20))
        grid_frame = tk.Frame(frame)
        grid_frame.pack(fill=tk.X)
        tk.Label(grid_frame, text="TV Shows Folder:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", pady=10)
        self.var_tv_path = tk.StringVar(value=self.station.config['paths'].get('tv', ''))
        tk.Entry(grid_frame, textvariable=self.var_tv_path, width=60).grid(row=0, column=1, padx=10)
        tk.Button(grid_frame, text="Browse...", command=lambda: self.browse_folder(self.var_tv_path)).grid(row=0, column=2)
        tk.Label(grid_frame, text="Movies Folder:", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", pady=10)
        self.var_movie_path = tk.StringVar(value=self.station.config['paths'].get('movies', ''))
        tk.Entry(grid_frame, textvariable=self.var_movie_path, width=60).grid(row=1, column=1, padx=10)
        tk.Button(grid_frame, text="Browse...", command=lambda: self.browse_folder(self.var_movie_path)).grid(row=1, column=2)
        tk.Label(grid_frame, text="Commercials Folder:", font=("Arial", 10, "bold")).grid(row=2, column=0, sticky="w", pady=10)
        self.var_comm_path = tk.StringVar(value=self.station.config['paths'].get('commercials', ''))
        tk.Entry(grid_frame, textvariable=self.var_comm_path, width=60).grid(row=2, column=1, padx=10)
        tk.Button(grid_frame, text="Browse...", command=lambda: self.browse_folder(self.var_comm_path)).grid(row=2, column=2)
        tk.Label(frame, text="Note: Changing folders will trigger a full library rescan.", fg="gray").pack(anchor=tk.W, pady=(30, 5))
        tk.Button(frame, text="💾 SAVE CONFIGURATION", bg="#2196F3", fg="white", font=("Arial", 12, "bold"), height=2, command=self.save_paths).pack(anchor=tk.W, fill=tk.X)

    def browse_folder(self, string_var):
        folder = filedialog.askdirectory()
        if folder:
            folder = os.path.normpath(folder)
            string_var.set(folder)

    def save_paths(self):
        new_paths = {
            "tv": self.var_tv_path.get(),
            "movies": self.var_movie_path.get(),
            "commercials": self.var_comm_path.get()
        }
        self.station.config["paths"] = new_paths
        self.station.save_config()
        try:
            messagebox.showinfo("Please Wait", "Rescanning libraries... this may take a moment.")
            self.root.update() 
            self.station.load_components()
            self.refresh_source_groups() 
            self.lst_source_shows.delete(0, tk.END)
            for s in sorted(self.station.library.keys()):
                self.lst_source_shows.insert(tk.END, s)
            self.lst_source_movies.delete(0, tk.END)
            if hasattr(self.station, 'movie_map'):
                for m_name in sorted(self.station.movie_map.keys()):
                    self.lst_source_movies.insert(tk.END, m_name)
            self.series_list.delete(0, tk.END)
            for s in sorted(self.station.library.keys()):
                self.series_list.insert(tk.END, s)
            messagebox.showinfo("Success", "Configuration saved and libraries rescanned!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to rescan libraries: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = StationManagerApp(root)
    root.mainloop()