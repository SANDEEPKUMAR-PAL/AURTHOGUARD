ESP32 Bluetooth Setup Guide (No WiFi Needed!)
===============================================

Step 1: Upload to ESP32 (On Any Laptop with Arduino IDE)
1. Copy the folder "OA_Detection_ESP32_BT" (which contains OA_Detection_ESP32_BT.ino and config.h).
2. Open OA_Detection_ESP32_BT.ino in Arduino IDE.
3. Select Tools -> Board -> ESP32 Dev Module and your serial port.
4. Click Upload (->). (Only "ArduinoJson" library is needed — WebSockets is not needed!)

Step 2: Pair Once on Your Mac
1. Power on the ESP32 (from a USB charger, power bank, or any laptop).
2. On your Mac, open System Settings -> Bluetooth.
3. Find "OA-Wearable-BT" and click Connect / Pair.

Step 3: Live on Dashboard
- The Bluetooth bridge on your Mac automatically detects "OA-Wearable-BT" within 2 seconds and streams data straight to http://localhost:8000!
- If the Bluetooth bridge was closed, start it with:
  /Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32/run_bt_bridge.sh

