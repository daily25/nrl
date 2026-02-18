from __future__ import annotations

import json
import mimetypes
import secrets
import sqlite3
import ssl
import sys
from email.parser import BytesParser
from email.policy import default as email_policy_default
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from nrl_tipping import auth
from nrl_tipping.config import (
    AVATAR_UPLOAD_DIR,
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_NAME,
    DEFAULT_ADMIN_PASSWORD,
    MAX_AVATAR_BYTES,
    SSL_CERTFILE,
    SSL_KEYFILE,
    STATIC_DIR,
    TIP_LOCK_MINUTES,
    get_facebook_oauth_config,
)
from nrl_tipping.db import connect_db, get_setting, init_db, set_setting
from nrl_tipping.queries import (
    apply_automatic_underdog_tips,
    get_all_teams,
    get_current_round,
    get_ladder,
    get_ladder_prediction_leaderboard,
    get_leaderboard,
    get_leaderboard_with_rounds,
    get_round_fixtures,
    get_round_leaderboard,
    get_round_numbers,
    get_round_tipsheet_data,
    get_user_ladder_prediction,
    get_user_tips_for_round,
    save_ladder_prediction,
    save_tips,
)
from nrl_tipping.score_worker import start_score_update_worker
from nrl_tipping.sync import sync_nrl_season
from nrl_tipping.utils import is_tip_locked, sydney_now, sydney_now_iso
from nrl_tipping.views import (
    render_admin,
    render_ladder,
    render_leaderboard,
    render_login,
    render_page,
    render_predict_ladder,
    render_profile,
    render_register,
    render_tips,
    render_tipsheet,
)


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
                f"[bootstrap] Created admin user {DEFAULT_ADMIN_EMAIL} with password {DEFAULT_ADMIN_PASSWORD}",
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


