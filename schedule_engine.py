import os
import json
import random
from tinytag import TinyTag

class ScheduleEngine:
    def __init__(self, library, movie_library=[], music_video_library=[], config_file="station_config.json", active_channel=None):
        self.library = library
        self.movie_library = movie_library
        self.music_video_library = music_video_library
        self.config_file = config_file
        
        self.history = self._load_json("station_history.json")
        self.config = self._load_json(config_file)

        # Migrate legacy single-channel configs to multi-channel
        if "channels" not in self.config:
            self._migrate_config()

        self.active_channel = active_channel if active_channel else self.config.get("active_channel", "Default Channel")
        
        # Internal Bookkeeping
        self.block_index = 0
        self.slot_play_count = 0
        self.items_since_break = 0
        
        self._init_rotations()

    def _load_json(self, path):
        if os.path.exists(path):
            with open(path, 'r') as f:
                try: return json.load(f)
                except: return {}
        return {}

    def _save_config(self):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"DEBUG: Could not save config: {e}")

    def _init_rotations(self):
        """Pre-rolls the current show for all rotation groups and movies in the block."""
        self.rotation_groups = self.config.get("rotation_groups", {})
        channel_data = self._get_channel_data()
        schedule_block = channel_data.get("schedule_block", [])
        for slot in schedule_block:
            # Resolve Rotation Groups
            if slot.get("type") == "rotate" and not slot.get("resolved_show"):
                group_name = slot.get("group")
                group_shows = self.rotation_groups.get(group_name, [])
                if group_shows:
                    slot["resolved_show"] = random.choice(group_shows)
            
            # Resolve Random Movies
            if slot.get("type") == "movie" and not slot.get("path") and not slot.get("resolved_path"):
                if self.movie_library:
                    movie_path = random.choice(self.movie_library)
                    slot["resolved_path"] = movie_path
                    
                    # Try to get the title from metadata, fallback to filename without extension
                    try:
                        tag = TinyTag.get(movie_path)
                        slot["resolved_show"] = tag.title if tag.title else os.path.splitext(os.path.basename(movie_path))[0]
                    except:
                        slot["resolved_show"] = os.path.splitext(os.path.basename(movie_path))[0]

    def _migrate_config(self):
        """Upgrades an old station_config.json to the new multi-channel format."""
        old_block = self.config.get("schedule_block", [])
        old_settings = self.config.get("settings", {
            "commercial_frequency": 3,
            "commercial_min_sec": 60,
            "commercial_max_sec": 120
        })
        
        self.config["channels"] = {
            "Default Channel": {
                "settings": old_settings,
                "schedule_block": old_block,
                "bookmarks": {}
            }
        }
        self.config["active_channel"] = "Default Channel"
        
        # Clean up root level
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
        """Returns a flat, sorted list of all episode paths for a show."""
        if show_name not in self.library:
            return []
        series_data = self.library[show_name]
        flat_eps = []
        for season in sorted(series_data.keys()):
            flat_eps.extend(sorted(series_data[season]))
            
        # Filter out blacklisted episodes
        blacklist = self.config.get("blacklist", [])
        return [ep for ep in flat_eps if ep not in blacklist]

    def _resolve_all_rotations(self):
        """Rolls the dice for all rotation groups and saves the choice in memory."""
        for channel in self.config.get("channels", {}).values():
            for slot in channel.get("schedule_block", []):
                if slot.get("type") == "rotate":
                    group_name = slot.get("group")
                    group_shows = self.rotation_groups.get(group_name, [])
                    if group_shows:
                        slot["resolved_show"] = random.choice(group_shows)

    def _get_episode(self, show_name, slot_data, peek=False):
        """The core brain of the Network: handles history, overrides, and modes."""
        flat_eps = self._flatten_series(show_name)
        if not flat_eps:
            return None

        mode = slot_data.get("mode", "sequential").lower()

        # 1. OVERRIDE START (Temporary Bookmark)
        if "override_start" in slot_data and slot_data["override_start"]:
            ep_path = slot_data["override_start"]
            
            if not peek:
                # Remove the override so it only happens once
                del slot_data["override_start"]
            
            # Sync the local bookmark so sequential play continues smoothly from here next time
            if ep_path in flat_eps:
                if not peek:
                    self._set_local_bookmark(show_name, flat_eps.index(ep_path) + 1)
            else:
                if not peek:
                    self._save_config() # Save the removal even if file wasn't found
                
            if ep_path in flat_eps:
                return ep_path

        # 2. SEQUENTIAL MODES
        if "sequential" in mode:
            if slot_data.get("sync_global", False):
                # --- SYNCED: Use Global Ledger ---
                last_played_idx = -1
                history_log = self.history.get("playback_log", {})
                
                # Search backwards to find the most recently watched episode
                for i in range(len(flat_eps) - 1, -1, -1):
                    fname = os.path.basename(flat_eps[i])
                    if fname in history_log and history_log[fname].get("status") == "watched":
                        last_played_idx = i
                        break
                        
                next_idx = last_played_idx + 1
                if next_idx >= len(flat_eps): 
                    next_idx = 0 # Loop back to pilot
                    
                ep_path = flat_eps[next_idx]
                
                if not peek:
                    # Update local bookmark passively just in case user turns sync off later
                    self._set_local_bookmark(show_name, next_idx + 1)
                return ep_path
                
            else:
                # --- UNSYNCED: Use Local Channel Bookmark ---
                idx = self._get_local_bookmark(show_name)
                if idx >= len(flat_eps): 
                    idx = 0 # Loop back to pilot
                    
                ep_path = flat_eps[idx]
                if not peek:
                    self._set_local_bookmark(show_name, idx + 1)
                return ep_path

        # 3. RANDOM NO-RERUNS MODE
        elif mode == "random_no_reruns":
            unwatched = []
            history_log = self.history.get("playback_log", {})
            for ep in flat_eps:
                fname = os.path.basename(ep)
                # If it's not in history, OR it's in history but wasn't fully watched
                if fname not in history_log or history_log[fname].get("status") != "watched":
                    unwatched.append(ep)
            
            # Fallback if they've watched everything: play pure random
            if not unwatched:
                unwatched = flat_eps
                
            return random.choice(unwatched)

        # 4. PURE RANDOM MODE
        else:
            return random.choice(flat_eps)


    def _get_movie(self, slot_data):
        if not self.movie_library:
            return None
        # Handle specific movie vs random movie
        target_path = slot_data.get("path")
        if target_path and target_path in self.movie_library:
            return target_path
        return random.choice(self.movie_library)

    def _get_music_video(self, slot_data):
        """Selects a specific music video or a random one."""
        if not self.music_video_library:
            return None
            
        target_path = slot_data.get("path")
        if target_path and target_path in self.music_video_library:
            return target_path
            
        return random.choice(self.music_video_library)

    def get_next_item(self):
        """Fetches the next chunk (Show or Commercial Break)"""
        channel_data = self._get_channel_data()
        schedule_block = channel_data.get("schedule_block", [])
        settings = channel_data.get("settings", {})
        
        if not schedule_block:
            return {"type": "video", "show": "System", "display": "No Schedule Defined", "path": None}

        # 1. Handle Commercial Break injection
        comm_freq = settings.get("commercial_frequency", 3)
        if self.items_since_break >= comm_freq:
            self.items_since_break = 0
            return {
                "type": "break",
                "min": settings.get("commercial_min_sec", 60),
                "max": settings.get("commercial_max_sec", 120)
            }

        # 2. Handle Schedule Block Progression
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
                # --- FIX: Grab the pre-resolved show ---
                group_name = slot.get("group")
                show_name = slot.get("resolved_show")
                
                # Fallback if config was manually edited while running
                if not show_name:
                    group_shows = self.rotation_groups.get(group_name, [])
                    show_name = random.choice(group_shows) if group_shows else "Unknown"

                ep_path = self._get_episode(show_name, slot)
                
                # --- FIX: Re-roll the slot for the next time we loop around! ---
                group_shows = self.rotation_groups.get(group_name, [])
                if group_shows:
                    slot["resolved_show"] = random.choice(group_shows)

            elif s_type == "movie":
                # --- FIX: Grab the pre-resolved movie path/title ---
                ep_path = slot.get("resolved_path")
                show_name = slot.get("resolved_show", "Feature Presentation")
                
                # Fallback if not pre-resolved (legacy or manual config edit)
                if not ep_path:
                    ep_path = self._get_movie(slot)
                    show_name = os.path.basename(ep_path) if ep_path else "Feature Presentation"

                # --- FIX: Re-roll the movie for the next time we loop around! ---
                if self.movie_library:
                    new_movie = random.choice(self.movie_library)
                    slot["resolved_path"] = new_movie
                    try:
                        tag = TinyTag.get(new_movie)
                        slot["resolved_show"] = tag.title if tag.title else os.path.splitext(os.path.basename(new_movie))[0]
                    except:
                        slot["resolved_show"] = os.path.splitext(os.path.basename(new_movie))[0]

            elif s_type == "music_video":
                show_name = "Music Video"
                ep_path = self._get_music_video(slot)

            if ep_path:
                self.items_since_break += 1
                
                # --- FIX: THE COUNT BOOKKEEPING ---
                self.slot_play_count += 1
                if self.slot_play_count >= target_count:
                    # We hit the target count. Reset the counter and move to the next block index.
                    self.slot_play_count = 0
                    self.block_index += 1
                    
                    # If this was a rotation group, roll a new show for the NEXT time we hit this slot
                    if s_type == "rotate":
                        group_shows = self.rotation_groups.get(slot.get("group"), [])
                        if group_shows:
                            slot["resolved_show"] = random.choice(group_shows)

                return {"type": "video", "show": show_name, "display": os.path.basename(ep_path), "path": ep_path}
                
            # If no file was found, skip this slot and move on to prevent infinite loops
            self.slot_play_count = 0
            self.block_index += 1
            loop_guard += 1

        return {"type": "video", "show": "System", "display": "No Valid Media Found in Block", "path": None}

    def get_upcoming_list(self, limit=10):
        """Returns a list of the next N items to be played for UI purposes."""
        channel_data = self._get_channel_data()
        schedule_block = channel_data.get("schedule_block", [])
        settings = channel_data.get("settings", {})
        
        if not schedule_block: return []
        
        upcoming = []
        sim_block_idx = self.block_index
        sim_slot_count = self.slot_play_count
        sim_items_since = self.items_since_break
        
        # We simulate the next few steps
        for _ in range(limit):
            comm_freq = settings.get("commercial_frequency", 3)
            if sim_items_since >= comm_freq:
                upcoming.append({
                    "type": "break", 
                    "min": settings.get("commercial_min_sec", 60), 
                    "max": settings.get("commercial_max_sec", 120)
                })
                sim_items_since = 0
                continue
                
            if sim_block_idx >= len(schedule_block): 
                sim_block_idx = 0
                sim_slot_count = 0
                
            slot = schedule_block[sim_block_idx]
            target_count = slot.get("count", 1)
            
            s_type = slot.get("type")
            ep_path = None
            show_name = "Unknown"

            if s_type == "anchor":
                show_name = slot.get("show", "Unknown")
            elif s_type == "rotate":
                show_name = slot.get("resolved_show", slot.get("group", "Unknown"))
            elif s_type == "movie":
                show_name = slot.get("resolved_show", "Feature Presentation")
            elif s_type == "music_video":
                show_name = "Music Video"

            upcoming.append({"type": "video", "show": show_name, "display": f"[{slot.get('mode', 'sequential').upper()}]"})
            
            # --- FIX: SIMULATE THE COUNT BOOKKEEPING ---
            sim_slot_count += 1
            if sim_slot_count >= target_count:
                sim_slot_count = 0
                sim_block_idx += 1
                
            sim_items_since += 1

        return upcoming

    def get_upcoming_durations(self, limit=3):
        """Returns actual durations (in seconds) for the next N media items."""
        channel_data = self._get_channel_data()
        schedule_block = channel_data.get("schedule_block", [])
        settings = channel_data.get("settings", {})
        
        if not schedule_block: return []
        
        upcoming_data = []
        sim_block_idx = self.block_index
        sim_slot_count = self.slot_play_count
        sim_items_since = self.items_since_break
        
        # To avoid side effects on rotation groups, we'd need to be careful.
        # But we only need durations, so we simulate.
        
        while len(upcoming_data) < limit:
            comm_freq = settings.get("commercial_frequency", 3)
            if sim_items_since >= comm_freq:
                # We hit a future break! Estimate its length using the average of min/max settings.
                c_min = settings.get("commercial_min_sec", 60)
                c_max = settings.get("commercial_max_sec", 120)
                avg_break = (c_min + c_max) // 2
                
                # Add this break time to the LAST show we added to the list, 
                # so the NEXT show starts at the correct time.
                if upcoming_data:
                    last_show, last_dur = upcoming_data[-1]
                    upcoming_data[-1] = (last_show, last_dur + avg_break)
                
                sim_items_since = 0
                continue 
                
            if sim_block_idx >= len(schedule_block): 
                sim_block_idx = 0
                sim_slot_count = 0
                
            slot = schedule_block[sim_block_idx]
            target_count = slot.get("count", 1)
            
            s_type = slot.get("type")
            ep_path = None
            show_name = "Unknown"

            if s_type == "anchor":
                show_name = slot.get("show")
                ep_path = self._get_episode(show_name, slot, peek=True)
            elif s_type == "rotate":
                show_name = slot.get("resolved_show")
                if not show_name:
                    group_name = slot.get("group")
                    group_shows = self.rotation_groups.get(group_name, [])
                    show_name = random.choice(group_shows) if group_shows else "Unknown"
                ep_path = self._get_episode(show_name, slot, peek=True)
            elif s_type == "movie":
                # --- FIX: Use pre-resolved path for accurate duration calculation ---
                ep_path = slot.get("resolved_path")
                show_name = slot.get("resolved_show", "Feature Presentation")
                
                # Fallback if not pre-resolved
                if not ep_path:
                    ep_path = self._get_movie(slot)
                    show_name = os.path.basename(ep_path) if ep_path else "Feature Presentation"
            elif s_type == "music_video":
                show_name = "Music Video"
                ep_path = self._get_music_video(slot)

            if ep_path and os.path.exists(ep_path):
                try:
                    tag = TinyTag.get(ep_path)
                    duration = int(tag.duration) if tag.duration else 1320
                except:
                    duration = 1320 # Default 22m
                upcoming_data.append((show_name, duration, s_type))
            else:
                # Fallback if path doesn't exist or is None
                dur = 5400 if s_type == "movie" else 1320
                upcoming_data.append((show_name, dur, s_type))
            
            sim_slot_count += 1
            if sim_slot_count >= target_count:
                sim_slot_count = 0
                sim_block_idx += 1
            sim_items_since += 1

        return upcoming_data

    def _load_history(self):
        return self._load_json("station_history.json")

    def inject_slot(self, slot_data, insert_next=True):
        """Injects a new slot into the current rotation. Protects original channels by cloning."""
        if not self.active_channel.startswith("Live DJ: "):
            new_channel_name = f"Live DJ: {self.active_channel}"
            # Duplicate the current channel settings
            current_data = self.config["channels"].get(self.active_channel, {})
            # Make a deep copy to avoid reference issues
            import copy
            self.config["channels"][new_channel_name] = copy.deepcopy(current_data)
            self.active_channel = new_channel_name
            self.config["active_channel"] = new_channel_name

        channel_data = self.config["channels"].get(self.active_channel)
        if "schedule_block" not in channel_data:
            channel_data["schedule_block"] = []
            
        schedule = channel_data["schedule_block"]
        
        if insert_next:
            schedule.insert(self.block_index, slot_data)
            # Reset play count so the injected item starts fresh
            self.slot_play_count = 0
        else:
            schedule.append(slot_data)
            
        self._save_config()
