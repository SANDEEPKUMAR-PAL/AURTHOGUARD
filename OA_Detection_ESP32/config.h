#pragma once
#ifndef OA_CONFIG_H
#define OA_CONFIG_H

#include <stdint.h>

#if __has_include("secrets.h")
  #include "secrets.h"
#elif __has_include("secrets.h.template")
  #include "secrets.h.template"
#endif

// ======================= Network Defaults =========================
#ifndef CONFIG_WIFI_SSID
#define CONFIG_WIFI_SSID              "YOUR_WIFI_SSID"
#endif

#ifndef CONFIG_WIFI_PASS
#define CONFIG_WIFI_PASS              "YOUR_WIFI_PASSWORD"
#endif

#ifndef CONFIG_BACKEND_HOST
#define CONFIG_BACKEND_HOST           "192.168.1.100"
#endif

#ifndef CONFIG_BACKEND_PORT
#define CONFIG_BACKEND_PORT           8000
#endif

#ifndef CONFIG_BACKEND_WS_PATH
#define CONFIG_BACKEND_WS_PATH        "/ws/esp32"
#endif

#ifndef CONFIG_BACKEND_HTTP_PATH
#define CONFIG_BACKEND_HTTP_PATH      "/api/telemetry"
#endif

#ifndef CONFIG_AP_SSID
#define CONFIG_AP_SSID                "OA-Wearable-Setup"
#endif

#ifndef CONFIG_AP_PASS
#define CONFIG_AP_PASS                ""
#endif

#ifndef CONFIG_WS_LOCAL_PORT
#define CONFIG_WS_LOCAL_PORT          81
#endif

#ifndef CONFIG_PORTAL_HTTP_PORT
#define CONFIG_PORTAL_HTTP_PORT       80
#endif

#define UNCONFIGURED_SSID_PLACEHOLDER "YOUR_WIFI_SSID"
#define UNCONFIGURED_HOST_PLACEHOLDER "192.168.1.100"

// Aliases for sketch compatibility
#define DEFAULT_WIFI_SSID             CONFIG_WIFI_SSID
#define DEFAULT_WIFI_PASS             CONFIG_WIFI_PASS
#define DEFAULT_BACKEND_HOST          CONFIG_BACKEND_HOST
#define DEFAULT_BACKEND_PORT          CONFIG_BACKEND_PORT
#define BACKEND_WS_PATH               CONFIG_BACKEND_WS_PATH
#define BACKEND_HTTP_PATH             CONFIG_BACKEND_HTTP_PATH
#define AP_SETUP_SSID                 CONFIG_AP_SSID

// ======================= Hardware Pinout ==========================
#ifndef PIN_I2C_SDA
#define PIN_I2C_SDA                   21
#endif

#ifndef PIN_I2C_SCL
#define PIN_I2C_SCL                   22
#endif

#ifndef PIN_FLEX_ADC
#define PIN_FLEX_ADC                  32
#endif

#ifndef PIN_FSR_HEEL_ADC
#define PIN_FSR_HEEL_ADC              34
#endif

#ifndef PIN_FSR_FORE_ADC
#define PIN_FSR_FORE_ADC              35
#endif

#ifndef PIN_LED_GREEN
#define PIN_LED_GREEN                 26
#endif

#ifndef PIN_LED_RED
#define PIN_LED_RED                   27
#endif

#ifndef PIN_BUZZER
#define PIN_BUZZER                    25
#endif

// Backward compatibility macro aliases
#define SDA_PIN                       PIN_I2C_SDA
#define SCL_PIN                       PIN_I2C_SCL
#define FLEX_PIN                      PIN_FLEX_ADC
#define FSR_HEEL_PIN                  PIN_FSR_HEEL_ADC
#define FSR_FORE_PIN                  PIN_FSR_FORE_ADC
#define GREEN_LED_PIN                 PIN_LED_GREEN
#define RED_LED_PIN                   PIN_LED_RED
#define BUZZER_PIN                    PIN_BUZZER

