import collections
import itertools
import json
import os
import random
from tinytag import TinyTag

class ScheduleEngine:
    def get_upcoming_durations(self, limit=3):
        """
        Looks at the upcoming queue and returns a list of tuples:
        [("Show Name", duration_in_seconds), ...]
        """
        upcoming_data = []
        # Create a temporary copy of the queue so we don't accidentally pop items
        queue_list = list(self.upcoming_queue)
        
        for item in queue_list:
            if item['type'] == 'video' and len(upcoming_data) < limit:
                try:
                    # Read the metadata of the upcoming file
                    tag = TinyTag.get(item['path'])
                    # If it can't read duration, fallback to a standard 22 min (1320s)
                    duration = tag.duration or 1320 
                    upcoming_data.append((item['show'], duration))
                except Exception as e:
                    print(f"Warning: Could not read duration for {item['path']} - {e}")
                    upcoming_data.append((item['show'], 1320))

        return upcoming_data
    # 1. ADD movie_library to init
    def __init__(self, tv_library, movie_library=[], config_file="station_config.json", history_file="station_history.json"):
        self.library = tv_library
        self.movie_library = movie_library # Store movies
        self.config_file = config_file
        self.history_file = history_file
        
        with open(self.config_file, 'r') as f:
            self.config = json.load(f)

        self.show_queues = {}
        self.rotation_groups = {}
        self.history = self._load_history()

        self.upcoming_queue = collections.deque() 
        self.queue_buffer_size = 10 

        self._init_queues()
        self._init_rotations()
        
        self.content_source = self._create_generator()
        self._fill_buffer()

    def _load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _init_queues(self):
        blacklist = set(self.config.get('blacklist', []))
        needed_shows = set()
        
        for slot in self.config['schedule_block']:
            if slot['type'] == 'anchor':
                needed_shows.add(slot['show'])
            elif slot['type'] == 'rotate':
                group_name = slot['group']
                for show in self.config['rotation_groups'].get(group_name, []):
                    needed_shows.add(show)

        for show_name in needed_shows:
            if show_name in self.library:
                all_eps = []
                series_data = self.library[show_name]
                for s in sorted(series_data.keys()):
                    for ep in series_data[s]:
                        if ep not in blacklist:
                            all_eps.append(ep)
                
                last_index = self.history.get(show_name, -1)
                if last_index >= 0 and last_index < len(all_eps) - 1:
                    rotate_amount = -(last_index + 1)
                    dq = collections.deque(all_eps)
                    dq.rotate(rotate_amount) 
                    self.show_queues[show_name] = dq
                else:
                    self.show_queues[show_name] = collections.deque(all_eps)

    def _init_rotations(self):
        for name, shows in self.config['rotation_groups'].items():
            self.rotation_groups[name] = itertools.cycle(shows)

    def _create_generator(self):
        """
        Infinite loop yielding Video, Break, or Movie.
        """
        block_counter = 0
        settings = self.config.get('settings', {})
        comm_freq = settings.get('commercial_frequency', 3)
        
        while True:
            # SAFETY CHECK: If block is empty, wait and retry
            # This prevents 100% CPU usage on a fresh install
            if not self.config['schedule_block']:
                yield {
                    'type': 'video',
                    'path': None,
                    'show': 'System',
                    'display': 'Please Configure Schedule'
                }
                import time
                time.sleep(2) # Wait before checking again
                continue

            for slot in self.config['schedule_block']:
                
                if slot['type'] in ['anchor', 'rotate']:
                    target_show = None
                    if slot['type'] == 'anchor':
                        target_show = slot['show']
                    elif slot['type'] == 'rotate':
                        group = slot['group']
                        if group in self.rotation_groups:
                            target_show = next(self.rotation_groups[group])

                    count = slot.get('count', 1)
                    is_random = slot.get('random', False) # <--- CHECK FLAG
                    
                    for _ in range(count):
                        if target_show and target_show in self.show_queues:
                            queue = self.show_queues[target_show]
                            if not queue: continue 
                            
                            episode_path = None
                            
                            if is_random and len(queue) > 1:
                                # --- RANDOM MODE ---
                                # 1. Pick a random index (anywhere in list)
                                rand_idx = random.randrange(len(queue))
                                
                                # 2. Extract that specific item
                                # Deques support index access but delete is O(N).
                                # However, N is small (<300), so this is fine.
                                episode_path = queue[rand_idx]
                                del queue[rand_idx]
                                
                                # 3. Move it to the back (Played)
                                queue.append(episode_path)
                                
                            else:
                                # --- SEQUENTIAL MODE (Default) ---
                                episode_path = queue[0]
                                queue.rotate(-1)
                            
                            yield {
                                'type': 'video', 
                                'path': episode_path, 
                                'show': target_show,
                                'display': os.path.basename(episode_path)
                            }
                            
                            block_counter += 1
                            
                            if block_counter % comm_freq == 0:
                                yield {
                                    'type': 'break',
                                    'min': settings.get('commercial_min_sec', 120),
                                    'max': settings.get('commercial_max_sec', 240),
                                    'display': "--- COMMERCIAL BREAK ---"
                                }

                # --- CASE 2: MOVIE TOKEN ---
                elif slot['type'] == 'movie':
                    target_movie_path = None
                    
                    # 1. Check if a specific movie was requested
                    if 'path' in slot and os.path.exists(slot['path']):
                        target_movie_path = slot['path']
                    
                    # 2. If not (or if file missing), pick random
                    elif self.movie_library:
                        target_movie_path = random.choice(self.movie_library)
                    
                    # 3. Yield result
                    if target_movie_path:
                        yield {
                            'type': 'video', 
                            'path': target_movie_path,
                            'show': 'FEATURE PRESENTATION', 
                            'display': os.path.basename(target_movie_path)
                        }

    def _fill_buffer(self):
        while len(self.upcoming_queue) < self.queue_buffer_size:
            try:
                next_item = next(self.content_source)
                self.upcoming_queue.append(next_item)
            except StopIteration:
                break

    def get_next_item(self):
        if not self.upcoming_queue:
            self._fill_buffer()
        item = self.upcoming_queue.popleft()
        self._fill_buffer()
        return item

    def get_upcoming_list(self):
        return list(self.upcoming_queue)