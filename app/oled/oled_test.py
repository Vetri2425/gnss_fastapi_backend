import time
from luma.core.interface.serial import spi
from luma.oled.device import ssd1306
from luma.core.render import canvas

def main():
    print("Initializing OLED via SPI...")
    
    # 1. Setup the SPI Interface
    # port=0, device=0 uses the RPi's hardware SPI0 bus (/dev/spidev0.0)
    # gpio_DC=24 corresponds to RPi Physical Pin 18
    # gpio_RST=25 corresponds to RPi Physical Pin 22
    serial = spi(port=0, device=0, gpio_DC=24, gpio_RST=25)
    
    # 2. Initialize the SSD1306 Display
    device = ssd1306(serial)
    
    # 3. Draw to the screen
    print("Drawing to screen. Press Ctrl+C to exit.")
    
    # The 'canvas' context manager handles rendering the frame to the display
    with canvas(device) as draw:
        # Draw a border around the edge of the screen
        draw.rectangle(device.bounding_box, outline="white", fill="black")
        
        # Write text. Coordinates are (X, Y) from the top-left corner
        draw.text((25, 20), "Hello World!", fill="white")
        draw.text((15, 40), "Rover OS Ready", fill="white")

    # Keep the script running so the screen stays on and refreshed
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting and clearing screen...")
        # Luma automatically clears the screen when the script ends naturally

if __name__ == "__main__":
    main()
