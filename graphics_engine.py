import os
from PIL import Image, ImageDraw, ImageFont
import datetime

class GraphicsEngine:
    def __init__(self, font_path="assets/comiclemon.ttf", resolution=(1920, 1080)):
        self.font_path = font_path
        self.width, self.height = resolution

    def generate_transparent_bumper(self, upcoming_shows, commercial_duration_sec, output_path="temp_overlay.png", target_width=1920, target_height=1080):
        """
        Creates a responsive transparent image that perfectly fits the target resolution.
        """
        # 1. Create canvas matching the video size
        img = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 2. RESPONSIVE MATH (Calculate sizes as % of screen height/width)
        # Margins: 10% from the edges
        SAFE_X = int(target_width * 0.10)
        SAFE_Y = int(target_height * 0.10)

        # Fonts: Sized relative to screen height
        header_size = int(target_height * 0.046) 
        show_size = int(target_height * 0.032)
        time_size = int(target_height * 0.027)

        header_font = ImageFont.truetype(self.font_path, header_size)
        show_font = ImageFont.truetype(self.font_path, show_size)
        time_font = ImageFont.truetype(self.font_path, time_size)

        # Spacing:
        line_height = int(target_height * 0.065)
        tab_indent = int(target_width * 0.10) # Space between Time and Show Name

        # 3. Draw the Header
        header_text = "WE'LL BE RIGHT BACK"
        draw.text((SAFE_X, SAFE_Y), header_text, font=header_font, fill=(255, 255, 255, 255))

        # 4. Calculate Times
        current_time = datetime.datetime.now()
        start_time = current_time + datetime.timedelta(seconds=commercial_duration_sec)

        # 5. Draw "UP NEXT:"
        y_offset = SAFE_Y + (line_height * 2)
        draw.text((SAFE_X, y_offset), "UP NEXT:", font=show_font, fill=(200, 200, 200, 255))
        y_offset += line_height

        # 6. Draw Shows
        for show_name, duration in upcoming_shows[:3]:
            time_str = start_time.strftime("%I:%M %p").lstrip("0") 

            # Truncation for very long names
            max_chars = 40
            display_name = (show_name[:max_chars] + '...') if len(show_name) > max_chars else show_name

            # Draw Time
            draw.text((SAFE_X, y_offset), time_str, font=time_font, fill=(255, 200, 50, 255))
            
            # Draw Show Name (Indented)
            draw.text((SAFE_X + tab_indent, y_offset), display_name, font=show_font, fill=(255, 255, 255, 255))

            start_time += datetime.timedelta(seconds=duration)
            y_offset += line_height

        img.save(output_path, "PNG")