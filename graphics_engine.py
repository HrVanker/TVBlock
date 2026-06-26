import os
import random
from PIL import Image, ImageDraw, ImageFont
import datetime
import time

class GraphicsEngine:
    def __init__(self, font_path="assets/MonoPolz.ttf", resolution=(1920, 1080), music_font="assets/vcr_mono.ttf"):
        self.font_path = font_path
        self.music_font = music_font
        self.width, self.height = resolution

    def generate_transparent_bumper(self, upcoming_shows, commercial_duration_sec, output_path="temp_overlay.png", target_width=1920, target_height=1080):
        # 1. Create canvas matching the video size
        img = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 2. RESPONSIVE MATH
        SAFE_X = int(target_width * 0.05)
        SAFE_Y = int(target_height * 0.05)

        header_size = int(target_height * 0.096) 
        up_size = int(target_height * 0.073)
        show_size = int(target_height * 0.056)
        time_size = int(target_height * 0.047)

        header_font = ImageFont.truetype(self.font_path, header_size)
        up_font = ImageFont.truetype(self.font_path, up_size)
        show_font = ImageFont.truetype(self.font_path, show_size)
        time_font = ImageFont.truetype(self.font_path, time_size)

        # Base the line height directly on the font size for safety
        line_height = int(show_size * 1.6)

        # 3. Draw the Header
        header_text = "WE'LL BE RIGHT BACK"
        draw.text((SAFE_X, SAFE_Y), header_text, font=header_font, fill=(255, 240, 120, 255), stroke_width=4, stroke_fill=(0,0,0,255))
        
        # 4. Calculate Times
        current_time = datetime.datetime.now()
        start_time = current_time + datetime.timedelta(seconds=commercial_duration_sec)
        is_dst = time.localtime().tm_isdst > 0
        tz_code = "PDT" if is_dst else "PST"

        # 5. Draw "UP NEXT:"
        y_offset = SAFE_Y + (line_height * 1.1)
        draw.text((SAFE_X*1.5, y_offset), "COMING UP NEXT:", font=up_font, fill=(255, 240, 120, 255),stroke_width=4, stroke_fill=(0,0,0,255))
        y_offset += (line_height * 1.1)

        # 6. Draw Shows
        drawn_count = 0
        for item_data in upcoming_shows:
            if drawn_count >= len(upcoming_shows): break
            
            # Unpack data (handle both old 2-tuple and new 3-tuple formats for safety)
            show_name = item_data[0]
            duration = item_data[1]
            s_type = item_data[2] if len(item_data) > 2 else "video"

            # SKIP MUSIC VIDEOS: Don't draw them, but add their time to start_time
            if s_type == "music_video":
                start_time += datetime.timedelta(seconds=duration)
                continue

            time_str = start_time.strftime("%I:%M %p").lstrip("0")
            if len(time_str.split(':')[0]) == 1:
                time_str = " " + time_str
            time_str += f" {tz_code}"

            time_width = int(draw.textlength(time_str + " | ", font=time_font))
            gap_padding = int(target_width * 0.02)
            title_x = SAFE_X * 1.75 + time_width + gap_padding

            draw.text((SAFE_X*1.75, y_offset), time_str + " | ", font=time_font, fill=(255, 210, 50, 255),stroke_width=4, stroke_fill=(0,0,0,255))

            # TITLE WRAPPING LOGIC
            max_line_chars = 24
            if len(show_name) <= max_line_chars:
                draw.text((title_x, y_offset), show_name, font=show_font, fill=(246, 141, 15, 255),stroke_width=4, stroke_fill=(0,0,0,255))
            else:
                # Find the space character closest to the 24th character
                split_idx = show_name.rfind(' ', 0, max_line_chars + 1)
                if split_idx == -1: split_idx = max_line_chars # Fallback if no space

                line1 = show_name[:split_idx].strip()
                line2 = show_name[split_idx:].strip()

                # Limit line 2 to prevent excessive length if needed (optional)
                if len(line2) > 30: line2 = line2[:27] + "..."

                draw.text((title_x, y_offset), line1, font=show_font, fill=(246, 141, 15, 255),stroke_width=4, stroke_fill=(0,0,0,255))
                y_offset += int(line_height * 0.6) # Move down for the wrapped line
                draw.text((title_x, y_offset), line2, font=show_font, fill=(246, 141, 15, 255),stroke_width=4, stroke_fill=(0,0,0,255))

            start_time += datetime.timedelta(seconds=duration)
            y_offset += (line_height * 0.8)
            drawn_count += 1


        # --- 7. DRAW THE TOP-RIGHT GRAPHIC (FLAIR OR Q&A) ---
        flair_dir = os.path.join("assets", "flair")
        qa_dir = os.path.join("assets", "qa")
        
        choices = []
        if os.path.exists(flair_dir) and any(f.endswith('.png') for f in os.listdir(flair_dir)):
            choices.append("flair")
        if os.path.exists(qa_dir) and [d for d in os.listdir(qa_dir) if os.path.isdir(os.path.join(qa_dir, d))]:
            choices.append("qa")
            
        mode = random.choice(choices) if choices else "none"

        # Helper function to size and paste graphics
        def paste_graphic(base_img, graphic_path):
            try:
                g_img = Image.open(graphic_path).convert("RGBA")
                
                # FIX: Make the flair responsive! Set it to ~65% of the screen height.
                target_height_g = int(target_height * 0.65)
                aspect = g_img.width / g_img.height
                target_width_g = int(target_height_g * aspect)
                
                g_img = g_img.resize((target_width_g, target_height_g), getattr(Image, 'Resampling', Image).LANCZOS)
                
                # FIX: Use safe margins instead of hardcoded 10px or 19px
                paste_x = target_width - SAFE_X - target_width_g
                paste_y = SAFE_Y
                base_img.paste(g_img, (paste_x, paste_y), mask=g_img)
            except Exception as e:
                print(f"DEBUG: Failed to paste graphic {graphic_path}: {e}")
            return base_img

        if mode == "flair":
            flair_files = [f for f in os.listdir(flair_dir) if f.lower().endswith('.png')]
            selected = random.choice(flair_files)
            img = paste_graphic(img, os.path.join(flair_dir, selected))
            img.save(output_path, "PNG")
            return ("flair", output_path)

        elif mode == "qa":
            qa_folders = [d for d in os.listdir(qa_dir) if os.path.isdir(os.path.join(qa_dir, d))]
            selected_qa = random.choice(qa_folders)
            q_path = os.path.join(qa_dir, selected_qa, "q.png")
            a_path = os.path.join(qa_dir, selected_qa, "a.png")
            
            q_img = img.copy()
            if os.path.exists(q_path): q_img = paste_graphic(q_img, q_path)
            q_out = output_path.replace(".png", "_q.png")
            q_img.save(q_out, "PNG")
            
            a_img = img.copy()
            if os.path.exists(a_path): a_img = paste_graphic(a_img, a_path)
            a_out = output_path.replace(".png", "_a.png")
            a_img.save(a_out, "PNG")
            
            return ("qa", q_out, a_out)

        else:
            img.save(output_path, "PNG")
            return ("none", output_path)

    def generate_mtv_bug(self, metadata, output_path="mtv_bug.png", target_width=1920, target_height=1080):
        img = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        SAFE_X = int(target_width * 0.025)
        SAFE_Y = int(target_height * 0.85) 

        title = metadata.get("title") or "Unknown Title"
        artist = metadata.get("artist") or "Unknown Artist"
        album = metadata.get("album")
        year = str(metadata.get("year", "")) 

        artist_size = int(target_height * 0.035)
        title_size = int(target_height * 0.045)

        artist_font = ImageFont.truetype(self.music_font, artist_size)
        title_font = ImageFont.truetype(self.music_font, title_size)

        y_offset = SAFE_Y
        draw.text((SAFE_X, y_offset), title, font=title_font, fill=(219, 223, 255, 255), stroke_width=3, stroke_fill=(5, 8, 33,255))
        y_offset += int(title_size * 1.2)
        
        draw.text((SAFE_X, y_offset), artist, font=artist_font, fill=(211, 214, 255, 255), stroke_width=3, stroke_fill=(5, 8, 33,255))
        y_offset += int(artist_size * 1.2)
        
        if album:
            draw.text((SAFE_X, y_offset), album + (f", {year}" if year else ""), font=artist_font, fill=(211, 214, 255, 255), stroke_width=3, stroke_fill=(0,0,0,255))
        elif not album and year:
            draw.text((SAFE_X, y_offset), year, font=artist_font, fill=(180, 180, 180, 255), stroke_width=3, stroke_fill=(0,0,0,255))

        img.save(output_path, "PNG")
        return output_path