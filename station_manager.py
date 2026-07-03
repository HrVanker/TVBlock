import tkinter as tk
from tkinter import ttk, messagebox, Menu, filedialog, simpledialog
from PIL import Image
from rotation_editor import RotationEditor
from graphics_engine import GraphicsEngine
import random
import json
import os
import sys
import threading
import logging
from flask import Flask, jsonify, request
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
    "paths": {"tv": "", "movies": "", "commercials": "", "music_videos": ""},
    "blacklist": [],
    "active_channel": "Default Channel",
    "channels": {
        "Default Channel": {
            "settings": {
                "commercial_frequency": 3,
                "commercial_min_sec": 60,
                "commercial_max_sec": 120
            },
            "schedule_block": [],
            "bookmarks": {}
        }
    }
}

# --- THE RESTORED SLOT EDITOR ---
class SlotEditorDialog(tk.Toplevel):
    def __init__(self, parent, s_type, name, current_vals):
        super().__init__(parent)
        self.title(f"Edit Slot: {name}")
        self.geometry("450x250")
        self.result = None
        self.s_type = s_type
        
        # Parse current values (type, name, count, mode, sync, override)
        c_count = int(current_vals[2]) if current_vals[2] else 1
        c_mode = current_vals[3] if current_vals[3] else "sequential"
        c_sync = True if current_vals[4] == "Yes" else False
        c_override = current_vals[5] if len(current_vals) > 5 and current_vals[5] else ""

        frame = tk.Frame(self, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="Play Count:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky=tk.W, pady=5)
        self.var_count = tk.IntVar(value=c_count)
        tk.Spinbox(frame, from_=1, to=10, textvariable=self.var_count, width=10).grid(row=0, column=1, sticky=tk.W)

        tk.Label(frame, text="Playback Mode:", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky=tk.W, pady=5)
        self.var_mode = tk.StringVar(value=c_mode)
        
        # Only TV Shows support complex modes. Movies/Music Videos default to Random
        if s_type in ["movie", "music_video"]:
            tk.Label(frame, text="Random").grid(row=1, column=1, sticky=tk.W)
            self.var_mode.set("random")
            self.var_sync = tk.BooleanVar(value=False)
            self.var_override = tk.StringVar(value="")
        else:
            modes = ["sequential", "random", "random_no_reruns"]
            ttk.Combobox(frame, textvariable=self.var_mode, values=modes, state="readonly", width=20).grid(row=1, column=1, sticky=tk.W)

            self.var_sync = tk.BooleanVar(value=c_sync)
            tk.Checkbutton(frame, text="Sync with Global History", variable=self.var_sync).grid(row=2, column=1, sticky=tk.W, pady=5)

            tk.Label(frame, text="Override Start File:", font=("Arial", 10, "bold")).grid(row=3, column=0, sticky=tk.W, pady=5)
            self.var_override = tk.StringVar(value=c_override)
            tk.Entry(frame, textvariable=self.var_override, width=25).grid(row=3, column=1, sticky=tk.W)
            tk.Button(frame, text="Browse", command=self.browse_file).grid(row=3, column=2, padx=5)

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Save Settings", bg="green", fg="white", command=self.save).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT)

        self.transient(parent)
        self.grab_set()
        self.wait_window(self)

    def browse_file(self):
        path = filedialog.askopenfilename(title="Select Starting Episode")
        if path:
            self.var_override.set(path)

    def save(self):
        self.result = {
            "count": self.var_count.get(),
            "mode": self.var_mode.get(),
            "sync_global": self.var_sync.get(),
            "override_start": self.var_override.get()
        }
        self.destroy()

class TVStationService:
    def __init__(self, gui_app):
        self.gui = gui_app
        self.running = False
        self.skip_flag = False
        self.current_meta = {"title": "Offline", "show": "", "percent": 0}
        self.gfx_engine = GraphicsEngine()
        self.load_components()
        self.start_ipc_server()

    def start_ipc_server(self):
        """Runs a background API server so the Discord bot can control the station."""
        app = Flask(__name__)
        
        # Silence Flask's default terminal spam so it doesn't mess up our logs
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

        @app.route('/status', methods=['GET'])
        def get_status():
            if not self.running:
                return jsonify({"now_playing": {"title": "Offline"}}), 200
                
            upcoming = self.scheduler.get_upcoming_list(limit=5) if self.scheduler else []
            return jsonify({
                "now_playing": self.current_meta,
                "upcoming": upcoming
            }), 200

        @app.route('/skip', methods=['GET'])
        def skip_item():
            self.skip_current()
            return jsonify({"status": "skipped"}), 200

        @app.route('/inject', methods=['POST'])
        def inject_item():
            data = request.json
            if self.scheduler and 'slot' in data:
                insert_next = data.get('insert_next', True)
                self.scheduler.inject_slot(data['slot'], insert_next)
            return jsonify({"status": "injected"}), 200

        @app.route('/sync', methods=['POST'])
        def sync_channels():
            # 1. Merge the newly exported JSON files into the master config
            self.gui.import_discord_channels_to_config()
            
            # 2. Safely tell the main Tkinter UI thread to update itself!
            self.gui.root.after(0, self.gui.sync_from_bot)
            return jsonify({"status": "synced"}), 200

        # Run it on Port 8000 in a daemon thread so it closes when the app closes
        self.ipc_thread = threading.Thread(
            target=lambda: app.run(host='127.0.0.1', port=8000, debug=False, use_reloader=False), 
            daemon=True
        )
        self.ipc_thread.start()

    def load_components(self):
        if not os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4)
            except Exception as e: print(f"Error creating default config: {e}")

        with open(CONFIG_FILE, 'r') as f: self.config = json.load(f)
            
        if "blacklist" not in self.config: self.config["blacklist"] = []
            
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
            for m in self.movie_library: self.movie_map[os.path.basename(m)] = m
            
        # RESTORED: MUSIC VIDEO SCANNER
        self.music_video_library = []
        self.music_video_map = {}
        mv_path = self.config['paths'].get('music_videos', '')
        if mv_path and os.path.exists(mv_path):
            # Using standard movie scan if scan_music_videos isn't updated in InventoryManager yet
            self.music_video_library = scanner.scan_movies(mv_path) if not hasattr(scanner, 'scan_music_videos') else scanner.scan_music_videos(mv_path)
            for mv in self.music_video_library: self.music_video_map[os.path.basename(mv)] = mv

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
            music_video_library=self.music_video_library,
            config_file=CONFIG_FILE
        )

    def start_broadcast(self, window_id):
        if self.running: return
        self.running = True
        self.window_id = window_id
        self.thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.thread.start()

    def stop_broadcast(self): self.running = False

    def skip_current(self):
        if self.running:
            print("USER COMMAND: SKIP")
            self.skip_flag = True

    def save_config(self):
        with open(CONFIG_FILE, 'w') as f: json.dump(self.config, f, indent=4)
        if hasattr(self, 'scheduler'): self.scheduler.hot_reload()

    def _get_random_bug_filter(self):
        bug_dir = os.path.join(app_dir, "assets", "bugs")
        if not os.path.exists(bug_dir): return None
        bugs = [f for f in os.listdir(bug_dir) if f.lower().endswith('.gif')]
        if not bugs: return None
            
        selected_bug = random.choice(bugs)
        bug_path = os.path.join(bug_dir, selected_bug)
        bug_path_ffmpeg = bug_path.replace("\\", "/").replace(":", "\\:")
        bug_height = 50
        return f"lavfi=[movie=filename='{bug_path_ffmpeg}':loop=0,scale=-1:{bug_height},setsar=1[logo];[in][logo]overlay=W-w-25:H-h-25]"

    def _broadcast_loop(self):
        player = None
        try:
            player = mpv.MPV(
                af='lavfi=[dynaudnorm=f=75:g=31:n=0:p=0.58]', wid=self.window_id,
                input_default_bindings=True, input_vo_keyboard=True,
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
                if current_content['type'] == 'break':
                    real_comm_duration = 15 
                    from tinytag import TinyTag
                    for clip in current_playlist:
                        try:
                            tag = TinyTag.get(clip)
                            if tag.duration: real_comm_duration += tag.duration
                        except: real_comm_duration += 30 
                            
                    comm_duration = int(real_comm_duration)
                    
                    active_chan = self.config.get("active_channel", "Default Channel")
                    chan_settings = self.config.get("channels", {}).get(active_chan, {}).get("settings", {})
                    comm_freq = chan_settings.get("commercial_frequency", 3)
                    upcoming_shows = self.scheduler.get_upcoming_durations(limit=comm_freq)

                    bg_folder = os.path.join(app_dir, "assets", "bg")
                    bumper_bg = None
                    if os.path.exists(bg_folder):
                        valid_exts = ('.mp4', '.mkv', '.avi', '.mov')
                        files = [f for f in os.listdir(bg_folder) if f.lower().endswith(valid_exts)]
                        if files: bumper_bg = os.path.join(bg_folder, random.choice(files))
                    if not bumper_bg: bumper_bg = os.path.join(app_dir, "assets", "up_next_bg.mp4")

                    music_folder = os.path.join(app_dir, "assets", "music")
                    bumper_music = None
                    if os.path.exists(music_folder):
                        valid_audio = ('.mp3', '.wav', '.ogg', '.m4a', '.flac')
                        music_files = [f for f in os.listdir(music_folder) if f.lower().endswith(valid_audio)]
                        if music_files: bumper_music = os.path.join(music_folder, random.choice(music_files))

                    player.play(bumper_bg)
                    timeout = time.time() + 5.0 
                    while player.dwidth is None and time.time() < timeout and self.running: time.sleep(0.1) 
                    bg_width = 1920
                    bg_height = 1080

                    if bumper_music:
                        try:
                            music_path_mpv = bumper_music.replace("\\", "/")
                            player.command("audio-add", music_path_mpv, "select")
                            player.volume = 75  
                        except: pass

                    temp_overlay = os.path.join(app_dir, "assets", "temp_overlay.png")
                    self.gfx_engine.generate_transparent_bumper(upcoming_shows, comm_duration, output_path=temp_overlay, target_width=bg_width, target_height=bg_height)

                    temp_overlay_ffmpeg = temp_overlay.replace("\\", "/").replace(":", "\\:")
                    bumper_filter = f"lavfi=[movie=filename='{temp_overlay_ffmpeg}'[logo];[in][logo]overlay=0:0]"
                    
                    try:
                        img = Image.open(temp_overlay).convert("RGBA")
                        r, g, b, a = img.split()
                        img_bgra = Image.merge("RGBA", (b, g, r, a))
                        bgra_path = os.path.join(app_dir, "assets", "temp_overlay.bgra")
                        with open(bgra_path, "wb") as f: f.write(img_bgra.tobytes())
                        w, h = img.size
                        player.command("overlay-add", 1, 0, 0, bgra_path.replace("\\", "/"), 0, "bgra", w, h, w * 4)
                    except Exception as e: print(f"DEBUG: Native OSD Bumper Error: {e}")

                    bug_filter = self._get_random_bug_filter()
                    if bug_filter:
                        # UNSILENCED: Let's see why the bug is crashing
                        try: player.command("vf", "add", f"@stationbug:{bug_filter}")
                        except Exception as e: print(f"DEBUG: Station Bug Filter Error (Bumper): {e}")

                    bumper_start_time = time.time()
                    bumper_duration = 14  
                    
                    while not getattr(player, 'idle_active', True) and self.running:
                        if self.skip_flag:
                            player.command("stop")
                            self.skip_flag = False
                            break
                        if time.time() - bumper_start_time >= bumper_duration:
                            player.command("stop")
                            break
                        time.sleep(0.1)

                    player.volume = 100
                    try: player.command("overlay-remove", 1) 
                    except: pass
                    try: player.command("vf", "remove", "@stationbug")
                    except: pass

                # --- PLAY CHUNK (Shows or Commercials) ---
                for filepath in current_playlist:
                    if not self.running: break
                    player.play(filepath)
                    time.sleep(0.5)
                    
                    # --- STREAMLINED MONITOR LOOP ---
                    while not getattr(player, 'idle_active', True) and self.running:
                        
                        # 1. Check for manual user skip
                        if self.skip_flag:
                            duration = player.duration if player.duration else 0
                            curr_time = player.time_pos if player.time_pos else 0
                            pct = (curr_time / duration) * 100 if duration > 0 else 0
                            
                            # Log partial watch if skipped
                            if current_content['type'] == 'video' and pct > 5:
                                self.update_history(current_content['show'], current_content['path'], "partial", pct)
                            
                            player.command("stop")
                            self.skip_flag = False
                            break 
                        
                        # 2. Update GUI Progress Bar
                        duration = player.duration if player.duration else 0
                        if duration > 0:
                            curr_time = player.time_pos if player.time_pos else 0
                            self.current_meta["percent"] = (curr_time / duration) * 100
                        
                        time.sleep(0.1)

                    # 3. Log completed watch
                    if current_content['type'] == 'video' and not self.skip_flag and self.running:
                        self.update_history(current_content['show'], current_content['path'], "watched", 100)

        except Exception as e:
            print(f"DEBUG: Broadcast Loop Terminated Safely.")
        finally:
            if player:
                try: player.terminate()
                except: pass
            self.current_meta = {"title": "Offline", "show": "", "percent": 0}

    def _prepare_playlist(self, content):
        playlist = []
        if content['type'] == 'video':
            if content.get('path'): playlist.append(content['path'])
        elif content['type'] == 'break':
            clips = self.comm_manager.generate_break(content['min'], content['max'])
            playlist.extend(clips)
        return playlist

    def update_history(self, show, path, status, percent):
        history = {}
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f: history = json.load(f)
            except: pass
        if "playback_log" not in history: history["playback_log"] = {}
        filename = os.path.basename(path)
        entry = {
            "show": show, "path": path, "status": status,
            "percent_watched": round(percent, 2), "last_played": str(datetime.datetime.now())
        }
        history["playback_log"][filename] = entry
        with open(HISTORY_FILE, 'w') as f: json.dump(history, f, indent=4)
        self.scheduler.history = self.scheduler._load_json(HISTORY_FILE)

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

    def sync_from_bot(self):
        """Called automatically when the Discord bot publishes a new channel."""
        try:
            # 1. Re-read the newly exported JSON from the hard drive
            with open(CONFIG_FILE, 'r') as f:
                self.station.config = json.load(f)
            
            # 2. Refresh the UI dropdown menu to show the new channels
            self.refresh_channel_dropdown()
            
            # 3. Hot-reload the engine just in case the actively playing channel was modified
            if hasattr(self.station, 'scheduler'):
                self.station.scheduler.config = self.station.config
                self.station.scheduler.hot_reload()
                self.load_channel_data()
                
            print("DEBUG: Successfully synced new channels from Discord Bot!")
        except Exception as e:
            print(f"DEBUG: Failed to sync from bot: {e}")

    def import_discord_channels_to_config(self):
        """Scans the discord_channels folder and merges them into the main config."""
        discord_dir = os.path.join(app_dir, "discord_channels")
        if not os.path.exists(discord_dir): 
            return
            
        changed = False
        for filename in os.listdir(discord_dir):
            if filename.endswith(".json"):
                channel_name = filename.replace(".json", "")
                file_path = os.path.join(discord_dir, filename)
                try:
                    with open(file_path, 'r') as f:
                        channel_data = json.load(f)
                        
                    if "channels" not in self.station.config:
                        self.station.config["channels"] = {}
                        
                    # Inject or update the channel in the master config
                    if channel_name not in self.station.config["channels"]:
                        self.station.config["channels"][channel_name] = {
                            "settings": channel_data.get("settings", {}),
                            "schedule_block": channel_data.get("schedule_block", []),
                            "bookmarks": {} 
                        }
                    else:
                        # If it exists, update the schedule but preserve bookmarks
                        self.station.config["channels"][channel_name]["settings"] = channel_data.get("settings", {})
                        self.station.config["channels"][channel_name]["schedule_block"] = channel_data.get("schedule_block", [])
                        
                    changed = True
                except Exception as e:
                    print(f"DEBUG: Failed to import Discord channel {filename}: {e}")
                    
        if changed:
            self.station.save_config()
            print("DEBUG: Merged Discord channels into master config!")

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
        for s in sorted(self.station.library.keys()): self.series_list.insert(tk.END, s)

    def on_series_select(self, event):
        sel = self.series_list.curselection()
        if not sel: return
        show_name = self.series_list.get(sel[0])
        series_data = self.station.library[show_name]
        blacklist = self.station.config.get("blacklist", [])
        playback_log = self.station.scheduler.history.get("playback_log", {})
        for i in self.ep_tree.get_children(): self.ep_tree.delete(i)
        for season_num in sorted(series_data.keys()):
            season_id = f"SEASON_ID_{season_num}" 
            season_text = f"Season {season_num}"
            all_eps = series_data[season_num]
            disabled_count = sum(1 for ep in all_eps if ep in blacklist)
            season_tag, season_status = "normal", ""
            if disabled_count == len(all_eps):
                season_tag, season_status = "disabled", "⛔ All Disabled"
            elif disabled_count > 0: season_status = f"⚠️ {disabled_count} Disabled"
            self.ep_tree.insert("", tk.END, iid=season_id, text=season_text, open=False, values=(season_text, season_status, ""), tags=(season_tag,))
            for ep_path in all_eps:
                filename = os.path.basename(ep_path)
                if ep_path in blacklist:
                    status, last_played, tag = "⛔ DISABLED", "-", "disabled"
                else:
                    status, last_played, tag = "Unwatched", "-", "normal"
                    if filename in playback_log:
                        data = playback_log[filename]
                        pct = data.get('percent_watched', 0)
                        if data['status'] == 'watched': status, tag = "✅ Watched", "watched"
                        elif data['status'] == 'partial': status, tag = f"⏸ Partial ({int(pct)}%)", "partial"
                        raw_date = data.get('last_played', '')
                        if raw_date: last_played = raw_date.split('.')[0]
                self.ep_tree.insert(season_id, tk.END, iid=ep_path, values=(filename, status, last_played), tags=(tag,))

    def show_context_menu(self, event):
        item = self.ep_tree.identify_row(event.y)
        if item:
            self.ep_tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def toggle_blacklist(self):
        selected = self.ep_tree.selection()
        if not selected: return
        item_id = selected[0]
        blacklist = self.station.config["blacklist"]
        changed = False
        if item_id.startswith("SEASON_ID_"):
            children = self.ep_tree.get_children(item_id)
            any_enabled = False
            for c in children:
                if c not in blacklist:
                    any_enabled = True
                    break
            for c in children:
                if any_enabled and c not in blacklist:
                    blacklist.append(c)
                    changed = True
                elif not any_enabled and c in blacklist:
                    blacklist.remove(c)
                    changed = True
        else:
            if item_id in blacklist: blacklist.remove(item_id)
            else: blacklist.append(item_id)
            changed = True
        if changed:
            self.station.save_config()
            open_seasons = [c for c in self.ep_tree.get_children() if self.ep_tree.item(c, "open")]
            self.on_series_select(None)
            for s_id in open_seasons:
                if self.ep_tree.exists(s_id): self.ep_tree.item(s_id, open=True)
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
        for item in self.up_next_tree.get_children(): self.up_next_tree.delete(item)
        upcoming = self.station.scheduler.get_upcoming_list()
        for item in upcoming:
            show = item.get('show', '---')
            title = item.get('display', 'Unknown')
            if item['type'] == 'break':
                show = "COMMERCIALS"
                title = f"{item['min']}s - {item['max']}s Block"
            self.up_next_tree.insert("", tk.END, values=(show, title))
        self.root.after(1000, self.update_ui_loop)

    # --- RESTORED: MULTI-CHANNEL SCHEDULE TAB ---
    def build_schedule_tab(self):
        top_bar = tk.Frame(self.tab_schedule, bg="#ddd", pady=10)
        top_bar.pack(fill=tk.X)
        
        tk.Label(top_bar, text="Active Channel: ", bg="#ddd", font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=(20, 5))
        self.channel_var = tk.StringVar()
        self.cb_channels = ttk.Combobox(top_bar, textvariable=self.channel_var, state="readonly", width=30)
        self.cb_channels.pack(side=tk.LEFT, padx=5)
        self.cb_channels.bind("<<ComboboxSelected>>", self.change_channel)
        
        tk.Button(top_bar, text="➕ New Channel", command=self.create_channel).pack(side=tk.LEFT, padx=10)
        tk.Button(top_bar, text="❌ Delete", fg="red", command=self.delete_channel).pack(side=tk.LEFT)

        paned = ttk.PanedWindow(self.tab_schedule, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        col1 = tk.LabelFrame(paned, text="1. Source Bin")
        paned.add(col1, weight=1)
        
        tk.Label(col1, text="📺 TV Shows", font=("Arial", 9, "bold")).pack(anchor=tk.W)
        self.lst_source_shows = tk.Listbox(col1, height=6, exportselection=False)
        self.lst_source_shows.pack(fill=tk.X, padx=5)
        for s in sorted(self.station.library.keys()): self.lst_source_shows.insert(tk.END, s)
        
        frame_rot = tk.Frame(col1)
        frame_rot.pack(fill=tk.X, pady=(5,0))
        tk.Label(frame_rot, text="🔄 Rotation Groups", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        tk.Button(frame_rot, text="⚙ Edit", font=("Arial", 8), command=self.open_rotation_editor).pack(side=tk.RIGHT, padx=5)
        self.lst_source_groups = tk.Listbox(col1, height=4, exportselection=False)
        self.lst_source_groups.pack(fill=tk.X, padx=5)
        self.refresh_source_groups()
        
        tk.Label(col1, text="🎬 Movies", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(5,0))
        self.lst_source_movies = tk.Listbox(col1, height=4, exportselection=False)
        self.lst_source_movies.pack(fill=tk.X, padx=5)
        if hasattr(self.station, 'movie_map'):
            for m_name in sorted(self.station.movie_map.keys()): self.lst_source_movies.insert(tk.END, m_name)

        tk.Label(col1, text="🎸 Music Videos", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(5,0))
        self.lst_source_mvs = tk.Listbox(col1, height=4, exportselection=False)
        self.lst_source_mvs.pack(fill=tk.X, padx=5)
        if hasattr(self.station, 'music_video_map'):
            for mv_name in sorted(self.station.music_video_map.keys()): self.lst_source_mvs.insert(tk.END, mv_name)
            
        tk.Label(col1, text="⭐ Special Tokens", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(5,0))
        self.lst_tokens = tk.Listbox(col1, height=2, exportselection=False)
        self.lst_tokens.pack(fill=tk.X, padx=5)
        self.lst_tokens.insert(tk.END, "[Random Movie]")
        self.lst_tokens.insert(tk.END, "[Random Music Video]")
        
        btn_add = tk.Button(col1, text="ADD TO CHANNEL ➡", bg="#ddd", font=("Arial", 10, "bold"), command=self.add_item_to_schedule)
        btn_add.pack(pady=10, fill=tk.X, padx=20)
        
        col2 = tk.LabelFrame(paned, text="2. Channel Programming Block")
        paned.add(col2, weight=3)
        
        # RESTORED: 6 COLUMN TREEVIEW
        columns = ("type", "name", "count", "mode", "sync", "override")
        self.sched_tree = ttk.Treeview(col2, columns=columns, show="headings", selectmode="browse")
        self.sched_tree.heading("type", text="Type")
        self.sched_tree.heading("name", text="Show / Group")
        self.sched_tree.heading("count", text="#")
        self.sched_tree.heading("mode", text="Playback Mode") 
        self.sched_tree.heading("sync", text="Global Sync") 
        self.sched_tree.heading("override", text="Override Start") 
        
        self.sched_tree.column("type", width=50)
        self.sched_tree.column("name", width=200)
        self.sched_tree.column("count", width=30)
        self.sched_tree.column("mode", width=120) 
        self.sched_tree.column("sync", width=80) 
        self.sched_tree.column("override", width=120) 
        
        self.sched_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.sched_tree.bind("<Double-1>", self.on_schedule_double_click)
        
        ctrl_frame = tk.Frame(col2)
        ctrl_frame.pack(fill=tk.X, pady=5)
        tk.Button(ctrl_frame, text="▲ Up", command=self.move_up).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        tk.Button(ctrl_frame, text="▼ Down", command=self.move_down).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        tk.Button(ctrl_frame, text="❌ Remove", command=self.remove_item, fg="red").pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        
        col3 = tk.LabelFrame(paned, text="3. Channel Settings")
        paned.add(col3, weight=1)
        
        tk.Label(col3, text="Commercial Frequency (Items)", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10,0), padx=5)
        self.var_comm_freq = tk.IntVar()
        tk.Scale(col3, variable=self.var_comm_freq, from_=1, to=10, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=5)
        tk.Label(col3, text="Min Duration (Seconds)", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10,0), padx=5)
        self.var_comm_min = tk.IntVar()
        tk.Entry(col3, textvariable=self.var_comm_min).pack(fill=tk.X, padx=5)
        tk.Label(col3, text="Max Duration (Seconds)", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10,0), padx=5)
        self.var_comm_max = tk.IntVar()
        tk.Entry(col3, textvariable=self.var_comm_max).pack(fill=tk.X, padx=5)
        tk.Button(col3, text="💾 SAVE CHANNEL", bg="green", fg="white", font=("Arial", 12, "bold"), height=2, command=self.save_full_schedule).pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=20)
        
        self.refresh_channel_dropdown()

    def refresh_channel_dropdown(self):
        channels = list(self.station.config.get("channels", {}).keys())
        self.cb_channels['values'] = channels
        active = self.station.config.get("active_channel", "Default Channel")
        if active in channels:
            self.channel_var.set(active)
            self.load_channel_data()

    def create_channel(self):
        name = simpledialog.askstring("New Channel", "Enter channel name:")
        if name and name not in self.station.config["channels"]:
            self.station.config["channels"][name] = {
                "settings": {"commercial_frequency": 3, "commercial_min_sec": 60, "commercial_max_sec": 120},
                "schedule_block": [], "bookmarks": {}
            }
            self.station.config["active_channel"] = name
            self.station.save_config()
            self.refresh_channel_dropdown()

    def delete_channel(self):
        target = self.channel_var.get()
        if target == "Default Channel":
            messagebox.showwarning("Warning", "Cannot delete the Default Channel.")
            return
        if messagebox.askyesno("Confirm", f"Delete the channel '{target}'?"):
            del self.station.config["channels"][target]
            self.station.config["active_channel"] = "Default Channel"
            self.station.save_config()
            self.refresh_channel_dropdown()

    def change_channel(self, event=None):
        new_channel = self.channel_var.get()
        self.station.config["active_channel"] = new_channel
        self.station.save_config()
        self.station.scheduler.hot_reload()
        self.load_channel_data()

    def load_channel_data(self):
        for i in self.sched_tree.get_children(): self.sched_tree.delete(i)
        
        active = self.channel_var.get()
        chan_data = self.station.config.get("channels", {}).get(active, {})
        
        settings = chan_data.get("settings", {})
        self.var_comm_freq.set(settings.get("commercial_frequency", 3))
        self.var_comm_min.set(settings.get("commercial_min_sec", 60))
        self.var_comm_max.set(settings.get("commercial_max_sec", 120))
        
        block = chan_data.get("schedule_block", [])
        for slot in block:
            s_type = slot.get("type", "anchor")
            name = ""
            count = slot.get("count", 1)
            mode = slot.get("mode", "sequential")
            sync_str = "Yes" if slot.get("sync_global", False) else "No"
            
            # FIX: Keep the full path so save_full_schedule doesn't corrupt it!
            override = slot.get("override_start", "")
            
            if s_type == "anchor": name = slot.get("show", "Unknown")
            elif s_type == "rotate": name = slot.get("group", "Unknown")
            elif s_type in ["movie", "music_video"]: 
                if "path" in slot: name = os.path.basename(slot["path"])
                else: name = "[Random Movie]" if s_type == "movie" else "[Random Music Video]"
                sync_str = "-"
                
            self.sched_tree.insert("", tk.END, values=(s_type, name, count, mode, sync_str, override))

    def on_schedule_double_click(self, event):
        item_id = self.sched_tree.identify_row(event.y)
        if not item_id: return
        
        vals = self.sched_tree.item(item_id)['values']
        s_type = vals[0]
        name = vals[1]
        
        dialog = SlotEditorDialog(self.root, s_type, name, vals)
        if dialog.result:
            r = dialog.result
            sync_str = "Yes" if r["sync_global"] else "No"
            ovr_str = r["override_start"]
            if s_type in ["movie", "music_video"]:
                sync_str = "-"
                ovr_str = ""
            
            self.sched_tree.item(item_id, values=(s_type, name, r["count"], r["mode"], sync_str, ovr_str))

    def add_item_to_schedule(self):
        if self.lst_source_shows.curselection():
            name = self.lst_source_shows.get(self.lst_source_shows.curselection())
            self.sched_tree.insert("", tk.END, values=("anchor", name, 1, "sequential", "No", ""))
            self.lst_source_shows.selection_clear(0, tk.END)
        elif self.lst_source_groups.curselection():
            name = self.lst_source_groups.get(self.lst_source_groups.curselection())
            self.sched_tree.insert("", tk.END, values=("rotate", name, 1, "sequential", "No", ""))
            self.lst_source_groups.selection_clear(0, tk.END)
        elif self.lst_source_movies.curselection():
            name = self.lst_source_movies.get(self.lst_source_movies.curselection())
            self.sched_tree.insert("", tk.END, values=("movie", name, 1, "random", "-", ""))
            self.lst_source_movies.selection_clear(0, tk.END)
        elif self.lst_source_mvs.curselection():
            name = self.lst_source_mvs.get(self.lst_source_mvs.curselection())
            self.sched_tree.insert("", tk.END, values=("music_video", name, 1, "random", "-", ""))
            self.lst_source_mvs.selection_clear(0, tk.END)
        elif self.lst_tokens.curselection():
            name = self.lst_tokens.get(self.lst_tokens.curselection())
            if name == "[Random Movie]":
                self.sched_tree.insert("", tk.END, values=("movie", name, 1, "random", "-", ""))
            elif name == "[Random Music Video]":
                self.sched_tree.insert("", tk.END, values=("music_video", name, 1, "random", "-", ""))
            self.lst_tokens.selection_clear(0, tk.END)

    def remove_item(self):
        selected = self.sched_tree.selection()
        if selected: self.sched_tree.delete(selected[0])

    def move_up(self):
        rows = self.sched_tree.selection()
        for row in rows: self.sched_tree.move(row, self.sched_tree.parent(row), self.sched_tree.index(row)-1)

    def move_down(self):
        rows = self.sched_tree.selection()
        for row in reversed(rows): self.sched_tree.move(row, self.sched_tree.parent(row), self.sched_tree.index(row)+1)

    def save_full_schedule(self):
        active = self.channel_var.get()
        if not active: return

        new_block = []
        for item_id in self.sched_tree.get_children():
            vals = self.sched_tree.item(item_id)['values']
            s_type = vals[0]
            name = vals[1]
            
            slot = {
                "type": s_type, 
                "count": int(vals[2]),
                "mode": vals[3],
                "sync_global": True if vals[4] == "Yes" else False
            }
            
            if vals[5]: slot["override_start"] = vals[5]

            if s_type == "anchor": slot["show"] = name
            elif s_type == "rotate": slot["group"] = name
            elif s_type == "movie":
                if name == "[Random Movie]": pass
                elif name in self.station.movie_map: slot["path"] = self.station.movie_map[name]
            elif s_type == "music_video":
                if name == "[Random Music Video]": pass
                elif hasattr(self.station, 'music_video_map') and name in self.station.music_video_map: 
                    slot["path"] = self.station.music_video_map[name]

            new_block.append(slot)
            
        chan_data = self.station.config["channels"][active]
        chan_data["schedule_block"] = new_block
        chan_data["settings"]["commercial_frequency"] = self.var_comm_freq.get()
        chan_data["settings"]["commercial_min_sec"] = self.var_comm_min.get()
        chan_data["settings"]["commercial_max_sec"] = self.var_comm_max.get()

        # FIX: Ensure the config's active channel matches the one we just saved
        self.station.config["active_channel"] = active
        self.station.save_config()
        
        # FIX: Hot reload the existing engine instead of destroying it!
  #      self.station.scheduler.hot_reload()
        
        self.station.save_config()
        # HOT RELOAD ENGAGED!
        self.station.scheduler.hot_reload()
        messagebox.showinfo("Success", f"Channel '{active}' updated and station reloaded!")

    def refresh_source_groups(self):
        self.lst_source_groups.delete(0, tk.END)
        groups = self.station.config.get('rotation_groups', {})
        for g in sorted(groups.keys()): self.lst_source_groups.insert(tk.END, g)

    def open_rotation_editor(self):
        editor = RotationEditor(self.root, self.station.library.keys(), self.refresh_app_data)

    def refresh_app_data(self):
        with open(CONFIG_FILE, 'r') as f: self.station.config = json.load(f)
        self.refresh_source_groups()
        self.station.scheduler.rotation_groups = self.station.config.get("rotation_groups", {})
        self.station.scheduler._resolve_all_rotations()

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
        
        tk.Label(grid_frame, text="Music Videos Folder:", font=("Arial", 10, "bold")).grid(row=3, column=0, sticky="w", pady=10)
        self.var_mv_path = tk.StringVar(value=self.station.config['paths'].get('music_videos', ''))
        tk.Entry(grid_frame, textvariable=self.var_mv_path, width=60).grid(row=3, column=1, padx=10)
        tk.Button(grid_frame, text="Browse...", command=lambda: self.browse_folder(self.var_mv_path)).grid(row=3, column=2)
        
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
            "commercials": self.var_comm_path.get(),
            "music_videos": self.var_mv_path.get()
        }
        self.station.config["paths"] = new_paths
        self.station.save_config()
        try:
            messagebox.showinfo("Please Wait", "Rescanning libraries... this may take a moment.")
            self.root.update() 
            self.station.load_components()
            self.refresh_source_groups() 
            
            self.lst_source_shows.delete(0, tk.END)
            for s in sorted(self.station.library.keys()): self.lst_source_shows.insert(tk.END, s)
            
            self.lst_source_movies.delete(0, tk.END)
            if hasattr(self.station, 'movie_map'):
                for m_name in sorted(self.station.movie_map.keys()): self.lst_source_movies.insert(tk.END, m_name)
                
            self.lst_source_mvs.delete(0, tk.END)
            if hasattr(self.station, 'music_video_map'):
                for mv_name in sorted(self.station.music_video_map.keys()): self.lst_source_mvs.insert(tk.END, mv_name)
                
            self.series_list.delete(0, tk.END)
            for s in sorted(self.station.library.keys()): self.series_list.insert(tk.END, s)
            
            messagebox.showinfo("Success", "Configuration saved and libraries rescanned!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to rescan libraries: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = StationManagerApp(root)
    root.mainloop()