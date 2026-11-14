"""Async SQLite persistence layer for patients, calibrations, sessions, telemetry, alerts, and settings."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from backend.config import settings


def utc_now_iso() -> str:
    """Return current timestamp in the configured display timezone (default IST) in ISO-8601 format."""
    return settings.now_tz_iso()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    age INTEGER,
    gender TEXT NOT NULL DEFAULT 'Male',
    monitored_leg TEXT NOT NULL DEFAULT 'Left',
    height_cm REAL,
    weight_kg REAL,
    region_district TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    thigh_pitch_offset REAL NOT NULL DEFAULT 0.0,
    thigh_roll_offset REAL NOT NULL DEFAULT 0.0,
    shin_pitch_offset REAL NOT NULL DEFAULT 0.0,
    shin_roll_offset REAL NOT NULL DEFAULT 0.0,
    thigh_polarity INTEGER NOT NULL DEFAULT 1,
    shin_polarity INTEGER NOT NULL DEFAULT 1,
    flex_straight INTEGER NOT NULL DEFAULT 2048,
    flex_bent INTEGER NOT NULL DEFAULT 1200,
    fsr_heel_tare INTEGER NOT NULL DEFAULT 0,
    fsr_fore_tare INTEGER NOT NULL DEFAULT 0,
    is_two_point INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    calibrated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER REFERENCES patients(id) ON DELETE SET NULL,
    patient_name TEXT NOT NULL DEFAULT 'Walk-in / Unassigned',
    clinician_name TEXT NOT NULL DEFAULT '',
    monitored_leg TEXT NOT NULL DEFAULT 'Left',
    calibration_id INTEGER REFERENCES calibrations(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'esp32',
    start_time TEXT NOT NULL,
    end_time TEXT,
    sample_count INTEGER NOT NULL DEFAULT 0,
    alert_count INTEGER NOT NULL DEFAULT 0,
    avg_knee_angle REAL,
    min_knee_angle REAL,
    max_knee_angle REAL,
    rom_deg REAL,
    oa_risk_score REAL,
    oa_risk_tier TEXT,
    risk_markers_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS telemetry_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    timestamp_ms INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    thigh_ax REAL, thigh_ay REAL, thigh_az REAL,
    thigh_gx REAL, thigh_gy REAL, thigh_gz REAL,
    thigh_pitch REAL, thigh_roll REAL, thigh_pitch_c REAL, thigh_roll_c REAL,
    shin_ax REAL, shin_ay REAL, shin_az REAL,
    shin_gx REAL, shin_gy REAL, shin_gz REAL,
    shin_pitch REAL, shin_roll REAL, shin_pitch_c REAL, shin_roll_c REAL,
    flex_raw INTEGER, flex_norm REAL,
    fsr_heel INTEGER, fsr_fore INTEGER,
    knee_angle REAL, knee_angle_c REAL,
    fsr_imbalance REAL,
    abnormal INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    oa_risk_score REAL
);

CREATE INDEX IF NOT EXISTS idx_telemetry_session_id ON telemetry_samples(session_id);
CREATE INDEX IF NOT EXISTS idx_alerts_session_id ON alerts(session_id);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    patient_id INTEGER REFERENCES patients(id) ON DELETE SET NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    marker_type TEXT NOT NULL DEFAULT 'threshold',
    message TEXT NOT NULL,
    knee_angle REAL,
    oa_risk_score REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exercise_trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER REFERENCES patients(id) ON DELETE SET NULL,
    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    monitored_leg TEXT NOT NULL DEFAULT 'Left',
    exercise_key TEXT NOT NULL,
    exercise_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    target_reps INTEGER NOT NULL DEFAULT 5,
    completed_reps INTEGER NOT NULL DEFAULT 0,
    quality_score REAL,
    finding TEXT,
    avg_rom_deg REAL,
    max_flexion_deg REAL,
    min_flexion_deg REAL,
    avg_imbalance_pct REAL,
    roll_wobble_deg REAL,
    flex_agreement_pct REAL,
    markers_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    ended_at TEXT
);
"""


# Note: create alerts table before creating idx_alerts_session_id
SCHEMA_SQL_ORDERED = SCHEMA_SQL.replace(
    "CREATE INDEX IF NOT EXISTS idx_alerts_session_id ON alerts(session_id);\n", ""
) + "\nCREATE INDEX IF NOT EXISTS idx_alerts_session_id ON alerts(session_id);\n"


