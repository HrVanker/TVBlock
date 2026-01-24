import os
from PIL import Image, ImageDraw, ImageFont
import datetime

class GraphicsEngine:
    def __init__(self, font_path="arial.ttf", resolution=(1920, 1080)):
        self.font_path = font_path
        self.width, self.height = resolution

    def generate_transparent_bumper(self, upcoming_shows, commercial_duration_sec, output_path="temp_overlay.png"):
        """
        Creates a 1920x1080 transparent image with the schedule text.
        """
        # 1. Create a fully transparent canvas (RGBA with 0 alpha)
        img = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 2. Setup Fonts
        # (Make sure to use a font path that exists on your system)
        header_font = ImageFont.truetype(self.font_path, 60)
        show_font = ImageFont.truetype(self.font_path, 40)
        time_font = ImageFont.truetype(self.font_path, 35)

        # 3. Draw the Header
        header_text = "WE'LL BE RIGHT BACK"
        draw.text((150, 150), header_text, font=header_font, fill=(255, 255, 255, 255))

        # 4. Calculate Times
        current_time = datetime.datetime.now()
        start_time = current_time + datetime.timedelta(seconds=commercial_duration_sec)

        y_offset = 300
        draw.text((150, y_offset), "UP NEXT:", font=show_font, fill=(200, 200, 200, 255))
        y_offset += 60

        for show_name, duration in upcoming_shows[:3]:
            time_str = start_time.strftime("%I:%M %p").lstrip("0") 
            
            # Draw Time and Show Name
            draw.text((150, y_offset), time_str, font=time_font, fill=(255, 200, 0, 255))
            draw.text((400, y_offset), show_name, font=show_font, fill=(255, 255, 255, 255))

            # Advance clock for next loop
            start_time += datetime.timedelta(seconds=duration)
            y_offset += 80

        # 5. Save as PNG to preserve transparency
        img.save(output_path, "PNG")
        return output_path