// ======================= I2C & Sensors ============================
#ifndef MPU_THIGH_ADDR
#define MPU_THIGH_ADDR                0x68
#endif
#ifndef MPU_THIGH_I2C_ADDR
#define MPU_THIGH_I2C_ADDR            0x68
#endif

#ifndef MPU_SHIN_ADDR
#define MPU_SHIN_ADDR                 0x69
#endif
#ifndef MPU_SHIN_I2C_ADDR
#define MPU_SHIN_I2C_ADDR             0x69
#endif

#ifndef I2C_BUS_CLOCK_HZ
#define I2C_BUS_CLOCK_HZ              400000
#endif

#ifndef I2C_TIMEOUT_MS
#define I2C_TIMEOUT_MS                50
#endif

// ======================= Buzzer Tuning ============================
#ifndef PASSIVE_BUZZER
#define PASSIVE_BUZZER                0
#endif

#ifndef BUZZER_FREQ_HZ
#define BUZZER_FREQ_HZ                2000
#endif

// ======================= Clinical Thresholds ======================
// AAOS Goniometry & AUC: 0° straight, -10° hyperextension, 135° full flexion
#ifndef DEFAULT_KNEE_ANGLE_MIN
#define DEFAULT_KNEE_ANGLE_MIN        -10.0f
#endif

#ifndef DEFAULT_KNEE_ANGLE_MAX
#define DEFAULT_KNEE_ANGLE_MAX        135.0f
#endif

#ifndef DEFAULT_FSR_IMBALANCE_THRESH
#define DEFAULT_FSR_IMBALANCE_THRESH  0.60f
#endif

#ifndef DEFAULT_FLEX_STIFF_THRESH
#define DEFAULT_FLEX_STIFF_THRESH     0.25f
#endif

// ======================= Calibration Defaults =====================
#ifndef DEFAULT_CALIB_FLEX_STRAIGHT
#define DEFAULT_CALIB_FLEX_STRAIGHT   2048
#endif

#ifndef DEFAULT_CALIB_FLEX_BENT
#define DEFAULT_CALIB_FLEX_BENT       1200
#endif

#ifndef DEFAULT_CALIB_FSR_HEEL_TARE
#define DEFAULT_CALIB_FSR_HEEL_TARE   0
#endif

#ifndef DEFAULT_CALIB_FSR_FORE_TARE
#define DEFAULT_CALIB_FSR_FORE_TARE   0
#endif

// ======================= Timing & Rate Constants ==================
#ifndef IMU_INTERVAL_MS
#define IMU_INTERVAL_MS               10    // 100 Hz IMU rate
#endif

#ifndef SEND_INTERVAL_MS
#define SEND_INTERVAL_MS              100   // 10 Hz streaming
#endif

#ifndef HTTP_FALLBACK_MS
#define HTTP_FALLBACK_MS              1000  // 1 Hz fallback
#endif

#ifndef HTTP_CONNECT_TIMEOUT_MS
#define HTTP_CONNECT_TIMEOUT_MS       80    // Fast connect timeout (<= 100ms)
#endif

#ifndef HTTP_READ_TIMEOUT_MS
#define HTTP_READ_TIMEOUT_MS          100   // Fast read timeout (<= 100ms)
#endif

#ifndef HTTP_TIMEOUT_MS
#define HTTP_TIMEOUT_MS               HTTP_READ_TIMEOUT_MS
#endif

#ifndef WS_RECONNECT_INTERVAL_MS
#define WS_RECONNECT_INTERVAL_MS      4000
#endif

#ifndef CALIBRATION_SAMPLES
#define CALIBRATION_SAMPLES           30
#endif

#ifndef FILTER_ALPHA
#define FILTER_ALPHA                  0.96f
#endif

#ifndef WATCHDOG_TIMEOUT_SECS
#define WATCHDOG_TIMEOUT_SECS         8
#endif

#ifndef MPU_REINIT_INTERVAL_MS
#define MPU_REINIT_INTERVAL_MS        3000  // Periodic offline recovery
#endif

#endif // OA_CONFIG_H