class Database:
    """Async SQLite repository wrapper."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """Open connection, enable WAL + foreign keys, and create schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(SCHEMA_SQL_ORDERED)
        await self._migrate_schema()
        await self._conn.commit()

    async def _migrate_schema(self) -> None:
        """Add bilateral monitored_leg column cleanly if upgrading an existing DB."""
        for table in ("sessions", "exercise_trials"):
            cur = await self.conn.execute(f"PRAGMA table_info({table})")
            cols = {row["name"] for row in await cur.fetchall()}
            if "monitored_leg" not in cols:
                await self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN monitored_leg TEXT NOT NULL DEFAULT 'Left'"
                )

    async def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database has not been initialized. Call await db.init() first.")
        return self._conn

    # -------------------- Calibration Helpers --------------------

    @staticmethod
    def _row_to_calibration(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        d["is_two_point"] = bool(d["is_two_point"])
        d["is_active"] = bool(d["is_active"])
        return d

    async def get_active_calibration(self, patient_id: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM calibrations WHERE patient_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
            (patient_id,),
        )
        row = await cur.fetchone()
        return self._row_to_calibration(row) if row else None

    async def list_calibrations(self, patient_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM calibrations WHERE patient_id = ? ORDER BY id DESC",
            (patient_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_calibration(r) for r in rows]

    async def create_calibration(
        self, patient_id: int, data: dict[str, Any], activate: bool = True
    ) -> dict[str, Any]:
        now = utc_now_iso()
        if activate:
            await self.conn.execute(
                "UPDATE calibrations SET is_active = 0 WHERE patient_id = ?",
                (patient_id,),
            )
        cur = await self.conn.execute(
            """
            INSERT INTO calibrations (
                patient_id, thigh_pitch_offset, thigh_roll_offset,
                shin_pitch_offset, shin_roll_offset, thigh_polarity, shin_polarity,
                flex_straight, flex_bent, fsr_heel_tare, fsr_fore_tare,
                is_two_point, is_active, calibrated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_id,
                float(data.get("thigh_pitch_offset", 0.0)),
                float(data.get("thigh_roll_offset", 0.0)),
                float(data.get("shin_pitch_offset", 0.0)),
                float(data.get("shin_roll_offset", 0.0)),
                int(data.get("thigh_polarity", 1)),
                int(data.get("shin_polarity", 1)),
                int(data.get("flex_straight", 2048)),
                int(data.get("flex_bent", 1200)),
                int(data.get("fsr_heel_tare", 0)),
                int(data.get("fsr_fore_tare", 0)),
                1 if data.get("is_two_point", False) else 0,
                1 if activate else 0,
                now,
            ),
        )
        await self.conn.commit()
        cal_id = cur.lastrowid
        cur2 = await self.conn.execute("SELECT * FROM calibrations WHERE id = ?", (cal_id,))
        row = await cur2.fetchone()
        return self._row_to_calibration(row)

    async def activate_calibration(self, patient_id: int, cal_id: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM calibrations WHERE id = ? AND patient_id = ?",
            (cal_id, patient_id),
        )
        target = await cur.fetchone()
        if not target:
            return None
        await self.conn.execute(
            "UPDATE calibrations SET is_active = 0 WHERE patient_id = ?", (patient_id,)
        )
        await self.conn.execute(
            "UPDATE calibrations SET is_active = 1 WHERE id = ?", (cal_id,)
        )
        await self.conn.commit()
        cur2 = await self.conn.execute("SELECT * FROM calibrations WHERE id = ?", (cal_id,))
        return self._row_to_calibration(await cur2.fetchone())

    # -------------------- Patient Helpers --------------------

    async def list_patients(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT p.*, COUNT(s.id) AS session_count
            FROM patients p
            LEFT JOIN sessions s ON s.patient_id = p.id
            GROUP BY p.id
            ORDER BY p.updated_at DESC, p.id DESC
            """
        )
        rows = await cur.fetchall()
        patients: list[dict[str, Any]] = []
        for row in rows:
            p = dict(row)
            p["active_calibration"] = await self.get_active_calibration(p["id"])
            patients.append(p)
        return patients

    async def get_patient(self, patient_id: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT p.*, COUNT(s.id) AS session_count
            FROM patients p
            LEFT JOIN sessions s ON s.patient_id = p.id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (patient_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        p = dict(row)
        p["active_calibration"] = await self.get_active_calibration(patient_id)
        return p

    async def create_patient(self, data: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        cur = await self.conn.execute(
            """
            INSERT INTO patients (
                name, age, gender, monitored_leg, height_cm, weight_kg,
                region_district, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                data.get("age"),
                data.get("gender", "Male"),
                data.get("monitored_leg", "Left"),
                data.get("height_cm"),
                data.get("weight_kg"),
                data.get("region_district", ""),
                data.get("notes", ""),
                now,
                now,
            ),
        )
        await self.conn.commit()
        return await self.get_patient(cur.lastrowid)

    async def update_patient(self, patient_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
        existing = await self.get_patient(patient_id)
        if not existing:
            return None
        fields = [
            "name",
            "age",
            "gender",
            "monitored_leg",
            "height_cm",
            "weight_kg",
            "region_district",
            "notes",
        ]
        updates = {f: data[f] for f in fields if f in data and data[f] is not None}
        if not updates:
            return existing
        updates["updated_at"] = utc_now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [patient_id]
        await self.conn.execute(f"UPDATE patients SET {set_clause} WHERE id = ?", params)
        await self.conn.commit()
        return await self.get_patient(patient_id)

    async def delete_patient(self, patient_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    # -------------------- Session Helpers --------------------

    @staticmethod
    def _row_to_session(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        raw_markers = d.pop("risk_markers_json", "[]")
        try:
            d["risk_markers"] = json.loads(raw_markers) if raw_markers else []
        except json.JSONDecodeError:
            d["risk_markers"] = []
        return d

    async def create_session(
        self,
        patient_id: Optional[int],
        patient_name: str,
        clinician_name: str = "",
        monitored_leg: str = "Left",
        calibration_id: Optional[int] = None,
        source: str = "esp32",
        notes: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        cur = await self.conn.execute(
            """
            INSERT INTO sessions (
                patient_id, patient_name, clinician_name, monitored_leg, calibration_id,
                status, source, start_time, notes
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (patient_id, patient_name, clinician_name, monitored_leg, calibration_id, source, now, notes),
        )
        await self.conn.commit()
        return await self.get_session(cur.lastrowid)

    async def get_session(self, session_id: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cur.fetchone()
        return self._row_to_session(row) if row else None

    async def list_sessions(self, patient_id: Optional[int] = None) -> list[dict[str, Any]]:
        if patient_id is not None:
            cur = await self.conn.execute(
                "SELECT * FROM sessions WHERE patient_id = ? ORDER BY id DESC",
                (patient_id,),
            )
        else:
            cur = await self.conn.execute("SELECT * FROM sessions ORDER BY id DESC")
        rows = await cur.fetchall()
        return [self._row_to_session(r) for r in rows]

    async def complete_session(
        self, session_id: int, summary: dict[str, Any], notes: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        existing = await self.get_session(session_id)
        if not existing:
            return None
        now = utc_now_iso()
        markers_json = json.dumps(summary.get("risk_markers", []))
        await self.conn.execute(
            """
            UPDATE sessions SET
                status = 'completed',
                end_time = ?,
                sample_count = ?,
                alert_count = ?,
                avg_knee_angle = ?,
                min_knee_angle = ?,
                max_knee_angle = ?,
                rom_deg = ?,
                oa_risk_score = ?,
                oa_risk_tier = ?,
                risk_markers_json = ?,
                notes = COALESCE(?, notes)
            WHERE id = ?
            """,
            (
                now,
                summary.get("sample_count", existing["sample_count"]),
                summary.get("alert_count", existing["alert_count"]),
                summary.get("avg_knee_angle"),
                summary.get("min_knee_angle"),
                summary.get("max_knee_angle"),
                summary.get("rom_deg"),
                summary.get("oa_risk_score"),
                summary.get("oa_risk_tier"),
                markers_json,
                notes,
                session_id,
            ),
        )
        await self.conn.commit()
        return await self.get_session(session_id)

    async def delete_session(self, session_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def update_session_notes(self, session_id: int, notes: str) -> Optional[dict[str, Any]]:
        existing = await self.get_session(session_id)
        if not existing:
            return None
        await self.conn.execute("UPDATE sessions SET notes = ? WHERE id = ?", (notes, session_id))
        await self.conn.commit()
        return await self.get_session(session_id)

    async def reconcile_dangling_sessions(self) -> int:
        """Finalize any previously 'active' sessions left open across server restarts."""
        cur = await self.conn.execute("SELECT id FROM sessions WHERE status = 'active'")
        rows = await cur.fetchall()
        fixed = 0
        for row in rows:
            sid = row["id"]
            existing = await self.get_session(sid)
            if not existing:
                continue
            stats = await self.compute_session_stats_from_db(sid)
            score = stats.get("avg_risk_score")
            if score is None:
                tier = "Pending"
            elif score > 65.0:
                tier = "High"
            elif score >= 35.0:
                tier = "Moderate"
            else:
                tier = "Low"
            existing_notes = str(existing.get("notes") or "").strip()
            note_val = existing_notes or "Auto-saved after session interruption"
            await self.complete_session(
                sid,
                {**stats, "oa_risk_score": score, "oa_risk_tier": tier, "risk_markers": []},
                notes=note_val,
            )
            fixed += 1
        return fixed

    async def get_session_biomechanics_summary(self, session_id: int) -> dict[str, Any]:
        """Compute aggregate plantar pressure and flexion biomechanics for one session."""
        cur = await self.conn.execute(
            """
            SELECT
                COUNT(*) AS sample_count,
                AVG(COALESCE(knee_angle_c, knee_angle)) AS avg_knee_angle,
                MIN(COALESCE(knee_angle_c, knee_angle)) AS min_knee_angle,
                MAX(COALESCE(knee_angle_c, knee_angle)) AS max_knee_angle,
                AVG(flex_norm) AS avg_flex_norm,
                AVG(fsr_heel) AS avg_heel_fsr,
                AVG(fsr_fore) AS avg_fore_fsr,
                AVG(fsr_imbalance) AS mean_imbalance_pct,
                SUM(CASE WHEN abnormal = 1 THEN 1 ELSE 0 END) AS abnormal_count
            FROM telemetry_samples
            WHERE session_id = ?
            """,
            (session_id,),
        )
        row = dict(await cur.fetchone())
        return self._format_biomechanics_row(row)

    @staticmethod
    def _format_biomechanics_row(row: dict[str, Any]) -> dict[str, Any]:
        cnt = int(row.get("sample_count") or 0)
        avg_h = float(row["avg_heel_fsr"]) if row.get("avg_heel_fsr") is not None else 0.0
        avg_f = float(row["avg_fore_fsr"]) if row.get("avg_fore_fsr") is not None else 0.0
        tot_fsr = avg_h + avg_f
        heel_pct = round(avg_h / tot_fsr * 100.0, 1) if tot_fsr > 20 else 50.0
        fore_pct = round(100.0 - heel_pct, 1) if tot_fsr > 20 else 50.0
        min_k = row.get("min_knee_angle")
        max_k = row.get("max_knee_angle")
        rom = (max_k - min_k) if (min_k is not None and max_k is not None) else None
        abn_cnt = int(row.get("abnormal_count") or 0)
        return {
            "sample_count": cnt,
            "avg_knee_angle": round(row["avg_knee_angle"], 1) if row.get("avg_knee_angle") is not None else None,
            "min_knee_angle": round(min_k, 1) if min_k is not None else None,
            "max_knee_angle": round(max_k, 1) if max_k is not None else None,
            "rom_deg": round(rom, 1) if rom is not None else None,
            "avg_flex_norm": round(row["avg_flex_norm"], 2) if row.get("avg_flex_norm") is not None else None,
            "avg_heel_fsr": round(avg_h, 0) if cnt > 0 else None,
            "avg_fore_fsr": round(avg_f, 0) if cnt > 0 else None,
            "heel_pct": heel_pct if cnt > 0 else None,
            "fore_pct": fore_pct if cnt > 0 else None,
            "mean_imbalance_pct": round(row["mean_imbalance_pct"], 1) if row.get("mean_imbalance_pct") is not None else None,
            "abnormal_count": abn_cnt,
            "abnormal_pct": round(abn_cnt / cnt * 100.0, 1) if cnt > 0 else 0.0,
        }

    async def compute_session_stats_from_db(self, session_id: int) -> dict[str, Any]:
        cur = await self.conn.execute(
            """
            SELECT
                COUNT(*) AS sample_count,
                AVG(COALESCE(knee_angle_c, knee_angle)) AS avg_knee_angle,
                MIN(COALESCE(knee_angle_c, knee_angle)) AS min_knee_angle,
                MAX(COALESCE(knee_angle_c, knee_angle)) AS max_knee_angle,
                AVG(oa_risk_score) AS avg_risk_score
            FROM telemetry_samples
            WHERE session_id = ?
            """,
            (session_id,),
        )
        row = dict(await cur.fetchone())
        cur_alerts = await self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM alerts WHERE session_id = ? AND severity != 'recovery'",
            (session_id,),
        )
        alert_cnt = (await cur_alerts.fetchone())["cnt"]
        min_k = row["min_knee_angle"]
        max_k = row["max_knee_angle"]
        rom = (max_k - min_k) if (min_k is not None and max_k is not None) else None
        return {
            "sample_count": int(row["sample_count"] or 0),
            "alert_count": int(alert_cnt or 0),
            "avg_knee_angle": round(row["avg_knee_angle"], 2) if row["avg_knee_angle"] is not None else None,
            "min_knee_angle": round(min_k, 2) if min_k is not None else None,
            "max_knee_angle": round(max_k, 2) if max_k is not None else None,
            "rom_deg": round(rom, 2) if rom is not None else None,
            "avg_risk_score": round(row["avg_risk_score"], 2) if row["avg_risk_score"] is not None else None,
        }

    # -------------------- Telemetry & Alert Helpers --------------------

    async def insert_telemetry_batch(self, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        await self.conn.executemany(
            """
            INSERT INTO telemetry_samples (
                session_id, timestamp_ms, recorded_at,
                thigh_ax, thigh_ay, thigh_az, thigh_gx, thigh_gy, thigh_gz,
                thigh_pitch, thigh_roll, thigh_pitch_c, thigh_roll_c,
                shin_ax, shin_ay, shin_az, shin_gx, shin_gy, shin_gz,
                shin_pitch, shin_roll, shin_pitch_c, shin_roll_c,
                flex_raw, flex_norm, fsr_heel, fsr_fore,
                knee_angle, knee_angle_c, fsr_imbalance,
                abnormal, reason, oa_risk_score
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
        await self.conn.commit()

    async def get_session_telemetry(
        self, session_id: int, limit: int = 5000, offset: int = 0
    ) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM telemetry_samples WHERE session_id = ? ORDER BY id ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        )
        rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["abnormal"] = bool(d["abnormal"])
            result.append(d)
        return result

    async def insert_alert(
        self,
        session_id: Optional[int],
        patient_id: Optional[int],
        severity: str,
        marker_type: str,
        message: str,
        knee_angle: Optional[float] = None,
        oa_risk_score: Optional[float] = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        cur = await self.conn.execute(
            """
            INSERT INTO alerts (
                session_id, patient_id, severity, marker_type, message,
                knee_angle, oa_risk_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, patient_id, severity, marker_type, message, knee_angle, oa_risk_score, now),
        )
        await self.conn.commit()
        cur2 = await self.conn.execute("SELECT * FROM alerts WHERE id = ?", (cur.lastrowid,))
        return dict(await cur2.fetchone())

    async def get_session_alerts(self, session_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM alerts WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_recent_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def clear_alerts(self, session_id: Optional[int] = None) -> int:
        if session_id is not None:
            cur = await self.conn.execute("DELETE FROM alerts WHERE session_id = ?", (session_id,))
        else:
            cur = await self.conn.execute("DELETE FROM alerts")
        await self.conn.commit()
        return cur.rowcount

    async def list_patient_alerts(self, patient_id: int, limit: int = 50) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT a.*
            FROM alerts a
            LEFT JOIN sessions s ON s.id = a.session_id
            WHERE a.patient_id = ? OR s.patient_id = ?
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (patient_id, patient_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_patient_telemetry_aggregate(self, patient_id: int) -> dict[str, Any]:
        cur = await self.conn.execute(
            """
            SELECT
                COUNT(t.id) AS sample_count,
                AVG(COALESCE(t.knee_angle_c, t.knee_angle)) AS avg_knee_angle,
                MIN(COALESCE(t.knee_angle_c, t.knee_angle)) AS min_knee_angle,
                MAX(COALESCE(t.knee_angle_c, t.knee_angle)) AS max_knee_angle,
                AVG(t.flex_norm) AS avg_flex_norm,
                AVG(t.fsr_heel) AS avg_heel_fsr,
                AVG(t.fsr_fore) AS avg_fore_fsr,
                AVG(t.fsr_imbalance) AS mean_imbalance_pct,
                SUM(CASE WHEN t.abnormal = 1 THEN 1 ELSE 0 END) AS abnormal_count
            FROM telemetry_samples t
            INNER JOIN sessions s ON s.id = t.session_id
            WHERE s.patient_id = ?
            """,
            (patient_id,),
        )
        row = dict(await cur.fetchone())
        return self._format_biomechanics_row(row)

    async def get_patient_summary(self, patient_id: int) -> Optional[dict[str, Any]]:
        patient = await self.get_patient(patient_id)
        if not patient:
            return None
        sessions = await self.list_sessions(patient_id=patient_id)
        trials = await self.list_exercise_trials(patient_id=patient_id)
        calibrations = await self.list_calibrations(patient_id=patient_id)
        alerts = await self.list_patient_alerts(patient_id, limit=50)
        biomechanics = await self.get_patient_telemetry_aggregate(patient_id)
        return {
            "patient": patient,
            "sessions": sessions,
            "exercise_trials": trials,
            "calibrations": calibrations,
            "alerts": alerts,
            "biomechanics_summary": biomechanics,
        }

    # -------------------- Settings Helpers --------------------

    async def get_setting(self, key: str, default: Any = None) -> Any:
        cur = await self.conn.execute(
            "SELECT value_json FROM device_settings WHERE key = ?", (key,)
        )
        row = await cur.fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    async def set_setting(self, key: str, value: Any) -> None:
        now = utc_now_iso()
        await self.conn.execute(
            """
            INSERT INTO device_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), now),
        )
        await self.conn.commit()

    # -------------------- Exercise Trials Helpers --------------------

    @staticmethod
    def _row_to_exercise_trial(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        raw_markers = d.pop("markers_json", "[]")
        try:
            d["markers"] = json.loads(raw_markers) if raw_markers else []
        except json.JSONDecodeError:
            d["markers"] = []
        return d

    async def create_exercise_trial(
        self,
        patient_id: Optional[int],
        session_id: Optional[int],
        monitored_leg: str,
        exercise_key: str,
        exercise_name: str,
        target_reps: int,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        cur = await self.conn.execute(
            """
            INSERT INTO exercise_trials (
                patient_id, session_id, monitored_leg, exercise_key, exercise_name,
                status, target_reps, started_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (patient_id, session_id, monitored_leg, exercise_key, exercise_name, target_reps, now),
        )
        await self.conn.commit()
        cur2 = await self.conn.execute("SELECT * FROM exercise_trials WHERE id = ?", (cur.lastrowid,))
        return self._row_to_exercise_trial(await cur2.fetchone())

    async def complete_exercise_trial(self, trial_id: int, snap: dict[str, Any]) -> Optional[dict[str, Any]]:
        now = utc_now_iso()
        await self.conn.execute(
            """
            UPDATE exercise_trials SET
                status = 'completed',
                completed_reps = ?,
                quality_score = ?,
                finding = ?,
                avg_rom_deg = ?,
                max_flexion_deg = ?,
                min_flexion_deg = ?,
                avg_imbalance_pct = ?,
                roll_wobble_deg = ?,
                flex_agreement_pct = ?,
                markers_json = ?,
                ended_at = ?
            WHERE id = ?
            """,
            (
                int(snap.get("completed_reps", 0)),
                snap.get("quality_score"),
                snap.get("finding"),
                snap.get("avg_rom_deg"),
                snap.get("max_flexion_deg"),
                snap.get("min_flexion_deg"),
                snap.get("avg_imbalance_pct"),
                snap.get("roll_wobble_deg"),
                snap.get("flex_agreement_pct"),
                json.dumps(snap.get("markers", [])),
                now,
                trial_id,
            ),
        )
        await self.conn.commit()
        cur = await self.conn.execute("SELECT * FROM exercise_trials WHERE id = ?", (trial_id,))
        row = await cur.fetchone()
        return self._row_to_exercise_trial(row) if row else None

    async def list_exercise_trials(self, patient_id: Optional[int] = None) -> list[dict[str, Any]]:
        if patient_id is not None:
            cur = await self.conn.execute(
                "SELECT * FROM exercise_trials WHERE patient_id = ? ORDER BY id DESC",
                (patient_id,),
            )
        else:
            cur = await self.conn.execute("SELECT * FROM exercise_trials ORDER BY id DESC")
        return [self._row_to_exercise_trial(r) for r in await cur.fetchall()]

    async def delete_exercise_trial(self, trial_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM exercise_trials WHERE id = ?", (trial_id,))
        await self.conn.commit()
        return cur.rowcount > 0


