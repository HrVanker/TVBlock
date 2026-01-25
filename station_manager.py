import tkinter as tk
from tkinter import ttk, messagebox, Menu
from rotation_editor import RotationEditor
from tkinter import ttk, messagebox, Menu, filedialog
import tv_player
import json
import os
import sys
import threading
import time
import os
import sys

# --- PORTABLE VLC SETUP (Must be before 'import vlc') ---
# 1. Check if we are running as a bundled exe or a script
if getattr(sys, 'frozen', False):
    # Running as PyInstaller EXE
    app_dir = os.path.dirname(sys.executable)
else:
    # Running as script
    app_dir = os.path.dirname(os.path.abspath(__file__))

# 2. Construct path to the 'vlc_portable' folder
# (We expect this folder to sit right next to TVStation.exe)
vlc_path = os.path.join(app_dir, "vlc_portable")

# 3. If that folder exists, force Python to use it
if os.path.exists(vlc_path):
    # For Python 3.8+ on Windows, we must explicitly trust this DLL directory
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(vlc_path)
    
    # Set the environment variable python-vlc checks
    os.environ['PYTHON_VLC_LIB_PATH'] = os.path.join(vlc_path, "libvlc.dll")
    os.environ['PYTHON_VLC_MODULE_PATH'] = vlc_path
# --------------------------------------------------------
import vlc
import datetime

