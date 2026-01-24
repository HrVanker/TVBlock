import os
import re
from pathlib import Path

class InventoryManager:
    def __init__(self):
        # Regex to find "Season 1", "S01", "s1", etc.
        self.season_pattern = re.compile(r"(?:season|s)[\s\.]*(\d+)", re.IGNORECASE)
        # Regex to find "1x01", "2x10", etc. inside a filename
        self.episode_pattern = re.compile(r"(\d+)[xX](\d+)")
        
        # Extensions we consider valid media files
        self.valid_extensions = {'.mkv', '.mp4', '.avi', '.mov', '.m4v'}

    def scan_series(self, library_path):
        """
        Scans a root folder for TV Series.
        Returns a dictionary: { "Series Name": { SeasonNumber: [List of Files] } }
        """
        library = {}
        
        if not os.path.exists(library_path):
            print(f"Error: Path not found: {library_path}")
            return library

        print(f"--- Scanning TV Library: {library_path} ---")

        # Loop through each folder in the root (Each folder is a Series)
        for series_name in os.listdir(library_path):
            series_path = os.path.join(library_path, series_name)

            if os.path.isdir(series_path):
                series_data = self._process_seasons(series_path)
                
                # Only add the series if we actually found episodes
                if series_data:
                    library[series_name] = series_data
                    print(f"Found Series: {series_name} ({len(series_data)} seasons)")

        return library

    def _process_seasons(self, series_path):
        """Helper function to find seasons within a series folder"""
        seasons = {}

        for item in os.listdir(series_path):
            item_path = os.path.join(series_path, item)

            if os.path.isdir(item_path):
                # Check if folder name matches "Season X" or "SXX"
                match = self.season_pattern.search(item)
                if match:
                    season_num = int(match.group(1)) # Convert "01" to 1
                    episodes = self._find_episodes(item_path)
                    
                    if episodes:
                        seasons[season_num] = episodes
        
        return seasons

    def _find_episodes(self, season_path):
        """Helper to find and sort episode files within a season folder"""
        episodes = []

        for root, _, files in os.walk(season_path):
            for filename in files:
                # Check file extension
                if Path(filename).suffix.lower() in self.valid_extensions:
                    
                    # Check if file has [SxEE] format
                    match = self.episode_pattern.search(filename)
                    if match:
                        # We store a tuple: (EpisodeNumber, FullPath)
                        # We use the tuple to sort by number, then strip it later
                        ep_num = int(match.group(2))
                        full_path = os.path.join(root, filename)
                        episodes.append((ep_num, full_path))

        # Sort by episode number (the first item in the tuple)
        episodes.sort(key=lambda x: x[0])

        # Return just the list of paths, now sorted
        return [x[1] for x in episodes]

    def scan_movies(self, movies_path):
        """
        Scans a folder for Movies.
        Assumes structure: Movie Name / MovieFile.ext
        Returns list of movie paths.
        """
        movies = []
        print(f"\n--- Scanning Movie Library: {movies_path} ---")
        
        if not os.path.exists(movies_path):
            print(f"Error: Path not found: {movies_path}")
            return movies

        for item in os.listdir(movies_path):
            item_path = os.path.join(movies_path, item)
            
            # If it's a folder, look inside for the media file
            if os.path.isdir(item_path):
                for file in os.listdir(item_path):
                    if Path(file).suffix.lower() in self.valid_extensions:
                        movies.append(os.path.join(item_path, file))
                        break # Found one file, assume it's the movie and move on
        
        print(f"Found {len(movies)} movies.")
        return movies