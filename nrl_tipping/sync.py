from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from nrl_tipping.config import DATA_DIR, DEFAULT_REGION, NRL_SPORT_KEY, ODDS_API_BASE_URL, get_odds_api_key
from nrl_tipping.queries import apply_automatic_underdog_tips
from nrl_tipping.scoring import recalculate_tip_scores
from nrl_tipping.utils import parse_iso_datetime, sydney_now, sydney_now_iso

try:
    import requests
except Exception:
    requests = None


@dataclass
class SyncPayload:
    source: str
    events: list[dict[str, Any]]
    details: dict[str, Any]


NRL_DRAW_URL = "https://www.nrl.com/draw/"
TELSTRA_PREMIERSHIP_COMPETITION_ID = 111
TELSTRA_PREMIERSHIP_MAX_ROUND = 27


def _http_get_json(path: str, params: dict[str, Any]) -> tuple[Any, dict[str, str]]:
    query = urlencode(params)
    url = f"{ODDS_API_BASE_URL}{path}?{query}"
    request = Request(url, headers={"User-Agent": "NRL-Tipping-App/1.0"})
    try:
        with urlopen(request, timeout=45) as response:
            body = response.read().decode("utf-8")
            headers = {k.lower(): v for k, v in response.headers.items()}
            return json.loads(body), headers
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Odds API HTTP {exc.code}: {body[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Odds API connection error: {exc.reason}") from exc


def _http_get_text(url: str) -> str:
    if requests is not None:
        try:
            response = requests.get(url, headers={"User-Agent": "NRL-Tipping-App/1.0"}, timeout=45)
            response.raise_for_status()
            return response.text
        except Exception:
            pass

    request = Request(url, headers={"User-Agent": "NRL-Tipping-App/1.0"})
    try:
        with urlopen(request, timeout=45) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"NRL draw HTTP {exc.code}: {body[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"NRL draw connection error: {exc.reason}") from exc


def _normalize_name_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _team_name_matches(odds_name: str, draw_name: str) -> bool:
    odds_token = _normalize_name_token(odds_name)
    draw_token = _normalize_name_token(draw_name)
    if not odds_token or not draw_token:
        return False
    return draw_token in odds_token or odds_token in draw_token


def _build_theme_logo_url(theme: dict[str, Any] | None) -> str | None:
    if not isinstance(theme, dict):
        return None
    key = theme.get("key")
    logos = theme.get("logos")
    if not key or not isinstance(logos, dict):
        return None
    for filename in ("badge.svg", "badge-light.svg", "badge.png", "badge-light.png"):
        bust = logos.get(filename)
        if bust:
            return f"https://www.nrl.com/.theme/{key}/{filename}?bust={bust}"
    return None


def _parse_round_number(round_title: str | None) -> int | None:
    if not round_title:
        return None
    match = re.search(r"(\d+)", round_title)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_draw_qdata(page_html: str) -> dict[str, Any]:
    match = re.search(r'id="vue-draw"[^>]*\bq-data="(.*?)"', page_html, re.S)
    if not match:
        return {}
    raw = html.unescape(match.group(1))
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _fetch_nrl_draw_schedule(season_year: int) -> list[dict[str, Any]]:
    def fetch_round(round_number: int) -> dict[str, Any]:
        query = urlencode(
            {
                "competition": TELSTRA_PREMIERSHIP_COMPETITION_ID,
                "round": round_number,
                "season": season_year,
            }
        )
        page = _http_get_text(f"{NRL_DRAW_URL}?{query}")
        return _extract_draw_qdata(page)

    first_round_data = fetch_round(1)
    round_values = [1]
    filter_rounds = first_round_data.get("filterRounds")
    if isinstance(filter_rounds, list):
        parsed_rounds = []
        for item in filter_rounds:
            if isinstance(item, dict) and isinstance(item.get("value"), int):
                parsed_rounds.append(int(item["value"]))
        if parsed_rounds:
            round_values = sorted(
                {
                    value
                    for value in parsed_rounds
                    if 1 <= value <= TELSTRA_PREMIERSHIP_MAX_ROUND
                }
            )

    if not round_values:
        round_values = list(range(1, TELSTRA_PREMIERSHIP_MAX_ROUND + 1))

    schedules: list[dict[str, Any]] = []
    round_payloads: dict[int, dict[str, Any]] = {1: first_round_data}
    for round_number in round_values:
        if round_number not in round_payloads:
            round_payloads[round_number] = fetch_round(round_number)
        data = round_payloads.get(round_number, {})
        fixtures = data.get("fixtures")
        if not isinstance(fixtures, list):
            continue
        for fixture in fixtures:
            if not isinstance(fixture, dict):
                continue
            if fixture.get("type") != "Match":
                continue
            match_centre_url = str(fixture.get("matchCentreUrl") or "")
            if "/draw/nrl-premiership/" not in match_centre_url:
                continue
            home_team = fixture.get("homeTeam") or {}
            away_team = fixture.get("awayTeam") or {}
            clock = fixture.get("clock") or {}
            kickoff = clock.get("kickOffTimeLong")
            if not kickoff:
                continue

            schedules.append(
                {
                    "round_number": round_number,
                    "home_name": str(home_team.get("nickName") or ""),
                    "away_name": str(away_team.get("nickName") or ""),
                    "kickoff_utc": str(kickoff),
                    "stadium_name": str(fixture.get("venue") or "").strip() or None,
                    "stadium_city": str(fixture.get("venueCity") or "").strip() or None,
                    "home_logo_url": _build_theme_logo_url(home_team.get("theme")),
                    "away_logo_url": _build_theme_logo_url(away_team.get("theme")),
                    "match_centre_url": match_centre_url,
                }
            )
    return schedules


def _kickoff_within_hours(start_a: str, start_b: str, hours: float) -> bool:
    try:
        a = parse_iso_datetime(start_a)
        b = parse_iso_datetime(start_b)
    except Exception:
        return False
    return abs((a - b).total_seconds()) <= hours * 3600


def _draw_event_id(season_year: int, draw: dict[str, Any]) -> str:
    round_number = int(draw.get("round_number") or 0)
    home_token = _normalize_name_token(str(draw.get("home_name") or "")) or "home"
    away_token = _normalize_name_token(str(draw.get("away_name") or "")) or "away"
    kickoff = str(draw.get("kickoff_utc") or "")
    kickoff_token = re.sub(r"[^0-9TZ:+-]", "", kickoff)
    return f"draw:{season_year}:r{round_number}:{home_token}:vs:{away_token}:{kickoff_token}"


def _apply_nrl_draw_fallback(
    fixtures: dict[str, dict[str, Any]],
    season_year: int,
) -> dict[str, int]:
    try:
        draw_fixtures = _fetch_nrl_draw_schedule(season_year)
    except Exception:
        return {
            "draw_fixtures_loaded": 0,
            "fixtures_enriched": 0,
            "fixtures_filtered_out": 0,
        }

    draw_fixtures = [
        draw
        for draw in draw_fixtures
        if 1 <= int(draw.get("round_number") or 0) <= TELSTRA_PREMIERSHIP_MAX_ROUND
    ]

    enriched = 0
    filtered_out = 0
    added = 0
    matched_draw_indexes: set[int] = set()
    remove_ids: list[str] = []
    for event_id, fixture in fixtures.items():
        if int(fixture.get("season_year") or 0) != season_year:
            continue
        best_match = None
        best_match_idx = None
        best_delta = None
        for draw_idx, draw in enumerate(draw_fixtures):
            if not _team_name_matches(fixture["home_team"], draw["home_name"]):
                continue
            if not _team_name_matches(fixture["away_team"], draw["away_name"]):
                continue
            if not _kickoff_within_hours(fixture["start_time_utc"], draw["kickoff_utc"], hours=36):
                continue
            delta = abs(
                (
                    parse_iso_datetime(fixture["start_time_utc"])
                    - parse_iso_datetime(draw["kickoff_utc"])
                ).total_seconds()
            )
            if best_match is None or delta < (best_delta or float("inf")):
                best_match = draw
                best_match_idx = draw_idx
                best_delta = delta
        if not best_match:
            remove_ids.append(event_id)
            filtered_out += 1
            continue

        fixture["start_time_utc"] = best_match["kickoff_utc"]
        fixture["round_number"] = best_match["round_number"]
        if best_match.get("stadium_name"):
            fixture["stadium_name"] = best_match.get("stadium_name")
        if best_match.get("stadium_city"):
            fixture["stadium_city"] = best_match.get("stadium_city")
        fixture["home_logo_url"] = best_match["home_logo_url"]
        fixture["away_logo_url"] = best_match["away_logo_url"]
        if best_match_idx is not None:
            matched_draw_indexes.add(best_match_idx)
        enriched += 1

    for event_id in remove_ids:
        fixtures.pop(event_id, None)

    # Add any official draw fixtures that were not found in Odds API payloads.
    # This ensures all regular-season rounds are available for tipping.
    for draw_idx, draw in enumerate(draw_fixtures):
        if draw_idx in matched_draw_indexes:
            continue
        home_name = str(draw.get("home_name") or "").strip()
        away_name = str(draw.get("away_name") or "").strip()
        kickoff_utc = str(draw.get("kickoff_utc") or "").strip()
        round_number = int(draw.get("round_number") or 0)
        if not home_name or not away_name or not kickoff_utc or not (1 <= round_number <= TELSTRA_PREMIERSHIP_MAX_ROUND):
            continue

        event_id = _draw_event_id(season_year, draw)
        if event_id in fixtures:
            continue

        fixtures[event_id] = {
            "source": "nrl_draw",
            "odds_event_id": event_id,
            "start_time_utc": kickoff_utc,
            "season_year": season_year,
            "home_team": home_name,
            "away_team": away_name,
            "stadium_name": draw.get("stadium_name"),
            "stadium_city": draw.get("stadium_city"),
            "home_logo_url": draw.get("home_logo_url"),
            "away_logo_url": draw.get("away_logo_url"),
            "round_number": round_number,
            "status": "scheduled",
            "home_score": None,
            "away_score": None,
            "winner": None,
            "home_price": None,
            "away_price": None,
            "raw_json": json.dumps({"source": "nrl_draw", "draw_fixture": draw}, separators=(",", ":")),
        }
        added += 1

    return {
        "draw_fixtures_loaded": len(draw_fixtures),
        "fixtures_enriched": enriched,
        "fixtures_filtered_out": filtered_out,
        "draw_fixtures_added": added,
    }


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "events", "odds"):
            raw = payload.get(key)
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
    return []


def _extract_h2h_prices(event: dict[str, Any]) -> tuple[float | None, float | None]:
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    bookmakers = event.get("bookmakers") or []
    if not home_team or not away_team:
        return None, None

    for bookmaker in bookmakers:
        markets = bookmaker.get("markets") or []
        for market in markets:
            if market.get("key") != "h2h":
                continue
            outcomes = market.get("outcomes") or []
            prices = {outcome.get("name"): outcome.get("price") for outcome in outcomes}
            home_price = prices.get(home_team)
            away_price = prices.get(away_team)
            if home_price is not None and away_price is not None:
                try:
                    return float(home_price), float(away_price)
                except (TypeError, ValueError):
                    return None, None
    return None, None


def _extract_scores(event: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    scores = event.get("scores")
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    if not isinstance(scores, list) or not home_team or not away_team:
        return None, None, None

    score_map: dict[str, int] = {}
    for row in scores:
        name = row.get("name")
        value = row.get("score")
        if name is None or value is None:
            continue
        try:
            score_map[str(name)] = int(value)
        except (TypeError, ValueError):
            continue

    if home_team not in score_map or away_team not in score_map:
        return None, None, None

    home_score = score_map[home_team]
    away_score = score_map[away_team]
    if home_score > away_score:
        winner = home_team
    elif away_score > home_score:
        winner = away_team
    else:
        winner = "draw"
    return home_score, away_score, winner


def _normalize_event(source: str, event: dict[str, Any]) -> dict[str, Any] | None:
    event_id = event.get("id")
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    commence = event.get("commence_time")
    if not event_id or not home_team or not away_team or not commence:
        return None

    home_price, away_price = _extract_h2h_prices(event)
    home_score, away_score, winner = _extract_scores(event)

    completed_flag = bool(event.get("completed"))
    status = "completed" if completed_flag or winner is not None else "scheduled"
    if status == "completed" and winner is None:
        # Completed without parseable scores still should show as final.
        winner = "unknown"

    stadium_name = (
        event.get("stadium_name")
        or event.get("venue_name")
        or event.get("venue")
        or event.get("stadium")
    )
    stadium_city = event.get("stadium_city") or event.get("venue_city") or event.get("city")

    return {
        "source": source,
        "odds_event_id": str(event_id),
        "start_time_utc": str(commence),
        "season_year": parse_iso_datetime(str(commence)).year,
        "home_team": str(home_team),
        "away_team": str(away_team),
        "stadium_name": str(stadium_name).strip() if stadium_name else None,
        "stadium_city": str(stadium_city).strip() if stadium_city else None,
        "home_logo_url": None,
        "away_logo_url": None,
        "round_number": None,
        "status": status,
        "home_score": home_score,
        "away_score": away_score,
        "winner": winner,
        "home_price": home_price,
        "away_price": away_price,
        "raw_json": json.dumps(event, separators=(",", ":")),
    }


def _merge_fixture(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)

    # Keep the earliest known kickoff if values differ unexpectedly.
    try:
        existing_time = parse_iso_datetime(existing["start_time_utc"])
        incoming_time = parse_iso_datetime(incoming["start_time_utc"])
        merged["start_time_utc"] = min(existing_time, incoming_time).isoformat()
    except Exception:
        merged["start_time_utc"] = incoming["start_time_utc"]

    for key in ("home_team", "away_team"):
        merged[key] = incoming.get(key) or merged.get(key)
    for key in ("stadium_name", "stadium_city"):
        merged[key] = incoming.get(key) or merged.get(key)
    merged["season_year"] = incoming.get("season_year") or merged.get("season_year")
    for key in ("home_logo_url", "away_logo_url", "round_number"):
        if merged.get(key) is None and incoming.get(key) is not None:
            merged[key] = incoming.get(key)

    for key in ("home_price", "away_price"):
        if merged.get(key) is None and incoming.get(key) is not None:
            merged[key] = incoming[key]

    if incoming.get("status") == "completed":
        merged["status"] = "completed"
    elif merged.get("status") is None:
        merged["status"] = incoming.get("status", "scheduled")

    for key in ("home_score", "away_score", "winner"):
        if incoming.get(key) is not None and incoming.get(key) != "unknown":
            merged[key] = incoming[key]

    merged["raw_json"] = incoming.get("raw_json") or merged.get("raw_json")
    merged["source"] = incoming.get("source") or merged.get("source")
    return merged


def _fetch_upcoming(api_key: str) -> SyncPayload:
    payload, headers = _http_get_json(
        f"/sports/{NRL_SPORT_KEY}/odds/",
        {
            "apiKey": api_key,
            "regions": DEFAULT_REGION,
            "markets": "h2h",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        },
    )
    events = _extract_events(payload)
    details = {
        "remaining_credits": headers.get("x-requests-remaining"),
        "source_endpoint": "odds",
    }
    return SyncPayload(source="upcoming_odds", events=events, details=details)


def _fetch_scores(api_key: str, days_back: int) -> SyncPayload:
    requested = max(1, int(days_back))
    candidates = [requested, 30, 14, 7, 3, 1]
    deduped_candidates: list[int] = []
    for value in candidates:
        if value not in deduped_candidates:
            deduped_candidates.append(value)

    last_error = None
    for candidate in deduped_candidates:
        try:
            payload, headers = _http_get_json(
                f"/sports/{NRL_SPORT_KEY}/scores/",
                {
                    "apiKey": api_key,
                    "daysFrom": candidate,
                    "dateFormat": "iso",
                },
            )
            events = _extract_events(payload)
            details = {
                "remaining_credits": headers.get("x-requests-remaining"),
                "days_back_requested": requested,
                "days_back_used": candidate,
                "source_endpoint": "scores",
            }
            return SyncPayload(source="scores", events=events, details=details)
        except RuntimeError as exc:
            last_error = exc
            text = str(exc)
            if "INVALID_SCORES_DAYS_FROM" in text or "HTTP 422" in text:
                continue
            raise

    # Do not fail the full season sync if scores endpoint rejects all daysFrom values.
    # We can still sync fixtures from odds + official NRL draw fallback.
    return SyncPayload(
        source="scores",
        events=[],
        details={
            "source_endpoint": "scores",
            "days_back_requested": requested,
            "days_back_used": None,
            "warning": (
                "Scores endpoint unavailable for requested daysFrom values. "
                f"Tried {deduped_candidates}. Last error: {last_error}"
            ),
        },
    )


def _fetch_history_snapshots(
    api_key: str,
    season_year: int,
    start_month: int = 3,
    end_month: int = 11,
    step_days: int = 7,
) -> SyncPayload:
    # NRL season is typically Mar-Oct; end_month is exclusive upper bound.
    start = datetime(season_year, start_month, 1, 12, tzinfo=timezone.utc)
    end = datetime(season_year, end_month, 1, 12, tzinfo=timezone.utc)
    cursor = start
    events: list[dict[str, Any]] = []
    successful_snapshots = 0
    attempted_snapshots = 0
    tried_endpoints: list[str] = []

    candidate_paths = (
        f"/sports/{NRL_SPORT_KEY}/odds-history/",
        f"/historical/sports/{NRL_SPORT_KEY}/odds/",
    )

    while cursor < end:
        attempted_snapshots += 1
        date_param = cursor.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = None

        for path in candidate_paths:
            if path not in tried_endpoints:
                tried_endpoints.append(path)
            try:
                payload, _ = _http_get_json(
                    path,
                    {
                        "apiKey": api_key,
                        "regions": DEFAULT_REGION,
                        "markets": "h2h",
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                        "date": date_param,
                    },
                )
                break
            except RuntimeError as exc:
                # Try the alternate path for compatibility across plan/account versions.
                if "HTTP 404" in str(exc):
                    continue
                raise

        if payload is not None:
            slice_events = _extract_events(payload)
            events.extend(slice_events)
            successful_snapshots += 1

        cursor += timedelta(days=step_days)

    return SyncPayload(
        source="historical_odds",
        events=events,
        details={
            "attempted_snapshots": attempted_snapshots,
            "successful_snapshots": successful_snapshots,
            "candidate_endpoints": tried_endpoints,
            "step_days": step_days,
            "season_year": season_year,
        },
    )


def assign_round_numbers(conn: sqlite3.Connection, new_round_gap_hours: int = 60) -> int:
    rows = conn.execute(
        """
        SELECT id, start_time_utc, season_year, round_number
        FROM fixtures
        ORDER BY season_year ASC, start_time_utc ASC
        """
    ).fetchall()
    if not rows:
        return 0

    last_start_by_season: dict[int, datetime] = {}
    round_by_season: dict[int, int] = {}
    updates = 0

    for row in rows:
        current_start = parse_iso_datetime(row["start_time_utc"])
        season_year = int(row["season_year"] or current_start.year)
        existing_round = row["round_number"]
        if existing_round is not None:
            round_value = int(existing_round)
            conn.execute(
                "UPDATE fixtures SET season_year = ?, round_number = ? WHERE id = ?",
                (season_year, round_value, row["id"]),
            )
            updates += 1
            last_start_by_season[season_year] = current_start
            round_by_season[season_year] = max(round_by_season.get(season_year, 1), round_value)
            continue

        prior_start = last_start_by_season.get(season_year)
        round_number = round_by_season.get(season_year, 1)
        if prior_start is not None:
            hours = (current_start - prior_start).total_seconds() / 3600
            if hours >= new_round_gap_hours:
                round_number += 1

        conn.execute(
            "UPDATE fixtures SET season_year = ?, round_number = ? WHERE id = ?",
            (season_year, round_number, row["id"]),
        )
        updates += 1
        last_start_by_season[season_year] = current_start
        round_by_season[season_year] = round_number

    conn.commit()
    return updates


def _save_raw_download(season_year: int, payload: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_DIR / f"nrl_season_{season_year}.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def update_completed_scores(
    conn: sqlite3.Connection,
    season_year: int | None = None,
    min_age_hours: float = 2.0,
    days_back: int | None = None,
) -> dict[str, Any]:
    api_key = get_odds_api_key()
    if not api_key:
        raise RuntimeError(
            "ODDS_API_KEY not found. Set ODDS_API_KEY or provide NFTeams .env path with ODDS_API_KEY."
        )

    target_year = season_year or sydney_now().year
    now_utc = sydney_now().astimezone(timezone.utc)
    age_hours = max(0.0, float(min_age_hours))
    cutoff_iso = (now_utc - timedelta(hours=age_hours)).isoformat()
    due_rows = conn.execute(
        """
        SELECT id, odds_event_id, start_time_utc
        FROM fixtures
        WHERE season_year = ?
          AND start_time_utc <= ?
          AND (
            status != 'completed'
            OR home_score IS NULL
            OR away_score IS NULL
            OR winner IS NULL
            OR winner = 'unknown'
          )
        ORDER BY start_time_utc ASC
        """,
        (target_year, cutoff_iso),
    ).fetchall()

    if not due_rows:
        return {
            "season_year": target_year,
            "pending_due_fixtures": 0,
            "api_completed_events": 0,
            "fixtures_updated": 0,
            "auto_underdog_tips_added": 0,
            "tips_rescored": 0,
            "days_back_requested": 0,
            "source_details": {},
        }

    try:
        oldest_due = parse_iso_datetime(str(due_rows[0]["start_time_utc"]))
        inferred_days_back = max(1, int((now_utc - oldest_due).total_seconds() // 86400) + 2)
    except Exception:
        inferred_days_back = 14
    requested_days_back = max(inferred_days_back, int(days_back or 1))

    pull = _fetch_scores(api_key, days_back=requested_days_back)
    completed_by_event: dict[str, dict[str, Any]] = {}
    for event in pull.events:
        normalized = _normalize_event("scores", event)
        if not normalized:
            continue
        if int(normalized.get("season_year") or 0) != target_year:
            continue
        if normalized.get("status") != "completed":
            continue
        if normalized.get("winner") in (None, "unknown"):
            continue
        event_id = str(normalized["odds_event_id"])
        existing = completed_by_event.get(event_id)
        completed_by_event[event_id] = _merge_fixture(existing, normalized) if existing else normalized

    now_iso = sydney_now_iso()
    updates = 0
    for row in due_rows:
        event = completed_by_event.get(str(row["odds_event_id"]))
        if not event:
            continue
        conn.execute(
            """
            UPDATE fixtures
            SET status = 'completed',
                home_score = ?,
                away_score = ?,
                winner = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                event.get("home_score"),
                event.get("away_score"),
                event.get("winner"),
                now_iso,
                int(row["id"]),
            ),
        )
        updates += 1
    if updates:
        conn.commit()

    auto_filled = apply_automatic_underdog_tips(conn, season_year=target_year, now=now_utc)
    rescored = recalculate_tip_scores(conn) if updates or auto_filled else 0
    return {
        "season_year": target_year,
        "pending_due_fixtures": len(due_rows),
        "api_completed_events": len(completed_by_event),
        "fixtures_updated": updates,
        "auto_underdog_tips_added": auto_filled,
        "tips_rescored": rescored,
        "days_back_requested": requested_days_back,
        "source_details": pull.details,
    }


def sync_nrl_season(
    conn: sqlite3.Connection,
    season_year: int | None = None,
    days_back: int = 30,
    prune_other_seasons: bool = True,
) -> dict[str, Any]:
    api_key = get_odds_api_key()
    if not api_key:
        raise RuntimeError(
            "ODDS_API_KEY not found. Set ODDS_API_KEY or provide NFTeams .env path with ODDS_API_KEY."
        )

    target_year = season_year or sydney_now().year
    pulls = [
        _fetch_upcoming(api_key),
        _fetch_scores(api_key, days_back=days_back),
        _fetch_history_snapshots(api_key, season_year=target_year),
    ]

    merged_events: dict[str, dict[str, Any]] = {}
    by_source_counts: dict[str, int] = {}

    for pull in pulls:
        by_source_counts[pull.source] = len(pull.events)
        for event in pull.events:
            normalized = _normalize_event(pull.source, event)
            if not normalized:
                continue
            event_id = normalized["odds_event_id"]
            existing = merged_events.get(event_id)
            merged_events[event_id] = (
                _merge_fixture(existing, normalized) if existing else normalized
            )

    draw_enrichment = _apply_nrl_draw_fallback(merged_events, target_year)

    inserted = 0
    updated = 0
    pruned = 0
    now_iso = sydney_now_iso()
    for fixture in merged_events.values():
        fixture["updated_at"] = now_iso
        existing = conn.execute(
            "SELECT id FROM fixtures WHERE odds_event_id = ?",
            (fixture["odds_event_id"],),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO fixtures(
                odds_event_id,
                start_time_utc,
                home_team,
                away_team,
                stadium_name,
                stadium_city,
                home_logo_url,
                away_logo_url,
                season_year,
                round_number,
                status,
                home_score,
                away_score,
                winner,
                home_price,
                away_price,
                raw_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(odds_event_id)
            DO UPDATE SET
                start_time_utc = excluded.start_time_utc,
                home_team = excluded.home_team,
                away_team = excluded.away_team,
                stadium_name = excluded.stadium_name,
                stadium_city = excluded.stadium_city,
                home_logo_url = excluded.home_logo_url,
                away_logo_url = excluded.away_logo_url,
                season_year = excluded.season_year,
                round_number = excluded.round_number,
                status = excluded.status,
                home_score = excluded.home_score,
                away_score = excluded.away_score,
                winner = excluded.winner,
                home_price = excluded.home_price,
                away_price = excluded.away_price,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                fixture["odds_event_id"],
                fixture["start_time_utc"],
                fixture["home_team"],
                fixture["away_team"],
                fixture.get("stadium_name"),
                fixture.get("stadium_city"),
                fixture.get("home_logo_url"),
                fixture.get("away_logo_url"),
                fixture["season_year"],
                fixture.get("round_number"),
                fixture["status"],
                fixture["home_score"],
                fixture["away_score"],
                fixture["winner"],
                fixture["home_price"],
                fixture["away_price"],
                fixture["raw_json"],
                fixture["updated_at"],
            ),
        )
        if existing:
            updated += 1
        else:
            inserted += 1

    conn.commit()

    if prune_other_seasons:
        cursor = conn.execute(
            "DELETE FROM fixtures WHERE season_year IS NOT NULL AND season_year != ?",
            (target_year,),
        )
        pruned = int(cursor.rowcount)
        conn.commit()

    rounds_assigned = assign_round_numbers(conn)
    auto_underdog_tips_added = apply_automatic_underdog_tips(conn, season_year=target_year)
    rescored = recalculate_tip_scores(conn)

    raw_payload = {
        "downloaded_at_utc": now_iso,
        "season_year": target_year,
        "sport_key": NRL_SPORT_KEY,
        "sources": [
            {"source": pull.source, "details": pull.details, "events": pull.events}
            for pull in pulls
        ],
        "merged_fixture_count": len(merged_events),
    }
    raw_file = _save_raw_download(target_year, raw_payload)

    summary = {
        "season_year": target_year,
        "inserted": inserted,
        "updated": updated,
        "total_merged": len(merged_events),
        "pruned_other_season_fixtures": pruned,
        "rounds_assigned": rounds_assigned,
        "auto_underdog_tips_added": auto_underdog_tips_added,
        "tips_rescored": rescored,
        "raw_download_file": str(raw_file),
        "by_source_counts": by_source_counts,
        "draw_enrichment": draw_enrichment,
    }
    return summary
