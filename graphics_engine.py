import os
from PIL import Image, ImageDraw, ImageFont
import datetime
import time

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
        SAFE_X = int(target_width * 0.1)
        SAFE_Y = int(target_height * 0.1)

        # Fonts: Sized relative to screen height
        header_size = int(target_height * 0.086) 
        up_size = int(target_height * 0.066)
        show_size = int(target_height * 0.046)
        time_size = int(target_height * 0.037)

        header_font = ImageFont.truetype(self.font_path, header_size)
        up_font = ImageFont.truetype(self.font_path, up_size)
        show_font = ImageFont.truetype(self.font_path, show_size)
        time_font = ImageFont.truetype(self.font_path, time_size)

        # Spacing:
        # Gets the exact pixel height of the font, no matter the resolution
        bbox = draw.textbbox((0, 0), "UP NEXT:", font=show_font)
        actual_text_height = bbox[3] - bbox[1] 
        
        # Set line height to the text height + 50% padding for breathing room
        line_height = int(actual_text_height * 1.5)
        tab_indent = int(target_width * 0.10) # Space between Time and Show Name

        # 3. Draw the Header
        header_text = "WE'LL BE RIGHT BACK \n \n "
        draw.text((SAFE_X, SAFE_Y), header_text, font=header_font, fill=(255, 240, 120, 255), stroke_width=4, stroke_fill=(0,0,0,255))
        
        # 4. Calculate Times
        current_time = datetime.datetime.now()
        start_time = current_time + datetime.timedelta(seconds=commercial_duration_sec)

        # --- NEW: Explicitly force the 3-letter abbreviation ---
        # tm_isdst > 0 means Daylight Saving Time is active (Summer)
        is_dst = time.localtime().tm_isdst > 0
        tz_code = "PDT" if is_dst else "PST"

        # 5. Draw "UP NEXT:"
        y_offset = SAFE_Y + (line_height * 3)
        draw.text((SAFE_X*1.5, y_offset), "COMING UP NEXT:", font=up_font, fill=(255, 240, 120, 255),stroke_width=4, stroke_fill=(0,0,0,255))
        y_offset += (line_height *2)

        # 6. Draw Shows
        for show_name, duration in upcoming_shows[:4]:
            # Attach the hardcoded timezone code to the end
            time_str = start_time.strftime("%I:%M %p").lstrip("0") + f" {tz_code}"

            max_chars = 40
            display_name = (show_name[:max_chars] + '...') if len(show_name) > max_chars else show_name

            
            # --- NEW: MEASURE TIME WIDTH AND CALCULATE SHOW POSITION ---
            # Get the exact pixel width of the time string we just drew
            time_width = int(draw.textlength(time_str, font=time_font))
            
            # Add 2% of the screen width as breathing room between Time and Show
            gap_padding = int(target_width * 0.02) 

            # Draw Time
            draw.text((SAFE_X*1.75, y_offset),time_str + " | ", font=time_font, fill=(255, 210, 50, 255),stroke_width=4, stroke_fill=(0,0,0,255))
            
            # Draw Show Name (Indented)
            draw.text((SAFE_X*1.75 + time_width + gap_padding, y_offset),"    " + display_name, font=show_font, fill=(246, 141, 15, 255),stroke_width=4, stroke_fill=(0,0,0,255))

            start_time += datetime.timedelta(seconds=duration)
            y_offset += (line_height * 2)

        img.save(output_path, "PNG")


if __name__ == "__main__":
    engine = GraphicsEngine()
    fake_shows = [
        ("Star Trek: The Next Generation", 1320),
        ("Batman The Animated Series", 1320),
        ("The Adventures of Pete & Pete", 1320),
        ("Batman The Animated Series", 1320)
    ]
    engine.generate_transparent_bumper(fake_shows, 150, "test_bumper.png")
    print("Test bumper saved to test_bumper.png")