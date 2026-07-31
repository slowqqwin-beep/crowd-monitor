"""Generate a CROWD daily event report from structured project data.

Default command:
    python scripts/generate_report.py

Required input:
    data/calendar.json

Optional inputs:
    data/crowd_snapshot.json
    data/analysis_YYYYMMDD.json
    data/ai_industry_analysis.json

Default output:
    CROWD_Event_Report_YYYYMMDD.md

Design rules:
1. Render data; do not hard-code market conclusions or scenario probabilities.
2. Preserve legacy calendar.json compatibility, but never treat a legacy
   ``date_confirmed`` flag as sufficient evidence for a verified fact.
3. Keep CROWD market-relative data, event facts, and AI analysis separate.
4. Fail clearly on malformed required data and degrade safely when optional
   inputs are missing.
5. Use only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CALENDAR_PATH = ROOT / "data" / "calendar.json"
DEFAULT_SNAPSHOT_PATH = ROOT / "data" / "crowd_snapshot.json"
DEFAULT_ANALYSIS_PATH = ROOT / "data" / "ai_industry_analysis.json"
OUTPUT_DIR = ROOT
TIMEZONE_NAME = "Asia/Shanghai"


CATEGORY_LABELS = {
    "memory_truth": "存储真值",
    "capex_guidance": "Capex指引",
    "optics_earnings": "光链财报",
    "trendforce_release": "TrendForce发布",
    "gpu_earnings": "GPU/ASIC财报",
    "power_cooling": "电力与液冷",
    "pcb_materials": "PCB/CCL与电子材料",
    "semicap_packaging": "设备与先进封装",
    "cloud_model": "云、模型与软件",
    "physical_ai": "物理AI",
    "other": "其他",
}


STATUS_LABELS = {
    "pending": "待确认",
    "partially_verified": "部分验证",
    "verified": "已验证",
    "conflicted": "来源冲突",
    "verification_failed": "验证失败",
}

STATUS_ICONS = {
    "pending": "⏳",
    "partially_verified": "◐",
    "verified": "✅",
    "conflicted": "⚠️",
    "verification_failed": "❌",
}

STATUS_ALIASES = {
    "pending": "pending",
    "待确认": "pending",
    "未确认": "pending",
    "partially_verified": "partially_verified",
    "partial": "partially_verified",
    "部分验证": "partially_verified",
    "verified": "verified",
    "已验证": "verified",
    "confirmed": "verified",
    "conflicted": "conflicted",
    "conflict": "conflicted",
    "来源冲突": "conflicted",
    "verification_failed": "verification_failed",
    "failed": "verification_failed",
    "验证失败": "verification_failed",
}

CYCLE_FIELDS = (
    ("technology_cycle", "技术周期"),
    ("supply_demand_cycle", "供需周期"),
    ("earnings_cycle", "盈利周期"),
    ("market_pricing_cycle", "二级市场周期"),
)


@dataclass
class Evidence:
    title: str = ""
    url: str = ""
    published_at: str = ""
    data_period: str = ""
    source_tier: str = ""
    claim: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.title and self.url and self.data_period)


@dataclass
class Event:
    event_date: date
    name: str
    category: str
    status: str
    hypothesis: str = ""
    actual_result: str = ""
    note: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    impact: dict[str, Any] = field(default_factory=dict)
    subevents: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""
    next_check: str = ""
    invalidation: str = ""
    legacy: bool = False


def now_in_timezone() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE_NAME))


def load_json(path: pathlib.Path, *, required: bool) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required JSON file not found: {path}")
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(f"Top-level JSON value must be an object: {path}")
    return data


def parse_iso_date(value: Any, *, field_name: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty YYYY-MM-DD string")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD: {value!r}") from exc


def split_legacy_result(hypothesis: str) -> tuple[str, str]:
    for marker in ("实际结果：", "实际结果:"):
        if marker in hypothesis:
            base, result = hypothesis.split(marker, 1)
            return base.strip(), result.strip()
    return hypothesis.strip(), ""


def normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    key = str(value).strip()
    return STATUS_ALIASES.get(key)


def normalize_evidence(raw_event: dict[str, Any]) -> list[Evidence]:
    raw_items = raw_event.get("evidence", [])
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        raw_items = []

    top_level_source = {
        "title": raw_event.get("source_title") or raw_event.get("source", ""),
        "url": raw_event.get("source_url") or raw_event.get("url", ""),
        "published_at": raw_event.get("published_at", ""),
        "data_period": raw_event.get("data_period", ""),
        "source_tier": raw_event.get("source_tier", ""),
        "claim": raw_event.get("source_claim", ""),
    }
    if any(top_level_source.values()):
        raw_items = [top_level_source, *raw_items]

    evidence: list[Evidence] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        evidence.append(
            Evidence(
                title=str(
                    item.get("title")
                    or item.get("source_title")
                    or item.get("source")
                    or ""
                ).strip(),
                url=str(item.get("url") or item.get("source_url") or "").strip(),
                published_at=str(item.get("published_at") or "").strip(),
                data_period=str(item.get("data_period") or "").strip(),
                source_tier=str(item.get("source_tier") or item.get("tier") or "").strip(),
                claim=str(item.get("claim") or "").strip(),
            )
        )
    return evidence


def normalize_subevents(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        items = []
        for name, value in raw.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("name", name)
            else:
                item = {"name": name, "status": value}
            items.append(item)
        return items

    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def derive_status_from_subevents(
    subevents: list[dict[str, Any]], warnings: list[str], event_name: str
) -> str | None:
    if not subevents:
        return None

    statuses: list[str] = []
    for item in subevents:
        raw_status = item.get("status")
        status = normalize_status(raw_status)
        if status is None:
            warnings.append(
                f"事件“{event_name}”的子事件“{item.get('name', 'N/A')}”"
                f"状态无法识别：{raw_status!r}"
            )
            status = "pending"
        statuses.append(status)

    if "conflicted" in statuses:
        return "conflicted"
    if all(status == "verified" for status in statuses):
        return "verified"
    if all(status == "verification_failed" for status in statuses):
        return "verification_failed"
    if any(status != "pending" for status in statuses):
        return "partially_verified"
    return "pending"


def parse_event(raw_event: dict[str, Any], warnings: list[str], index: int) -> Event:
    if not isinstance(raw_event, dict):
        raise ValueError(f"events[{index}] must be an object")

    name = str(raw_event.get("name") or "").strip()
    if not name:
        raise ValueError(f"events[{index}].name is required")

    event_date = parse_iso_date(raw_event.get("date"), field_name=f"events[{index}].date")
    category = str(raw_event.get("category") or "other").strip()

    hypothesis = str(raw_event.get("hypothesis") or "").strip()
    legacy_hypothesis, legacy_result = split_legacy_result(hypothesis)
    explicit_result = str(raw_event.get("actual_result") or "").strip()
    actual_result = explicit_result or legacy_result
    if legacy_result and not explicit_result:
        hypothesis = legacy_hypothesis

    evidence = normalize_evidence(raw_event)
    subevents = normalize_subevents(raw_event.get("subevents"))
    subevent_status = derive_status_from_subevents(subevents, warnings, name)

    explicit_status = normalize_status(
        raw_event.get("status") or raw_event.get("verification_status")
    )
    raw_explicit_status = raw_event.get("status") or raw_event.get("verification_status")
    if raw_explicit_status is not None and explicit_status is None:
        warnings.append(
            f"事件“{name}”状态无法识别：{raw_explicit_status!r}，已安全降级为待确认"
        )

    legacy_confirmed = bool(raw_event.get("date_confirmed", False))
    legacy = raw_explicit_status is None

    if subevent_status is not None:
        status = subevent_status
        if explicit_status and explicit_status != subevent_status:
            warnings.append(
                f"事件“{name}”显式状态“{STATUS_LABELS[explicit_status]}”与"
                f"子事件汇总状态“{STATUS_LABELS[subevent_status]}”不一致，"
                "已采用更可审计的子事件汇总状态"
            )
    elif explicit_status is not None:
        status = explicit_status
    elif legacy_confirmed or actual_result:
        status = "partially_verified"
        warnings.append(
            f"事件“{name}”使用旧字段date_confirmed/实际结果，"
            "缺少显式verification_status，已降级为部分验证"
        )
    else:
        status = "pending"

    if status == "verified" and not actual_result:
        status = "partially_verified"
        warnings.append(f"事件“{name}”标为已验证但缺少actual_result，已降级为部分验证")

    if status == "verified" and not any(item.is_complete for item in evidence):
        status = "partially_verified"
        warnings.append(
            f"事件“{name}”标为已验证但缺少完整证据（标题、链接、数据期），"
            "已降级为部分验证"
        )

    raw_impact = raw_event.get("impact")
    impact = raw_impact if isinstance(raw_impact, dict) else {}

    return Event(
        event_date=event_date,
        name=name,
        category=category,
        status=status,
        hypothesis=hypothesis,
        actual_result=actual_result,
        note=str(raw_event.get("note") or "").strip(),
        evidence=evidence,
        impact=impact,
        subevents=subevents,
        updated_at=str(raw_event.get("updated_at") or "").strip(),
        next_check=str(raw_event.get("next_check") or "").strip(),
        invalidation=str(raw_event.get("invalidation") or "").strip(),
        legacy=legacy,
    )


def parse_events(calendar: dict[str, Any], warnings: list[str]) -> list[Event]:
    raw_events = calendar.get("events", [])
    if not isinstance(raw_events, list):
        raise ValueError("calendar.json field 'events' must be an array")

    events = [parse_event(item, warnings, index) for index, item in enumerate(raw_events)]
    events.sort(key=lambda event: (event.event_date, event.name))

    seen: set[tuple[date, str]] = set()
    for event in events:
        key = (event.event_date, event.name)
        if key in seen:
            warnings.append(
                f"发现重复事件：{event.event_date.isoformat()} | {event.name}"
            )
        seen.add(key)
    return events


def resolve_optional_path(
    explicit: pathlib.Path | None,
    candidates: Iterable[pathlib.Path],
) -> pathlib.Path | None:
    if explicit is not None:
        return explicit
    return next((candidate for candidate in candidates if candidate.exists()), None)


def table_text(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return re.sub(r"\s+", " ", text).strip() or "N/A"


def display_number(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:g}{suffix}"
    return table_text(value)


def direction_text(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    normalized = str(value).strip().lower()
    mapping = {
        "up": "↑",
        "rising": "↑",
        "increase": "↑",
        "向上": "↑",
        "上升": "↑",
        "down": "↓",
        "falling": "↓",
        "decrease": "↓",
        "向下": "↓",
        "下降": "↓",
        "flat": "→",
        "stable": "→",
        "横盘": "→",
        "持平": "→",
    }
    return mapping.get(normalized, table_text(value))


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category or "其他")


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def countdown_text(event_date: date, as_of: date) -> str:
    delta = (event_date - as_of).days
    if delta > 0:
        return f"T-{delta}"
    if delta == 0:
        return "今天"
    return f"已过期{abs(delta)}天"


def event_fact_label(event: Event) -> str:
    return "[已验证]" if event.status == "verified" else "[不确定]"


def _us_market_holidays(year: int) -> set[date]:
    """Return major US market holidays for a given year (fixed + approximate)."""
    holidays: set[date] = set()
    # Fixed-date holidays
    holidays.add(date(year, 1, 1))   # New Year's Day
    holidays.add(date(year, 7, 4))   # Independence Day
    holidays.add(date(year, 12, 25)) # Christmas
    holidays.add(date(year, 6, 19))  # Juneteenth
    # Approximate floating holidays (sufficient for staleness check)
    # MLK: 3rd Monday of January
    d = date(year, 1, 1)
    mondays = 0
    while mondays < 3:
        if d.weekday() == 0:
            mondays += 1
            if mondays == 3:
                holidays.add(d)
        d += timedelta(days=1)
    # Presidents: 3rd Monday of February
    d = date(year, 2, 1)
    mondays = 0
    while mondays < 3:
        if d.weekday() == 0:
            mondays += 1
            if mondays == 3:
                holidays.add(d)
        d += timedelta(days=1)
    # Memorial: last Monday of May
    d = date(year, 5, 31)
    while d.weekday() != 0:
        d -= timedelta(days=1)
    holidays.add(d)
    # Labor: 1st Monday of September
    d = date(year, 9, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    holidays.add(d)
    # Thanksgiving: 4th Thursday of November
    d = date(year, 11, 1)
    thursdays = 0
    while thursdays < 4:
        if d.weekday() == 3:
            thursdays += 1
            if thursdays == 4:
                holidays.add(d)
        d += timedelta(days=1)
    return holidays


def _is_us_trading_day(d: date) -> bool:
    """Check if a date is a US trading day (weekday and not a major holiday)."""
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return d not in _us_market_holidays(d.year)


def _latest_completed_trading_day(reference: date) -> date:
    """Return the most recent completed US trading day strictly before reference.

    CI runs at Beijing 10:00 which is before US market opens, so the latest
    completed session is always strictly before the reference date.
    """
    d = reference - timedelta(days=1)
    while not _is_us_trading_day(d):
        d -= timedelta(days=1)
    return d


def render_snapshot(snapshot: dict[str, Any] | None, warnings: list[str], report_date: date | None = None) -> list[str]:
    lines = ["## 一、CROWD市场相对强度快照", ""]
    if snapshot is None:
        lines.extend(
            [
                "> [不确定] 未提供CROWD结构化快照；本报告不生成剪刀差或篮子读数。",
                "",
            ]
        )
        return lines

    as_of = snapshot.get("as_of") or snapshot.get("date") or "N/A"
    scissors = snapshot.get("scissors")
    if not isinstance(scissors, dict):
        scissors = {}
    benchmark = (
        snapshot.get("benchmark")
        or scissors.get("benchmark")
        or scissors.get("formula")
        or "由快照提供"
    )

    lines.extend(
        [
            f"- **数据日期**：{table_text(as_of)}",
            f"- **基准/剪刀差公式**：{table_text(benchmark)}",
            f"- **剪刀差当前值**：{display_number(scissors.get('value'))}",
            f"- **20日方向**："
            f"{direction_text(scissors.get('direction_20d') or scissors.get('momentum_20d'))}",
            f"- **60日方向**："
            f"{direction_text(scissors.get('direction_60d') or scissors.get('momentum_60d'))}",
            "",
        ]
    )

    # Staleness check: compare against most recent completed US trading day
    if report_date is not None and as_of != "N/A":
        try:
            snapshot_date = date.fromisoformat(str(as_of).strip())
            expected_td = _latest_completed_trading_day(report_date)
            if snapshot_date < expected_td:
                stale_days = (expected_td - snapshot_date).days
                stale_msg = (
                    f"[不确定] CROWD快照数据陈旧：快照数据日期为{as_of}，"
                    f"最近已完成交易日为{expected_td.isoformat()}，滞后{stale_days}个交易日。"
                    "以下读数反映的是快照日期状态，不代表最新收盘市场。"
                )
                lines.extend([f"> {stale_msg}", ""])
                warnings.append(
                    f"CROWD快照数据陈旧：as_of={as_of}，"
                    f"最近交易日={expected_td.isoformat()}，滞后{stale_days}个交易日"
                )
        except (ValueError, TypeError):
            pass

    lines.extend(
        [
            "> 看板读数只证明二级市场相对强弱，不自动证明产业供需、盈利或资金因果关系。",
            "",
        ]
    )

    raw_baskets = snapshot.get("baskets")
    if isinstance(raw_baskets, dict):
        baskets = list(raw_baskets.items())
    elif isinstance(raw_baskets, list):
        baskets = []
        for index, raw_basket in enumerate(raw_baskets):
            basket = raw_basket if isinstance(raw_basket, dict) else {}
            code = (
                basket.get("code")
                or basket.get("name")
                or basket.get("label")
                or f"basket_{index + 1}"
            )
            baskets.append((str(code), basket))
    else:
        baskets = []

    if not baskets:
        warnings.append("CROWD快照缺少baskets对象或数组")
        lines.extend(["> [不确定] 快照中没有可渲染的篮子数据。", ""])
        return lines

    lines.extend(
        [
            "| 篮子 | 比值 | 20日动量 | 60日动量 | z-score |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for code, raw_basket in baskets:
        basket = raw_basket if isinstance(raw_basket, dict) else {}
        label = basket.get("label") or code
        lines.append(
            "| {label} | {ratio} | {m20} | {m60} | {z} |".format(
                label=table_text(label),
                ratio=display_number(basket.get("ratio")),
                m20=display_number(basket.get("momentum_20d"), "%"),
                m60=display_number(basket.get("momentum_60d"), "%"),
                z=display_number(basket.get("z_score")),
            )
        )
    lines.append("")
    return lines


def render_status_summary(events: list[Event], as_of: date) -> list[str]:
    counts = Counter(event.status for event in events)
    lines = ["## 二、事件状态总览", ""]
    lines.append(
        "- "
        + "；".join(
            f"**{label}**：{counts.get(status, 0)}"
            for status, label in STATUS_LABELS.items()
        )
    )
    lines.extend(
        [
            "",
            "| 日期 | 倒计时 | 事件 | 类别 | 状态 | 下一节点 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for event in events:
        lines.append(
            "| {date} | {countdown} | {name} | {category} | {status} | {next_check} |".format(
                date=event.event_date.isoformat(),
                countdown=countdown_text(event.event_date, as_of),
                name=table_text(event.name),
                category=table_text(category_label(event.category)),
                status=f"{STATUS_ICONS[event.status]} {status_label(event.status)}",
                next_check=table_text(event.next_check),
            )
        )
    lines.append("")
    return lines


def render_evidence(evidence: list[Evidence]) -> list[str]:
    if not evidence:
        return ["> 证据：N/A", ""]

    lines = [
        "| 来源 | 等级 | 发布日期 | 数据期 | 支持命题 |",
        "|---|---|---|---|---|",
    ]
    for item in evidence:
        source = table_text(item.title)
        if item.url:
            source = f"[{source}]({item.url})"
        lines.append(
            f"| {source} | {table_text(item.source_tier)} | "
            f"{table_text(item.published_at)} | {table_text(item.data_period)} | "
            f"{table_text(item.claim)} |"
        )
    lines.append("")
    return lines


def render_subevents(subevents: list[dict[str, Any]]) -> list[str]:
    if not subevents:
        return []

    lines = [
        "**子事件状态**：",
        "",
        "| 子事件 | 状态 |",
        "|---|---|",
    ]
    for item in subevents:
        status = normalize_status(item.get("status")) or "pending"
        lines.append(
            f"| {table_text(item.get('name'))} | "
            f"{STATUS_ICONS[status]} {status_label(status)} |"
        )
    lines.append("")
    return lines


def render_event_details(events: list[Event]) -> list[str]:
    lines = ["## 三、已发生或正在验证的事件", ""]
    active_events = [event for event in events if event.status != "pending"]
    if not active_events:
        lines.extend(["> 今日没有已发生或正在验证的事件。", ""])
        return lines

    for event in active_events:
        lines.extend(
            [
                f"### {STATUS_ICONS[event.status]} {event.event_date.isoformat()} | {event.name}",
                "",
                f"- **类别**：{category_label(event.category)}",
                f"- **状态**：{status_label(event.status)}",
                f"- **事实标签**：{event_fact_label(event)}",
                "",
                "**验证假设**：",
                "",
                f"> {event.hypothesis or 'N/A'}",
                "",
                "**实际结果**：",
                "",
                f"> {event.actual_result or 'N/A'}",
                "",
            ]
        )
        if event.note:
            lines.extend(["**备注**：", "", f"> {event.note}", ""])
        lines.extend(render_subevents(event.subevents))
        lines.extend(render_evidence(event.evidence))
        if event.invalidation:
            lines.extend([f"- **证伪条件**：{event.invalidation}", ""])
    return lines


def render_pending_events(events: list[Event], as_of: date) -> list[str]:
    lines = ["## 四、待确认事件", ""]
    pending = [event for event in events if event.status == "pending"]
    if not pending:
        lines.extend(["> 当前没有待确认事件。", ""])
        return lines

    lines.extend(
        [
            "| 日期 | 倒计时 | 事件 | 验证假设 | 备注 |",
            "|---|---|---|---|---|",
        ]
    )
    for event in pending:
        lines.append(
            f"| {event.event_date.isoformat()} | {countdown_text(event.event_date, as_of)} | "
            f"{table_text(event.name)} | {table_text(event.hypothesis)} | "
            f"{table_text(event.note)} |"
        )
    lines.append("")
    return lines


def collect_cycle_updates(
    events: list[Event], analysis: dict[str, Any] | None
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []

    if analysis:
        raw_updates = analysis.get("cycle_updates", [])
        if isinstance(raw_updates, dict):
            for segment, value in raw_updates.items():
                item = dict(value) if isinstance(value, dict) else {"reasoning": value}
                item.setdefault("segment", segment)
                updates.append(item)
        elif isinstance(raw_updates, list):
            updates.extend(item for item in raw_updates if isinstance(item, dict))

    for event in events:
        if not event.impact:
            continue
        item = dict(event.impact)
        item.setdefault("segment", category_label(event.category))
        item.setdefault("source_event", event.name)
        updates.append(item)
    return updates


def render_cycle_updates(
    events: list[Event], analysis: dict[str, Any] | None
) -> list[str]:
    lines = ["## 五、受影响环节的多周期更新", ""]
    updates = collect_cycle_updates(events, analysis)
    if not updates:
        lines.extend(
            [
                "> [不确定] 未提供结构化impact或cycle_updates。"
                "本脚本不会根据事件名称自动编造产业结论。",
                "",
            ]
        )
        return lines

    lines.extend(
        [
            "| 环节 | 技术周期 | 供需周期 | 盈利周期 | 二级市场周期 | "
            "一阶导 | 二阶导 | 置信度 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in updates:
        values = {key: item.get(key) for key, _ in CYCLE_FIELDS}
        lines.append(
            "| {segment} | {technology} | {supply} | {earnings} | {market} | "
            "{first} | {second} | {confidence} |".format(
                segment=table_text(item.get("segment")),
                technology=table_text(values["technology_cycle"]),
                supply=table_text(values["supply_demand_cycle"]),
                earnings=table_text(values["earnings_cycle"]),
                market=table_text(values["market_pricing_cycle"]),
                first=table_text(item.get("first_derivative")),
                second=table_text(item.get("second_derivative")),
                confidence=table_text(item.get("confidence")),
            )
        )
    lines.append("")

    for item in updates:
        reasoning = item.get("reasoning") or item.get("judgment")
        if reasoning:
            label = item.get("fact_label") or "[推断]"
            lines.append(
                f"- {table_text(label)} **{table_text(item.get('segment'))}**："
                f"{table_text(reasoning)}"
            )
    if lines[-1] != "":
        lines.append("")
    return lines


def render_scenarios(
    analysis: dict[str, Any] | None, warnings: list[str]
) -> list[str]:
    lines = ["## 六、情景变化与证伪条件", ""]
    if not analysis:
        lines.extend(["> 本次没有结构化情景输入；不生成固定情景或概率。", ""])
        return lines

    raw_scenarios = analysis.get("scenarios", [])
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        lines.extend(["> 本次没有需要更新的情景。", ""])
        return lines

    global_method = str(analysis.get("probability_method") or "").strip()
    lines.extend(
        [
            "| 情景 | 权重/概率 | 触发条件 | 产业结果 | 二级市场含义 | 证伪条件 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for index, scenario in enumerate(raw_scenarios):
        if not isinstance(scenario, dict):
            continue
        probability = scenario.get("probability")
        method = str(scenario.get("probability_method") or global_method).strip()
        if probability is not None and not method:
            warnings.append(
                f"情景“{scenario.get('name', index + 1)}”提供了概率但没有概率方法，"
                "报告已省略该概率"
            )
            probability_text = table_text(scenario.get("weight") or "定性")
        elif probability is not None:
            probability_text = f"{table_text(probability)}（方法：{table_text(method)}）"
        else:
            probability_text = table_text(scenario.get("weight") or "定性")

        lines.append(
            f"| {table_text(scenario.get('name') or f'情景{index + 1}')} | "
            f"{probability_text} | {table_text(scenario.get('trigger'))} | "
            f"{table_text(scenario.get('industry_result') or scenario.get('result'))} | "
            f"{table_text(scenario.get('market_implication'))} | "
            f"{table_text(scenario.get('invalidation'))} |"
        )
    lines.append("")
    return lines


def render_monitoring(analysis: dict[str, Any] | None) -> list[str]:
    lines = ["## 七、下一验证节点", ""]
    monitoring = analysis.get("monitoring", []) if analysis else []
    if not isinstance(monitoring, list) or not monitoring:
        lines.extend(["> N/A", ""])
        return lines

    lines.extend(
        [
            "| 指标/事件 | 当前值 | 方向 | 下一次更新 | 确认什么 | 证伪什么 |",
            "|---|---:|---|---|---|---|",
        ]
    )
    for item in monitoring:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| {table_text(item.get('name'))} | {table_text(item.get('current_value'))} | "
            f"{direction_text(item.get('direction'))} | "
            f"{table_text(item.get('next_update'))} | "
            f"{table_text(item.get('confirms'))} | "
            f"{table_text(item.get('invalidates'))} |"
        )
    lines.append("")
    return lines


def filter_events(
    events: list[Event], as_of: date, lookback_days: int, lookahead_days: int
) -> list[Event]:
    start = as_of - timedelta(days=lookback_days)
    end = as_of + timedelta(days=lookahead_days)
    return [event for event in events if start <= event.event_date <= end]


def build_report(
    calendar: dict[str, Any],
    snapshot: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    *,
    as_of: date,
    calendar_path: pathlib.Path,
    snapshot_path: pathlib.Path | None,
    analysis_path: pathlib.Path | None,
    lookback_days: int,
    lookahead_days: int,
) -> tuple[str, list[str], list[Event]]:
    warnings: list[str] = []
    all_events = parse_events(calendar, warnings)
    events = filter_events(all_events, as_of, lookback_days, lookahead_days)

    if len(events) != len(all_events):
        warnings.append(
            f"事件窗口仅展示{len(events)}/{len(all_events)}项；"
            f"范围为回看{lookback_days}天、前瞻{lookahead_days}天"
        )
    if snapshot_path is None:
        warnings.append("未找到CROWD结构化快照")
    if analysis_path is None:
        warnings.append("未找到AI产业链结构化分析文件")

    generated_at = now_in_timezone()
    status_counts = Counter(event.status for event in events)
    lines = [
        "# CROWD硬件链每日事件影响报告",
        "",
        f"> 生成时间：{generated_at.isoformat(timespec='seconds')}",
        f"> 数据日期：{as_of.isoformat()}",
        f"> 事件来源：{calendar_path}",
        f"> CROWD快照：{snapshot_path or 'N/A'}",
        f"> 结构化分析：{analysis_path or 'N/A'}",
        "> 定位：人眼研究监视器，不产生机械信号",
        "",
        "---",
        "",
        "## 执行摘要",
        "",
        f"- **窗口内事件**：{len(events)}个",
        f"- **已验证**：{status_counts.get('verified', 0)}个",
        f"- **部分验证**：{status_counts.get('partially_verified', 0)}个",
        f"- **待确认**：{status_counts.get('pending', 0)}个",
        f"- **来源冲突/验证失败**："
        f"{status_counts.get('conflicted', 0) + status_counts.get('verification_failed', 0)}个",
        "",
        "> 本脚本不内置产业结论。没有结构化证据或分析输入时，对应结论保持N/A。",
        "",
    ]

    lines.extend(render_snapshot(snapshot, warnings, report_date=as_of))
    lines.extend(render_status_summary(events, as_of))
    lines.extend(render_event_details(events))
    lines.extend(render_pending_events(events, as_of))
    lines.extend(render_cycle_updates(events, analysis))
    lines.extend(render_scenarios(analysis, warnings))
    lines.extend(render_monitoring(analysis))

    lines.extend(["## 八、数据质量与执行警告", ""])
    if warnings:
        lines.extend(f"- [不确定] {warning}" for warning in warnings)
    else:
        lines.append("- [已验证] 本次结构检查未发现警告。")

    lines.extend(
        [
            "",
            "## 九、免责声明",
            "",
            "> 本报告为人眼研究监视器输出，仅供人工判断参考。",
            "> 不产生机械信号，不接入ABCD、ABCDS或其他交易状态机。",
            "> 不执行证券交易，不构成投资建议。",
            "",
        ]
    )
    return "\n".join(lines), warnings, events


def atomic_write(path: pathlib.Path, content: str, *, no_overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if no_overwrite and path.exists():
        raise FileExistsError(f"Output already exists and --no-overwrite was set: {path}")

    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a CROWD daily event report without hard-coded conclusions."
    )
    parser.add_argument(
        "--calendar",
        type=pathlib.Path,
        default=DEFAULT_CALENDAR_PATH,
        help=f"Calendar JSON path (default: {DEFAULT_CALENDAR_PATH})",
    )
    parser.add_argument(
        "--snapshot",
        type=pathlib.Path,
        default=None,
        help="Optional CROWD snapshot JSON path",
    )
    parser.add_argument(
        "--analysis",
        type=pathlib.Path,
        default=None,
        help="Optional AI industry analysis JSON path",
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        default=OUTPUT_DIR,
        help=f"Report output directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=None,
        help="Explicit output file path; overrides --output-dir",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Report date in YYYY-MM-DD; default is current Asia/Shanghai date",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Include events this many days before the report date (default: 30)",
    )
    parser.add_argument(
        "--lookahead-days",
        type=int,
        default=60,
        help="Include events this many days after the report date (default: 60)",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Fail if the target report already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the report to stdout instead of writing a file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.lookback_days < 0 or args.lookahead_days < 0:
        parser.error("--lookback-days and --lookahead-days must be non-negative")

    as_of = (
        parse_iso_date(args.as_of, field_name="--as-of")
        if args.as_of
        else now_in_timezone().date()
    )

    snapshot_path = resolve_optional_path(
        args.snapshot,
        [
            DEFAULT_SNAPSHOT_PATH,
            ROOT / "data" / "latest_snapshot.json",
        ],
    )
    analysis_path = resolve_optional_path(
        args.analysis,
        [
            ROOT / "data" / f"analysis_{as_of.strftime('%Y%m%d')}.json",
            DEFAULT_ANALYSIS_PATH,
        ],
    )

    try:
        calendar = load_json(args.calendar, required=True)
        snapshot = (
            load_json(snapshot_path, required=False) if snapshot_path is not None else None
        )
        analysis = (
            load_json(analysis_path, required=False) if analysis_path is not None else None
        )
        assert calendar is not None

        report, warnings, events = build_report(
            calendar,
            snapshot,
            analysis,
            as_of=as_of,
            calendar_path=args.calendar,
            snapshot_path=snapshot_path,
            analysis_path=analysis_path,
            lookback_days=args.lookback_days,
            lookahead_days=args.lookahead_days,
        )

        if args.dry_run:
            print(report)
            return 0

        output_path = args.output or (
            args.output_dir / f"CROWD_Event_Report_{as_of.strftime('%Y%m%d')}.md"
        )
        atomic_write(output_path, report, no_overwrite=args.no_overwrite)

        print(f"[report] Generated: {output_path}")
        print(f"[report] Events rendered: {len(events)}")
        print(f"[report] Warnings: {len(warnings)}")
        return 0

    except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        print(f"[report] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
