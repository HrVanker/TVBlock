import os
import random
from tinytag import TinyTag

class CommercialManager:
    def __init__(self, commercials_path):
        self.commercials_path = commercials_path
        self.clips = [] # Stores tuples: (filepath, duration_in_seconds)
        self._scan_commercials()

    def _scan_commercials(self):
        """Scans the folder and caches file durations."""
        if not os.path.exists(self.commercials_path):
            print(f"Warning: Commercial path not found: {self.commercials_path}")
            return

        print("--- Scanning Commercials (This may take a moment to read durations) ---")
        valid_exts = {'.mp4', '.mkv', '.avi', '.mov', '.mpg'}
        
        for root, _, files in os.walk(self.commercials_path):
            for file in files:
                if os.path.splitext(file)[1].lower() in valid_exts:
                    full_path = os.path.join(root, file)
                    try:
                        # TinyTag reads the metadata for duration
                        tag = TinyTag.get(full_path)
                        if tag.duration:
                            self.clips.append((full_path, tag.duration))
                    except:
                        # If a file is unreadable, we skip it
                        print(f"Could not read metadata for: {file}")
        
        print(f"Loaded {len(self.clips)} commercial clips.")

    def generate_break(self, min_duration=120, max_duration=240):
        """
        Returns a list of file paths that sum up to a random time 
        between min_duration and max_duration.
        """
        if not self.clips:
            return []

        break_block = []
        current_duration = 0
        
        # Pick a random target for THIS specific break (e.g., 145 seconds)
        target = random.randint(min_duration, max_duration)
        
        # Create a shuffled copy so we don't repeat the same pattern
        pool = list(self.clips)
        random.shuffle(pool)

        for path, duration in pool:
            # If adding this clip keeps us under the max, add it
            if current_duration + duration <= max_duration:
                break_block.append(path)
                current_duration += duration
            
            # If we have reached at least the minimum, we can stop early
            if current_duration >= min_duration:
                break
        
        return break_block