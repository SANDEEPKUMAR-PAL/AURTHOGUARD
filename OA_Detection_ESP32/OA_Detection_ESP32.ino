/*
 * SIH26004 - AI-Assisted Early Detection System for Osteoarthritis (OA) Risk Markers
 * ESP32 WROOM-32 Upload-Ready WiFi Firmware — v4.0
 *
 * Pin Connections (Verified from Hardware Diagram & Master Pin Table):
 *   - MPU6050 #1 (Thigh, 0x68, AD0→GND) : SDA = GPIO 21, SCL = GPIO 22
 *   - MPU6050 #2 (Shin,  0x69, AD0→3V3) : SDA = GPIO 21, SCL = GPIO 22
 *   - Flex Sensor (Knee, 10kΩ pull-down): GPIO 32 (ADC)
 *   - FSR #1 (Heel,      10kΩ pull-down): GPIO 34 (ADC)
 *   - FSR #2 (Forefoot,  10kΩ pull-down): GPIO 35 (ADC)
 *   - Green LED (220Ω resistor)         : GPIO 26 (Init=Blink, Normal=ON, Abnormal=OFF)
 *   - Red LED   (220Ω resistor)         : GPIO 27 (Normal=OFF, Abnormal=ON)
 *   - Buzzer    (BC547 NPN + 1kΩ base)  : GPIO 25 (Normal=OFF, Abnormal=ON)
 *
 * Required Arduino IDE Libraries (Sketch -> Include Library -> Manage Libraries):
 *   1) WebSockets by Markus Sattler (Links2004)
 *   2) ArduinoJson by Benoit Blanchon (v6.x OR v7.x supported automatically)
 *   (WiFi, HTTPClient, WebServer, Wire, Preferences are built into ESP32 Board Package)
 *
 * Quick Setup:
 *   1) Copy 'secrets.h.template' -> 'secrets.h' and fill in your WiFi & laptop IP, OR
 *   2) Leave defaults and configure via Serial Monitor: set_wifi:MyWiFi,MyPass,192.168.1.50
 *   3) Or connect phone/laptop to WiFi AP "OA-Wearable-Setup" (192.168.4.1) if router fails.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <WebSocketsClient.h>
#include <WebSocketsServer.h>
#include <Wire.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <math.h>
#include <esp_task_wdt.h>
#include "config.h"

// ArduinoJson v6 & v7 seamless compatibility
#if ARDUINOJSON_VERSION_MAJOR >= 7
  #define JSON_DOC(sz) JsonDocument
  #define NESTED_OBJ(doc, key) doc[key].to<JsonObject>()
  #define HAS_KEY(doc, key) (!doc[key].isNull())
#else
  #define JSON_DOC(sz) StaticJsonDocument<sz>
  #define NESTED_OBJ(doc, key) doc.createNestedObject(key)
  #define HAS_KEY(doc, key) doc.containsKey(key)
#endif

// ======================= Clinical Thresholds (AAOS & Gait Lit) =====
float KNEE_ANGLE_MIN       = DEFAULT_KNEE_ANGLE_MIN;       // AAOS physiological hyper-extension limit (< -10°)
float KNEE_ANGLE_MAX       = DEFAULT_KNEE_ANGLE_MAX;       // AAOS normal adult full flexion upper limit (> 135°)
float FSR_IMBALANCE_THRESH = DEFAULT_FSR_IMBALANCE_THRESH; // Sustained unilateral plantar offloading (> 60%)
float FLEX_STIFF_THRESH    = DEFAULT_FLEX_STIFF_THRESH;    // Normalized knee tendon stiffness ratio (< 0.25 at >45°)

// ======================= Storage & State ==========================
Preferences prefs;

String cfgSsid;
String cfgPass;
String cfgBackendHost;
uint16_t cfgBackendPort = DEFAULT_BACKEND_PORT;
bool apModeActive = false;

struct CalibrationData {
  uint8_t isCalibrated;      // 0 = none, 1 = 1-point, 2 = 2-point
  float   thighPitchOffset;
  float   thighRollOffset;
  float   shinPitchOffset;
  float   shinRollOffset;
  int8_t  thighPolarity;     // +1 or -1
  int8_t  shinPolarity;      // +1 or -1
  int32_t flexStraight;
  int32_t flexBent;
  int32_t fsrHeelTare;
  int32_t fsrForeTare;
};

CalibrationData calibration = {0, 0, 0, 0, 0, 1, 1, DEFAULT_CALIB_FLEX_STRAIGHT, DEFAULT_CALIB_FLEX_BENT, DEFAULT_CALIB_FSR_HEEL_TARE, DEFAULT_CALIB_FSR_FORE_TARE};

enum CalibState { CAL_IDLE, CAL_STEP1_STRAIGHT, CAL_STEP2_BENT };
CalibState calState = CAL_IDLE;
int calSampleCount = 0;
float calThighPitchSum = 0, calThighRollSum = 0;
float calShinPitchSum = 0, calShinRollSum = 0;
long calFlexSum = 0, calHeelSum = 0, calForeSum = 0;

// ======================= Networking Globals =======================
WebSocketsClient wsClient;
WebSocketsServer wsServer = WebSocketsServer(CONFIG_WS_LOCAL_PORT);
WebServer portalServer(CONFIG_PORTAL_HTTP_PORT);
bool backendWsConnected = false;

// ======================= Sensor State =============================
struct MPUData {
  float ax, ay, az;
  float gx, gy, gz;
  float gxBias, gyBias, gzBias;
  float pitch, roll;
  float pitchCorrected, rollCorrected;
  bool  filterInitialized;
  bool  gyroCalibrated;
  bool  online;
};

MPUData thighData = {0};
MPUData shinData  = {0};

int   flexRaw      = 0;
float flexNorm     = 0.0f;
int   fsrHeelRaw   = 0;
int   fsrForeRaw   = 0;
float kneeAngleRaw = 0.0f;
float kneeAngleCal = 0.0f;
float fsrImbalance = 0.0f;
bool  isAbnormal   = false;
String abnormalReason = "";
bool  backendCriticalAlert = false;
bool  backendModerateAlert = false;
String backendCriticalReason = "";

unsigned long lastImuTime      = 0;
unsigned long lastSendTime     = 0;
unsigned long lastHttpPostTime = 0;
bool wdtInitialized            = false;
bool passiveBuzzerMode         = (PASSIVE_BUZZER != 0);
String serialLineBuf           = "";

// ======================= Buzzer & Output Helpers ==================

void setBuzzer(bool on) {
  if (passiveBuzzerMode) {
    if (on) tone(BUZZER_PIN, BUZZER_FREQ_HZ);
    else {
      noTone(BUZZER_PIN);
      digitalWrite(BUZZER_PIN, LOW);
    }
  } else {
    noTone(BUZZER_PIN);
    digitalWrite(BUZZER_PIN, on ? HIGH : LOW);
  }
}

void setGreenLed(uint8_t level) {
  analogWrite(GREEN_LED_PIN, level);
}

void runHardwareAlarmTest() {
  Serial.println("[TEST] Testing Green LED, Red LED, and Buzzer (Active + Passive)...");
  if (wdtInitialized) esp_task_wdt_reset();
  setGreenLed(255); delay(250); setGreenLed(0);
  digitalWrite(RED_LED_PIN, HIGH);
  noTone(BUZZER_PIN);
  digitalWrite(BUZZER_PIN, HIGH); delay(350);
  digitalWrite(BUZZER_PIN, LOW);  delay(100);
  tone(BUZZER_PIN, BUZZER_FREQ_HZ); delay(350);
  noTone(BUZZER_PIN);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(RED_LED_PIN, LOW);
  Serial.println("[TEST] Done.");
}

void sendToAll(const String &msg) {
  String copy = msg;
  if (backendWsConnected) {
    wsClient.sendTXT(copy);
  }
  wsServer.broadcastTXT(copy);
}

// ======================= Preferences Storage ======================

void loadConfigFromPrefs() {
  prefs.begin("oa_cfg", false);
  String lastDefSsid = prefs.getString("def_ssid", "");
  String lastDefHost = prefs.getString("def_host", "");

  cfgSsid        = prefs.getString("ssid", DEFAULT_WIFI_SSID);
  cfgPass        = prefs.getString("pass", DEFAULT_WIFI_PASS);
  cfgBackendHost = prefs.getString("host", DEFAULT_BACKEND_HOST);
  cfgBackendPort = prefs.getUShort("port", DEFAULT_BACKEND_PORT);
  passiveBuzzerMode = prefs.getBool("bz_pass", PASSIVE_BUZZER != 0);

  // If user edited DEFAULT_* in the sketch/config.h and re-uploaded, pick up the newly compiled defaults
  if (String(DEFAULT_WIFI_SSID) != UNCONFIGURED_SSID_PLACEHOLDER && lastDefSsid != String(DEFAULT_WIFI_SSID)) {
    cfgSsid = DEFAULT_WIFI_SSID;
    cfgPass = DEFAULT_WIFI_PASS;
    prefs.putString("ssid", cfgSsid);
    prefs.putString("pass", cfgPass);
    prefs.putString("def_ssid", DEFAULT_WIFI_SSID);
  }
  if (String(DEFAULT_BACKEND_HOST) != UNCONFIGURED_HOST_PLACEHOLDER && lastDefHost != String(DEFAULT_BACKEND_HOST)) {
    cfgBackendHost = DEFAULT_BACKEND_HOST;
    prefs.putString("host", cfgBackendHost);
    prefs.putString("def_host", DEFAULT_BACKEND_HOST);
  }
  prefs.end();
}

void saveWifiConfig(const String &newSsid, const String &newPass, const String &newHost, uint16_t newPort) {
  prefs.begin("oa_cfg", false);
  prefs.putString("ssid", newSsid);
  prefs.putString("pass", newPass);
  prefs.putString("host", newHost);
  prefs.putUShort("port", newPort);
  prefs.end();
  cfgSsid = newSsid;
  cfgPass = newPass;
  cfgBackendHost = newHost;
  cfgBackendPort = newPort;
  Serial.printf("[CFG] Saved WiFi='%s' Backend=%s:%u\n", cfgSsid.c_str(), cfgBackendHost.c_str(), cfgBackendPort);
}

void saveCalibrationToPrefs() {
  prefs.begin("oa_cal", false);
  prefs.putUChar("ver", 2);
  prefs.putUChar("step", calibration.isCalibrated);
  prefs.putFloat("tp", calibration.thighPitchOffset);
  prefs.putFloat("tr", calibration.thighRollOffset);
  prefs.putFloat("sp", calibration.shinPitchOffset);
  prefs.putFloat("sr", calibration.shinRollOffset);
  prefs.putChar("tpol", calibration.thighPolarity);
  prefs.putChar("spol", calibration.shinPolarity);
  prefs.putInt("fs", calibration.flexStraight);
  prefs.putInt("fb", calibration.flexBent);
  prefs.putInt("ht", calibration.fsrHeelTare);
  prefs.putInt("ft", calibration.fsrForeTare);
  prefs.end();
  Serial.println("[CAL] Calibration saved to flash");
}

void resetCalibrationData() {
  calibration = {
    0, 0, 0, 0, 0, 1, 1,
    DEFAULT_CALIB_FLEX_STRAIGHT, DEFAULT_CALIB_FLEX_BENT,
    DEFAULT_CALIB_FSR_HEEL_TARE, DEFAULT_CALIB_FSR_FORE_TARE
  };
  saveCalibrationToPrefs();
  Serial.println("[CAL] Calibration reset");
}

void loadCalibrationFromPrefs() {
  prefs.begin("oa_cal", true);
  uint8_t ver = prefs.getUChar("ver", 0);
  uint8_t step = prefs.getUChar("step", 0);
  if (ver == 2 && step > 0) {
    calibration.isCalibrated     = step;
    calibration.thighPitchOffset = prefs.getFloat("tp", 0.0f);
    calibration.thighRollOffset  = prefs.getFloat("tr", 0.0f);
    calibration.shinPitchOffset  = prefs.getFloat("sp", 0.0f);
    calibration.shinRollOffset   = prefs.getFloat("sr", 0.0f);
    calibration.thighPolarity    = prefs.getChar("tpol", 1);
    calibration.shinPolarity     = prefs.getChar("spol", 1);
    calibration.flexStraight     = prefs.getInt("fs", DEFAULT_CALIB_FLEX_STRAIGHT);
    calibration.flexBent         = prefs.getInt("fb", DEFAULT_CALIB_FLEX_BENT);
    calibration.fsrHeelTare      = prefs.getInt("ht", DEFAULT_CALIB_FSR_HEEL_TARE);
    calibration.fsrForeTare      = prefs.getInt("ft", DEFAULT_CALIB_FSR_FORE_TARE);
    if (calibration.thighPolarity == 0) calibration.thighPolarity = 1;
    if (calibration.shinPolarity == 0)  calibration.shinPolarity = 1;
    prefs.end();
    Serial.printf("[CAL] Loaded %u-point calibration from flash\n", calibration.isCalibrated);
  } else {
    prefs.end();
    Serial.println("[CAL] No calibration stored yet.");
  }
}

// ======================= I2C Scanner & MPU6050 ====================

void scanI2CBus() {
  Serial.printf("\n[I2C] Scanning bus (SDA=%d, SCL=%d)...\n", SDA_PIN, SCL_PIN);
  int found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    if (wdtInitialized) esp_task_wdt_reset();
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  - Device found at 0x%02X", addr);
      if (addr == MPU_THIGH_ADDR) Serial.print(" (MPU6050 #1 Thigh)");
      if (addr == MPU_SHIN_ADDR)  Serial.print(" (MPU6050 #2 Shin)");
      Serial.println();
      found++;
    }
  }
  if (found == 0) Serial.printf("  [!] No I2C devices found! Check SDA=%d, SCL=%d, 3.3V, GND.\n", SDA_PIN, SCL_PIN);
}

bool initMPU6050(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write(0x6B);
  Wire.write(0x00);
  if (Wire.endTransmission(true) != 0) return false;
  delay(10);
  // Universal gait IMU settings (100 Hz, 20 Hz DLPF, ±500°/s swing gyro, ±4g heel-strike accel)
  Wire.beginTransmission(addr); Wire.write(0x19); Wire.write(0x09); Wire.endTransmission(true); // SMPLRT_DIV=9 -> 100Hz
  Wire.beginTransmission(addr); Wire.write(0x1A); Wire.write(0x04); Wire.endTransmission(true); // DLPF ~20Hz
  Wire.beginTransmission(addr); Wire.write(0x1B); Wire.write(0x08); Wire.endTransmission(true); // ±500°/s (65.5 LSB/°/s)
  Wire.beginTransmission(addr); Wire.write(0x1C); Wire.write(0x08); Wire.endTransmission(true); // ±4g (8192 LSB/g)
  return true;
}

void calibrateGyroBias(uint8_t addr, MPUData &data, const char* label) {
  const int samples = 40;
  float sumX = 0, sumY = 0, sumZ = 0;
  float minX = 1e6f, maxX = -1e6f, minY = 1e6f, maxY = -1e6f;
  int valid = 0;

  for (int i = 0; i < samples; i++) {
    if (wdtInitialized) esp_task_wdt_reset();
    Wire.beginTransmission(addr);
    Wire.write(0x43); // GYRO_XOUT_H
    if (Wire.endTransmission(false) == 0) {
      Wire.requestFrom(addr, (uint8_t)6, (uint8_t)true);
      if (Wire.available() >= 6) {
        int16_t rx = (Wire.read() << 8) | Wire.read();
        int16_t ry = (Wire.read() << 8) | Wire.read();
        int16_t rz = (Wire.read() << 8) | Wire.read();
        float gx = rx / 65.5f;
        float gy = ry / 65.5f;
        float gz = rz / 65.5f;
        sumX += gx; sumY += gy; sumZ += gz;
        if (gx < minX) minX = gx; if (gx > maxX) maxX = gx;
        if (gy < minY) minY = gy; if (gy > maxY) maxY = gy;
        valid++;
      }
    }
    delay(5);
  }

  // Only apply if sensor stayed reasonably still during sampling (<12 deg/s spread)
  if (valid >= 25 && (maxX - minX) < 12.0f && (maxY - minY) < 12.0f) {
    data.gxBias = sumX / valid;
    data.gyBias = sumY / valid;
    data.gzBias = sumZ / valid;
    data.gyroCalibrated = true;
    Serial.printf("[CAL] %s gyro drift zeroed (X=%.2f, Y=%.2f, Z=%.2f deg/s)\n",
                  label, data.gxBias, data.gyBias, data.gzBias);
  } else {
    Serial.printf("[CAL] %s kept moving during boot -> skipped auto gyro zero\n", label);
  }
}

int readAveragedADC(uint8_t pin, uint8_t samples = 9) {
  const uint8_t n = samples < 3 ? 3 : samples;
  long total = 0;
  int vMin = 4096;
  int vMax = -1;
  for (uint8_t i = 0; i < n; i++) {
    int v = analogRead(pin);
    total += v;
    if (v < vMin) vMin = v;
    if (v > vMax) vMax = v;
  }
  int trimmed = (int)((total - vMin - vMax) / (n - 2));

  static float emaFlex = -1.0f, emaHeel = -1.0f, emaFore = -1.0f;
  float *slot = (pin == FLEX_PIN) ? &emaFlex : ((pin == FSR_HEEL_PIN) ? &emaHeel : ((pin == FSR_FORE_PIN) ? &emaFore : nullptr));
  if (!slot) return trimmed;
  if (*slot < 0.0f) {
    *slot = (float)trimmed;
  } else {
    *slot = 0.40f * (float)trimmed + 0.60f * (*slot);
  }
  return (int)(*slot + 0.5f);
}

bool readMPU6050(uint8_t addr, MPUData &data, bool isThigh, float dtSec) {
  Wire.beginTransmission(addr);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) {
    data.online = false;
    return false;
  }

  Wire.requestFrom(addr, (uint8_t)14, (uint8_t)true);
  if (Wire.available() < 14) {
    data.online = false;
    return false;
  }

  int16_t rawAx = (Wire.read() << 8) | Wire.read();
  int16_t rawAy = (Wire.read() << 8) | Wire.read();
  int16_t rawAz = (Wire.read() << 8) | Wire.read();
  int16_t rawT  = (Wire.read() << 8) | Wire.read();
  (void)rawT;
  int16_t rawGx = (Wire.read() << 8) | Wire.read();
  int16_t rawGy = (Wire.read() << 8) | Wire.read();
  int16_t rawGz = (Wire.read() << 8) | Wire.read();

  data.online = true;
  data.ax = rawAx / 8192.0f;  // ±4g full-scale
  data.ay = rawAy / 8192.0f;
  data.az = rawAz / 8192.0f;
  data.gx = (rawGx / 65.5f) - data.gxBias; // ±500°/s full-scale, bias-removed
  data.gy = (rawGy / 65.5f) - data.gyBias;
  data.gz = (rawGz / 65.5f) - data.gzBias;

  float accelPitch = atan2(data.ay, sqrt(data.ax * data.ax + data.az * data.az)) * 180.0f / PI;
  float accelRoll  = atan2(-data.ax, data.az) * 180.0f / PI;

  if (!data.filterInitialized) {
    data.pitch = accelPitch;
    data.roll  = accelRoll;
    data.filterInitialized = true;
  } else {
    data.pitch = FILTER_ALPHA * (data.pitch + data.gx * dtSec) + (1.0f - FILTER_ALPHA) * accelPitch;
    data.roll  = FILTER_ALPHA * (data.roll  + data.gy * dtSec) + (1.0f - FILTER_ALPHA) * accelRoll;
  }

  if (calibration.isCalibrated > 0) {
    if (isThigh) {
      data.pitchCorrected = calibration.thighPolarity * (data.pitch - calibration.thighPitchOffset);
      data.rollCorrected  = data.roll - calibration.thighRollOffset;
    } else {
      data.pitchCorrected = calibration.shinPolarity * (data.pitch - calibration.shinPitchOffset);
      data.rollCorrected  = data.roll - calibration.shinRollOffset;
    }
  } else {
    data.pitchCorrected = data.pitch;
    data.rollCorrected  = data.roll;
  }

  return true;
}

// ======================= Kinematics & Abnormality =================

void computeKinematics() {
  kneeAngleRaw = shinData.pitch - thighData.pitch;

  float flexAngle = shinData.pitchCorrected - thighData.pitchCorrected;
  if (flexAngle < -30.0f) flexAngle = -30.0f;
  if (flexAngle > 165.0f) flexAngle = 165.0f;
  kneeAngleCal = flexAngle;

  int32_t span = calibration.flexBent - calibration.flexStraight;
  if (calibration.isCalibrated > 0 && abs(span) >= 20) {
    flexNorm = (float)(flexRaw - calibration.flexStraight) / (float)span;
  } else {
    flexNorm = (float)(2048 - flexRaw) / 1000.0f;
  }
  if (flexNorm < 0.0f) flexNorm = 0.0f;
  if (flexNorm > 1.5f) flexNorm = 1.5f;
}

void checkAbnormality() {
  isAbnormal = false;
  abnormalReason = "";

  bool imuReady = thighData.online && shinData.online;
  float angleToCheck = (calibration.isCalibrated > 0) ? kneeAngleCal : kneeAngleRaw;
  const float angleTol = 3.5f; // Allow +/-3.5 deg strap/sensor deflection before alarming

  if (imuReady) {
    if (angleToCheck < (KNEE_ANGLE_MIN - angleTol)) {
      isAbnormal = true;
      abnormalReason = "Knee hyper-extension (<" + String((int)KNEE_ANGLE_MIN) + "°)";
    } else if (angleToCheck > (KNEE_ANGLE_MAX + angleTol)) {
      isAbnormal = true;
      abnormalReason = "Knee hyper-flexion (>" + String((int)KNEE_ANGLE_MAX) + "°)";
    }
  }

  int32_t heelAdj = max((int32_t)0, (int32_t)fsrHeelRaw - calibration.fsrHeelTare);
  int32_t foreAdj = max((int32_t)0, (int32_t)fsrForeRaw - calibration.fsrForeTare);
  float totalFSR = (float)(heelAdj + foreAdj);
  if (totalFSR > 220.0f) {
    float heelRatio = (float)heelAdj / totalFSR;
    float foreRatio = (float)foreAdj / totalFSR;
    fsrImbalance = fabs(heelRatio - foreRatio) * 100.0f;
    if (fsrImbalance > FSR_IMBALANCE_THRESH * 100.0f) {
      isAbnormal = true;
      if (abnormalReason.length() > 0) abnormalReason += " | ";
      abnormalReason += "Gait imbalance (>" + String((int)(FSR_IMBALANCE_THRESH * 100)) + "%)";
    }
  } else {
    fsrImbalance = 0.0f;
  }

  if (imuReady && calibration.isCalibrated >= 2 && angleToCheck > 45.0f && flexNorm < FLEX_STIFF_THRESH) {
    isAbnormal = true;
    if (abnormalReason.length() > 0) abnormalReason += " | ";
    abnormalReason += "Knee stiffness detected";
  }

  if (imuReady && backendCriticalAlert) {
    isAbnormal = true;
    if (abnormalReason.length() > 0) abnormalReason += " | ";
    abnormalReason += backendCriticalReason.length() > 0 ? backendCriticalReason : "Critical OA Risk Alert";
  }

  static uint8_t abnormalStreak = 0;
  if (isAbnormal) {
    if (abnormalStreak < 10) abnormalStreak++;
  } else {
    abnormalStreak = 0;
  }
  bool hwAbnormal = backendCriticalAlert || (abnormalStreak >= 2);
  bool hwModerate = !hwAbnormal && (backendModerateAlert || (imuReady && totalFSR > 220.0f && fsrImbalance > 30.0f));

  if (calState == CAL_IDLE) {
    if (!imuReady) {
      // Sensor fault / loose wire guard: blink Green gently, Red & Buzzer OFF
      setGreenLed(((millis() / 450) % 2 == 0) ? 255 : 0);
      digitalWrite(RED_LED_PIN, LOW);
      setBuzzer(false);
    } else if (hwAbnormal) {
      setGreenLed(0);
      digitalWrite(RED_LED_PIN, HIGH);
      setBuzzer(true);
    } else if (hwModerate) {
      setGreenLed(35);
      digitalWrite(RED_LED_PIN, LOW);
      setBuzzer(false);
    } else {
      setGreenLed(255);
      digitalWrite(RED_LED_PIN, LOW);
      setBuzzer(false);
    }
  }
}

// ======================= 2-Step Calibration =======================

void startCalibrationStep(CalibState step) {
  calState = step;
  calSampleCount = 0;
  calThighPitchSum = 0; calThighRollSum = 0;
  calShinPitchSum = 0;  calShinRollSum = 0;
  calFlexSum = 0; calHeelSum = 0; calForeSum = 0;

  setGreenLed(0);
  digitalWrite(RED_LED_PIN, LOW);
  setBuzzer(false);

  int stepNum = (step == CAL_STEP1_STRAIGHT) ? 1 : 2;
  Serial.printf("[CAL] Step %d started (%s)...\n", stepNum, stepNum == 1 ? "Stand Straight 0°" : "Bend Knee 90°");

  JSON_DOC(128) doc;
  doc["type"] = "calStatus";
  doc["step"] = stepNum;
  doc["status"] = "collecting";
  doc["progress"] = 0;
  String msg;
  serializeJson(doc, msg);
  sendToAll(msg);
}

void processCalibrationSample() {
  if (calState == CAL_IDLE) return;
  if (!thighData.online || !shinData.online) return;

  calThighPitchSum += thighData.pitch;
  calThighRollSum  += thighData.roll;
  calShinPitchSum  += shinData.pitch;
  calShinRollSum   += shinData.roll;
  calFlexSum       += flexRaw;
  calHeelSum       += fsrHeelRaw;
  calForeSum       += fsrForeRaw;
  calSampleCount++;

  if (calSampleCount % 5 == 0) {
    static bool calLedBlink = false;
    calLedBlink = !calLedBlink;
    setGreenLed(calLedBlink ? 255 : 0);
    digitalWrite(RED_LED_PIN, !digitalRead(RED_LED_PIN));

    int stepNum = (calState == CAL_STEP1_STRAIGHT) ? 1 : 2;
    JSON_DOC(128) doc;
    doc["type"] = "calStatus";
    doc["step"] = stepNum;
    doc["status"] = "collecting";
    doc["progress"] = (calSampleCount * 100) / CALIBRATION_SAMPLES;
    String msg;
    serializeJson(doc, msg);
    sendToAll(msg);
  }

  if (calSampleCount < CALIBRATION_SAMPLES) return;

  if (calState == CAL_STEP1_STRAIGHT) {
    calibration.thighPitchOffset = calThighPitchSum / calSampleCount;
    calibration.thighRollOffset  = calThighRollSum  / calSampleCount;
    calibration.shinPitchOffset  = calShinPitchSum  / calSampleCount;
    calibration.shinRollOffset   = calShinRollSum   / calSampleCount;
    calibration.thighPolarity    = 1;
    calibration.shinPolarity     = 1;
    calibration.flexStraight     = calFlexSum / calSampleCount;
    // Keep explicit unloaded FSR tares (0 unless tared with foot lifted) so standing weight isn't subtracted
    calibration.isCalibrated     = 1;
  } else {
    float avgThigh = calThighPitchSum / calSampleCount;
    float avgShin  = calShinPitchSum  / calSampleCount;
    float deltaT   = avgThigh - calibration.thighPitchOffset;
    float deltaS   = avgShin  - calibration.shinPitchOffset;

    calibration.thighPolarity = 1;
    calibration.shinPolarity  = 1;
    if ((deltaS - deltaT) < 0.0f) {
      if (fabsf(deltaT) > fabsf(deltaS)) {
        calibration.thighPolarity = -1;
      } else {
        calibration.shinPolarity = -1;
      }
    }

    int32_t avgFlexBent = calFlexSum / calSampleCount;
    calibration.flexBent = (abs(avgFlexBent - calibration.flexStraight) >= 20)
      ? avgFlexBent
      : (calibration.flexStraight - 800);
    calibration.isCalibrated = 2;
  }

  saveCalibrationToPrefs();
  calState = CAL_IDLE;

  JSON_DOC(384) res;
  res["type"]             = "calResult";
  res["status"]           = "complete";
  res["step"]             = calibration.isCalibrated;
  res["isTwoPoint"]       = (calibration.isCalibrated == 2);
  res["thighPitchOffset"] = round(calibration.thighPitchOffset * 100) / 100.0;
  res["thighRollOffset"]  = round(calibration.thighRollOffset * 100) / 100.0;
  res["shinPitchOffset"]  = round(calibration.shinPitchOffset * 100) / 100.0;
  res["shinRollOffset"]   = round(calibration.shinRollOffset * 100) / 100.0;
  res["thighPolarity"]    = calibration.thighPolarity;
  res["shinPolarity"]     = calibration.shinPolarity;
  res["flexStraight"]     = calibration.flexStraight;
  res["flexBent"]         = calibration.flexBent;
  res["fsrHeelTare"]      = calibration.fsrHeelTare;
  res["fsrForeTare"]      = calibration.fsrForeTare;

  String msg;
  serializeJson(res, msg);
  sendToAll(msg);
  Serial.printf("[CAL] Step %u complete!\n", calibration.isCalibrated);
}

// ======================= JSON & WiFi Commands =====================

void handleIncomingCommand(uint8_t* payload, size_t length) {
  JSON_DOC(512) cmdDoc;
  if (deserializeJson(cmdDoc, payload, length)) return;

  const char* cmd = cmdDoc["cmd"];
  if (!cmd) return;

  if (strcmp(cmd, "calibrate_step1") == 0 || strcmp(cmd, "calibrate") == 0) {
    startCalibrationStep(CAL_STEP1_STRAIGHT);
  } else if (strcmp(cmd, "calibrate_step2") == 0) {
    startCalibrationStep(CAL_STEP2_BENT);
  } else if (strcmp(cmd, "resetCalibration") == 0) {
    resetCalibrationData();
  } else if (strcmp(cmd, "applyCalibration") == 0) {
    calibration.thighPitchOffset = cmdDoc["thighPitchOffset"] | 0.0f;
    calibration.thighRollOffset  = cmdDoc["thighRollOffset"]  | 0.0f;
    calibration.shinPitchOffset  = cmdDoc["shinPitchOffset"]  | 0.0f;
    calibration.shinRollOffset   = cmdDoc["shinRollOffset"]   | 0.0f;
    calibration.thighPolarity    = cmdDoc["thighPolarity"]    | 1;
    calibration.shinPolarity     = cmdDoc["shinPolarity"]     | 1;
    calibration.flexStraight     = cmdDoc["flexStraight"]     | (cmdDoc["flexBaseline"] | 2048);
    calibration.flexBent         = cmdDoc["flexBent"]         | 1200;
    calibration.fsrHeelTare      = cmdDoc["fsrHeelTare"]      | 0;
    calibration.fsrForeTare      = cmdDoc["fsrForeTare"]      | 0;
    bool twoPt                   = cmdDoc["isTwoPoint"]       | false;
    calibration.isCalibrated     = twoPt ? 2 : 1;
    saveCalibrationToPrefs();

    JSON_DOC(96) ack;
    ack["type"] = "calApplied";
    ack["status"] = "ok";
    String ackStr;
    serializeJson(ack, ackStr);
    sendToAll(ackStr);
  } else if (strcmp(cmd, "setAlertState") == 0) {
    backendCriticalAlert = cmdDoc["critical"] | false;
    backendModerateAlert = cmdDoc["moderate"] | false;
    const char* r = cmdDoc["reason"];
    backendCriticalReason = r ? String(r) : "Critical OA Risk Alert";
    Serial.printf("[ALERT] Backend critical state=%s (%s)\n",
                  backendCriticalAlert ? "CRITICAL" : "NORMAL", backendCriticalReason.c_str());
  } else if (strcmp(cmd, "setThresholds") == 0) {
    if (HAS_KEY(cmdDoc, "kneeMin")) KNEE_ANGLE_MIN = cmdDoc["kneeMin"];
    if (HAS_KEY(cmdDoc, "kneeMax")) KNEE_ANGLE_MAX = cmdDoc["kneeMax"];
    if (HAS_KEY(cmdDoc, "fsrImb"))  FSR_IMBALANCE_THRESH = cmdDoc["fsrImb"].as<float>() / 100.0f;
    if (HAS_KEY(cmdDoc, "flexSt"))  FLEX_STIFF_THRESH = cmdDoc["flexSt"];
    Serial.printf("[CFG] Thresholds synced: [%.0f, %.0f] fsr=%.0f%% flex=%.2f\n",
                  KNEE_ANGLE_MIN, KNEE_ANGLE_MAX, FSR_IMBALANCE_THRESH * 100.0f, FLEX_STIFF_THRESH);
  } else if (strcmp(cmd, "testBuzzer") == 0) {
    runHardwareAlarmTest();
  } else if (strcmp(cmd, "setBuzzerMode") == 0) {
    passiveBuzzerMode = cmdDoc["passive"] | false;
    prefs.begin("oa_cfg", false);
    prefs.putBool("bz_pass", passiveBuzzerMode);
    prefs.end();
    Serial.printf("[CFG] Buzzer mode set to %s\n", passiveBuzzerMode ? "PASSIVE (tone)" : "ACTIVE (DC)");
  }
}

void sendConfigPacket() {
  JSON_DOC(512) cfg;
  cfg["type"] = "config";
  cfg["kneeAngleMin"] = KNEE_ANGLE_MIN;
  cfg["kneeAngleMax"] = KNEE_ANGLE_MAX;
  cfg["fsrImbalanceThresh"] = FSR_IMBALANCE_THRESH * 100.0;
  cfg["flexStiffThresh"] = FLEX_STIFF_THRESH;
  cfg["dataRateHz"] = 1000 / SEND_INTERVAL_MS;
  cfg["calibrated"] = (calibration.isCalibrated > 0);
  cfg["calibrationStep"] = calibration.isCalibrated;

  if (calibration.isCalibrated > 0) {
    JsonObject cal = NESTED_OBJ(cfg, "calibration");
    cal["thighPitchOffset"] = round(calibration.thighPitchOffset * 100) / 100.0;
    cal["thighRollOffset"]  = round(calibration.thighRollOffset * 100) / 100.0;
    cal["shinPitchOffset"]  = round(calibration.shinPitchOffset * 100) / 100.0;
    cal["shinRollOffset"]   = round(calibration.shinRollOffset * 100) / 100.0;
    cal["thighPolarity"]    = calibration.thighPolarity;
    cal["shinPolarity"]     = calibration.shinPolarity;
    cal["flexStraight"]     = calibration.flexStraight;
    cal["flexBent"]         = calibration.flexBent;
    cal["isTwoPoint"]       = (calibration.isCalibrated == 2);
  }

  String cfgStr;
  serializeJson(cfg, cfgStr);
  sendToAll(cfgStr);
}

void onWsClientEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      backendWsConnected = false;
      Serial.println("[WS-Client] Backend disconnected (HTTP fallback active)");
      break;
    case WStype_CONNECTED:
      backendWsConnected = true;
      Serial.printf("[WS-Client] Connected to ws://%s:%u%s\n",
                    cfgBackendHost.c_str(), cfgBackendPort, BACKEND_WS_PATH);
      sendConfigPacket();
      break;
    case WStype_TEXT:
      handleIncomingCommand(payload, length);
      break;
    default:
      break;
  }
}

void onWsServerEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
  if (type == WStype_CONNECTED) {
    Serial.printf("[WS-Server] Local client #%u connected\n", num);
    sendConfigPacket();
  } else if (type == WStype_TEXT) {
    handleIncomingCommand(payload, length);
  }
}

String buildTelemetryJson() {
  JSON_DOC(1024) doc;

  doc["type"] = "data";
  doc["timestamp"] = millis();
  doc["calibrated"] = (calibration.isCalibrated > 0);
  doc["calibrationStep"] = calibration.isCalibrated;

  JsonObject thigh = NESTED_OBJ(doc, "thigh");
  thigh["online"] = thighData.online;
  thigh["ax"] = round(thighData.ax * 1000) / 1000.0;
  thigh["ay"] = round(thighData.ay * 1000) / 1000.0;
  thigh["az"] = round(thighData.az * 1000) / 1000.0;
  thigh["gx"] = round(thighData.gx * 100) / 100.0;
  thigh["gy"] = round(thighData.gy * 100) / 100.0;
  thigh["gz"] = round(thighData.gz * 100) / 100.0;
  thigh["pitch"]  = round(thighData.pitch * 10) / 10.0;
  thigh["roll"]   = round(thighData.roll * 10) / 10.0;
  thigh["pitchC"] = round(thighData.pitchCorrected * 10) / 10.0;
  thigh["rollC"]  = round(thighData.rollCorrected * 10) / 10.0;

  JsonObject shin = NESTED_OBJ(doc, "shin");
  shin["online"] = shinData.online;
  shin["ax"] = round(shinData.ax * 1000) / 1000.0;
  shin["ay"] = round(shinData.ay * 1000) / 1000.0;
  shin["az"] = round(shinData.az * 1000) / 1000.0;
  shin["gx"] = round(shinData.gx * 100) / 100.0;
  shin["gy"] = round(shinData.gy * 100) / 100.0;
  shin["gz"] = round(shinData.gz * 100) / 100.0;
  shin["pitch"]  = round(shinData.pitch * 10) / 10.0;
  shin["roll"]   = round(shinData.roll * 10) / 10.0;
  shin["pitchC"] = round(shinData.pitchCorrected * 10) / 10.0;
  shin["rollC"]  = round(shinData.rollCorrected * 10) / 10.0;

  doc["flex"]         = flexRaw;
  doc["flexNorm"]     = round(flexNorm * 1000) / 1000.0;
  doc["flexBaseline"] = calibration.flexStraight;
  doc["fsrHeel"]      = fsrHeelRaw;
  doc["fsrFore"]      = fsrForeRaw;
  doc["kneeAngle"]    = round(kneeAngleRaw * 10) / 10.0;
  doc["kneeAngleC"]   = round(kneeAngleCal * 10) / 10.0;
  doc["fsrImbalance"] = round(fsrImbalance * 10) / 10.0;
  doc["abnormal"]     = isAbnormal;
  doc["reason"]       = abnormalReason;

  String out;
  serializeJson(doc, out);
  return out;
}

void postTelemetryHttpFallback(const String &jsonStr) {
  if (WiFi.status() != WL_CONNECTED) return;
  if (cfgBackendHost.length() < 3 || cfgBackendHost == "0.0.0.0") return;

  HTTPClient http;
  String url = "http://" + cfgBackendHost + ":" + String(cfgBackendPort) + BACKEND_HTTP_PATH;
  http.setConnectTimeout(HTTP_CONNECT_TIMEOUT_MS);
  http.setTimeout(HTTP_READ_TIMEOUT_MS);
  if (http.begin(url)) {
    http.addHeader("Content-Type", "application/json");
    http.POST(jsonStr);
    http.end();
  }
}

// ======================= Captive Setup Portal (Fallback AP) =======

void startSetupPortal() {
  apModeActive = true;
  WiFi.mode(WIFI_AP);
  if (strlen(CONFIG_AP_PASS) >= 8) {
    WiFi.softAP(AP_SETUP_SSID, CONFIG_AP_PASS);
  } else {
    WiFi.softAP(AP_SETUP_SSID);
  }
  IPAddress apIP = WiFi.softAPIP();
  Serial.printf("\n[AP] WiFi Portal started! Connect to '%s' -> http://%s\n",
                AP_SETUP_SSID, apIP.toString().c_str());

  portalServer.on("/", HTTP_GET, []() {
    String html = "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
                  "<title>OA Wearable WiFi Setup</title><style>body{font-family:sans-serif;max-width:420px;margin:24px auto;padding:16px;background:#f2f6f5;color:#1e2d2f}"
                  "input{width:100%;padding:10px;margin:6px 0 14px;border:1px solid #cbd5e1;border-radius:8px;box-sizing:border-box}"
                  "button{width:100%;padding:12px;background:#0d9488;color:#fff;border:none;border-radius:8px;font-weight:700;font-size:15px}</style></head>"
                  "<body><h2>OA Wearable WiFi Setup</h2><form method='POST' action='/save'>"
                  "<label>WiFi Name (SSID)</label><input name='ssid' value='";
    html += cfgSsid;
    html += "' required><label>WiFi Password</label><input name='pass' type='password' value='";
    html += cfgPass;
    html += "'><label>Laptop Backend IP</label><input name='host' value='";
    html += cfgBackendHost;
    html += "' required><label>Backend Port</label><input name='port' type='number' value='";
    html += String(cfgBackendPort);
    html += "'><button type='submit'>Save &amp; Connect</button></form></body></html>";
    portalServer.send(200, "text/html", html);
  });

  portalServer.on("/save", HTTP_POST, []() {
    String s = portalServer.arg("ssid");
    String p = portalServer.arg("pass");
    String h = portalServer.arg("host");
    uint16_t port = portalServer.arg("port").toInt();
    if (port == 0) port = DEFAULT_BACKEND_PORT;
    saveWifiConfig(s, p, h, port);
    portalServer.send(200, "text/html", "<h3>Saved! Rebooting ESP32 into WiFi Station mode...</h3>");
    delay(1200);
    ESP.restart();
  });

  portalServer.begin();
}

void connectStationWiFi() {
  if (cfgSsid == UNCONFIGURED_SSID_PLACEHOLDER || cfgSsid.length() == 0) {
    Serial.println("[WiFi] Default placeholder SSID detected -> launching Setup AP");
    startSetupPortal();
    return;
  }

  Serial.printf("[WiFi] Connecting to '%s' ", cfgSsid.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(cfgSsid.c_str(), cfgPass.c_str());

  unsigned long startMs = millis();
  bool blink = false;
  while (WiFi.status() != WL_CONNECTED && millis() - startMs < 15000) {
    delay(400);
    Serial.print(".");
    blink = !blink;
    setGreenLed(blink ? 255 : 0);
  }

  if (WiFi.status() == WL_CONNECTED) {
    apModeActive = false;
    Serial.println("\n[WiFi] Connected!");
    Serial.print("  ESP32 IP     : "); Serial.println(WiFi.localIP());
    Serial.printf("  Backend WS   : ws://%s:%u%s\n", cfgBackendHost.c_str(), cfgBackendPort, BACKEND_WS_PATH);
    Serial.printf("  Local WS     : ws://%s:%u\n", WiFi.localIP().toString().c_str(), CONFIG_WS_LOCAL_PORT);
    setGreenLed(255);

    wsClient.begin(cfgBackendHost.c_str(), cfgBackendPort, BACKEND_WS_PATH);
    wsClient.onEvent(onWsClientEvent);
    wsClient.setReconnectInterval(WS_RECONNECT_INTERVAL_MS);
  } else {
    Serial.println("\n[WiFi] Connection failed -> starting fallback Setup AP");
    startSetupPortal();
  }
}

// ======================= Serial Diagnostics =======================

void handleSerialCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      serialLineBuf += c;
      continue;
    }

    String line = serialLineBuf;
    serialLineBuf = "";
    line.trim();
    if (line.length() == 0) continue;

    if (line == "help") {
      Serial.println("\n--- Serial Commands ---");
      Serial.println("  scan                        -> Scan I2C bus (0x68 Thigh, 0x69 Shin)");
      Serial.println("  status                      -> Print WiFi, sensors & calibration status");
      Serial.println("  cal1 / cal2 / cal_reset     -> Run Step 1 (0°), Step 2 (90°), or reset");
      Serial.println("  test_alarm                  -> Flash Red/Green LEDs & beep Buzzer");
      Serial.println("  set_wifi:SSID,PASS,HOST_IP  -> Save WiFi & laptop IP and reboot");
      Serial.println("  clear_wifi                  -> Erase saved WiFi config");
    } else if (line == "scan") {
      scanI2CBus();
    } else if (line == "status") {
      Serial.printf("[STATUS] WiFi=%s (%s) | BackendWS=%s | Thigh(0x68)=%s | Shin(0x69)=%s\n",
                    WiFi.status() == WL_CONNECTED ? "OK" : (apModeActive ? "AP_SETUP" : "DOWN"),
                    WiFi.localIP().toString().c_str(),
                    backendWsConnected ? "CONNECTED" : "DISCONNECTED",
                    thighData.online ? "OK" : "ERR",
                    shinData.online ? "OK" : "ERR");
      Serial.printf("  Knee=%.1f° (raw %.1f°) | Flex=%d (%.0f%%) | Heel=%d | Fore=%d | %s\n",
                    kneeAngleCal, kneeAngleRaw, flexRaw, flexNorm * 100.0f,
                    fsrHeelRaw, fsrForeRaw, isAbnormal ? "ABNORMAL" : "Normal");
    } else if (line == "cal1") {
      startCalibrationStep(CAL_STEP1_STRAIGHT);
    } else if (line == "cal2") {
      startCalibrationStep(CAL_STEP2_BENT);
    } else if (line == "cal_reset") {
      resetCalibrationData();
    } else if (line == "test_alarm") {
      runHardwareAlarmTest();
    } else if (line.startsWith("set_wifi:")) {
      String args = line.substring(9);
      int c1 = args.indexOf(',');
      int c2 = args.indexOf(',', c1 + 1);
      if (c1 > 0 && c2 > c1) {
        String s = args.substring(0, c1);
        String p = args.substring(c1 + 1, c2);
        String h = args.substring(c2 + 1);
        s.trim(); p.trim(); h.trim();
        saveWifiConfig(s, p, h, DEFAULT_BACKEND_PORT);
        Serial.println("[CFG] Rebooting to apply new WiFi...");
        delay(500);
        ESP.restart();
      } else {
        Serial.println("[ERR] Format: set_wifi:MySSID,MyPassword,192.168.1.50");
      }
    } else if (line == "clear_wifi") {
      prefs.begin("oa_cfg", false);
      prefs.clear();
      prefs.end();
      Serial.println("[CFG] Cleared saved WiFi config. Rebooting...");
      delay(500);
      ESP.restart();
    }
  }
}

// ======================= Setup & Main Loop ========================

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n==================================================");
  Serial.println("SIH26004 - OA Risk Detection Wearable v4.0");
  Serial.println("Type 'help' in Serial Monitor for diagnostics/setup");
  Serial.println("==================================================");

  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(RED_LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  setGreenLed(0);
  digitalWrite(RED_LED_PIN, LOW);
  setBuzzer(false);

  loadConfigFromPrefs();
  loadCalibrationFromPrefs();

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  Wire.setClock(I2C_BUS_CLOCK_HZ);
  scanI2CBus();

  bool tInit = initMPU6050(MPU_THIGH_ADDR);
  bool sInit = initMPU6050(MPU_SHIN_ADDR);
  Serial.printf("MPU6050 Thigh (0x%02X): %s | Shin (0x%02X): %s\n",
                MPU_THIGH_ADDR, tInit ? "OK" : "NOT FOUND",
                MPU_SHIN_ADDR, sInit ? "OK" : "NOT FOUND");
  if (tInit) calibrateGyroBias(MPU_THIGH_ADDR, thighData, "Thigh");
  if (sInit) calibrateGyroBias(MPU_SHIN_ADDR, shinData, "Shin");

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  connectStationWiFi();

  wsServer.begin();
  wsServer.onEvent(onWsServerEvent);

  // Task Watchdog Timer (compatible with both ESP32 Arduino Core 2.x and 3.x / IDF 5+)
  #if defined(ESP_IDF_VERSION_MAJOR) && (ESP_IDF_VERSION_MAJOR >= 5)
    esp_task_wdt_config_t twdt_config = {
        .timeout_ms = WATCHDOG_TIMEOUT_SECS * 1000,
        .idle_core_mask = 0,
        .trigger_panic = true
    };
    if (esp_task_wdt_reconfigure(&twdt_config) == ESP_ERR_INVALID_STATE) {
      esp_task_wdt_init(&twdt_config);
    }
    if (esp_task_wdt_status(NULL) != ESP_OK) {
      esp_task_wdt_add(NULL);
    }
  #else
    esp_task_wdt_init(WATCHDOG_TIMEOUT_SECS, true);
    esp_task_wdt_add(NULL);
  #endif
  wdtInitialized = true;
}

void loop() {
  esp_task_wdt_reset();
  handleSerialCommands();

  if (apModeActive) {
    portalServer.handleClient();
    // Device Initializing / Setup AP: Green LED blinks gently, Red LED OFF, Buzzer OFF
    setGreenLed(((millis() / 450) % 2 == 0) ? 255 : 0);
    digitalWrite(RED_LED_PIN, LOW);
    setBuzzer(false);
  } else {
    wsClient.loop();
  }
  wsServer.loop();

  unsigned long now = millis();

  // Periodic MPU6050 auto-recovery if either IMU dropped offline
  static unsigned long lastMpuRecoveryTime = 0;
  if (!thighData.online || !shinData.online) {
    if (now - lastMpuRecoveryTime >= MPU_REINIT_INTERVAL_MS) {
      lastMpuRecoveryTime = now;
      if (!thighData.online) initMPU6050(MPU_THIGH_ADDR);
      if (!shinData.online) initMPU6050(MPU_SHIN_ADDR);
    }
  }

  // 100 Hz IMU & Complementary Filter update
  if (now - lastImuTime >= IMU_INTERVAL_MS) {
    float dt = (now - lastImuTime) / 1000.0f;
    if (dt <= 0.0f || dt > 0.2f) dt = (float)IMU_INTERVAL_MS / 1000.0f;
    lastImuTime = now;

    readMPU6050(MPU_THIGH_ADDR, thighData, true, dt);
    readMPU6050(MPU_SHIN_ADDR, shinData, false, dt);
  }

  // 10 Hz Fusion, Safety Indicators & WiFi Transmission
  if (now - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = now;

    flexRaw    = readAveragedADC(FLEX_PIN);
    fsrHeelRaw = readAveragedADC(FSR_HEEL_PIN);
    fsrForeRaw = readAveragedADC(FSR_FORE_PIN);

    computeKinematics();

    if (calState != CAL_IDLE) {
      processCalibrationSample();
    }

    checkAbnormality();

    String jsonStr = buildTelemetryJson();
    sendToAll(jsonStr);

    // Automatic HTTP POST fallback if WebSocket push is disconnected
    if (!apModeActive && !backendWsConnected && (now - lastHttpPostTime >= HTTP_FALLBACK_MS)) {
      lastHttpPostTime = now;
      postTelemetryHttpFallback(jsonStr);
    }
  }
}
