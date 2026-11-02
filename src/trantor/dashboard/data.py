"""Read-only data access for the Trantor research dashboard.

The dashboard is deliberately a separate, browser-facing read model. It never
imports broker or execution code, never runs a backtest, and never writes to the
authoritative research artifacts. Large decision files are indexed and paginated
on the server; only individual pages are sent to the browser.
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trantor.config import load_settings, settings_snapshot
from trantor.research.backtest.data import DEFAULT_CHECKSUMS, load_research_data
from trantor.research.diagnostics import DiagnosticConfig, TradeDiagnosticsService
from trantor.strategy.research_record import canonical_json

APP_VERSION = "0.1.0"
DEFAULT_ARTIFACT_REL = Path(
    "research_results/xauusd_p/phase_e_authoritative_20261102"
)
REQUIRED_ARTIFACTS = (
    "summary.json",
    "manifest.json",
    "trades.json",
    "entry_attempts.json",
    "decisions.jsonl",
)
SPLIT_ORDER = ("development", "validation", "holdout")
SPLIT_LABELS = {
    "development": "2024 Development",
    "validation": "2025 Validation",
    "holdout": "2026 Holdout",
}
DEFAULT_LABELS = {
    "gross": "GROSS BASELINE - EXECUTION COSTS EXCLUDED",
    "research": "RESEARCH-ONLY FILTER VARIANT - NOT COMPLETE APPROVED STRATEGY",
}
EXPECTED_SOURCE_KEYS = {
    "d1_final": "d1",
    "h4_final": "h4",
    "m15_final": "m15",
    "d1_warmup": "d1_warmup",
}
TIMEFRAME_ALIASES = {
    "d1": "d1",
    "1d": "d1",
    "h4": "h4",
    "4h": "h4",
    "m15": "m15",
    "15m": "m15",
}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class DashboardError(RuntimeError):
    """A dashboard read or request error with an HTTP-safe message."""

    def __init__(self, message: str, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    repo_root: Path
    artifact_dir: Path
    settings_path: Path | None = None
    settings_example_path: Path | None = None
    app_version: str = APP_VERSION

    @classmethod
    def default(cls) -> "DashboardConfig":
        root = Path(__file__).resolve().parents[3]
        return cls(
            repo_root=root,
            artifact_dir=root / DEFAULT_ARTIFACT_REL,
            settings_path=root / "config" / "settings.json",
            settings_example_path=root / "config" / "settings.example.json",
        )

    def __post_init__(self) -> None:
        for name in ("repo_root", "artifact_dir"):
            value = getattr(self, name)
            if not isinstance(value, Path):
                object.__setattr__(self, name, Path(value))
        if not self.artifact_dir.is_absolute():
            object.__setattr__(self, "artifact_dir", self.repo_root / self.artifact_dir)

    def relative_path(self, path: Path | str) -> str:
        value = Path(path)
        try:
            return value.resolve().relative_to(self.repo_root.resolve()).as_posix()
        except (OSError, ValueError):
            return "<external>"


@dataclass(frozen=True, slots=True)
class DecisionIndexEntry:
    offset: int
    length: int
    decision_time: str
    period: str
    status: str
    has_candidate: bool
    side: str | None
    score: float
    confidence: str
    rejections: tuple[str, ...]
    record_hash: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _iso(value: Any) -> str | None:
    return value if isinstance(value, str) else None


class ResearchRepository:
    """Validated, read-only access to one authoritative Phase E artifact set."""

    def __init__(self, config: DashboardConfig | None = None) -> None:
        self.config = config or DashboardConfig.default()
        self._loaded = False
        self._summary: dict[str, Any] | None = None
        self._manifest: dict[str, Any] | None = None
        self._trades: list[dict[str, Any]] = []
        self._entry_attempts: list[dict[str, Any]] = []
        self._artifact_info: dict[str, dict[str, Any]] = {}
        self._warnings: list[str] = []
        self._decision_index: tuple[DecisionIndexEntry, ...] | None = None
        self._decision_hash_map: dict[str, DecisionIndexEntry] | None = None
        self._decision_error: str | None = None
        self._linkage: dict[str, Any] | None = None
        self._source_status_cache: list[dict[str, Any]] | None = None
        self._tail_cache: dict[int, list[dict[str, Any]]] = {}
        self._research_data: Any = None
        self._settings_cache: dict[str, Any] | None = None
        self._diagnostics_cache: dict[str, Any] | None = None

    # ---- artifact loading -------------------------------------------------

    def _artifact_path(self, name: str) -> Path:
        if name not in REQUIRED_ARTIFACTS:
            raise DashboardError("unknown artifact", 404)
        return self.config.artifact_dir / name

    def _load_json_artifact(self, name: str, expected_type: type) -> Any:
        path = self._artifact_path(name)
        info = {
            "name": name,
            "exists": path.is_file(),
            "parsed": False,
            "error": None,
            "bytes": path.stat().st_size if path.is_file() else 0,
        }
        self._artifact_info[name] = info
        if not info["exists"]:
            info["error"] = f"{name} is missing"
            self._warnings.append(info["error"])
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            info["error"] = f"{name} is not valid JSON: {exc}"
            self._warnings.append(info["error"])
            return None
        if not isinstance(value, expected_type):
            info["error"] = f"{name} has an unexpected top-level type"
            self._warnings.append(info["error"])
            return None
        info["parsed"] = True
        return value

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._summary = self._load_json_artifact("summary.json", dict)
        self._manifest = self._load_json_artifact("manifest.json", dict)
        trades = self._load_json_artifact("trades.json", list)
        attempts = self._load_json_artifact("entry_attempts.json", list)
        self._trades = [item for item in trades or [] if isinstance(item, dict)]
        self._entry_attempts = [
            item for item in attempts or [] if isinstance(item, dict)
        ]

        decisions_path = self._artifact_path("decisions.jsonl")
        decisions_info = {
            "name": "decisions.jsonl",
            "exists": decisions_path.is_file(),
            "parsed": False,
            "header_parsed": False,
            "index_state": "on_demand",
            "error": None,
            "bytes": decisions_path.stat().st_size if decisions_path.is_file() else 0,
        }
        self._artifact_info["decisions.jsonl"] = decisions_info
        if not decisions_info["exists"]:
            decisions_info["error"] = "decisions.jsonl is missing"
            self._warnings.append(decisions_info["error"])
        elif decisions_info["bytes"] == 0:
            decisions_info["error"] = "decisions.jsonl is empty"
            self._warnings.append(decisions_info["error"])
        else:
            # A full decision index is deliberately lazy. This checks that the
            # first complete record is structurally readable without scanning
            # the 66k-record file during dashboard startup.
            try:
                with decisions_path.open("rb") as handle:
                    first = handle.readline()
                if not first.strip():
                    raise ValueError("first line is blank")
                record = json.loads(first.decode("utf-8"))
                if not isinstance(record, dict) or not isinstance(
                    record.get("canonical"), dict
                ):
                    raise ValueError("first record lacks canonical data")
                decisions_info["header_parsed"] = True
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                decisions_info["error"] = (
                    f"decisions.jsonl is not valid JSONL: {exc}"
                )
                self._warnings.append(decisions_info["error"])

        if self._summary is not None:
            self._validate_summary_consistency()
        if self._manifest is not None:
            self._validate_manifest_consistency()

    def _validate_summary_consistency(self) -> None:
        assert self._summary is not None
        labels = self._summary.get("labels")
        if not isinstance(labels, dict):
            self._warnings.append("summary.json has no labels object")
        overall = self._summary.get("overall")
        if not isinstance(overall, dict):
            self._warnings.append("summary.json has no overall metrics object")
            return
        if overall.get("filled_trade_count") != len(self._trades):
            self._warnings.append(
                "summary filled trade count does not match trades.json"
            )
        if overall.get("trades_with_decision_hash") != len(self._trades):
            self._warnings.append(
                "summary trade-link count does not match trades.json"
            )
        if overall.get("entry_attempt_count") != len(self._entry_attempts):
            self._warnings.append(
                "summary attempt count does not match entry_attempts.json"
            )
        if overall.get("decision_count") != overall.get("decision_record_count"):
            self._warnings.append(
                "summary decision count and canonical record count differ"
            )

    def _validate_manifest_consistency(self) -> None:
        assert self._manifest is not None
        sources = self._manifest.get("sources")
        if not isinstance(sources, list):
            self._warnings.append("manifest.json has no source list")
        contract = self._manifest.get("record_contract")
        summary_contract = (self._summary or {}).get("record_contract")
        if isinstance(contract, dict) and isinstance(summary_contract, dict):
            if contract != summary_contract:
                self._warnings.append(
                    "manifest record contract differs from summary record contract"
                )

    def artifact_status(self) -> list[dict[str, Any]]:
        self._load()
        return [
            dict(self._artifact_info[name])
            for name in REQUIRED_ARTIFACTS
            if name in self._artifact_info
        ]

    @property
    def warnings(self) -> list[str]:
        self._load()
        return list(self._warnings)

    @property
    def summary(self) -> dict[str, Any] | None:
        self._load()
        return self._summary

    @property
    def manifest(self) -> dict[str, Any] | None:
        self._load()
        return self._manifest

    @property
    def trades(self) -> list[dict[str, Any]]:
        self._load()
        return self._trades

    @property
    def entry_attempts(self) -> list[dict[str, Any]]:
        self._load()
        return self._entry_attempts

    # ---- decision index ---------------------------------------------------

    def _record_compact(
        self,
        record: dict[str, Any],
        *,
        offset: int = 0,
        length: int = 0,
    ) -> dict[str, Any]:
        canonical = record.get("canonical")
        if not isinstance(canonical, dict):
            raise DashboardError("decision record has no canonical object")
        score_obj = canonical.get("score") if isinstance(canonical.get("score"), dict) else {}
        candidate = canonical.get("candidate")
        candidate_obj = candidate if isinstance(candidate, dict) else {}
        rejections = canonical.get("rejections")
        rejection_items = rejections if isinstance(rejections, list) else []
        return {
            "offset": offset,
            "length": length,
            "decision_time": record.get("decision_time"),
            "period": record.get("period"),
            "status": record.get("status"),
            "has_candidate": bool(record.get("has_candidate")),
            "side": candidate_obj.get("side"),
            "score": _number(score_obj.get("total")),
            "confidence": score_obj.get("confidence"),
            "rejections": [
                item.get("code")
                for item in rejection_items
                if isinstance(item, dict) and item.get("code")
            ],
            "record_hash": record.get("record_hash"),
        }

    def _build_decision_index(self) -> None:
        if self._decision_index is not None:
            return
        if self._decision_error is not None:
            raise DashboardError(self._decision_error)
        self._load()
        path = self._artifact_path("decisions.jsonl")
        if not path.is_file():
            self._decision_error = "decisions.jsonl is missing"
            raise DashboardError(self._decision_error)
        entries: list[DecisionIndexEntry] = []
        hash_map: dict[str, DecisionIndexEntry] = {}
        try:
            with path.open("rb") as handle:
                line_no = 0
                offset = 0
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    line_no += 1
                    length = len(line)
                    if not line.strip():
                        raise ValueError(f"blank line at line {line_no}")
                    try:
                        record = json.loads(line.decode("utf-8"))
                    except (UnicodeError, json.JSONDecodeError) as exc:
                        raise ValueError(f"line {line_no} is not valid JSON: {exc}") from exc
                    if not isinstance(record, dict):
                        raise ValueError(f"line {line_no} is not an object")
                    canonical = record.get("canonical")
                    if not isinstance(canonical, dict):
                        raise ValueError(f"line {line_no} lacks canonical data")
                    record_hash = record.get("record_hash")
                    if not isinstance(record_hash, str) or not record_hash:
                        raise ValueError(f"line {line_no} lacks record_hash")
                    compact = self._record_compact(
                        record, offset=offset, length=length
                    )
                    entry = DecisionIndexEntry(
                        offset=offset,
                        length=length,
                        decision_time=str(compact["decision_time"]),
                        period=str(compact["period"]),
                        status=str(compact["status"]),
                        has_candidate=bool(compact["has_candidate"]),
                        side=compact["side"],
                        score=compact["score"] or 0.0,
                        confidence=str(compact["confidence"] or ""),
                        rejections=tuple(compact["rejections"]),
                        record_hash=record_hash,
                    )
                    if record_hash in hash_map:
                        raise ValueError(f"duplicate record_hash at line {line_no}")
                    entries.append(entry)
                    hash_map[record_hash] = entry
                    offset += length
        except (OSError, ValueError) as exc:
            self._decision_error = f"decision indexing failed: {exc}"
            self._warnings.append(self._decision_error)
            raise DashboardError(self._decision_error) from exc

        self._decision_index = tuple(entries)
        self._decision_hash_map = hash_map
        self._artifact_info["decisions.jsonl"]["parsed"] = True
        self._artifact_info["decisions.jsonl"]["index_state"] = "indexed"
        self._linkage = self._check_linkage()
        summary = self._summary or {}
        overall = summary.get("overall") if isinstance(summary.get("overall"), dict) else {}
        declared = overall.get("decision_count")
        if isinstance(declared, int) and declared != len(entries):
            self._warnings.append(
                "summary decision count does not match decisions.jsonl records"
            )

    def _check_linkage(self) -> dict[str, Any]:
        hashes = set(self._decision_hash_map or {})
        missing_trades = [
            trade.get("trade_id")
            for trade in self._trades
            if trade.get("decision_hash") not in hashes
        ]
        missing_attempts = [
            attempt.get("decision_time")
            for attempt in self._entry_attempts
            if attempt.get("decision_hash") not in hashes
        ]
        overall = (self._summary or {}).get("overall") or {}
        return {
            "status": "PASS" if not missing_trades and not missing_attempts else "FAIL",
            "trades_checked": len(self._trades),
            "attempts_checked": len(self._entry_attempts),
            "missing_trade_ids": missing_trades,
            "missing_attempt_times": missing_attempts,
            "summary_trade_link_count": overall.get("trades_with_decision_hash"),
            "decision_record_count": len(hashes),
        }

    def _load_record_at(self, entry: DecisionIndexEntry) -> dict[str, Any]:
        path = self._artifact_path("decisions.jsonl")
        with path.open("rb") as handle:
            handle.seek(entry.offset)
            line = handle.read(entry.length)
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DashboardError(f"decision record is invalid: {exc}") from exc
        if not isinstance(record, dict):
            raise DashboardError("decision record is invalid")
        return record

    def _tail_records(self, limit: int) -> list[dict[str, Any]]:
        self._load()
        limit = max(1, min(limit, 25))
        if limit in self._tail_cache:
            return list(self._tail_cache[limit])
        path = self._artifact_path("decisions.jsonl")
        if not path.is_file():
            return []
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                chunk_size = 2 * 1024 * 1024
                start = max(0, size - chunk_size)
                handle.seek(start)
                raw = handle.read()
            lines = raw.splitlines()
            if start > 0 and lines:
                lines = lines[1:]
            records: list[dict[str, Any]] = []
            for line in lines[-limit:]:
                if not line.strip():
                    continue
                records.append(json.loads(line.decode("utf-8")))
            self._tail_cache[limit] = records
            return list(records)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._warnings.append(f"recent decision read failed: {exc}")
            return []

    def recent_decisions(self, limit: int = 5) -> list[dict[str, Any]]:
        if self._decision_index is not None:
            entries = list(self._decision_index[-limit:])
            return [
                self._record_compact(self._load_record_at(entry), offset=entry.offset, length=entry.length)
                for entry in reversed(entries)
            ]
        records = list(reversed(self._tail_records(limit)))
        compact: list[dict[str, Any]] = []
        for record in records:
            try:
                compact.append(self._record_compact(record))
            except DashboardError:
                continue
        return compact

    def _filter_decisions(
        self,
        entries: list[DecisionIndexEntry],
        filters: dict[str, str],
    ) -> list[DecisionIndexEntry]:
        status = filters.get("status", "").strip()
        side = filters.get("side", "").strip().lower()
        period = filters.get("period", "").strip()
        rejection = filters.get("rejection", "").strip()
        score_min_raw = filters.get("score_min", "").strip()
        score_max_raw = filters.get("score_max", "").strip()
        try:
            score_min = float(score_min_raw) if score_min_raw else None
            score_max = float(score_max_raw) if score_max_raw else None
        except ValueError as exc:
            raise DashboardError("score filters must be numeric", 400) from exc
        result = []
        for entry in entries:
            if status and entry.status != status:
                continue
            if side and (entry.side or "") != side:
                continue
            if period and entry.period != period:
                continue
            if rejection and rejection not in entry.rejections:
                continue
            if score_min is not None and entry.score < score_min:
                continue
            if score_max is not None and entry.score > score_max:
                continue
            result.append(entry)
        return result

    def decisions_payload(self, filters: dict[str, str]) -> dict[str, Any]:
        self._build_decision_index()
        assert self._decision_index is not None
        try:
            page = int(filters.get("page", "1"))
            page_size = int(filters.get("page_size", "25"))
        except ValueError as exc:
            raise DashboardError("page and page_size must be integers", 400) from exc
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        filtered = self._filter_decisions(list(self._decision_index), filters)
        order = filters.get("sort", "newest")
        if order == "oldest":
            filtered.sort(key=lambda item: item.decision_time)
        else:
            filtered.sort(key=lambda item: item.decision_time, reverse=True)
        total = len(filtered)
        start = (page - 1) * page_size
        page_entries = filtered[start : start + page_size]
        items = []
        for entry in page_entries:
            record = self._load_record_at(entry)
            item = self._record_compact(record, offset=entry.offset, length=entry.length)
            items.append(item)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "filters": {
                "status": filters.get("status", ""),
                "side": filters.get("side", ""),
                "period": filters.get("period", ""),
                "rejection": filters.get("rejection", ""),
                "score_min": filters.get("score_min", ""),
                "score_max": filters.get("score_max", ""),
                "sort": filters.get("sort", "newest"),
            },
        }

    def decision_detail(self, record_hash: str) -> dict[str, Any]:
        if not _HASH_RE.fullmatch(record_hash):
            raise DashboardError("invalid decision hash", 400)
        self._build_decision_index()
        assert self._decision_hash_map is not None
        entry = self._decision_hash_map.get(record_hash)
        if entry is None:
            raise DashboardError("decision record not found", 404)
        record = self._load_record_at(entry)
        canonical = record.get("canonical")
        if not isinstance(canonical, dict):
            raise DashboardError("decision record is invalid")
        expected = hashlib.sha256(canonical_json(canonical).encode()).hexdigest()
        if expected != entry.record_hash:
            raise DashboardError("decision record hash verification failed")
        return record

    # ---- source/data helpers ---------------------------------------------

    def _source_status(self) -> list[dict[str, Any]]:
        if self._source_status_cache is not None:
            return list(self._source_status_cache)
        self._load()
        manifest = self._manifest or {}
        sources = manifest.get("sources")
        raw_sources = sources if isinstance(sources, list) else []
        result = []
        for source in raw_sources:
            if not isinstance(source, dict):
                continue
            label = str(source.get("label") or "")
            expected_key = EXPECTED_SOURCE_KEYS.get(label)
            expected = DEFAULT_CHECKSUMS.get(expected_key) if expected_key else None
            declared = source.get("checksum_sha256")
            path = Path(str(source.get("path", "")))
            relative = self.config.relative_path(path)
            exists = path.is_file()
            actual = _sha256(path) if exists and relative != "<external>" else None
            manifest_match = bool(expected and declared == expected)
            file_match = bool(actual and declared and actual == declared)
            result.append(
                {
                    "label": label,
                    "timeframe": source.get("timeframe"),
                    "rows": source.get("row_count"),
                    "first_observation": source.get("first_observation"),
                    "last_observation": source.get("last_observation"),
                    "declared_checksum": declared,
                    "expected_checksum": expected,
                    "actual_checksum": actual,
                    "path": relative,
                    "exists": exists,
                    "manifest_checksum_match": manifest_match,
                    "file_checksum_match": file_match,
                    "verified": bool(exists and manifest_match and file_match),
                }
            )
        self._source_status_cache = result
        return list(result)

    def data_payload(self) -> dict[str, Any]:
        sources = self._source_status()
        complete = bool(sources) and all(item["verified"] for item in sources)
        return {
            "status": "PASS" if complete else "WARNING",
            "sources": sources,
            "timestamps": {
                "coordinate": "dataset_time",
                "absolute_utc_provenance": "unverified",
                "session_compliance": "not_claimed",
                "news_compliance": "not_claimed",
            },
            "warnings": [
                "Timezone provenance is dataset_time; absolute UTC provenance is unverified.",
                "Historical candles are not current market prices.",
            ],
        }

    def _execution_payload(self) -> dict[str, Any]:
        settings_payload = self.config_payload()
        settings = settings_payload.get("snapshot") or {}
        return {
            "status": "DISABLED",
            "mode": "RESEARCH / READ-ONLY",
            "settings_execution_mode": settings.get("execution_mode"),
            "settings_trading_mode": settings.get("trading_mode"),
            "order_submission": "BLOCKED",
            "broker_adapter": "NOT CONNECTED",
            "demo": "BLOCKED",
            "live": "BLOCKED",
            "reason": (
                "This browser dashboard has no broker adapter or order route. "
                "It cannot submit, modify, or close orders."
            ),
        }

    def config_payload(self) -> dict[str, Any]:
        if self._settings_cache is not None:
            return dict(self._settings_cache)
        active = self.config.settings_path
        example = self.config.settings_example_path
        path = active if active and active.is_file() else example
        source = "active" if path == active and active and active.is_file() else "example"
        result: dict[str, Any] = {
            "available": False,
            "source": source,
            "warning": (
                "No config/settings.json is present; showing the safe example "
                "template. It is not active runtime configuration."
                if source == "example"
                else None
            ),
        }
        if path is None or not path.is_file():
            result["error"] = "No settings file is available."
            self._settings_cache = result
            return dict(result)
        try:
            settings = load_settings(path)
            result.update(
                {
                    "available": True,
                    "snapshot": settings_snapshot(settings),
                }
            )
        except Exception as exc:  # configuration errors are reported, not exposed
            result["error"] = f"Settings could not be loaded: {type(exc).__name__}"
        self._settings_cache = result
        return dict(result)

    # ---- strategy/spec ----------------------------------------------------

    def strategy_payload(self) -> dict[str, Any]:
        self._load()
        summary = self._summary or {}
        strategy = summary.get("strategy") if isinstance(summary.get("strategy"), dict) else {}
        parameters = strategy.get("parameters") if isinstance(strategy.get("parameters"), dict) else {}
        return {
            "spec_document": "docs/strategy/xauusd_p_strategy_spec_v1.md",
            "complete_strategy": {
                "name": "XAUUSD-P D1/H4/M15 multi-timeframe strategy",
                "instrument": "XAUUSD-P",
                "timeframes": ["D1", "H4", "M15"],
                "description": (
                    "The intended specification combines D1 EMA bias, H4 structure "
                    "and zones, M15 trigger confirmation, an eight-point confluence "
                    "score, structural stops, partial targets, and risk/cooldown controls."
                ),
            },
            "run_variant": {
                "name": strategy.get("name"),
                "variant_id": strategy.get("variant_id"),
                "variant_name": strategy.get("variant_name"),
                "version": strategy.get("version"),
                "research_only": strategy.get("research_only"),
            },
            "parameters": parameters,
            "sections": [
                {
                    "title": "Timeframes and decision timing",
                    "items": [
                        "D1 completed close defines market bias.",
                        "H4 completed close defines trend, structure, and zones.",
                        "M15 completed close is the trigger; decisions use only completed bars.",
                        "Entry reference is the next M15 open, never the trigger close.",
                    ],
                },
                {
                    "title": "Confluence and setup",
                    "items": [
                        "D1 bias, H4 structure, H4 zone/Fibonacci context, and M15 RSI/pattern/volume are combined.",
                        "The score has a maximum of 8; score below 5 is rejected.",
                        "Countertrend trades are disabled and H4 direction is mandatory.",
                    ],
                },
                {
                    "title": "Risk, stops, and exits",
                    "items": [
                        "Structural stop with H4 ATR buffer and maximum stop-width rules.",
                        "TP1 at 2.0R and TP2 at 3.0R before obstacle adjustment; TP2 must remain at least 2.5R.",
                        "TP1 partial exit and breakeven stop after TP1 are separate simulation events.",
                        "Maximum 2 concurrent positions, 3 entries per day, cooldowns, and -3% daily loss ceiling.",
                    ],
                },
            ],
            "variant_warning": (
                "This historical run used the research-isolated filter variant. "
                "It is not the complete approved strategy: session isolation and "
                "news-unaware research mode change the filter behavior."
            ),
            "limitations": summary.get("limitations") or [],
        }

    # ---- overview and reports --------------------------------------------

    def _split_payload(self, name: str, raw: Any) -> dict[str, Any]:
        split = raw if isinstance(raw, dict) else {}
        metrics = split.get("metrics") if isinstance(split.get("metrics"), dict) else {}
        exits = metrics.get("exit_reason_counts") if isinstance(metrics.get("exit_reason_counts"), dict) else {}
        return {
            "name": name,
            "label": SPLIT_LABELS.get(name, name),
            "period": split.get("split") or {},
            "decision_count": split.get("decision_count", 0),
            "candidate_count": split.get("candidate_count", 0),
            "filled_trade_count": split.get("filled_trade_count", 0),
            "closed_trade_count": split.get("closed_trade_count", metrics.get("closed_trade_count", 0)),
            "open_or_truncated_trade_count": split.get("open_or_truncated_trade_count", 0),
            "win_count": metrics.get("win_count", 0),
            "loss_count": metrics.get("loss_count", 0),
            "win_rate": metrics.get("win_rate"),
            "gross_realized_r": metrics.get("gross_realized_r"),
            "average_r": metrics.get("average_r"),
            "profit_factor": metrics.get("profit_factor"),
            "max_drawdown_r": metrics.get("max_drawdown_r"),
            "ambiguous_bar_count": metrics.get("ambiguous_bar_count", 0),
            "exit_reason_counts": exits,
            "tp1_partial_count": exits.get("tp1", 0),
            "tp2_count": exits.get("tp2", 0),
            "inconclusive_under_30_trades": bool(metrics.get("inconclusive_under_30_trades")),
            "sample_size_gate": metrics.get("sample_size_gate"),
            "open_or_truncated_trades": split.get("open_or_truncated_trades", []),
            "carried_position_count_at_split_start": split.get("carried_position_count_at_split_start", 0),
        }

    def _overall_payload(self) -> dict[str, Any]:
        summary = self._summary or {}
        overall = summary.get("overall") if isinstance(summary.get("overall"), dict) else {}
        metrics = overall.get("metrics") if isinstance(overall.get("metrics"), dict) else {}
        exits = metrics.get("exit_reason_counts") if isinstance(metrics.get("exit_reason_counts"), dict) else {}
        return {
            "decision_count": overall.get("decision_count", 0),
            "candidate_count": overall.get("candidate_count", 0),
            "entry_attempt_count": overall.get("entry_attempt_count", 0),
            "filled_trade_count": overall.get("filled_trade_count", 0),
            "closed_trade_count": overall.get("closed_trade_count", 0),
            "open_or_truncated_trade_count": overall.get("open_or_truncated_trade_count", 0),
            "trades_with_decision_hash": overall.get("trades_with_decision_hash", 0),
            "win_count": metrics.get("win_count", 0),
            "loss_count": metrics.get("loss_count", 0),
            "win_rate": metrics.get("win_rate"),
            "gross_realized_r": metrics.get("gross_realized_r"),
            "average_r": metrics.get("average_r"),
            "profit_factor": metrics.get("profit_factor"),
            "max_drawdown_r": metrics.get("max_drawdown_r"),
            "ambiguous_bar_count": metrics.get("ambiguous_bar_count", 0),
            "exit_reason_counts": exits,
            "tp1_partial_count": exits.get("tp1", 0),
            "tp2_count": exits.get("tp2", 0),
            "breakeven_count": metrics.get("breakeven_count", 0),
        }

    def record_validation(self) -> dict[str, Any]:
        self._load()
        summary = self._summary or {}
        contract = summary.get("record_contract") if isinstance(summary.get("record_contract"), dict) else {}
        overall = summary.get("overall") if isinstance(summary.get("overall"), dict) else {}
        decisions_info = self._artifact_info.get("decisions.jsonl", {})
        decision_file_ok = bool(
            decisions_info.get("exists")
            and not decisions_info.get("error")
            and decisions_info.get("header_parsed")
        )
        authoritative = bool(
            contract.get("authoritative")
            and contract.get("canonical_records_enabled")
            and decision_file_ok
        )
        linked = overall.get("trades_with_decision_hash") == len(self._trades)
        attempts_match = overall.get("entry_attempt_count") == len(
            self._entry_attempts
        )
        counts_match = overall.get("decision_count") == overall.get(
            "decision_record_count"
        )
        status = (
            "PASS"
            if authoritative and linked and attempts_match and counts_match
            else "WARNING"
        )
        return {
            "status": status,
            "authoritative": authoritative,
            "canonical_records_enabled": bool(
                contract.get("canonical_records_enabled")
            ),
            "decision_source": contract.get("decision_source"),
            "decision_count": overall.get("decision_count"),
            "decision_record_count": overall.get("decision_record_count"),
            "trades_checked": len(self._trades),
            "trades_with_decision_hash": overall.get("trades_with_decision_hash"),
            "attempts_checked": len(self._entry_attempts),
            "scope": "summary/manifest linkage counts; detail hash verified on demand",
        }

    def overview(self) -> dict[str, Any]:
        self._load()
        summary = self._summary or {}
        manifest = self._manifest or {}
        data = self.data_payload()
        metrics = self._overall_payload()
        splits = {
            name: self._split_payload(
                name,
                (summary.get("splits") or {}).get(name) if isinstance(summary.get("splits"), dict) else None,
            )
            for name in SPLIT_ORDER
        }
        warnings = list(self._warnings)
        if not summary:
            warnings.append("Authoritative summary.json is unavailable; metrics are not rendered.")
        if not manifest:
            warnings.append("Run manifest.json is unavailable; provenance is incomplete.")
        recent = self.recent_decisions(5)
        last = recent[0] if recent else None
        return {
            "application": {
                "name": "Trantor",
                "version": self.config.app_version,
                "mode": "Research / read-only",
            },
            "instrument": summary.get("instrument_profile") or {
                "symbol": "XAUUSD-P",
                "asset_class": "unverified_gold",
            },
            "strategy": summary.get("strategy") or {},
            "execution": self._execution_payload(),
            "data": data,
            "run": {
                "artifact_dir": self.config.relative_path(self.config.artifact_dir),
                "config_fingerprint": summary.get("config_fingerprint"),
                "record_contract": summary.get("record_contract") or {},
                "labels": summary.get("labels") or DEFAULT_LABELS,
                "splits": [split.get("split") for split in splits.values()],
            },
            "metrics": metrics,
            "splits": splits,
            "record_validation": self.record_validation(),
            "last_decision": last,
            "recent_decisions": recent,
            "artifact_status": self.artifact_status(),
            "warnings": list(dict.fromkeys(warnings)),
            "limitations": summary.get("limitations") or [],
            "labels": summary.get("labels") or DEFAULT_LABELS,
        }

    def research_payload(self) -> dict[str, Any]:
        self._load()
        summary = self._summary or {}
        splits = {
            name: self._split_payload(
                name,
                (summary.get("splits") or {}).get(name) if isinstance(summary.get("splits"), dict) else None,
            )
            for name in SPLIT_ORDER
        }
        return {
            "labels": summary.get("labels") or DEFAULT_LABELS,
            "overall": self._overall_payload(),
            "splits": splits,
            "warnings": [
                "Each split has fewer than 30 closed trades and is inconclusive.",
                "Results are gross price/R only; no execution costs or monetary returns are included.",
            ],
            "limitations": summary.get("limitations") or [],
        }

    def diagnostics_payload(self) -> dict[str, Any]:
        """Return the verified Phase F trade-level diagnostics, cached per server."""

        if self._diagnostics_cache is None:
            self._load()
            try:
                service = TradeDiagnosticsService(
                    DiagnosticConfig(
                        artifact_dir=self.config.artifact_dir,
                        repo_root=self.config.repo_root,
                    )
                )
                self._diagnostics_cache = service.build()
            except Exception as exc:
                raise DashboardError(
                    f"Trade diagnostics could not be validated: {type(exc).__name__}: {exc}"
                ) from exc
        return self._diagnostics_cache

    def trades_payload(self, filters: dict[str, str]) -> dict[str, Any]:
        self._load()
        sides = filters.get("side", "").strip().lower()
        periods = filters.get("period", "").strip()
        statuses = filters.get("status", "").strip()
        sort = filters.get("sort", "entry_time_desc")
        items = list(self._trades)
        if sides:
            items = [item for item in items if item.get("side") == sides]
        if periods:
            items = [item for item in items if item.get("entry_period") == periods]
        if statuses:
            items = [item for item in items if item.get("status") == statuses]
        key_map = {
            "entry_time_desc": lambda item: str(item.get("entry_time", "")),
            "entry_time_asc": lambda item: str(item.get("entry_time", "")),
            "realized_r_desc": lambda item: _number(item.get("realized_r")) or 0.0,
            "realized_r_asc": lambda item: _number(item.get("realized_r")) or 0.0,
            "trade_id": lambda item: item.get("trade_id", 0),
        }
        key = key_map.get(sort, key_map["entry_time_desc"])
        items.sort(key=key, reverse=sort in {"entry_time_desc", "realized_r_desc"})
        return {
            "items": items,
            "total": len(items),
            "filters": {"side": sides, "period": periods, "status": statuses, "sort": sort},
            "attempts": self._entry_attempts,
        }

    def data_page_payload(self) -> dict[str, Any]:
        return {
            "data": self.data_payload(),
            "manifest": {
                "config_fingerprint": (self._manifest or {}).get("config_fingerprint"),
                "record_contract": (self._manifest or {}).get("record_contract"),
                "time_semantics": (self._manifest or {}).get("time_semantics"),
                "assumptions": (self._manifest or {}).get("assumptions") or [],
                "limitations": (self._manifest or {}).get("limitations") or [],
            },
            "artifact_status": self.artifact_status(),
        }

    def reports_payload(self) -> dict[str, Any]:
        self._load()
        return {
            "artifact_dir": self.config.relative_path(self.config.artifact_dir),
            "artifacts": [
                {
                    **status,
                    "path": self.config.relative_path(
                        self.config.artifact_dir / status["name"]
                    ),
                }
                for status in self.artifact_status()
            ],
            "summary": {
                "config_fingerprint": (self._summary or {}).get("config_fingerprint"),
                "labels": (self._summary or {}).get("labels") or DEFAULT_LABELS,
                "record_contract": (self._summary or {}).get("record_contract") or {},
                "overall": self._overall_payload(),
                "data": self.data_payload(),
            },
            "manifest": {
                "config_fingerprint": (self._manifest or {}).get("config_fingerprint"),
                "strategy": (self._manifest or {}).get("strategy"),
                "instrument_profile": (self._manifest or {}).get("instrument_profile"),
                "record_contract": (self._manifest or {}).get("record_contract"),
                "splits": (self._manifest or {}).get("splits"),
                "assumptions": (self._manifest or {}).get("assumptions") or [],
                "limitations": (self._manifest or {}).get("limitations") or [],
                "time_semantics": (self._manifest or {}).get("time_semantics") or {},
                "costs": (self._manifest or {}).get("costs") or {},
            },
            "record_validation": self.record_validation(),
            "warnings": self.warnings,
        }

    def system_payload(self) -> dict[str, Any]:
        data = self.data_payload()
        return {
            "execution": self._execution_payload(),
            "read_only": True,
            "data": data,
            "record_validation": self.record_validation(),
            "capabilities": [
                {
                    "name": "Research dashboard",
                    "status": "AVAILABLE",
                    "detail": "Read-only summary and artifact inspection.",
                },
                {
                    "name": "Trade explorer",
                    "status": "AVAILABLE",
                    "detail": "Simulated historical trades from trades.json.",
                },
                {
                    "name": "Decision history",
                    "status": "AVAILABLE",
                    "detail": "Server-side filtered and paginated decisions.",
                },
                {
                    "name": "Historical chart",
                    "status": "AVAILABLE",
                    "detail": "D1/H4/M15 historical candles; not live prices.",
                },
                {
                    "name": "Order submission",
                    "status": "BLOCKED",
                    "detail": "No order route or broker adapter exists in this app.",
                },
                {
                    "name": "Demo / live execution",
                    "status": "BLOCKED",
                    "detail": "Execution remains disabled.",
                },
                {
                    "name": "Monetary sizing",
                    "status": "UNAVAILABLE",
                    "detail": "Gross R research only; no account sizing.",
                },
                {
                    "name": "Verified UTC / session / news",
                    "status": "UNVERIFIED",
                    "detail": "Dataset-time provenance and calendar compliance are unverified.",
                },
            ],
            "limitations": (self._summary or {}).get("limitations") or [],
            "config": self.config_payload(),
        }

    # ---- historical chart -------------------------------------------------

    def _load_research_data(self) -> Any:
        if self._research_data is None:
            try:
                self._research_data = load_research_data()
            except Exception as exc:
                raise DashboardError(
                    f"Historical data validation failed: {type(exc).__name__}"
                ) from exc
        return self._research_data

    def candles_payload(self, filters: dict[str, str]) -> dict[str, Any]:
        self._load()
        requested = filters.get("timeframe", "D1").strip().lower()
        key = TIMEFRAME_ALIASES.get(requested)
        if key is None:
            raise DashboardError("timeframe must be one of D1, H4, or M15", 400)
        try:
            limit = int(filters.get("limit", "120"))
        except ValueError as exc:
            raise DashboardError("limit must be an integer", 400) from exc
        limit = max(20, min(limit, 400))
        data = self._load_research_data()
        series = getattr(data, key)
        times = [bar.observed_at for bar in series]
        end = None
        raw_end = filters.get("end", "").strip()
        if raw_end:
            try:
                end = datetime.fromisoformat(raw_end)
            except ValueError as exc:
                raise DashboardError("end must be an ISO-8601 timestamp", 400) from exc
            if end.tzinfo is None or end.utcoffset() is None:
                raise DashboardError("end must include a timezone", 400)
            end = end.astimezone(timezone.utc)
        end_index = bisect_right(times, end) if end is not None else len(series)
        start = max(0, end_index - limit)
        selected = series[start:end_index]
        candles = [
            {
                "time": bar.observed_at.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in selected
        ]
        markers: list[dict[str, Any]] = []
        marker_note = None
        if key == "m15":
            selected_times = {bar.observed_at: index for index, bar in enumerate(selected)}
            for trade in self._trades:
                entry_time = trade.get("entry_time")
                try:
                    parsed = datetime.fromisoformat(str(entry_time))
                except (TypeError, ValueError):
                    continue
                if parsed in selected_times:
                    markers.append(
                        {
                            "trade_id": trade.get("trade_id"),
                            "entry_time": entry_time,
                            "entry_price": trade.get("entry_price"),
                            "stop": trade.get("stop"),
                            "tp1": trade.get("tp1"),
                            "tp2": trade.get("tp2"),
                            "side": trade.get("side"),
                            "realized_r": trade.get("realized_r"),
                        }
                    )
        else:
            marker_note = (
                "Trade markers are shown only on the M15 historical view; "
                "D1/H4 candles are aggregates of the same research data."
            )
        source_status = self._source_status()
        return {
            "timeframe": "M15" if key == "m15" else key.upper(),
            "candles": candles,
            "markers": markers,
            "marker_note": marker_note,
            "historical_label": "HISTORICAL RESEARCH DATA - NOT LIVE PRICES",
            "source": [
                {
                    "label": item["label"],
                    "timeframe": item["timeframe"],
                    "path": item["path"],
                    "verified": item["verified"],
                }
                for item in source_status
            ],
            "start": candles[0]["time"] if candles else None,
            "end": candles[-1]["time"] if candles else None,
            "limit": limit,
        }
