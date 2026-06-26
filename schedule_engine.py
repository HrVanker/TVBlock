import os
import random
import json
from tinytag import TinyTag

class ScheduleEngine:
    def __init__(self, library, movie_library=[], music_video_library=[], config_file="station_config.json", active_channel=None):
        self.library = library
        self.movie_library = movie_library
        self.music_video_library = music_video_library
        self.config_file = config_file
        
        self.config = self._load_json(config_file)
        self.history = self._load_json("station_history.json")
        
        # 1. Automatic Network Migration
        if "channels" not in self.config:
            self._migrate_old_config()

        # 2. Set Active Channel
        if active_channel:
            self.active_channel = active_channel
            self.config["active_channel"] = active_channel
            self._save_config()
        else:
            self.active_channel = self.config.get("active_channel", "Default Channel")

        # Ensure channel has a bookmarks dict
        if "bookmarks" not in self._get_channel_data():
            self.config["channels"][self.active_channel]["bookmarks"] = {}
            self._save_config()

        self.rotation_groups = self.config.get("rotation_groups", {})
        
        # Tracking variables
        self.block_index = 0
        self.slot_play_count = 0
        self.items_since_break = 0
        
        self._resolve_all_rotations()

    # --- NEW: HOT RELOAD ---
    def hot_reload(self):
        """Reloads config from disk while preserving playback trackers."""
        self.config = self._load_json(self.config_file)
        self.rotation_groups = self.config.get("rotation_groups", {})
        self._resolve_all_rotations()
        
        # Safety bound check in case they deleted slots making the block shorter
        new_block = self._get_channel_data().get("schedule_block", [])
        if self.block_index >= len(new_block) and len(new_block) > 0:
            self.block_index = 0
            self.slot_play_count = 0

    def _load_json(self, filepath):
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f: return json.load(f)
            except: pass
        return {}

    def _save_config(self):
        try:
            with open(self.config_file, 'w') as f: json.dump(self.config, f, indent=4)
        except Exception as e: print(f"DEBUG: Could not save config: {e}")

    def _migrate_old_config(self):
        print("DEBUG: Migrating station_config.json to Multi-Channel Architecture...")
        old_block = self.config.get("schedule_block", [])
        old_settings = self.config.get("settings", {
            "commercial_frequency": 3, "commercial_min_sec": 60, "commercial_max_sec": 120
        })
        self.config["channels"] = {
            "Default Channel": {"settings": old_settings, "schedule_block": old_block, "bookmarks": {}}
        }
        self.config["active_channel"] = "Default Channel"
        if "schedule_block" in self.config: del self.config["schedule_block"]
        if "settings" in self.config: del self.config["settings"]
        self._save_config()

    def _get_channel_data(self):
        return self.config.get("channels", {}).get(self.active_channel, {})

    def _get_local_bookmark(self, show_name):
        return self._get_channel_data().get("bookmarks", {}).get(show_name, 0)

    def _set_local_bookmark(self, show_name, index):
        self.config["channels"][self.active_channel]["bookmarks"][show_name] = index
        self._save_config()

    def _flatten_series(self, show_name):
        if show_name not in self.library: return []
        series_data = self.library[show_name]
        flat_eps = []
        for season in sorted(series_data.keys()): flat_eps.extend(sorted(series_data[season]))
        blacklist = self.config.get("blacklist", [])
        return [ep for ep in flat_eps if ep not in blacklist]

    def _resolve_all_rotations(self):
        for channel in self.config.get("channels", {}).values():
            for slot in channel.get("schedule_block", []):
                if slot.get("type") == "rotate":
                    # Only resolve if it doesn't already have one, to preserve state during hot reload
                    if "resolved_show" not in slot:
                        group_name = slot.get("group")
                        group_shows = self.rotation_groups.get(group_name, [])
                        if group_shows:
                            slot["resolved_show"] = random.choice(group_shows)

    def _get_episode(self, show_name, slot_data):
        flat_eps = self._flatten_series(show_name)
        if not flat_eps: return None

        mode = slot_data.get("mode", "sequential").lower()

        # 1. OVERRIDE START (BULLETPROOF MATCHING)
        if "override_start" in slot_data and slot_data["override_start"]:
            target_ep = os.path.normpath(slot_data["override_start"])
            del slot_data["override_start"]
            
            # Check if the requested override matches the end of any file in the library
            match_path = None
            for p in flat_eps:
                if os.path.normpath(p).endswith(target_ep):
                    match_path = p
                    break
                    
            if match_path:
                self._set_local_bookmark(show_name, flat_eps.index(match_path) + 1)
                return match_path
            else:
                self._save_config()

        # 2. SEQUENTIAL
        if "sequential" in mode:
            if slot_data.get("sync_global", False):
                last_played_idx = -1
                history_log = self.history.get("playback_log", {})
                for i in range(len(flat_eps) - 1, -1, -1):
                    fname = os.path.basename(flat_eps[i])
                    if fname in history_log and history_log[fname].get("status") == "watched":
                        last_played_idx = i
                        break
                next_idx = last_played_idx + 1
                if next_idx >= len(flat_eps): next_idx = 0 
                ep_path = flat_eps[next_idx]
                self._set_local_bookmark(show_name, next_idx + 1)
                return ep_path
            else:
                idx = self._get_local_bookmark(show_name)
                if idx >= len(flat_eps): idx = 0 
                ep_path = flat_eps[idx]
                self._set_local_bookmark(show_name, idx + 1)
                return ep_path

        # 3. RANDOM NO-RERUNS
        elif mode == "random_no_reruns":
            unwatched = []
            history_log = self.history.get("playback_log", {})
            for ep in flat_eps:
                fname = os.path.basename(ep)
                if fname not in history_log or history_log[fname].get("status") != "watched":
                    unwatched.append(ep)
            if not unwatched: unwatched = flat_eps
            return random.choice(unwatched)

        # 4. RANDOM
        else:
            return random.choice(flat_eps)

    def _get_movie(self, slot_data):
        if not self.movie_library: return None
        target_path = slot_data.get("path")
        if target_path and target_path in self.movie_library: return target_path
        return random.choice(self.movie_library)
        
    def _get_music_video(self, slot_data):
        if not self.music_video_library: return None
        target_path = slot_data.get("path")
        if target_path and target_path in self.music_video_library: return target_path
        return random.choice(self.music_video_library)

    def get_next_item(self):
        channel_data = self._get_channel_data()
        schedule_block = channel_data.get("schedule_block", [])
        settings = channel_data.get("settings", {})
        
        if not schedule_block:
            return {"type": "video", "show": "System", "display": "No Schedule Block Configured", "path": None}

        comm_freq = settings.get("commercial_frequency", 3)
        if self.items_since_break >= comm_freq:
            self.items_since_break = 0
            return {"type": "break", "min": settings.get("commercial_min_sec", 60), "max": settings.get("commercial_max_sec", 120)}

        loop_guard = 0
        while loop_guard < len(schedule_block):
            if self.block_index >= len(schedule_block):
                self.block_index = 0
                self.slot_play_count = 0
                
            slot = schedule_block[self.block_index]
            target_count = slot.get("count", 1)
            
            s_type = slot.get("type")
            ep_path = None
            show_name = "Unknown"

            if s_type == "anchor":
                show_name = slot.get("show")
                ep_path = self._get_episode(show_name, slot)
                
            elif s_type == "rotate":
                group_name = slot.get("group")
                show_name = slot.get("resolved_show")
                if not show_name:
                    group_shows = self.rotation_groups.get(group_name, [])
                    show_name = random.choice(group_shows) if group_shows else "Unknown"
                ep_path = self._get_episode(show_name, slot)

            elif s_type == "movie":
                show_name = "Feature Presentation"
                ep_path = self._get_movie(slot)
                
            elif s_type == "music_video":
                show_name = "Music Video"
                ep_path = self._get_music_video(slot)

            if ep_path:
                self.items_since_break += 1
                
                self.slot_play_count += 1
                if self.slot_play_count >= target_count:
                    self.slot_play_count = 0
                    self.block_index += 1
                    
                    if s_type == "rotate":
                        group_shows = self.rotation_groups.get(slot.get("group"), [])
                        if group_shows:
                            slot["resolved_show"] = random.choice(group_shows)

                return {"type": "video", "show": show_name, "display": os.path.basename(ep_path), "path": ep_path}
                
            self.slot_play_count = 0
            self.block_index += 1
            loop_guard += 1

        return {"type": "video", "show": "System", "display": "No Valid Media Found in Block", "path": None}

    def get_upcoming_list(self, limit=10):
        upcoming = []
        sim_block_idx = self.block_index
        sim_slot_count = self.slot_play_count
        sim_items_since = self.items_since_break
        
        channel_data = self._get_channel_data()
        schedule_block = channel_data.get("schedule_block", [])
        settings = channel_data.get("settings", {})
        comm_freq = settings.get("commercial_frequency", 3)

        if not schedule_block: return upcoming

        for _ in range(limit):
            if sim_items_since >= comm_freq:
                upcoming.append({"type": "break", "min": settings.get("commercial_min_sec", 60), "max": settings.get("commercial_max_sec", 120)})
                sim_items_since = 0
                continue
                
            if sim_block_idx >= len(schedule_block):
                sim_block_idx = 0
                sim_slot_count = 0
                
            slot = schedule_block[sim_block_idx]
            target_count = slot.get("count", 1)
            s_type = slot.get("type")
            
            if s_type == "rotate": name = slot.get("resolved_show", slot.get("group"))
            elif s_type == "movie": name = "Feature Presentation" if not slot.get("path") else os.path.basename(slot.get("path"))
            elif s_type == "music_video": name = "Music Video" if not slot.get("path") else os.path.basename(slot.get("path"))
            else: name = slot.get("show", "Unknown")
                
            upcoming.append({"type": "video", "show": name, "display": f"[{slot.get('mode', 'sequential').upper()}]"})
            
            sim_slot_count += 1
            if sim_slot_count >= target_count:
                sim_slot_count = 0
                sim_block_idx += 1
                
            sim_items_since += 1

        return upcoming

    def get_upcoming_durations(self, limit=3):
        upcoming = []
        future_items = [i for i in self.get_upcoming_list(limit=limit+1) if i['type'] != 'break'][:limit]
        
        for item in future_items:
            if item.get("show") == "Feature Presentation": dur = 5400
            elif item.get("show") == "Music Video": dur = 240
            else: dur = 1320
            upcoming.append((item.get("show", "Unknown"), dur))
            
        return upcoming