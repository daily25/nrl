from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    get_flashed_messages,
    make_response,
    redirect,
    request,
    send_from_directory,
    session,
    url_for,
)

from nrl_tipping import auth
from nrl_tipping.config import (
    AVATAR_UPLOAD_DIR,
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_NAME,
    DEFAULT_ADMIN_PASSWORD,
    MAX_AVATAR_BYTES,
    STATIC_DIR,
    TIP_LOCK_MINUTES,
    VAPID_PUBLIC_KEY,
    get_facebook_oauth_config,
)
from nrl_tipping.db import connect_db, get_setting, init_db, set_setting
from nrl_tipping.queries import (
    apply_automatic_underdog_tips,
    get_all_ladder_predictions,
    get_all_teams,
    get_completed_round_numbers,
    get_current_round,
    get_ladder,
    get_ladder_prediction_leaderboard,
    get_leaderboard_with_rounds,
    get_round_fixtures,
    get_round_numbers,
    get_round_tipsheet_data,
    get_user_adjustments,
    get_user_ladder_prediction,
    get_user_tips_for_round,
    is_season_started,
    save_ladder_adjustment,
    save_ladder_prediction,
    save_tips,
)
from nrl_tipping.score_worker import start_score_update_worker
from nrl_tipping.sync import sync_nrl_season
from nrl_tipping.notify_worker import start_notify_worker
from nrl_tipping.sync_worker import start_sync_worker
from nrl_tipping.utils import is_round_locked, is_tip_locked, sydney_now, sydney_now_iso
from nrl_tipping.views import (
    render_admin,
    render_admin_users,
    render_all_predictions,
    render_data_deletion,
    render_ladder,
    render_ladder_adjust,
    render_leaderboard,
    render_login,
    render_page,
    render_predict_ladder,
    render_privacy,
    render_profile,
    render_register,
    render_tips,
    render_tipsheet,
)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    static_url_path="/static",
)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_default_admin() -> None:
    conn = connect_db()
    try:
        init_db(conn)
        existing = auth.get_user_by_email(conn, DEFAULT_ADMIN_EMAIL)
        if existing is None:
            auth.create_user(
                conn,
                email=DEFAULT_ADMIN_EMAIL,
                display_name=DEFAULT_ADMIN_NAME,
                password=DEFAULT_ADMIN_PASSWORD,
                is_admin=True,
            )
            print(
                f"[bootstrap] Created admin user {DEFAULT_ADMIN_EMAIL} "
                f"with password {DEFAULT_ADMIN_PASSWORD}",
                file=sys.stderr,
            )
    finally:
        conn.close()


def _mask_value(value: str, keep_start: int = 4, keep_end: int = 2) -> str:
    text = value.strip()
    if not text:
        return ""
    if len(text) <= keep_start + keep_end:
        return "*" * len(text)
    return f"{text[:keep_start]}...{text[-keep_end:]}"


def _get_db():
    """Open a DB connection and stash it on Flask's ``g`` object."""
    if "db" not in g:
        g.db = connect_db()
        init_db(g.db)
    return g.db


def _current_user(conn):
    auth.purge_expired_sessions(conn)
    session_id = session.get("session_id")
    return auth.get_user_for_session(conn, session_id) if session_id else None


def _facebook_config() -> dict[str, str]:
    return get_facebook_oauth_config()


def _facebook_enabled() -> bool:
    config = _facebook_config()
    return bool(config["app_id"] and config["app_secret"])


def _facebook_picture_url(profile: dict) -> str | None:
    picture = profile.get("picture")
    if not isinstance(picture, dict):
        return None
    data = picture.get("data")
    if not isinstance(data, dict):
        return None
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    return url.strip()