# Import our custom modules
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
    """
    Runs the VLC Player in a background thread so the GUI doesn't freeze.
    """
    def __init__(self, gui_app):
        self.gui = gui_app
        self.running = False
        self.skip_flag = False
        self.current_meta = {"title": "Offline", "show": "", "percent": 0}
        
        # Load Components
        self.load_components()

    def load_components(self):
        # 1. CREATE CONFIG IF MISSING
        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4)
            except Exception as e:
                print(f"Error creating default config: {e}")

        # 2. LOAD CONFIG
        with open(CONFIG_FILE, 'r') as f:
            self.config = json.load(f)
            
        # Ensure blacklist exists (for legacy configs)
        if "blacklist" not in self.config:
            self.config["blacklist"] = []
            
        # 3. SAFE SCANNING (Check if paths exist first)
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
        # Only init commercial manager if path is valid, else it might crash
        if comm_path and os.path.exists(comm_path):
            self.comm_manager = CommercialManager(comm_path)
        else:
            # Dummy manager if no path
            class DummyComm:
                def generate_break(self, a, b): return []
            self.comm_manager = DummyComm()
        
        # 4. INIT SCHEDULER
        self.scheduler = ScheduleEngine(
            self.library, 
            movie_library=self.movie_library, 
            config_file=CONFIG_FILE
        )

    def start_broadcast(self, window_id):
        if self.running: return
        self.running = True
        self.window_id = window_id # Store it
        self.thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.thread.start()

    def stop_broadcast(self):
        self.running = False

    def skip_current(self):
        self.skip_flag = True

    def save_config(self):
        """Saves current config (blacklist changes) to disk"""
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)
            
        # We also need to tell the scheduler about the change immediately
        # so it doesn't queue up a disabled show in the next buffer refill
        if hasattr(self, 'scheduler'):
            # Force scheduler to reload its internal blacklist set
            self.scheduler._init_queues() 

    def _broadcast_loop(self):
        import platform
        
        # --- SAFE MODE SETTINGS (HDD Friendly) ---
        vlc_args = [
            "--no-video-title-show",
            "--mouse-hide-timeout=0",
            "--quiet",
            
            # INCREASED BUFFER: 10,000ms (10 seconds)
            # This fills RAM with 10s of video. If the drive locks up for 5s,
            # you won't notice because the video plays from RAM.
            "--file-caching=10000", 
            "--network-caching=10000",
            
            "--clock-jitter=0",
            "--clock-synchro=0",
            "--avcodec-hw=any",
            "--avcodec-skiploopfilter=1" 
        ]
        
        vlc_instance = vlc.Instance(vlc_args)
        player = vlc_instance.media_player_new()
        
        sys_platform = platform.system()
        if sys_platform == "Windows":
            player.set_hwnd(self.window_id)
        elif sys_platform == "Linux":
            player.set_xwindow(self.window_id)
        elif sys_platform == "Darwin":
            player.set_nsobject(self.window_id)
        
        # Pre-fetch
        next_content = self.scheduler.get_next_item()
        next_playlist = self._prepare_playlist(next_content)
        
        while self.running:
            # 1. Get Next Item (Sequential, not concurrent)
            current_content = self.scheduler.get_next_item()
            current_playlist = self._prepare_playlist(current_content)
            
            # Update Meta
            if current_content['type'] == 'video':
                filename = os.path.basename(current_content['path'])
                display_name = os.path.splitext(filename)[0]
                self.current_meta = {
                    "title": display_name, 
                    "show": current_content['show'], 
                    "percent": 0
                }
            else:
                self.current_meta = {"title": "Commercial Break", "show": "---", "percent": 0}

            # Play Chunk
            for filepath in current_playlist:
                if not self.running: break
                
                media = vlc_instance.media_new(filepath)
                player.set_media(media)
                player.play()

                time.sleep(2.0)

                # Only check resume for actual videos, not commercials
                if current_content['type'] == 'video':
                    filename = os.path.basename(filepath)
                    history_log = self.scheduler.history.get("playback_log", {})
                    
                    if filename in history_log:
                        data = history_log[filename]
                        if data.get("status") == "partial":
                            pct = data.get("percent_watched", 0)
                            
                            # Only resume if it's a meaningful amount
                            if 5 < pct < 95:
                                # Subtract 2% for context, but don't go below 0%
                                resume_pct = max(0, pct - 2)
                                
                                player.set_position(resume_pct / 100.0)
                                print(f"Resuming {filename} at {resume_pct}% (Rewound from {pct}%)")
                # ------------------------
                
                duration = player.get_length()
                
                # Monitor Loop
                while self.running:
                    state = player.get_state()
                    
                    if duration > 0:
                        curr_time = player.get_time()
                        pct = (curr_time / duration) * 100
                        self.current_meta["percent"] = pct
                    else:
                        pct = 0

                    # --- CASE 1: USER SKIPPED ---
                    if self.skip_flag:
                        # Save partial progress before skipping
                        if current_content['type'] == 'video' and pct > 5:
                            self.update_history(current_content['show'], current_content['path'], "partial", pct)
                        
                        player.stop()
                        self.skip_flag = False
                        break 

                    # --- CASE 2: VIDEO ENDED NATURALLY ---
                    if state == vlc.State.Ended:
                        if current_content['type'] == 'video':
                            self.update_history(current_content['show'], current_content['path'], "watched", 100)
                        break
                    
                    # --- CASE 3: ERROR ---
                    if state == vlc.State.Error:
                        print(f"Playback Error on: {filepath}")
                        break
                        
                    time.sleep(0.5)

                # --- CASE 4: USER STOPPED STATION (Mid-file) ---
                # The 'while self.running' loop broke, meaning user clicked STOP
                if not self.running and current_content['type'] == 'video' and duration > 0:
                    # Retrieve final time before killing player
                    curr_time = player.get_time()
                    if curr_time > 0:
                        final_pct = (curr_time / duration) * 100
                        if final_pct > 5 and final_pct < 95:
                            self.update_history(current_content['show'], current_content['path'], "partial", final_pct)

        player.stop()
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

    def _prepare_playlist(self, content):
        """Helper to convert content item into a list of file paths"""
        playlist = []
        if content['type'] == 'video':
            if content['path']: # Handle empty/dummy paths
                playlist.append(content['path'])
        elif content['type'] == 'break':
            clips = self.comm_manager.generate_break(content['min'], content['max'])
            playlist.extend(clips)
        return playlist

    def update_history(self, show, path, status, percent):
        # Load existing history
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
        
        # Save
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=4)
            
        # Update Scheduler's in-memory history so Library tab refreshes correctly
        self.scheduler.history = self.scheduler._load_history()

class StationManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TV Station Control Panel")
        self.root.geometry("1200x800")
        
        self.station = TVStationService(self)
        self.create_widgets()
        
        # --- NEW: FIRST RUN CHECK ---
        # If TV path is empty, assume first run
        if not self.station.config['paths']['tv']:
            # Force selection of Settings Tab (Index 3)
            self.notebook.select(3) 
            messagebox.showinfo("Welcome!", "Welcome to your TV Station!\n\nIt looks like this is a fresh start. Please select your 'TV Shows Folder' in the settings below to begin.")
        
        self.update_ui_loop()

    def create_widgets(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tab 1: Dashboard
        self.tab_dashboard = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_dashboard, text=" 📡 Live Dashboard ")
        self.build_dashboard_tab()

        # Tab 2: Schedule Editor (NEW!)
        self.tab_schedule = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_schedule, text=" 📅 Schedule Editor ")
        self.build_schedule_tab() # <--- New Function

        # Tab 3: Library
        self.tab_library = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_library, text=" 📂 Library Manager ")
        self.build_library_tab()

        # Tab 4: Global Settings (NEW!)
        self.tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_settings, text=" ⚙ Configuration ")
        self.build_settings_tab()

    def build_dashboard_tab(self):
        # ... (Same as before) ...
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
        
        # Left: Series List
        left = tk.LabelFrame(paned, text="Series")
        paned.add(left, weight=1)
        
        self.series_list = tk.Listbox(left, font=("Arial", 11), selectmode=tk.SINGLE)
        self.series_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.series_list.bind('<<ListboxSelect>>', self.on_series_select)
        
        # Right: Episode Details
        right = tk.LabelFrame(paned, text="Episodes (Right-click to Toggle Disable)")
        paned.add(right, weight=3)
        
        # Define Columns
        columns = ("episode", "status", "last_played")
        self.ep_tree = ttk.Treeview(right, columns=columns, show="headings")
        self.ep_tree.heading("episode", text="Episode Title")
        self.ep_tree.heading("status", text="Playback Status")
        self.ep_tree.heading("last_played", text="Last Played")
        
        self.ep_tree.column("episode", width=400)
        self.ep_tree.column("status", width=120)
        self.ep_tree.column("last_played", width=180)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.ep_tree.yview)
        self.ep_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.ep_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Context Menu
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Toggle Enable/Disable", command=self.toggle_blacklist)
        self.ep_tree.bind("<Button-3>", self.show_context_menu)

        # Style Tags
        self.ep_tree.tag_configure("watched", foreground="green")
        self.ep_tree.tag_configure("partial", foreground="#cfb000") # Gold
        self.ep_tree.tag_configure("disabled", foreground="gray")
        self.ep_tree.tag_configure("normal", foreground="black")

        # Populate Series List
        for s in sorted(self.station.library.keys()):
            self.series_list.insert(tk.END, s)

    def on_series_select(self, event):
        sel = self.series_list.curselection()
        if not sel: return
        show_name = self.series_list.get(sel[0])
        
        # Get data references
        series_data = self.station.library[show_name]
        blacklist = self.station.config.get("blacklist", [])
        
        # Reload history
        self.station.scheduler.history = self.station.scheduler._load_history()
        playback_log = self.station.scheduler.history.get("playback_log", {})
        
        # Clear Tree
        for i in self.ep_tree.get_children(): 
            self.ep_tree.delete(i)
        
        # --- NEW HIERARCHY LOGIC ---
        for season_num in sorted(series_data.keys()):
            # 1. Create the Season Parent Node
            season_id = f"SEASON_ID_{season_num}" # Unique ID for the folder
            season_text = f"Season {season_num}"
            
            # Check if all episodes in this season are disabled (for visual clue)
            all_eps = series_data[season_num]
            disabled_count = sum(1 for ep in all_eps if ep in blacklist)
            
            season_tag = "normal"
            season_status = ""
            if disabled_count == len(all_eps):
                season_tag = "disabled"
                season_status = "⛔ All Disabled"
            elif disabled_count > 0:
                season_status = f"⚠️ {disabled_count} Disabled"

            # Insert Season Row
            self.ep_tree.insert("", tk.END, iid=season_id, text=season_text, open=False, values=(season_text, season_status, ""), tags=(season_tag,))

            # 2. Insert Episodes as Children
            for ep_path in all_eps:
                filename = os.path.basename(ep_path)
                
                # Check status
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

                # Insert Episode (Note: parent=season_id)
                self.ep_tree.insert(season_id, tk.END, iid=ep_path, values=(filename, status, last_played), tags=(tag,))

    def toggle_random(self):
        """Switches the selected item between Sequential and Random"""
        selected = self.sched_tree.selection()
        if not selected: return
        
        item_id = selected[0]
        vals = self.sched_tree.item(item_id)['values']
        # vals = (type, name, count, random)
        
        s_type = vals[0]
        if s_type == "movie": return # Movies handle random differently (via tokens)

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

        # CASE A: User clicked a SEASON FOLDER
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

        # CASE B: User clicked a specific EPISODE
        else:
            file_path = item_id
            if file_path in blacklist:
                blacklist.remove(file_path)
            else:
                blacklist.append(file_path)
            changed = True
        
        if changed:
            self.station.save_config()
            
            # --- STATE PRESERVATION FIX ---
            # 1. Capture which seasons are currently open
            open_seasons = []
            for child in self.ep_tree.get_children():
                if self.ep_tree.item(child, "open"):
                    open_seasons.append(child)
            
            # 2. Refresh the view (rebuilds tree)
            self.on_series_select(None)
            
            # 3. Restore open state
            for season_id in open_seasons:
                if self.ep_tree.exists(season_id):
                    self.ep_tree.item(season_id, open=True)
            
            # 4. Re-select the item you clicked so you don't lose your place
            if self.ep_tree.exists(item_id):
                self.ep_tree.selection_set(item_id)
                self.ep_tree.focus(item_id) # Set focus back to item

    def toggle_station(self):
        if not self.station.running:
            # 1. Create a permanent black window for the video
            self.video_window = tk.Toplevel(self.root)
            self.video_window.title("TV Station Broadcast")
            self.video_window.configure(bg="black")
            
            # 2. Make it Fullscreen
            self.video_window.attributes("-fullscreen", True)
            
            # 3. Handle closing the window manually
            # (If user hits Escape or closes window, stop station)
            self.video_window.bind("<Escape>", lambda e: self.toggle_station())
            self.video_window.protocol("WM_DELETE_WINDOW", self.toggle_station)
            
            # 4. Force window creation to get the ID immediately
            self.video_window.update()
            
            # 5. Get the Window ID (HWND)
            window_id = self.video_window.winfo_id()
            
            # 6. Start Broadcast with this window
            self.station.start_broadcast(window_id)
            self.btn_start.config(text="⏹ STOP STATION", bg="red")
        else:
            self.station.stop_broadcast()
            
            # Destroy the video window if it exists
            if hasattr(self, 'video_window') and self.video_window:
                self.video_window.destroy()
                self.video_window = None
                
            self.btn_start.config(text="▶ START STATION", bg="green")

    def update_ui_loop(self):
        # Update Dashboard
        meta = self.station.current_meta
        self.lbl_show.config(text=meta['show'] if meta['show'] else "---")
        self.lbl_episode.config(text=meta['title'])
        self.progress_var.set(meta['percent'])

        # Update Up Next List (Simple redraw)
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

        # ==========================
        # COL 1: SOURCE BIN
        # ==========================
        col1 = tk.LabelFrame(paned, text="1. Source Bin")
        paned.add(col1, weight=1)

        # A. TV Shows
        tk.Label(col1, text="📺 TV Shows", font=("Arial", 9, "bold")).pack(anchor=tk.W)
        self.lst_source_shows = tk.Listbox(col1, height=6, exportselection=False)
        self.lst_source_shows.pack(fill=tk.X, padx=5)
        for s in sorted(self.station.library.keys()):
            self.lst_source_shows.insert(tk.END, s)

        # B. Rotation Groups
        frame_rot = tk.Frame(col1)
        frame_rot.pack(fill=tk.X, pady=(5,0))
        tk.Label(frame_rot, text="🔄 Rotation Groups", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        tk.Button(frame_rot, text="⚙ Edit", font=("Arial", 8), command=self.open_rotation_editor).pack(side=tk.RIGHT, padx=5)
        
        self.lst_source_groups = tk.Listbox(col1, height=4, exportselection=False)
        self.lst_source_groups.pack(fill=tk.X, padx=5)
        self.refresh_source_groups()

        # C. Movies
        tk.Label(col1, text="🎬 Individual Movies", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(5,0))
        self.lst_source_movies = tk.Listbox(col1, height=6, exportselection=False)
        self.lst_source_movies.pack(fill=tk.X, padx=5)
        if hasattr(self.station, 'movie_map'):
            for m_name in sorted(self.station.movie_map.keys()):
                self.lst_source_movies.insert(tk.END, m_name)

        # D. Tokens
        tk.Label(col1, text="⭐ Special Tokens", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(5,0))
        self.lst_tokens = tk.Listbox(col1, height=2, exportselection=False)
        self.lst_tokens.pack(fill=tk.X, padx=5)
        self.lst_tokens.insert(tk.END, "[Random Movie]")

        btn_add = tk.Button(col1, text="ADD TO BLOCK ➡", bg="#ddd", font=("Arial", 10, "bold"), command=self.add_item_to_schedule)
        btn_add.pack(pady=10, fill=tk.X, padx=20)

        # ==========================
        # COL 2: SCHEDULE SEQUENCE
        # ==========================
        col2 = tk.LabelFrame(paned, text="2. Programming Block")
        paned.add(col2, weight=2)

        # Updated Columns: Added "random"
        columns = ("type", "name", "count", "random")
        self.sched_tree = ttk.Treeview(col2, columns=columns, show="headings", selectmode="browse")
        self.sched_tree.heading("type", text="Type")
        self.sched_tree.heading("name", text="Show / Group")
        self.sched_tree.heading("count", text="#")
        self.sched_tree.heading("random", text="Mode") # New Header
        
        self.sched_tree.column("type", width=50)
        self.sched_tree.column("name", width=200)
        self.sched_tree.column("count", width=30)
        self.sched_tree.column("random", width=60) # New Column
        
        self.sched_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.sched_tree.bind("<Double-1>", self.on_schedule_double_click)

        # Controls
        ctrl_frame = tk.Frame(col2)
        ctrl_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(ctrl_frame, text="▲ Up", command=self.move_up).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        tk.Button(ctrl_frame, text="▼ Down", command=self.move_down).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        # New Toggle Button
        tk.Button(ctrl_frame, text="🎲 Randomize", command=self.toggle_random).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        tk.Button(ctrl_frame, text="❌ Remove", command=self.remove_item, fg="red").pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        # ==========================
        # COL 3: SETTINGS
        # ==========================
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

    # --- LOGIC METHODS ---

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
                random_str = "-" # N/A for movies
                if "path" in slot: name = os.path.basename(slot["path"])
                else: name = "[Random Movie]"
            
            self.sched_tree.insert("", tk.END, values=(s_type, name, count, random_str))

    def add_item_to_schedule(self):
        # Default random to "No" ("")
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
        """Simple popup to edit the Count"""
        item_id = self.sched_tree.identify_row(event.y)
        if not item_id: return
        
        current_vals = self.sched_tree.item(item_id)['values']
        # current_vals is (type, name, count)
        
        if current_vals[0] == "movie": return # Count irrelevant for movie token
        
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
            
            # Save Random Flag
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
        
        # Reload Scheduler
        self.station.scheduler = ScheduleEngine(
            self.station.library, 
            movie_library=self.station.movie_library, 
            config_file=CONFIG_FILE
        )
        messagebox.showinfo("Success", "Schedule updated and station reloaded!")

    def refresh_source_groups(self):
        """Reloads the list of groups from config"""
        self.lst_source_groups.delete(0, tk.END)
        groups = self.station.config.get('rotation_groups', {})
        for g in sorted(groups.keys()):
            self.lst_source_groups.insert(tk.END, g)

    def open_rotation_editor(self):
        """Opens the popup window"""
        # Pass the list of all shows and a callback function
        editor = RotationEditor(self.root, self.station.library.keys(), self.refresh_app_data)

    def refresh_app_data(self):
        """Called when Rotation Editor saves. Reloads config."""
        # Reload config from disk
        with open(CONFIG_FILE, 'r') as f:
            self.station.config = json.load(f)
        
        # Refresh the Source Bin list
        self.refresh_source_groups()
        # Also refresh scheduler internal state
        self.station.scheduler.rotation_groups = {} # Clear
        self.station.scheduler._init_rotations()    # Re-init

    def build_settings_tab(self):
        # Container Frame
        frame = tk.Frame(self.tab_settings, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="Library Locations", font=("Arial", 14, "bold")).pack(anchor=tk.W, pady=(0, 20))

        # --- PATH INPUTS ---
        # We use a grid layout for alignment
        grid_frame = tk.Frame(frame)
        grid_frame.pack(fill=tk.X)

        # 1. TV Shows
        tk.Label(grid_frame, text="TV Shows Folder:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", pady=10)
        self.var_tv_path = tk.StringVar(value=self.station.config['paths'].get('tv', ''))
        tk.Entry(grid_frame, textvariable=self.var_tv_path, width=60).grid(row=0, column=1, padx=10)
        tk.Button(grid_frame, text="Browse...", command=lambda: self.browse_folder(self.var_tv_path)).grid(row=0, column=2)

        # 2. Movies
        tk.Label(grid_frame, text="Movies Folder:", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", pady=10)
        self.var_movie_path = tk.StringVar(value=self.station.config['paths'].get('movies', ''))
        tk.Entry(grid_frame, textvariable=self.var_movie_path, width=60).grid(row=1, column=1, padx=10)
        tk.Button(grid_frame, text="Browse...", command=lambda: self.browse_folder(self.var_movie_path)).grid(row=1, column=2)

        # 3. Commercials
        tk.Label(grid_frame, text="Commercials Folder:", font=("Arial", 10, "bold")).grid(row=2, column=0, sticky="w", pady=10)
        self.var_comm_path = tk.StringVar(value=self.station.config['paths'].get('commercials', ''))
        tk.Entry(grid_frame, textvariable=self.var_comm_path, width=60).grid(row=2, column=1, padx=10)
        tk.Button(grid_frame, text="Browse...", command=lambda: self.browse_folder(self.var_comm_path)).grid(row=2, column=2)

        # --- SAVE BUTTON ---
        tk.Label(frame, text="Note: Changing folders will trigger a full library rescan.", fg="gray").pack(anchor=tk.W, pady=(30, 5))
        tk.Button(frame, text="💾 SAVE CONFIGURATION", bg="#2196F3", fg="white", font=("Arial", 12, "bold"), height=2, command=self.save_paths).pack(anchor=tk.W, fill=tk.X)

    def browse_folder(self, string_var):
        """Opens directory picker and sets the variable"""
        folder = filedialog.askdirectory()
        if folder:
            # Convert to standard path format
            folder = os.path.normpath(folder)
            string_var.set(folder)

    def save_paths(self):
        # Update Config Object
        new_paths = {
            "tv": self.var_tv_path.get(),
            "movies": self.var_movie_path.get(),
            "commercials": self.var_comm_path.get()
        }
        
        self.station.config["paths"] = new_paths
        self.station.save_config()
        
        # Trigger Rescan
        try:
            messagebox.showinfo("Please Wait", "Rescanning libraries... this may take a moment.")
            self.root.update() # Force UI to draw the popup before freezing during scan
            
            # Reload components in the service
            self.station.load_components()
            
            # Refresh GUI Lists
            self.refresh_source_groups() # Re-reads config
            
            # Refresh Source Bin Lists
            self.lst_source_shows.delete(0, tk.END)
            for s in sorted(self.station.library.keys()):
                self.lst_source_shows.insert(tk.END, s)

            self.lst_source_movies.delete(0, tk.END)
            if hasattr(self.station, 'movie_map'):
                for m_name in sorted(self.station.movie_map.keys()):
                    self.lst_source_movies.insert(tk.END, m_name)
                    
            # Refresh Library Tab
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