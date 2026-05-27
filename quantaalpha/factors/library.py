"""
Factor library manager - SQLite-based storage for all mined factors.
Replaces the previous JSON-based factor library.
"""

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_FACTOR_CACHE_DIR = os.environ.get(
    "FACTOR_CACHE_DIR", "data/results/factor_cache"
)
DEFAULT_DB_PATH = os.environ.get(
    "FACTOR_LIBRARY_DB", "data/factorlib/factor_library.db"
)


class FactorLibraryManager:
    """Manage unified factor library via SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── database setup ──────────────────────────────────────────────

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS libraries (
                    name TEXT PRIMARY KEY,
                    description TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    metadata TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS factors (
                    factor_id TEXT NOT NULL,
                    library_name TEXT NOT NULL,
                    factor_name TEXT NOT NULL,
                    factor_expression TEXT,
                    factor_implementation_code TEXT,
                    factor_description TEXT,
                    factor_formulation TEXT,
                    cache_workspace_suffix TEXT,
                    cache_workspace_path TEXT,
                    cache_factor_dir TEXT,
                    cache_result_h5_path TEXT,
                    meta_experiment_id TEXT,
                    meta_round_number INTEGER,
                    meta_evolution_phase TEXT,
                    meta_trajectory_id TEXT,
                    meta_parent_trajectory_ids TEXT,
                    meta_hypothesis TEXT,
                    meta_initial_direction TEXT,
                    meta_planning_direction TEXT,
                    meta_created_at TEXT,
                    backtest_results TEXT DEFAULT '{}',
                    feedback TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (library_name, factor_id)
                );
                CREATE INDEX IF NOT EXISTS idx_factors_library ON factors(library_name);
                CREATE INDEX IF NOT EXISTS idx_factors_name ON factors(factor_name);
                CREATE INDEX IF NOT EXISTS idx_factors_expr ON factors(factor_expression);
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_library(self, name: str, description: str = ""):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO libraries (name, description) VALUES (?, ?)",
                (name, description),
            )

    # ── write ───────────────────────────────────────────────────────

    def add_factors_from_experiment(
        self,
        experiment,
        experiment_id: str = "unknown",
        round_number: int = 0,
        hypothesis: Optional[str] = None,
        feedback: Any = None,
        initial_direction: Optional[str] = None,
        user_initial_direction: Optional[str] = None,
        planning_direction: Optional[str] = None,
        evolution_phase: str = "original",
        trajectory_id: str = "",
        parent_trajectory_ids: Optional[list] = None,
        library_name: str = "default",
    ):
        if experiment is None:
            logger.warning("experiment is None, skip saving factors")
            return

        self._ensure_library(library_name)
        backtest_results = self._extract_backtest_results(experiment)
        feedback_dict = self._extract_feedback(feedback)
        sub_tasks = getattr(experiment, "sub_tasks", []) or []
        sub_workspaces = getattr(experiment, "sub_workspace_list", []) or []

        with self._connect() as conn:
            for idx, task in enumerate(sub_tasks):
                factor_name = getattr(
                    task, "factor_name", getattr(task, "name", f"factor_{idx}")
                )
                factor_expr = getattr(task, "factor_expression", "")
                factor_desc = getattr(
                    task, "factor_description", getattr(task, "description", "")
                )
                factor_form = getattr(task, "factor_formulation", "")

                factor_id = hashlib.md5(
                    f"{factor_name}_{factor_expr}".encode()
                ).hexdigest()[:16]

                code = ""
                cache_ws_suffix = None
                cache_ws_path = None
                cache_factor_dir = None
                cache_h5_path = None

                if idx < len(sub_workspaces):
                    ws = sub_workspaces[idx]
                    code_dict = getattr(ws, "code_dict", {})
                    code = "\n".join(
                        f"File: {fname}\n\n{content}"
                        for fname, content in code_dict.items()
                    )
                    ws_path = getattr(ws, "workspace_path", None)
                    if ws_path:
                        ws_path = Path(ws_path)
                        for part in ws_path.parts:
                            if part.startswith("workspace_"):
                                cache_ws_suffix = part.replace("workspace_", "")
                                break
                        cache_factor_dir = ws_path.name
                        cache_ws_path = str(ws_path.parent)
                        h5_file = ws_path / "result.h5"
                        if h5_file.exists():
                            cache_h5_path = str(h5_file)

                conn.execute(
                    """
                    INSERT OR REPLACE INTO factors (
                        library_name, factor_id, factor_name, factor_expression,
                        factor_implementation_code, factor_description, factor_formulation,
                        cache_workspace_suffix, cache_workspace_path, cache_factor_dir,
                        cache_result_h5_path,
                        meta_experiment_id, meta_round_number, meta_evolution_phase,
                        meta_trajectory_id, meta_parent_trajectory_ids, meta_hypothesis,
                        meta_initial_direction, meta_planning_direction, meta_created_at,
                        backtest_results, feedback
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        library_name,
                        factor_id,
                        factor_name,
                        factor_expr,
                        code,
                        factor_desc,
                        factor_form,
                        cache_ws_suffix,
                        cache_ws_path,
                        cache_factor_dir,
                        cache_h5_path,
                        experiment_id,
                        round_number,
                        evolution_phase,
                        trajectory_id,
                        json.dumps(parent_trajectory_ids or []),
                        str(hypothesis) if hypothesis else "",
                        initial_direction or "",
                        planning_direction or "",
                        datetime.now().isoformat(),
                        json.dumps(backtest_results, ensure_ascii=False, default=str),
                        json.dumps(feedback_dict, ensure_ascii=False, default=str),
                    ),
                )

                if factor_expr and cache_h5_path:
                    self._sync_h5_to_md5_cache(factor_expr, cache_h5_path)

        logger.info(
            f"Saved {len(sub_tasks)} factors to library '{library_name}'"
        )

    # ── read ────────────────────────────────────────────────────────

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        backtest_results = json.loads(d.pop("backtest_results", "{}"))
        feedback = json.loads(d.pop("feedback", "{}"))
        parent_ids = json.loads(d.pop("meta_parent_trajectory_ids", "[]"))
        created_at = d.pop("meta_created_at", "")

        cache_location = {}
        if d.get("cache_result_h5_path"):
            cache_location = {
                "workspace_suffix": d.get("cache_workspace_suffix") or "",
                "workspace_path": d.get("cache_workspace_path") or "",
                "factor_dir": d.get("cache_factor_dir") or "",
                "result_h5_path": d["cache_result_h5_path"],
            }
        elif d.get("cache_factor_dir"):
            cache_location = {
                "workspace_suffix": d.get("cache_workspace_suffix") or "",
                "workspace_path": d.get("cache_workspace_path") or "",
                "factor_dir": d.get("cache_factor_dir") or "",
            }

        return {
            "factor_id": d.get("factor_id"),
            "factor_name": d.get("factor_name"),
            "factor_expression": d.get("factor_expression"),
            "factor_implementation_code": d.get("factor_implementation_code"),
            "factor_description": d.get("factor_description"),
            "factor_formulation": d.get("factor_formulation"),
            "cache_location": cache_location,
            "metadata": {
                "experiment_id": d.get("meta_experiment_id") or "",
                "round_number": d.get("meta_round_number") or 0,
                "evolution_phase": d.get("meta_evolution_phase") or "",
                "trajectory_id": d.get("meta_trajectory_id") or "",
                "parent_trajectory_ids": parent_ids,
                "hypothesis": d.get("meta_hypothesis") or "",
                "initial_direction": d.get("meta_initial_direction") or "",
                "planning_direction": d.get("meta_planning_direction") or "",
                "created_at": created_at,
            },
            "backtest_results": backtest_results,
            "feedback": feedback,
        }

    def get_factors_by_library(self, library_name: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM factors WHERE library_name = ? ORDER BY created_at",
                (library_name,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_factor(
        self, library_name: str, factor_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM factors WHERE library_name = ? AND factor_id = ?",
                (library_name, factor_id),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def get_all_libraries(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT l.*, COUNT(f.factor_id) AS factor_count
                FROM libraries l
                LEFT JOIN factors f ON f.library_name = l.name
                GROUP BY l.name
                ORDER BY l.created_at DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_library(self, name: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM factors WHERE library_name = ?", (name,))
            conn.execute("DELETE FROM libraries WHERE name = ?", (name,))

    # ── cache management ────────────────────────────────────────────

    @staticmethod
    def _sync_h5_to_md5_cache(
        factor_expression: str, h5_path: str, cache_dir: Optional[str] = None
    ) -> bool:
        cache_dir = Path(cache_dir or DEFAULT_FACTOR_CACHE_DIR)
        h5_file = Path(h5_path)
        if not h5_file.exists():
            return False
        md5_key = hashlib.md5(factor_expression.encode()).hexdigest()
        pkl_file = cache_dir / f"{md5_key}.pkl"
        if pkl_file.exists():
            return True
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            result = pd.read_hdf(str(h5_file))
            result.to_pickle(pkl_file)
            logger.debug(f"Synced factor cache -> {pkl_file.name}")
            return True
        except Exception as e:
            logger.debug(f"Sync factor cache failed [{h5_path}]: {e}")
            return False

    def check_cache_status(self, library_name: Optional[str] = None) -> Dict[str, Any]:
        cache_dir = Path(DEFAULT_FACTOR_CACHE_DIR)
        if library_name:
            factors = self.get_factors_by_library(library_name)
        else:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM factors").fetchall()
                factors = [self._row_to_dict(r) for r in rows]

        total = len(factors)
        h5_cached = 0
        md5_cached = 0
        need_compute = 0
        details = []

        for finfo in factors:
            expr = finfo.get("factor_expression", "")
            cloc = finfo.get("cache_location", {})
            h5_path = cloc.get("result_h5_path", "")

            status = "need_compute"
            if h5_path and Path(h5_path).exists():
                status = "h5_cached"
                h5_cached += 1
            elif expr:
                md5_key = hashlib.md5(expr.encode()).hexdigest()
                if (cache_dir / f"{md5_key}.pkl").exists():
                    status = "md5_cached"
                    md5_cached += 1

            if status == "need_compute":
                need_compute += 1

            details.append({
                "factor_id": finfo.get("factor_id"),
                "factor_name": finfo.get("factor_name"),
                "status": status,
            })

        return {
            "total": total,
            "h5_cached": h5_cached,
            "md5_cached": md5_cached,
            "need_compute": need_compute,
            "factors": details,
        }

    def warm_cache(self, library_name: Optional[str] = None) -> Dict[str, Any]:
        cache_dir_path = Path(DEFAULT_FACTOR_CACHE_DIR)
        if library_name:
            factors = self.get_factors_by_library(library_name)
        else:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM factors").fetchall()
                factors = [self._row_to_dict(r) for r in rows]

        synced = 0
        skipped = 0
        failed = 0
        already_cached = 0
        no_source = 0

        for finfo in factors:
            expr = finfo.get("factor_expression", "")
            cloc = finfo.get("cache_location", {})
            h5_path = cloc.get("result_h5_path", "")

            if not expr or not h5_path:
                no_source += 1
                skipped += 1
                continue

            md5_key = hashlib.md5(expr.encode()).hexdigest()
            pkl_file = cache_dir_path / f"{md5_key}.pkl"

            if pkl_file.exists():
                already_cached += 1
                skipped += 1
                continue

            if not Path(h5_path).exists():
                failed += 1
                continue

            try:
                cache_dir_path.mkdir(parents=True, exist_ok=True)
                result = pd.read_hdf(str(h5_path))
                result.to_pickle(pkl_file)
                synced += 1
            except Exception:
                failed += 1

        return {
            "total": len(factors),
            "synced": synced,
            "skipped": skipped,
            "failed": failed,
            "already_cached": already_cached,
            "no_source": no_source,
        }

    # ── migration from JSON ────────────────────────────────────────

    @staticmethod
    def migrate_from_json(
        json_path: str,
        db_path: Optional[str] = None,
        library_name: Optional[str] = None,
    ):
        json_path = Path(json_path)
        if not json_path.exists():
            logger.error(f"JSON file not found: {json_path}")
            return

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if library_name is None:
            stem = json_path.stem
            if stem == "all_factors_library":
                library_name = "default"
            else:
                library_name = stem.replace("all_factors_library_", "")

        manager = FactorLibraryManager(db_path)
        manager._ensure_library(library_name)

        factors = data.get("factors", {})
        if not factors:
            logger.warning(f"No factors found in {json_path}")
            return

        with manager._connect() as conn:
            for factor_id, finfo in factors.items():
                if not isinstance(finfo, dict):
                    continue

                cloc = finfo.get("cache_location", {}) or {}
                meta = finfo.get("metadata", {}) or {}
                bt = finfo.get("backtest_results", {}) or {}
                fb = finfo.get("feedback", {}) or {}

                conn.execute(
                    """
                    INSERT OR REPLACE INTO factors (
                        library_name, factor_id, factor_name, factor_expression,
                        factor_implementation_code, factor_description, factor_formulation,
                        cache_workspace_suffix, cache_workspace_path, cache_factor_dir,
                        cache_result_h5_path,
                        meta_experiment_id, meta_round_number, meta_evolution_phase,
                        meta_trajectory_id, meta_parent_trajectory_ids, meta_hypothesis,
                        meta_initial_direction, meta_planning_direction, meta_created_at,
                        backtest_results, feedback
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        library_name,
                        factor_id,
                        finfo.get("factor_name", ""),
                        finfo.get("factor_expression", ""),
                        finfo.get("factor_implementation_code", ""),
                        finfo.get("factor_description", ""),
                        finfo.get("factor_formulation", ""),
                        cloc.get("workspace_suffix"),
                        cloc.get("workspace_path"),
                        cloc.get("factor_dir"),
                        cloc.get("result_h5_path"),
                        meta.get("experiment_id", ""),
                        meta.get("round_number", 0),
                        meta.get("evolution_phase", ""),
                        meta.get("trajectory_id", ""),
                        json.dumps(meta.get("parent_trajectory_ids", [])),
                        meta.get("hypothesis", ""),
                        meta.get("initial_direction", ""),
                        meta.get("planning_direction", ""),
                        meta.get("created_at", ""),
                        json.dumps(bt, ensure_ascii=False, default=str),
                        json.dumps(fb, ensure_ascii=False, default=str),
                    ),
                )

        logger.info(
            f"Migrated {len(factors)} factors from {json_path} -> library '{library_name}'"
        )

    # ── extract helpers ─────────────────────────────────────────────

    @staticmethod
    def _extract_backtest_results(experiment) -> dict:
        result = getattr(experiment, "result", None)
        if result is None:
            return {}
        if isinstance(result, pd.Series):
            out = {}
            for key, val in result.items():
                if isinstance(val, (float, np.floating)):
                    if np.isnan(val) or np.isinf(val):
                        out[str(key)] = None
                    else:
                        out[str(key)] = round(float(val), 8)
                else:
                    out[str(key)] = val
            return out
        if isinstance(result, pd.DataFrame):
            try:
                return {
                    str(k): round(float(v), 8)
                    if isinstance(v, (float, np.floating)) and not np.isnan(v)
                    else None
                    for k, v in result.iloc[:, 0].items()
                }
            except Exception:
                pass
        if isinstance(result, dict):
            return result
        return {}

    @staticmethod
    def _extract_feedback(feedback) -> dict:
        if feedback is None:
            return {}
        if isinstance(feedback, dict):
            return feedback
        out = {}
        for attr in [
            "observations", "hypothesis_evaluation", "decision", "reason",
            "new_hypothesis", "feedback_str",
        ]:
            val = getattr(feedback, attr, None)
            if val is not None:
                out[attr] = str(val) if not isinstance(val, (bool, int, float)) else val
        if not out:
            out["raw"] = str(feedback)
        return out