class NRLTippingHandler(BaseHTTPRequestHandler):
    server_version = "NRLTipping/1.0"

    def _db(self) -> sqlite3.Connection:
        conn = connect_db()
        init_db(conn)
        return conn

    def _parse_query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def _parse_form(self) -> tuple[dict[str, list[str]], dict[str, dict[str, object]]]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""
        content_type = self.headers.get("Content-Type", "")
        if content_type.lower().startswith("multipart/form-data"):
            return self._parse_multipart_form(raw, content_type)
        body = raw.decode("utf-8", errors="replace") if raw else ""
        return parse_qs(body), {}

    def _parse_multipart_form(
        self,
        body: bytes,
        content_type: str,
    ) -> tuple[dict[str, list[str]], dict[str, dict[str, object]]]:
        fields: dict[str, list[str]] = {}
        files: dict[str, dict[str, object]] = {}
        if not body:
            return fields, files

        envelope = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
            + body
        )
        try:
            message = BytesParser(policy=email_policy_default).parsebytes(envelope)
        except Exception:
            return fields, files
        if not message.is_multipart():
            return fields, files

        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                files[name] = {
                    "filename": str(filename),
                    "content_type": str(part.get_content_type() or ""),
                    "data": payload,
                }
                continue
            charset = part.get_content_charset() or "utf-8"
            value = payload.decode(charset, errors="replace")
            fields.setdefault(name, []).append(value)
        return fields, files

    def _base_url(self) -> str:
        forwarded_proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
        is_ssl = isinstance(self.request, ssl.SSLSocket)
        scheme = "https" if forwarded_proto == "https" or is_ssl else "http"
        host = (
            (self.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
            or (self.headers.get("Host") or "").strip()
        )
        if not host:
            host = "127.0.0.1:8080"
        return f"{scheme}://{host}"

    def _read_json_url(self, url: str) -> dict[str, object]:
        request = Request(url, headers={"User-Agent": "NRL-Tipping-App/1.0"})
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                return parsed if isinstance(parsed, dict) else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body[:240]}") from exc
        except URLError as exc:
            raise RuntimeError(f"Connection error: {exc.reason}") from exc

    def _cookie_value(self, name: str) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    def _clear_cookie_header(self, name: str) -> str:
        return f"{name}=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax"

    def _facebook_config(self) -> dict[str, str]:
        return get_facebook_oauth_config()

    def _facebook_enabled(self) -> bool:
        config = self._facebook_config()
        return bool(config["app_id"] and config["app_secret"])

    def _facebook_picture_url(self, profile: dict[str, object]) -> str | None:
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

    def _image_extension(self, content_type: str | None, data: bytes) -> str | None:
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

    def _delete_local_avatar(self, avatar_url: str | None) -> None:
        if not avatar_url or not avatar_url.startswith("/static/avatars/"):
            return
        relative = avatar_url[len("/static/") :]
        target = (STATIC_DIR / relative).resolve()
        static_root = STATIC_DIR.resolve()
        if static_root in target.parents and target.is_file():
            try:
                target.unlink()
            except OSError:
                pass

    def _save_avatar_file(
        self,
        user_id: int,
        file_data: dict[str, object],
        current_avatar_url: str | None,
    ) -> str:
        raw = file_data.get("data", b"")
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError("Invalid image upload.")
        blob = bytes(raw)
        if not blob:
            raise ValueError("Please choose an image file.")
        if len(blob) > MAX_AVATAR_BYTES:
            raise ValueError("Image is too large. Max size is 5 MB.")

        content_type = str(file_data.get("content_type") or "")
        extension = self._image_extension(content_type, blob)
        if extension is None:
            raise ValueError("Unsupported image type. Use PNG, JPG, WEBP, or GIF.")

        AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"user_{user_id}_{secrets.token_hex(8)}{extension}"
        target = (AVATAR_UPLOAD_DIR / filename).resolve()
        avatar_root = AVATAR_UPLOAD_DIR.resolve()
        if avatar_root not in target.parents:
            raise ValueError("Invalid avatar path.")
        target.write_bytes(blob)
        self._delete_local_avatar(current_avatar_url)
        return f"/static/avatars/{filename}"

    def _flash_from_query(self, query: dict[str, list[str]]) -> tuple[str | None, str]:
        msg = query.get("msg", [None])[0]
        kind = query.get("kind", ["ok"])[0]
        return msg, kind

    def _send_html(self, html: str, status: int = HTTPStatus.OK) -> None:
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, object], status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, target, content_type_override: str | None = None) -> None:
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        data = target.read_bytes()
        content_type = content_type_override or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if target.name in {"service-worker.js", "manifest.webmanifest"}:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        if not path.startswith("/static/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        relative = path[len("/static/") :]
        target = (STATIC_DIR / relative).resolve()
        static_root = STATIC_DIR.resolve()
        if static_root not in target.parents and target != static_root:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._serve_file(target)

    def _redirect(self, location: str, cookies: list[str] | None = None) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _redirect_with_message(self, path: str, msg: str, kind: str = "ok") -> None:
        query = urlencode({"msg": msg, "kind": kind})
        self._redirect(f"{path}?{query}")

    def _session_cookie(self) -> str | None:
        return self._cookie_value("session_id")

    def _current_user(self, conn: sqlite3.Connection):
        auth.purge_expired_sessions(conn)
        session_id = self._session_cookie()
        return auth.get_user_for_session(conn, session_id) if session_id else None

    def do_GET(self) -> None:  # noqa: N802
        path, query = self._parse_query()
        if path == "/healthz":
            self._send_json({"ok": True})
            return
        if path.startswith("/static/"):
            self._serve_static(path)
            return
        if path == "/manifest.webmanifest":
            self._serve_file(STATIC_DIR / "manifest.webmanifest", "application/manifest+json")
            return
        if path == "/service-worker.js":
            self._serve_file(STATIC_DIR / "service-worker.js", "application/javascript; charset=utf-8")
            return
        if path == "/offline.html":
            self._serve_file(STATIC_DIR / "offline.html", "text/html; charset=utf-8")
            return

        conn = self._db()
        try:
            user = self._current_user(conn)
            flash, kind = self._flash_from_query(query)

            if path == "/login":
                if user:
                    self._redirect("/")
                    return
                self._send_html(
                    render_page(
                        "Login",
                        render_login(facebook_enabled=self._facebook_enabled()),
                        flash=flash,
                        flash_kind=kind,
                    )
                )
                return

            if path == "/register":
                if user:
                    self._redirect("/")
                    return
                self._send_html(
                    render_page(
                        "Register",
                        render_register(facebook_enabled=self._facebook_enabled()),
                        flash=flash,
                        flash_kind=kind,
                    )
                )
                return

            if path == "/auth/facebook/start":
                if user:
                    self._redirect("/tips")
                    return
                facebook_config = self._facebook_config()
                if not self._facebook_enabled():
                    self._redirect_with_message(
                        "/login",
                        "Facebook login is not configured yet.",
                        "error",
                    )
                    return
                state = secrets.token_urlsafe(24)
                redirect_uri = f"{self._base_url()}/auth/facebook/callback"
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
                oauth_cookie = (
                    f"fb_oauth_state={state}; Path=/; Max-Age=600; HttpOnly; SameSite=Lax"
                )
                self._redirect(oauth_url, cookies=[oauth_cookie])
                return

            if path == "/auth/facebook/callback":
                if user:
                    self._redirect("/tips")
                    return
                state = query.get("state", [None])[0]
                code = query.get("code", [None])[0]
                oauth_state = self._cookie_value("fb_oauth_state")
                clear_state_cookie = self._clear_cookie_header("fb_oauth_state")
                if not state or not oauth_state or state != oauth_state or not code:
                    self._redirect(
                        "/login?msg=Facebook+login+failed.+Please+try+again.&kind=error",
                        cookies=[clear_state_cookie],
                    )
                    return
                if not self._facebook_enabled():
                    self._redirect(
                        "/login?msg=Facebook+login+is+not+configured.&kind=error",
                        cookies=[clear_state_cookie],
                    )
                    return
                facebook_config = self._facebook_config()
                redirect_uri = f"{self._base_url()}/auth/facebook/callback"
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
                    token_payload = self._read_json_url(token_url)
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
                    profile = self._read_json_url(profile_url)
                    facebook_id = str(profile.get("id") or "").strip()
                    if not facebook_id:
                        raise RuntimeError("Facebook profile missing ID.")
                    display_name = str(profile.get("name") or "Facebook User").strip() or "Facebook User"
                    email = str(profile.get("email") or "").strip().lower()
                    if not email:
                        email = f"facebook_{facebook_id}@facebook.local"
                    picture_url = self._facebook_picture_url(profile)

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
                        # Keep a user-uploaded local avatar, otherwise refresh Facebook avatar.
                        has_local_avatar = bool(
                            account["avatar_url"]
                            and str(account["avatar_url"]).startswith("/static/avatars/")
                        )
                        if picture_url and not has_local_avatar:
                            auth.set_user_avatar(conn, int(account["id"]), picture_url)
                            account = auth.get_user_by_id(conn, int(account["id"]))

                    if account is None:
                        raise RuntimeError("Could not create Facebook account.")

                    session_id = auth.create_session(conn, int(account["id"]))
                    session_cookie = f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax"
                    self._redirect(
                        "/tips?msg=Logged+in+with+Facebook&kind=ok",
                        cookies=[clear_state_cookie, session_cookie],
                    )
                    return
                except Exception:
                    self._redirect(
                        "/login?msg=Facebook+login+failed.+Please+try+again.&kind=error",
                        cookies=[clear_state_cookie],
                    )
                    return

            if path == "/":
                if not user:
                    self._redirect("/login")
                    return
                self._redirect("/tips")
                return

            if path == "/tips":
                if not user:
                    self._redirect("/login")
                    return
                season_year = sydney_now().year
                apply_automatic_underdog_tips(
                    conn,
                    season_year=season_year,
                    user_id=int(user["id"]),
                )
                current_round = get_current_round(conn, season_year=season_year)
                all_rounds = get_round_numbers(conn, season_year=season_year)
                selectable_rounds = [
                    round_value
                    for round_value in all_rounds
                    if current_round is None or round_value >= current_round
                ]
                if not selectable_rounds:
                    selectable_rounds = list(all_rounds)

                selected_round_raw = query.get("round", [None])[0]
                selected_round = None
                if selected_round_raw:
                    try:
                        candidate_round = int(selected_round_raw)
                        if candidate_round in selectable_rounds:
                            selected_round = candidate_round
                    except ValueError:
                        selected_round = None

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
                        conn,
                        int(user["id"]),
                        selected_round,
                        season_year=season_year,
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
                self._send_html(
                    render_page("Weekly Tips", page, user=user, flash=flash, flash_kind=kind)
                )
                return

            if path == "/leaderboard":
                if not user:
                    self._redirect("/login")
                    return
                season_year = sydney_now().year
                apply_automatic_underdog_tips(conn, season_year=season_year)
                all_rounds = get_round_numbers(conn, season_year=season_year)
                players = get_leaderboard_with_rounds(conn, season_year=season_year, round_numbers=all_rounds)
                page = render_leaderboard(
                    user=user,
                    players=players,
                    round_numbers=all_rounds,
                    season_year=season_year,
                )
                self._send_html(
                    render_page("Leaderboard", page, user=user, flash=flash, flash_kind=kind)
                )
                return

            if path == "/ladder":
                if not user:
                    self._redirect("/login")
                    return
                season_year = sydney_now().year
                ladder_data = get_ladder(conn, season_year=season_year)
                page = render_ladder(ladder=ladder_data, season_year=season_year)
                self._send_html(
                    render_page("NRL Ladder", page, user=user, flash=flash, flash_kind=kind)
                )
                return

            if path == "/predict-ladder":
                if not user:
                    self._redirect("/login")
                    return
                season_year = sydney_now().year
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
                self._send_html(
                    render_page("Predict the Ladder", page, user=user, flash=flash, flash_kind=kind)
                )
                return

            if path == "/tipsheet":
                if not user:
                    self._redirect("/login")
                    return
                season_year = sydney_now().year
                apply_automatic_underdog_tips(conn, season_year=season_year)
                round_numbers = get_round_numbers(conn, season_year=season_year)
                selected_round_raw = query.get("round", [None])[0]
                selected_round = None
                if selected_round_raw:
                    try:
                        selected_round = int(selected_round_raw)
                    except ValueError:
                        selected_round = None
                if selected_round is None:
                    selected_round = get_current_round(conn, season_year=season_year)

                tipsheet = (
                    get_round_tipsheet_data(
                        conn,
                        season_year=season_year,
                        round_number=selected_round,
                        include_admin=False,
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
                    fixtures=tipsheet["fixtures"],
                    participants=tipsheet["participants"],
                    tips_by_user_fixture=tipsheet["tips_by_user_fixture"],
                    all_submitted=tipsheet["all_submitted"],
                    total_required=tipsheet["total_required"],
                )
                self._send_html(
                    render_page("Tipsheet", page, user=user, flash=flash, flash_kind=kind)
                )
                return

            if path == "/profile":
                if not user:
                    self._redirect("/login")
                    return
                page = render_profile(user)
                self._send_html(
                    render_page("Profile", page, user=user, flash=flash, flash_kind=kind)
                )
                return

            if path == "/admin":
                if not user:
                    self._redirect("/login")
                    return
                if int(user["is_admin"]) != 1:
                    self._redirect_with_message("/", "Admin access required.", "error")
                    return
                last_sync = get_setting(conn, "last_sync_utc")
                latest_summary = get_setting(conn, "last_sync_summary")
                facebook_config = self._facebook_config()
                missing: list[str] = []
                if not facebook_config.get("app_id"):
                    missing.append("FACEBOOK_APP_ID")
                if not facebook_config.get("app_secret"):
                    missing.append("FACEBOOK_APP_SECRET")
                page = render_admin(
                    user,
                    last_sync,
                    latest_summary,
                    facebook_check={
                        "enabled": self._facebook_enabled(),
                        "app_id_display": _mask_value(str(facebook_config.get("app_id") or ""), keep_start=6, keep_end=3)
                        if facebook_config.get("app_id")
                        else "missing",
                        "app_secret_status": "set" if facebook_config.get("app_secret") else "missing",
                        "graph_version": str(facebook_config.get("graph_version") or ""),
                        "scopes": str(facebook_config.get("oauth_scopes") or ""),
                        "callback_url": f"{self._base_url()}/auth/facebook/callback",
                        "missing": missing,
                    },
                )
                self._send_html(render_page("Admin", page, user=user, flash=flash, flash_kind=kind))
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        finally:
            conn.close()

    def do_POST(self) -> None:  # noqa: N802
        path, _ = self._parse_query()
        form, files = self._parse_form()
        conn = self._db()

        try:
            user = self._current_user(conn)

            if path == "/register":
                email = (form.get("email", [""])[0]).strip().lower()
                display_name = (form.get("display_name", [""])[0]).strip()
                password = form.get("password", [""])[0]

                if not email or not display_name or len(password) < 8:
                    html = render_page(
                        "Register",
                        render_register(
                            "All fields are required and password must be at least 8 characters.",
                            facebook_enabled=self._facebook_enabled(),
                        ),
                    )
                    self._send_html(html, status=HTTPStatus.BAD_REQUEST)
                    return

                if auth.get_user_by_email(conn, email):
                    html = render_page(
                        "Register",
                        render_register(
                            "That email is already registered.",
                            facebook_enabled=self._facebook_enabled(),
                        ),
                    )
                    self._send_html(html, status=HTTPStatus.BAD_REQUEST)
                    return

                user_id = auth.create_user(conn, email, display_name, password, is_admin=False)
                session_id = auth.create_session(conn, user_id)
                cookie = f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax"
                self._redirect("/tips?msg=Registration+complete&kind=ok", cookies=[cookie])
                return

            if path == "/login":
                email = (form.get("email", [""])[0]).strip().lower()
                password = form.get("password", [""])[0]
                existing = auth.get_user_by_email(conn, email)
                if not existing or not auth.verify_password(password, existing["password_hash"]):
                    html = render_page(
                        "Login",
                        render_login(
                            "Invalid email or password.",
                            facebook_enabled=self._facebook_enabled(),
                        ),
                    )
                    self._send_html(html, status=HTTPStatus.UNAUTHORIZED)
                    return

                session_id = auth.create_session(conn, int(existing["id"]))
                cookie = f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax"
                self._redirect("/tips?msg=Logged+in&kind=ok", cookies=[cookie])
                return

            if path == "/logout":
                session_id = self._session_cookie()
                if session_id:
                    auth.delete_session(conn, session_id)
                expire_cookie = "session_id=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax"
                self._redirect("/login?msg=Logged+out&kind=ok", cookies=[expire_cookie])
                return

            if path == "/tips/save":
                if not user:
                    self._redirect("/login")
                    return

                try:
                    round_number = int(form.get("round", [""])[0])
                except ValueError:
                    self._redirect_with_message("/tips", "Invalid round selected.", "error")
                    return

                season_year_raw = form.get("season_year", [""])[0]
                try:
                    season_year = int(season_year_raw)
                except ValueError:
                    season_year = sydney_now().year

                fixtures = get_round_fixtures(conn, round_number, season_year=season_year)
                now = sydney_now()
                picks: list[tuple[int, str]] = []
                blocked_update_count = 0

                for fixture in fixtures:
                    fixture_id = int(fixture["id"])
                    key = f"tip_{fixture_id}"
                    if key not in form:
                        continue
                    pick = form[key][0]
                    valid_teams = {fixture["home_team"], fixture["away_team"]}
                    if pick not in valid_teams:
                        continue
                    if is_tip_locked(fixture["start_time_utc"], now=now, lock_minutes=TIP_LOCK_MINUTES):
                        blocked_update_count += 1
                        continue
                    picks.append((fixture_id, pick))

                saved = save_tips(conn, int(user["id"]), picks) if picks else 0
                auto_filled = apply_automatic_underdog_tips(
                    conn,
                    season_year=season_year,
                    round_number=round_number,
                    user_id=int(user["id"]),
                    now=now,
                )
                msg = f"Saved {saved} tip(s)."
                if auto_filled:
                    msg += f" Auto-picked {auto_filled} underdog tip(s) for locked fixture(s)."
                if blocked_update_count:
                    msg += (
                        f" {blocked_update_count} selection(s) were already locked "
                        f"({TIP_LOCK_MINUTES} minutes before kickoff)."
                    )
                redirect_query = urlencode(
                    {
                        "round": str(round_number),
                        "msg": msg,
                        "kind": "ok",
                    }
                )
                self._redirect(f"/tips?{redirect_query}")
                return

            if path == "/predict-ladder":
                if not user:
                    self._redirect("/login")
                    return
                season_year_raw = form.get("season_year", [""])[0]
                try:
                    season_year = int(season_year_raw)
                except ValueError:
                    season_year = sydney_now().year
                # Enforce deadline: 12 March at 8pm Sydney time
                from datetime import datetime, timezone, timedelta
                deadline = datetime(season_year, 3, 12, 20, 0, 0, tzinfo=timezone(timedelta(hours=11)))
                if sydney_now().astimezone(timezone.utc) >= deadline.astimezone(timezone.utc):
                    self._redirect_with_message("/predict-ladder", "Predictions are closed.", "error")
                    return
                order_raw = form.get("order", [""])[0]
                try:
                    ordered_teams = json.loads(order_raw)
                    if not isinstance(ordered_teams, list):
                        raise ValueError
                except (json.JSONDecodeError, ValueError):
                    self._redirect_with_message("/predict-ladder", "Invalid prediction data.", "error")
                    return
                saved = save_ladder_prediction(conn, int(user["id"]), season_year, ordered_teams)
                self._redirect_with_message(
                    "/predict-ladder",
                    f"Prediction saved! ({saved} teams)",
                    "ok",
                )
                return

            if path == "/profile/details":
                if not user:
                    self._redirect("/login")
                    return

                display_name = (form.get("display_name", [""])[0]).strip()
                if not display_name:
                    self._redirect_with_message("/profile", "Display name is required.", "error")
                    return
                if len(display_name) > 50:
                    self._redirect_with_message("/profile", "Display name must be 50 characters or fewer.", "error")
                    return

                conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (display_name, int(user["id"])),
                )
                conn.commit()
                self._redirect_with_message("/profile", "Profile updated.", "ok")
                return

            if path == "/profile/avatar":
                if not user:
                    self._redirect("/login")
                    return
                upload = files.get("avatar")
                if upload is None:
                    self._redirect_with_message("/profile", "Please choose an image file to upload.", "error")
                    return
                try:
                    avatar_url = self._save_avatar_file(
                        int(user["id"]),
                        upload,
                        str(user["avatar_url"]) if user["avatar_url"] else None,
                    )
                except ValueError as exc:
                    self._redirect_with_message("/profile", str(exc), "error")
                    return
                auth.set_user_avatar(conn, int(user["id"]), avatar_url)
                self._redirect_with_message("/profile", "Profile picture updated.", "ok")
                return

            if path == "/profile/password":
                if not user:
                    self._redirect("/login")
                    return

                current_password = form.get("current_password", [""])[0]
                new_password = form.get("new_password", [""])[0]
                confirm_password = form.get("confirm_password", [""])[0]

                if not auth.verify_password(current_password, user["password_hash"]):
                    self._redirect_with_message("/profile", "Current password is incorrect.", "error")
                    return
                if len(new_password) < 8:
                    self._redirect_with_message("/profile", "New password must be at least 8 characters.", "error")
                    return
                if new_password != confirm_password:
                    self._redirect_with_message("/profile", "New password and confirmation do not match.", "error")
                    return
                if current_password == new_password:
                    self._redirect_with_message("/profile", "New password must be different from current password.", "error")
                    return

                auth.set_user_password(conn, int(user["id"]), new_password)
                session_id = self._session_cookie()
                auth.delete_sessions_for_user(conn, int(user["id"]), except_session_id=session_id)
                self._redirect_with_message("/profile", "Password updated.", "ok")
                return

            if path == "/admin/sync":
                if not user:
                    self._redirect("/login")
                    return
                if int(user["is_admin"]) != 1:
                    self._redirect_with_message("/", "Admin access required.", "error")
                    return

                year_raw = form.get("season_year", [""])[0].strip()
                try:
                    season_year = int(year_raw) if year_raw else sydney_now().year
                except ValueError:
                    season_year = sydney_now().year

                try:
                    summary = sync_nrl_season(conn, season_year=season_year)
                    set_setting(conn, "last_sync_utc", sydney_now_iso())
                    set_setting(conn, "last_sync_summary", json.dumps(summary, indent=2))
                    self._redirect_with_message(
                        "/admin",
                        f"Sync complete. {summary['total_merged']} fixtures processed.",
                        "ok",
                    )
                except Exception as exc:
                    self._redirect_with_message("/admin", f"Sync failed: {exc}", "error")
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        finally:
            conn.close()


def run(host: str = "0.0.0.0", port: int = 8080) -> None:
    ensure_default_admin()
    start_score_update_worker()
    httpd = ThreadingHTTPServer((host, port), NRLTippingHandler)
    use_ssl = SSL_CERTFILE.exists() and SSL_KEYFILE.exists()
    if use_ssl:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(SSL_CERTFILE), keyfile=str(SSL_KEYFILE))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        print(f"NRL Tipping app running at https://{host}:{port}")
    else:
        print(f"NRL Tipping app running at http://{host}:{port}")
        print("  (No SSL certs found — Facebook OAuth requires HTTPS)", file=sys.stderr)
    httpd.serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    host = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
    run(host=host, port=port)
