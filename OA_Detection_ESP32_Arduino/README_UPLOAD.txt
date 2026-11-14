ESP32 Arduino IDE Upload Steps (Windows)
==========================================

1. Copy the entire folder named "OA_Detection_ESP32" onto your Windows PC
   (for example: Documents\Arduino\OA_Detection_ESP32).
   IMPORTANT: Keep the folder name "OA_Detection_ESP32" unchanged, with all 3 files
   inside it:
     - OA_Detection_ESP32.ino
     - config.h
     - secrets.h

2. Open "OA_Detection_ESP32.ino" in Arduino IDE.
   You will see 3 tabs at the top: OA_Detection_ESP32, config.h, secrets.h.

3. One-time Arduino IDE setup:
   - Boards Manager: install "esp32" by Espressif Systems
   - Library Manager: install "WebSockets" (by Markus Sattler)
   - Library Manager: install "ArduinoJson" (by Benoit Blanchon, v6 or v7)
   - Tools -> Board -> ESP32 Dev Module
   - Tools -> Port  -> select your ESP32 COM port

4. WiFi setup (choose ONE):
   - Option A (fastest): Click the "secrets.h" tab, replace YOUR_WIFI_SSID and
     YOUR_WIFI_PASSWORD with your 2.4 GHz WiFi details, then click Upload.
   - Option B (no edit needed): Click Upload right away. After booting, connect
     your phone/PC to WiFi "OA-Wearable-Setup", open http://192.168.4.1, and
     enter your WiFi name + password (Backend IP 10.119.92.200 is pre-filled).

5. Hardware check:
   - 5V Goli Active Buzzer matches config.h as-is (PASSIVE_BUZZER = 0).
   - Mount both MPU6050 boards with the printed Y arrow pointing UP along the leg.

