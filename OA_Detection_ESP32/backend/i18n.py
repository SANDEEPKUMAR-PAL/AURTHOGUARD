"""Bilingual (English + Hindi) localization for guided OA screening exercises & voice cues."""

from __future__ import annotations

from typing import Literal

SupportedLang = Literal["en", "hi"]


EXERCISE_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "normal_walk": {
            "name": "Normal Gait Walk Test",
            "category": "Dynamic Gait",
            "instructions": "Walk steadily across the floor. System tracks stride ROM (0°–65°), heel-to-toe pressure transfer, and lateral joint stability.",
        },
        "seated_flex_ext": {
            "name": "Seated Flexion–Extension Sweep",
            "category": "Joint ROM & Stiffness",
            "instructions": "Sit upright, bend knee slowly toward 110°, then extend completely straight to 0°. Detects flexion contracture and tendon stiffness.",
        },
        "sit_to_stand": {
            "name": "5× Sit-to-Stand Chair Transfer",
            "category": "Functional Strength & Load",
            "instructions": "Stand up smoothly from seated 90° without hand support and sit back down. Tracks thigh tilt drive, extension speed, and plantar weight shift.",
        },
        "mini_squat": {
            "name": "Loaded Mini-Squat Stability",
            "category": "Varus/Valgus Stability",
            "instructions": "Perform controlled shallow squats to ~55°–65° while keeping heel & forefoot grounded. Measures IMU roll wobble and load symmetry.",
        },
        "weight_shift": {
            "name": "Heel–Toe Plantar Weight Shift",
            "category": "Proprioception & FSR",
            "instructions": "Stand with knee straight (0°–15°) and rock weight deliberately between heel and forefoot. Detects antalgic offloading and posture lock.",
        },
    },
    "hi": {
        "normal_walk": {
            "name": "सामान्य चाल परीक्षण (Normal Walk)",
            "category": "गतिशील चाल विश्लेषण",
            "instructions": "सामान्य गति से सीधे चलें। सिस्टम घुटने के मोड़ (0°–65°), एड़ी-से-पंजे के दबाव और संतुलन की जाँच करता है।",
        },
        "seated_flex_ext": {
            "name": "बैठकर घुटना मोड़ना और सीधा करना",
            "category": "जोड़ गति और जकड़न",
            "instructions": "सीधे बैठें, घुटने को धीरे-धीरे 110° तक मोड़ें और फिर 0° तक पूरा सीधा करें। यह जकड़न की जाँच करता है।",
        },
        "sit_to_stand": {
            "name": "कुर्सी से उठना-बैठना (5 बार)",
            "category": "मांसपेशी बल और भार संतुलन",
            "instructions": "बिना हाथ का सहारा लिए आराम से खड़े हों और वापस बैठें। यह जांघ की गति और पैर के भार बदलाव को मापता है।",
        },
        "mini_squat": {
            "name": "हल्का स्क्वाट स्थिरता परीक्षण",
            "category": "घुटना स्थिरता",
            "instructions": "दोनों पैरों को जमीन पर रखकर हल्का सा 55°–65° तक झुकें और ऊपर आएं। यह घुटने के डगमगाने की जाँच करता है।",
        },
        "weight_shift": {
            "name": "एड़ी से पंजे पर भार बदलना",
            "category": "संतुलन और दबाव जाँच",
            "instructions": "घुटने को सीधा (0°–15°) रखकर शरीर का वजन बारी-बारी से एड़ी और आगे के पंजे पर डालें।",
        },
    },
}

CUE_TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "ready": "Ready to begin exercise",
        "bend": "Bend knee smoothly toward {target}° (now {angle}°)",
        "extend": "Extend leg straight toward 0° (now {angle}°)",
        "flexed_hold": "Target reached ({angle}°) — now extend straight to 0°",
        "rep_done": "Rep {rep} of {total} complete!",
        "wobble": "Keep knee steady — lateral wobble detected",
        "ws_fore_ok": "Good forefoot load — now shift weight back onto heel",
        "ws_heel_rep": "Rep {rep} complete! Shift smoothly forward",
        "ws_straight": "Keep knee straighter (<15°) during weight shift",
        "ws_rock": "Rock weight smoothly between heel and forefoot",
        "Pending": "Pending",
        "Normal": "Normal",
        "Mild Deficit": "Mild Deficit",
        "High Risk Marker": "High Risk Marker",
    },
    "hi": {
        "ready": "व्यायाम शुरू करने के लिए तैयार",
        "bend": "घुटने को धीरे से {target}° की ओर मोड़ें (अभी {angle}°)",
        "extend": "पैर को 0° तक सीधा करें (अभी {angle}°)",
        "flexed_hold": "लक्ष्य पूरा ({angle}°) — अब पैर सीधा करें",
        "rep_done": "{total} में से {rep} दोहराव पूरा!",
        "wobble": "घुटने को स्थिर रखें — हल्का कंपन देखा गया",
        "ws_fore_ok": "आगे के पंजे पर भार अच्छा है — अब एड़ी पर वापस जाएं",
        "ws_heel_rep": "{rep} दोहराव पूरा! आगे पंजे की ओर झुकें",
        "ws_straight": "भार बदलते समय घुटने को 15° से सीधा रखें",
        "ws_rock": "एड़ी और पंजे के बीच आराम से वजन बदलें",
        "Pending": "प्रतीक्षा में",
        "Normal": "सामान्य",
        "Mild Deficit": "हल्की कमी",
        "High Risk Marker": "उच्च जोखिम संकेत",
    },
}


def normalize_lang(lang: str | None) -> SupportedLang:
    return "hi" if lang == "hi" else "en"


def get_localized_exercise(key: str, lang: str | None = "en") -> dict[str, str]:
    l = normalize_lang(lang)
    return EXERCISE_TEXT[l].get(key, EXERCISE_TEXT["en"][key])


def format_cue(lang: str | None, cue_key: str, **kwargs) -> str:
    l = normalize_lang(lang)
    tmpl = CUE_TEMPLATES[l].get(cue_key, CUE_TEMPLATES["en"].get(cue_key, cue_key))
    return tmpl.format(**kwargs) if kwargs else tmpl

