import os
import random
from PIL import Image, ImageDraw, ImageFont
import datetime
import time

class GraphicsEngine:
    def __init__(self, font_path="assets/MonoPolz.ttf", resolution=(1920, 1080)):
        self.font_path = font_path
        self.width, self.height = resolution

    def generate_transparent_bumper(self, upcoming_shows, commercial_duration_sec, output_path="temp_overlay.png", target_width=1920, target_height=1080):
        # 1. Create canvas matching the video size
        img = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 2. RESPONSIVE MATH
        SAFE_X = int(target_width * 0.05)
        SAFE_Y = int(target_height * 0.1)

        header_size = int(target_height * 0.096) 
        up_size = int(target_height * 0.076)
        show_size = int(target_height * 0.056)
        time_size = int(target_height * 0.047)

        header_font = ImageFont.truetype(self.font_path, header_size)
        up_font = ImageFont.truetype(self.font_path, up_size)
        show_font = ImageFont.truetype(self.font_path, show_size)
        time_font = ImageFont.truetype(self.font_path, time_size)

        bbox = draw.textbbox((0, 0), "UP NEXT:", font=show_font)
        actual_text_height = bbox[3] - bbox[1] 
        line_height = int(actual_text_height * 1.5)

        # 3. Draw the Header
        header_text = "WE'LL BE RIGHT BACK \n \n "
        draw.text((SAFE_X, SAFE_Y), header_text, font=header_font, fill=(255, 240, 120, 255), stroke_width=4, stroke_fill=(0,0,0,255))
        
        # 4. Calculate Times
        current_time = datetime.datetime.now()
        start_time = current_time + datetime.timedelta(seconds=commercial_duration_sec)
        is_dst = time.localtime().tm_isdst > 0
        tz_code = "PDT" if is_dst else "PST"

        # 5. Draw "UP NEXT:"
        y_offset = SAFE_Y + (line_height * 3)
        draw.text((SAFE_X*1.5, y_offset), "COMING UP NEXT:", font=up_font, fill=(255, 240, 120, 255),stroke_width=4, stroke_fill=(0,0,0,255))
        y_offset += (line_height *2)

        # 6. Draw Shows
        for show_name, duration in upcoming_shows[:4]:
            time_str = start_time.strftime("%I:%M %p").lstrip("0") + f" {tz_code}"
            max_chars = 40
            display_name = (show_name[:max_chars] + '...') if len(show_name) > max_chars else show_name
            time_width = int(draw.textlength(time_str, font=time_font))
            gap_padding = int(target_width * 0.02) 

            draw.text((SAFE_X*1.75, y_offset),time_str + " | ", font=time_font, fill=(255, 210, 50, 255),stroke_width=4, stroke_fill=(0,0,0,255))
            draw.text((SAFE_X*1.75 + time_width + gap_padding, y_offset),"    " + display_name, font=show_font, fill=(246, 141, 15, 255),stroke_width=4, stroke_fill=(0,0,0,255))

            start_time += datetime.timedelta(seconds=duration)
            y_offset += (line_height * 2)

        # --- 7. DRAW THE TOP-RIGHT GRAPHIC (FLAIR OR Q&A) ---
        flair_dir = os.path.join("assets", "flair")
        qa_dir = os.path.join("assets", "qa")
        
        # Decide which mode to run based on available folders
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
                target_height = 725
                aspect = g_img.width / g_img.height
                target_width_g = int(target_height * aspect)
                
                g_img = g_img.resize((target_width_g, target_height), getattr(Image, 'Resampling', Image).LANCZOS)
                
                paste_x = target_width - 19 - target_width_g
                paste_y = 10
                base_img.paste(g_img, (paste_x, paste_y), mask=g_img)
            except Exception as e:
                print(f"DEBUG: Failed to paste graphic {graphic_path}: {e}")
            return base_img

        # Output logic based on mode
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
            
            # Generate Q Frame
            q_img = img.copy()
            if os.path.exists(q_path): q_img = paste_graphic(q_img, q_path)
            q_out = output_path.replace(".png", "_q.png")
            q_img.save(q_out, "PNG")
            
            # Generate A Frame
            a_img = img.copy()
            if os.path.exists(a_path): a_img = paste_graphic(a_img, a_path)
            a_out = output_path.replace(".png", "_a.png")
            a_img.save(a_out, "PNG")
            
            return ("qa", q_out, a_out)

        else:
            img.save(output_path, "PNG")
            return ("none", output_path)