def _image_extension(content_type: str | None, data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    lowered = (content_type or "").lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    return mapping.get(lowered)


def _delete_local_avatar(avatar_url: str | None) -> None:
    if not avatar_url or not avatar_url.startswith("/static/avatars/"):
        return
    relative = avatar_url[len("/static/"):]
    target = (STATIC_DIR / relative).resolve()
    static_root = STATIC_DIR.resolve()
    if static_root in target.parents and target.is_file():
        try:
            target.unlink()
        except OSError:
            pass


def _save_avatar_file(
    user_id: int,
    file_storage,
    current_avatar_url: str | None,
) -> str:
    blob = file_storage.read()
    if not blob:
        raise ValueError("Please choose an image file.")
    if len(blob) > MAX_AVATAR_BYTES:
        raise ValueError("Image is too large. Max size is 5 MB.")

    content_type = file_storage.content_type or ""
    extension = _image_extension(content_type, blob)
    if extension is None:
        raise ValueError("Unsupported image type. Use PNG, JPG, WEBP, or GIF.")

    AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"user_{user_id}_{secrets.token_hex(8)}{extension}"
    target = (AVATAR_UPLOAD_DIR / filename).resolve()
    avatar_root = AVATAR_UPLOAD_DIR.resolve()
    if avatar_root not in target.parents:
        raise ValueError("Invalid avatar path.")
    target.write_bytes(blob)
    _delete_local_avatar(current_avatar_url)
    return f"/static/avatars/{filename}"


def _read_json_url(url: str) -> dict:
    """Fetch a JSON URL and return the parsed dict."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": "NRL-Tipping-App/1.0"})
    try:
        with urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            return parsed if isinstance(parsed, dict) else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:240]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}") from exc


def _flash_msg():
    """Pull flash message from Flask flash or query-string fallback."""
    messages = get_flashed_messages(with_categories=True)
    if messages:
        kind, msg = messages[0]
        return msg, kind
    # Fallback: support ?msg=...&kind=... from old-style redirects
    msg = request.args.get("msg")
    kind = request.args.get("kind", "ok")
    return msg, kind


def login_required(f):
    """Decorator that redirects to /login if no user is logged in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        conn = _get_db()
        user = _current_user(conn)
        if not user:
            return redirect("/login")
        g.user = user
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator requiring an authenticated admin user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        conn = _get_db()
        user = _current_user(conn)
        if not user:
            return redirect("/login")
        if int(user["is_admin"]) != 1:
            flash("Admin access required.", "error")
            return redirect("/")
        g.user = user
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------


@app.teardown_appcontext
def _close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ---------------------------------------------------------------------------
# Routes — PWA assets served from root
# ---------------------------------------------------------------------------


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(
        str(STATIC_DIR), "manifest.webmanifest", mimetype="application/manifest+json"
    )


@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(
        str(STATIC_DIR),
        "service-worker.js",
        mimetype="application/javascript; charset=utf-8",
    )


@app.route("/offline.html")
def offline():
    return send_from_directory(
        str(STATIC_DIR), "offline.html", mimetype="text/html; charset=utf-8"
    )


# ---------------------------------------------------------------------------
# Routes — Health check
# ---------------------------------------------------------------------------


@app.route("/healthz")
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------


@app.route("/login", methods=["GET", "POST"])
def login():
    conn = _get_db()
    user = _current_user(conn)
    if user and request.method == "GET":
        return redirect("/")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        existing = auth.get_user_by_email(conn, email)
        if not existing or not auth.verify_password(password, existing["password_hash"]):
            html = render_page(
                "Login",
                render_login(
                    "Invalid email or password.",
                    facebook_enabled=_facebook_enabled(),
                ),
            )
            return make_response(html, 401)

        sid = auth.create_session(conn, int(existing["id"]))
        session["session_id"] = sid
        flash("Logged in", "ok")
        return redirect("/tips")

    # GET
    flash_msg, flash_kind = _flash_msg()
    html = render_page(
        "Login",
        render_login(facebook_enabled=_facebook_enabled()),
        flash=flash_msg,
        flash_kind=flash_kind,
    )
    return html


@app.route("/register", methods=["GET", "POST"])
def register():
    conn = _get_db()
    user = _current_user(conn)
    if user and request.method == "GET":
        return redirect("/")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")

        if not email or not display_name or len(password) < 8:
            html = render_page(
                "Register",
                render_register(
                    "All fields are required and password must be at least 8 characters.",
                    facebook_enabled=_facebook_enabled(),
                ),
            )
            return make_response(html, 400)

        if auth.get_user_by_email(conn, email):
            html = render_page(
                "Register",
                render_register(
                    "That email is already registered.",
                    facebook_enabled=_facebook_enabled(),
                ),
            )
            return make_response(html, 400)

        user_id = auth.create_user(conn, email, display_name, password, is_admin=False)
        sid = auth.create_session(conn, user_id)
        session["session_id"] = sid
        flash("Registration complete", "ok")
        return redirect("/tips")

    # GET
    flash_msg, flash_kind = _flash_msg()
    html = render_page(
        "Register",
        render_register(facebook_enabled=_facebook_enabled()),
        flash=flash_msg,
        flash_kind=flash_kind,
    )
    return html


@app.route("/logout", methods=["POST", "GET"])
def logout():
    conn = _get_db()
    sid = session.pop("session_id", None)
    if sid:
        auth.delete_session(conn, sid)
    flash("Logged out", "ok")
    return redirect("/login")


# ---------------------------------------------------------------------------
# Routes — Facebook OAuth
# ---------------------------------------------------------------------------


@app.route("/auth/facebook/start")
def facebook_start():
    conn = _get_db()
    user = _current_user(conn)
    if user:
        return redirect("/tips")

    facebook_config = _facebook_config()
    if not _facebook_enabled():
        flash("Facebook login is not configured yet.", "error")
        return redirect("/login")

    state = secrets.token_urlsafe(24)
    base_url = request.host_url.rstrip("/")
    redirect_uri = f"{base_url}/auth/facebook/callback"
    from urllib.parse import urlencode

    oauth_url = (
        f"https://www.facebook.com/{facebook_config['graph_version']}/dialog/oauth?"
        + urlencode(
            {
                "client_id": facebook_config["app_id"],
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": facebook_config["oauth_scopes"],
                "response_type": "code",
            }
        )
    )
    session["fb_oauth_state"] = state
    return redirect(oauth_url)


@app.route("/auth/facebook/callback")
def facebook_callback():
    conn = _get_db()
    user = _current_user(conn)
    if user:
        return redirect("/tips")

    from urllib.parse import urlencode

    state = request.args.get("state")
    code = request.args.get("code")
    oauth_state = session.pop("fb_oauth_state", None)

    if not state or not oauth_state or state != oauth_state or not code:
        flash("Facebook login failed. Please try again.", "error")
        return redirect("/login")

    if not _facebook_enabled():
        flash("Facebook login is not configured.", "error")
        return redirect("/login")

    facebook_config = _facebook_config()
    base_url = request.host_url.rstrip("/")
    redirect_uri = f"{base_url}/auth/facebook/callback"
    token_url = (
        f"https://graph.facebook.com/{facebook_config['graph_version']}/oauth/access_token?"
        + urlencode(
            {
                "client_id": facebook_config["app_id"],
                "redirect_uri": redirect_uri,
                "client_secret": facebook_config["app_secret"],
                "code": code,
            }
        )
    )

    try:
        token_payload = _read_json_url(token_url)
        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("No access token returned.")
        profile_url = (
            f"https://graph.facebook.com/{facebook_config['graph_version']}/me?"
            + urlencode(
                {
                    "fields": "id,name,email,picture.type(large)",
                    "access_token": access_token,
                }
            )
        )
        profile = _read_json_url(profile_url)
        facebook_id = str(profile.get("id") or "").strip()
        if not facebook_id:
            raise RuntimeError("Facebook profile missing ID.")
        display_name = (
            str(profile.get("name") or "Facebook User").strip() or "Facebook User"
        )
        email = str(profile.get("email") or "").strip().lower()
        if not email:
            email = f"facebook_{facebook_id}@facebook.local"
        picture_url = _facebook_picture_url(profile)

        account = auth.get_user_by_facebook_id(conn, facebook_id)
        if account is None:
            existing_email = auth.get_user_by_email(conn, email)
            if existing_email is not None:
                auth.link_facebook_account(
                    conn,
                    int(existing_email["id"]),
                    facebook_id,
                    avatar_url=picture_url,
                )
                account = auth.get_user_by_id(conn, int(existing_email["id"]))
            else:
                temp_password = auth.generate_temp_password(16)
                user_id = auth.create_user(
                    conn,
                    email=email,
                    display_name=display_name,
                    password=temp_password,
                    is_admin=False,
                    avatar_url=picture_url,
                    auth_provider="facebook",
                    facebook_id=facebook_id,
                )
                account = auth.get_user_by_id(conn, user_id)
        else:
            has_local_avatar = bool(
                account["avatar_url"]
                and str(account["avatar_url"]).startswith("/static/avatars/")
            )
            if picture_url and not has_local_avatar:
                auth.set_user_avatar(conn, int(account["id"]), picture_url)
                account = auth.get_user_by_id(conn, int(account["id"]))

        if account is None:
            raise RuntimeError("Could not create Facebook account.")

        sid = auth.create_session(conn, int(account["id"]))
        session["session_id"] = sid
        flash("Logged in with Facebook", "ok")
        return redirect("/tips")

    except Exception:
        flash("Facebook login failed. Please try again.", "error")
        return redirect("/login")


# ---------------------------------------------------------------------------
# Routes — Main pages
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    conn = _get_db()
    user = _current_user(conn)
    if not user:
        return redirect("/login")
    return redirect("/tips")


@app.route("/tips")
@login_required
def tips():
    conn = _get_db()
    user = g.user
    season_year = sydney_now().year
    apply_automatic_underdog_tips(
        conn, season_year=season_year, user_id=int(user["id"])
    )
    current_round = get_current_round(conn, season_year=season_year)
    all_rounds = get_round_numbers(conn, season_year=season_year)
    selectable_rounds = [
        r for r in all_rounds if current_round is None or r >= current_round
    ]
    if not selectable_rounds:
        selectable_rounds = list(all_rounds)

    selected_round_raw = request.args.get("round")
    selected_round = None
    if selected_round_raw:
        try:
            candidate = int(selected_round_raw)
            if candidate in selectable_rounds:
                selected_round = candidate
        except ValueError:
            pass

    if selected_round is None:
        if current_round is not None and current_round in selectable_rounds:
            selected_round = current_round
        elif selectable_rounds:
            selected_round = selectable_rounds[0]

    fixtures = (
        get_round_fixtures(conn, selected_round, season_year=season_year)
        if selected_round is not None
        else []
    )
    tip_map = (
        get_user_tips_for_round(
            conn, int(user["id"]), selected_round, season_year=season_year
        )
        if selected_round is not None
        else {}
    )
    page = render_tips(
        user=user,
        round_number=selected_round,
        season_year=season_year,
        fixtures=fixtures,
        tips_by_fixture=tip_map,
        selectable_rounds=selectable_rounds,
        current_round=current_round,
    )
    flash_msg, flash_kind = _flash_msg()
    return render_page("Weekly Tips", page, user=user, flash=flash_msg, flash_kind=flash_kind)


@app.route("/tips/save", methods=["POST"])
@login_required
def tips_save():
    conn = _get_db()
    user = g.user
    from urllib.parse import urlencode

    try:
        round_number = int(request.form.get("round", ""))
    except ValueError:
        flash("Invalid round selected.", "error")
        return redirect("/tips")

    season_year_raw = request.form.get("season_year", "")
    try:
        season_year = int(season_year_raw)
    except ValueError:
        season_year = sydney_now().year

    fixtures = get_round_fixtures(conn, round_number, season_year=season_year)
    now = sydney_now()
    round_locked = is_round_locked(fixtures, now=now, lock_minutes=TIP_LOCK_MINUTES)

    if round_locked:
        # Round is locked — auto-fill any missing tips with underdogs
        auto_filled = apply_automatic_underdog_tips(
            conn,
            season_year=season_year,
            round_number=round_number,
            user_id=int(user["id"]),
            now=now,
        )
        msg = "This round is locked — tips can no longer be changed."
        if auto_filled:
            msg += f" Auto-picked {auto_filled} underdog tip(s) for untipped games."
        flash(msg, "error")
        return redirect(f"/tips?round={round_number}")

    picks: list[tuple[int, str]] = []
    for fixture in fixtures:
        fixture_id = int(fixture["id"])
        key = f"tip_{fixture_id}"
        if key not in request.form:
            continue
        pick = request.form[key]
        valid_teams = {fixture["home_team"], fixture["away_team"]}
        if pick not in valid_teams:
            continue
        picks.append((fixture_id, pick))

    saved = save_tips(conn, int(user["id"]), picks) if picks else 0
    msg = f"Saved {saved} tip(s)."
    flash(msg, "ok")
    return redirect(f"/tips?round={round_number}")


@app.route("/leaderboard")
@login_required
def leaderboard():
    conn = _get_db()
    user = g.user
    season_year = sydney_now().year
    apply_automatic_underdog_tips(conn, season_year=season_year)
    all_rounds = get_round_numbers(conn, season_year=season_year)
    players = get_leaderboard_with_rounds(
        conn, season_year=season_year, round_numbers=all_rounds
    )
    page = render_leaderboard(
        user=user,
        players=players,
        round_numbers=all_rounds,
        season_year=season_year,
    )
    flash_msg, flash_kind = _flash_msg()
    return render_page(
        "Leaderboard", page, user=user, flash=flash_msg, flash_kind=flash_kind
    )


@app.route("/ladder")
@login_required
def ladder():
    conn = _get_db()
    user = g.user
    season_year = sydney_now().year
    ladder_data = get_ladder(conn, season_year=season_year)
    page = render_ladder(ladder=ladder_data, season_year=season_year)
    flash_msg, flash_kind = _flash_msg()
    return render_page(
        "NRL Ladder", page, user=user, flash=flash_msg, flash_kind=flash_kind
    )


@app.route("/predict-ladder", methods=["GET", "POST"])
@login_required
def predict_ladder():
    conn = _get_db()
    user = g.user
    season_year = sydney_now().year

    if request.method == "POST":
        season_year_raw = request.form.get("season_year", "")
        try:
            season_year = int(season_year_raw)
        except ValueError:
            season_year = sydney_now().year

        deadline = datetime(
            season_year, 3, 12, 20, 0, 0, tzinfo=timezone(timedelta(hours=11))
        )
        if sydney_now().astimezone(timezone.utc) >= deadline.astimezone(timezone.utc):
            flash("Predictions are closed.", "error")
            return redirect("/predict-ladder")

        order_raw = request.form.get("order", "")
        try:
            ordered_teams = json.loads(order_raw)
            if not isinstance(ordered_teams, list):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            flash("Invalid prediction data.", "error")
            return redirect("/predict-ladder")

        saved = save_ladder_prediction(
            conn, int(user["id"]), season_year, ordered_teams
        )
        flash(f"Prediction saved! ({saved} teams)", "ok")
        return redirect("/predict-ladder")

    # GET
    teams = get_all_teams(conn, season_year=season_year)
    existing = get_user_ladder_prediction(conn, int(user["id"]), season_year)
    actual_ladder = get_ladder(conn, season_year=season_year)
    lb = get_ladder_prediction_leaderboard(conn, season_year, actual_ladder)
    page = render_predict_ladder(
        user=user,
        teams=teams,
        existing_prediction=existing,
        leaderboard=lb,
        actual_ladder=actual_ladder,
        season_year=season_year,
    )
    # Adjustment section (available after season starts)
    completed_rounds = get_completed_round_numbers(conn, season_year)
    adjustments = get_user_adjustments(conn, int(user["id"]), season_year)
    used_rounds = {a["round_number"] for a in adjustments}
    adjust_html = render_ladder_adjust(
        user=user,
        predictions=existing,
        teams=teams,
        completed_rounds=completed_rounds,
        used_rounds=used_rounds,
        season_year=season_year,
    )
    page += adjust_html
    flash_msg, flash_kind = _flash_msg()
    return render_page(
        "Predict the Ladder",
        page,
        user=user,
        flash=flash_msg,
        flash_kind=flash_kind,
    )


@app.route("/predict-ladder/adjust", methods=["POST"])
@login_required
def predict_ladder_adjust():
    conn = _get_db()
    user = g.user
    season_year_raw = request.form.get("season_year", "")
    try:
        season_year = int(season_year_raw)
    except ValueError:
        season_year = sydney_now().year

    round_raw = request.form.get("round_number", "")
    try:
        round_number = int(round_raw)
    except ValueError:
        flash("Invalid round number.", "error")
        return redirect("/predict-ladder")

    team = request.form.get("team", "").strip()
    direction = request.form.get("direction", "").strip()

    if not team or not direction:
        flash("Please select a team and direction.", "error")
        return redirect("/predict-ladder")

    error = save_ladder_adjustment(
        conn, int(user["id"]), season_year, round_number, team, direction
    )
    if error:
        flash(error, "error")
    else:
        arrow = "↑" if direction == "up" else "↓"
        flash(f"Round {round_number} adjustment applied: {team} moved {arrow}", "ok")
    return redirect("/predict-ladder")


@app.route("/predictions")
@login_required
def predictions():
    conn = _get_db()
    user = g.user
    season_year = sydney_now().year
    teams = get_all_teams(conn, season_year=season_year)
    started = is_season_started(conn, season_year)
    predictions_data = get_all_ladder_predictions(conn, season_year) if started else {}
    actual_ladder = get_ladder(conn, season_year=season_year)
    lb = get_ladder_prediction_leaderboard(conn, season_year, actual_ladder) if started else []
    page = render_all_predictions(
        user=user,
        predictions_by_user=predictions_data,
        teams=teams,
        season_year=season_year,
        season_started=started,
        actual_ladder=actual_ladder,
        leaderboard=lb,
    )
    flash_msg, flash_kind = _flash_msg()
    return render_page(
        "Ladder Predictions",
        page,
        user=user,
        flash=flash_msg,
        flash_kind=flash_kind,
    )


@app.route("/tipsheet")
@login_required
def tipsheet():
    conn = _get_db()
    user = g.user
    season_year = sydney_now().year
    apply_automatic_underdog_tips(conn, season_year=season_year)
    round_numbers = get_round_numbers(conn, season_year=season_year)
    selected_round_raw = request.args.get("round")
    selected_round = None
    if selected_round_raw:
        try:
            selected_round = int(selected_round_raw)
        except ValueError:
            pass
    if selected_round is None:
        selected_round = get_current_round(conn, season_year=season_year)

    tipsheet_data = (
        get_round_tipsheet_data(
            conn,
            season_year=season_year,
            round_number=selected_round,
            include_admin=True,
        )
        if selected_round is not None
        else {
            "fixtures": [],
            "participants": [],
            "tips_by_user_fixture": {},
            "all_submitted": False,
            "total_required": 0,
        }
    )
    page = render_tipsheet(
        user=user,
        season_year=season_year,
        round_number=selected_round,
        round_numbers=round_numbers,
        fixtures=tipsheet_data["fixtures"],
        participants=tipsheet_data["participants"],
        tips_by_user_fixture=tipsheet_data["tips_by_user_fixture"],
        all_submitted=tipsheet_data["all_submitted"],
        total_required=tipsheet_data["total_required"],
        round_locked=is_round_locked(tipsheet_data["fixtures"]),
        current_user_id=int(user["id"]),
    )
    flash_msg, flash_kind = _flash_msg()
    return render_page(
        "Tipsheet", page, user=user, flash=flash_msg, flash_kind=flash_kind
    )


@app.route("/profile")
@login_required
def profile():
    conn = _get_db()
    user = g.user
    page = render_profile(user)
    flash_msg, flash_kind = _flash_msg()
    return render_page(
        "Profile", page, user=user, flash=flash_msg, flash_kind=flash_kind
    )


@app.route("/profile/details", methods=["POST"])
@login_required
def profile_details():
    conn = _get_db()
    user = g.user

    display_name = request.form.get("display_name", "").strip()
    if not display_name:
        flash("Display name is required.", "error")
        return redirect("/profile")
    if len(display_name) > 50:
        flash("Display name must be 50 characters or fewer.", "error")
        return redirect("/profile")

    conn.execute(
        "UPDATE users SET display_name = ? WHERE id = ?",
        (display_name, int(user["id"])),
    )
    conn.commit()
    flash("Profile updated.", "ok")
    return redirect("/profile")


@app.route("/profile/avatar", methods=["POST"])
@login_required
def profile_avatar():
    conn = _get_db()
    user = g.user

    upload = request.files.get("avatar")
    if upload is None or upload.filename == "":
        flash("Please choose an image file to upload.", "error")
        return redirect("/profile")
    try:
        avatar_url = _save_avatar_file(
            int(user["id"]),
            upload,
            str(user["avatar_url"]) if user["avatar_url"] else None,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect("/profile")
    auth.set_user_avatar(conn, int(user["id"]), avatar_url)
    flash("Profile picture updated.", "ok")
    return redirect("/profile")


@app.route("/profile/password", methods=["POST"])
@login_required
def profile_password():
    conn = _get_db()
    user = g.user

    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not auth.verify_password(current_password, user["password_hash"]):
        flash("Current password is incorrect.", "error")
        return redirect("/profile")
    if len(new_password) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect("/profile")
    if new_password != confirm_password:
        flash("New password and confirmation do not match.", "error")
        return redirect("/profile")
    if current_password == new_password:
        flash("New password must be different from current password.", "error")
        return redirect("/profile")

    auth.set_user_password(conn, int(user["id"]), new_password)
    sid = session.get("session_id")
    auth.delete_sessions_for_user(conn, int(user["id"]), except_session_id=sid)
    flash("Password updated.", "ok")
    return redirect("/profile")


# ---------------------------------------------------------------------------
# Routes — Push Notifications API
# ---------------------------------------------------------------------------


@app.route("/api/push/vapid-key")
def push_vapid_key():
    return {"vapid_public_key": VAPID_PUBLIC_KEY}


@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    conn = _get_db()
    user = g.user
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return {"error": "Invalid JSON"}, 400

    endpoint = data.get("endpoint", "").strip()
    keys = data.get("keys")
    if not endpoint or not isinstance(keys, dict):
        return {"error": "Missing endpoint or keys"}, 400

    keys_json = json.dumps(keys)
    now = sydney_now_iso()
    conn.execute(
        """
        INSERT INTO push_subscriptions(user_id, endpoint, keys_json, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            user_id = excluded.user_id,
            keys_json = excluded.keys_json
        """,
        (int(user["id"]), endpoint, keys_json, now),
    )
    conn.commit()
    return {"ok": True}


@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    conn = _get_db()
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return {"error": "Invalid JSON"}, 400

    endpoint = data.get("endpoint", "").strip()
    if not endpoint:
        return {"error": "Missing endpoint"}, 400

    conn.execute(
        "DELETE FROM push_subscriptions WHERE endpoint = ?",
        (endpoint,),
    )
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes — Admin
# ---------------------------------------------------------------------------


@app.route("/admin")
@admin_required
def admin():
    conn = _get_db()
    user = g.user
    last_sync = get_setting(conn, "last_sync_utc")
    latest_summary = get_setting(conn, "last_sync_summary")
    facebook_config = _facebook_config()
    missing: list[str] = []
    if not facebook_config.get("app_id"):
        missing.append("FACEBOOK_APP_ID")
    if not facebook_config.get("app_secret"):
        missing.append("FACEBOOK_APP_SECRET")
    base_url = request.host_url.rstrip("/")
    page = render_admin(
        user,
        last_sync,
        latest_summary,
        facebook_check={
            "enabled": _facebook_enabled(),
            "app_id_display": (
                _mask_value(
                    str(facebook_config.get("app_id") or ""),
                    keep_start=6,
                    keep_end=3,
                )
                if facebook_config.get("app_id")
                else "missing"
            ),
            "app_secret_status": (
                "set" if facebook_config.get("app_secret") else "missing"
            ),
            "graph_version": str(facebook_config.get("graph_version") or ""),
            "scopes": str(facebook_config.get("oauth_scopes") or ""),
            "callback_url": f"{base_url}/auth/facebook/callback",
            "missing": missing,
        },
    )
    flash_msg, flash_kind = _flash_msg()
    return render_page(
        "Admin", page, user=user, flash=flash_msg, flash_kind=flash_kind
    )


@app.route("/admin/sync", methods=["POST"])
@admin_required
def admin_sync():
    conn = _get_db()

    year_raw = request.form.get("season_year", "").strip()
    try:
        season_year = int(year_raw) if year_raw else sydney_now().year
    except ValueError:
        season_year = sydney_now().year

    try:
        summary = sync_nrl_season(conn, season_year=season_year)
        set_setting(conn, "last_sync_utc", sydney_now_iso())
        set_setting(conn, "last_sync_summary", json.dumps(summary, indent=2))
        flash(
            f"Sync complete. {summary['total_merged']} fixtures processed.", "ok"
        )
    except Exception as exc:
        flash(f"Sync failed: {exc}", "error")
    return redirect("/admin")


@app.route("/admin/users")
@admin_required
def admin_users():
    conn = _get_db()
    user = g.user
    users = conn.execute(
        "SELECT * FROM users ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    flash_password = session.pop("admin_reset_password", None)
    page = render_admin_users(users, flash_password=flash_password)
    flash_msg, flash_kind = _flash_msg()
    return render_page(
        "User Management", page, user=user, flash=flash_msg, flash_kind=flash_kind
    )


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    conn = _get_db()
    target = auth.get_user_by_id(conn, user_id)
    if not target:
        flash("User not found.", "error")
        return redirect("/admin/users")
    new_pw = auth.generate_temp_password(12)
    auth.set_user_password(conn, user_id, new_pw)
    auth.delete_sessions_for_user(conn, user_id)
    session["admin_reset_password"] = {"user_id": user_id, "password": new_pw}
    flash(f"Password reset for {target['display_name']}.", "ok")
    return redirect("/admin/users")


@app.route("/admin/users/<int:user_id>/avatar", methods=["POST"])
@admin_required
def admin_user_avatar(user_id):
    conn = _get_db()
    target = auth.get_user_by_id(conn, user_id)
    if not target:
        flash("User not found.", "error")
        return redirect("/admin/users")
    upload = request.files.get("avatar")
    if upload is None or upload.filename == "":
        flash("Please choose an image file.", "error")
        return redirect("/admin/users")
    try:
        avatar_url = _save_avatar_file(
            user_id,
            upload,
            str(target["avatar_url"]) if target["avatar_url"] else None,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect("/admin/users")
    auth.set_user_avatar(conn, user_id, avatar_url)
    flash(f"Photo updated for {target['display_name']}.", "ok")
    return redirect("/admin/users")


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    conn = _get_db()
    user = g.user
    if int(user["id"]) == user_id:
        flash("You cannot delete your own account.", "error")
        return redirect("/admin/users")
    target = auth.get_user_by_id(conn, user_id)
    if not target:
        flash("User not found.", "error")
        return redirect("/admin/users")
    name = target["display_name"]
    auth.delete_user(conn, user_id)
    flash(f"User {name} and all their data have been permanently deleted.", "ok")
    return redirect("/admin/users")

# ---------------------------------------------------------------------------
# Routes — Public pages (Privacy / Data Deletion)
# ---------------------------------------------------------------------------


@app.route("/privacy")
def privacy():
    page = render_privacy()
    return render_page("Privacy Policy", page)


@app.route("/remove")
def data_deletion():
    page = render_data_deletion()
    return render_page("Data Deletion", page)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(host: str = "0.0.0.0", port: int = 8080) -> None:
    ensure_default_admin()
    start_score_update_worker()
    start_sync_worker()
    start_notify_worker()
    print(f"NRL Tipping app running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    host = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
    run(host=host, port=port)
