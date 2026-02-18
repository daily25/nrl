from __future__ import annotations

from html import escape
from sqlite3 import Row
from typing import Any

from nrl_tipping.config import TIP_LOCK_MINUTES
from nrl_tipping.utils import display_sydney, is_tip_locked, sydney_now


def _nav(user: Row | None) -> str:
    if not user:
        return ""
    admin_link = '<a href="/admin">Admin</a>' if int(user["is_admin"]) == 1 else ""
    return f"""
    <div class="menu-wrap">
      <button id="menu-toggle" class="menu-toggle" type="button" aria-expanded="false" aria-controls="site-menu" aria-label="Open menu">
        <span></span><span></span><span></span>
      </button>
      <nav id="site-menu" class="top-nav menu-panel" hidden>
        <a href="/tips">Weekly Tips</a>
        <a href="/tipsheet">Tipsheet</a>
        <a href="/leaderboard">Leaderboard</a>
        <a href="/ladder">NRL Ladder</a>
        <a href="/profile">Profile</a>
        {admin_link}
        <form method="post" action="/logout" class="logout-form">
          <button type="submit">Logout</button>
        </form>
      </nav>
    </div>
    """


def _mobile_footer_nav(user: Row | None, title: str) -> str:
    if not user:
        return ""
    active_key = {
        "Weekly Tips": "tips",
        "Leaderboard": "leaderboard",
        "Tipsheet": "tipsheet",
        "NRL Ladder": "ladder",
    }.get(title, "")
    tips_active = ' class="active"' if active_key == "tips" else ""
    leaderboard_active = ' class="active"' if active_key == "leaderboard" else ""
    tipsheet_active = ' class="active"' if active_key == "tipsheet" else ""
    ladder_active = ' class="active"' if active_key == "ladder" else ""
    return f"""
    <nav class="mobile-footer-nav" aria-label="Footer navigation">
      <a href="/tips"{tips_active}>My Tips</a>
      <a href="/leaderboard"{leaderboard_active}>Leaderboard</a>
      <a href="/tipsheet"{tipsheet_active}>Tipsheet</a>
      <a href="/ladder"{ladder_active}>NRL Ladder</a>
    </nav>
    """


def render_page(
    title: str,
    body: str,
    user: Row | None = None,
    flash: str | None = None,
    flash_kind: str = "ok",
) -> str:
    flash_html = ""
    if flash:
        flash_html = f'<div class="flash {escape(flash_kind)}">{escape(flash)}</div>'
    body_class = "has-mobile-footer" if user else ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#006d5b">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Aldo Tips">
  <title>{escape(title)}</title>
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/static/icon-512.png" type="image/png">
  <link rel="apple-touch-icon" href="/static/icon-512.png">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body class="{body_class}">
  <div id="app-loader" class="loading-screen">
    <div class="loading-card">
      <img src="/static/icon-512.png" alt="Aldo's Tipping Comp logo" class="loading-logo">
      <p>Loading Aldo&apos;s Tipping Comp...</p>
    </div>
  </div>
  <main class="container">
    <header class="header">
      <div class="brand-wrap">
        <img src="/static/icon-512.png" alt="Aldo's Tipping Comp logo" class="app-logo">
        <h1>Aldo&apos;s Tipping Comp</h1>
      </div>
      {_nav(user)}
    </header>
    {flash_html}
    {body}
  </main>
  {_mobile_footer_nav(user, title)}
  <script>
    (() => {{
      const loader = document.getElementById("app-loader");
      if (!loader) return;
      const hideLoader = () => {{
        loader.classList.add("hidden");
        setTimeout(() => loader.remove(), 420);
      }};
      window.addEventListener("load", hideLoader, {{ once: true }});
      setTimeout(hideLoader, 1500);
    }})();
    (() => {{
      const toggle = document.getElementById("menu-toggle");
      const menu = document.getElementById("site-menu");
      if (!toggle || !menu) return;

      const closeMenu = () => {{
        menu.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
      }};

      closeMenu();

      toggle.addEventListener("click", () => {{
        const opening = menu.hidden;
        menu.hidden = !opening;
        toggle.setAttribute("aria-expanded", opening ? "true" : "false");
      }});

      document.addEventListener("click", (event) => {{
        if (menu.hidden) return;
        const target = event.target;
        if (!(target instanceof Node)) return;
        if (!menu.contains(target) && !toggle.contains(target)) {{
          closeMenu();
        }}
      }});

      document.addEventListener("keydown", (event) => {{
        if (event.key === "Escape") {{
          closeMenu();
        }}
      }});
    }})();
    if ("serviceWorker" in navigator) {{
      const isLocalDev = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
      if (isLocalDev) {{
        navigator.serviceWorker.getRegistrations().then((regs) => {{
          regs.forEach((reg) => reg.unregister());
        }}).catch(() => {{}});
      }} else {{
        window.addEventListener("load", () => {{
          navigator.serviceWorker.register("/service-worker.js").catch(() => {{}});
        }}, {{ once: true }});
      }}
    }}
  </script>
</body>
</html>"""


def render_login(error: str | None = None, facebook_enabled: bool = True) -> str:
    error_html = f'<p class="inline-error">{escape(error)}</p>' if error else ""
    social_html = (
        '<p class="auth-divider">or</p>'
        '<a class="social-login facebook" href="/auth/facebook/start">Continue with Facebook</a>'
        if facebook_enabled
        else "<p class=\"auth-divider\">Facebook login unavailable</p>"
    )
    return f"""
    <section class="card auth-card">
      <h2>Log in</h2>
      {error_html}
      <form method="post" action="/login" class="stack">
        <label>Email <input type="email" name="email" required></label>
        <label>Password <input type="password" name="password" required></label>
        <button type="submit">Log in</button>
      </form>
      {social_html}
      <p>Need an account? <a href="/register">Register</a></p>
    </section>
    """


def render_register(error: str | None = None, facebook_enabled: bool = True) -> str:
    error_html = f'<p class="inline-error">{escape(error)}</p>' if error else ""
    social_html = (
        '<p class="auth-divider">or</p>'
        '<a class="social-login facebook" href="/auth/facebook/start">Sign up with Facebook</a>'
        if facebook_enabled
        else "<p class=\"auth-divider\">Facebook signup unavailable</p>"
    )
    return f"""
    <section class="card auth-card">
      <h2>Create Account</h2>
      {error_html}
      <form method="post" action="/register" class="stack">
        <label>Display name <input type="text" name="display_name" required maxlength="50"></label>
        <label>Email <input type="email" name="email" required></label>
        <label>Password <input type="password" name="password" required minlength="8"></label>
        <button type="submit">Create account</button>
      </form>
      {social_html}
      <p>Already registered? <a href="/login">Log in</a></p>
    </section>
    """


def _avatar_html(display_name: str, avatar_url: str | None, css_class: str) -> str:
    if avatar_url:
        return f'<img src="{escape(avatar_url)}" alt="{escape(display_name)} avatar" class="{css_class}">'
    initials = "".join(part[:1] for part in display_name.split()[:2]).upper() or "U"
    return f'<div class="{css_class} tipster-avatar-fallback">{escape(initials)}</div>'


def render_profile(user: Row) -> str:
    role_text = "Admin" if int(user["is_admin"]) == 1 else "User"
    avatar_url = str(user["avatar_url"]) if user["avatar_url"] else None
    avatar_html = _avatar_html(str(user["display_name"]), avatar_url, "profile-avatar")
    return f"""
    <section class="card">
      <h2>Profile</h2>
      <div class="profile-hero">
        {avatar_html}
        <div>
          <p><strong>Email:</strong> {escape(str(user["email"]))}</p>
          <p><strong>Role:</strong> {role_text}</p>
        </div>
      </div>
    </section>

    <section class="grid two">
      <article class="card">
        <h3>Profile Picture</h3>
        <form method="post" action="/profile/avatar" class="stack" enctype="multipart/form-data">
          <label>Upload image
            <input type="file" name="avatar" accept="image/png,image/jpeg,image/webp,image/gif,image/*" required>
          </label>
          <button type="submit">Upload picture</button>
        </form>
      </article>
      <article class="card">
        <h3>Update Display Name</h3>
        <form method="post" action="/profile/details" class="stack">
          <label>Display name
            <input type="text" name="display_name" maxlength="50" required value="{escape(str(user["display_name"]))}">
          </label>
          <button type="submit">Save name</button>
        </form>
      </article>
    </section>

    <section class="card">
      <h3>Change Password</h3>
      <form method="post" action="/profile/password" class="stack">
        <label>Current password <input type="password" name="current_password" required></label>
        <label>New password <input type="password" name="new_password" minlength="8" required></label>
        <label>Confirm new password <input type="password" name="confirm_password" minlength="8" required></label>
        <button type="submit">Update password</button>
      </form>
    </section>
    """


def render_dashboard(
    user: Row,
    counts: dict[str, int],
    current_round: int | None,
    next_fixtures: list[Row],
    recent_fixtures: list[Row],
) -> str:
    current_round_text = str(current_round) if current_round is not None else "Not set"

    upcoming_rows = "".join(
        [
            f"<tr><td>{escape(f['home_team'])} vs {escape(f['away_team'])}</td>"
            f"<td>{display_sydney(f['start_time_utc'])}</td>"
            f"<td>Round {f['round_number'] or '-'}</td></tr>"
            for f in next_fixtures
        ]
    ) or '<tr><td colspan="3">No upcoming fixtures loaded yet.</td></tr>'

    recent_rows = "".join(
        [
            f"<tr><td>{escape(f['home_team'])} {f['home_score'] if f['home_score'] is not None else '-'} "
            f"- {f['away_score'] if f['away_score'] is not None else '-'} {escape(f['away_team'])}</td>"
            f"<td>{display_sydney(f['start_time_utc'])}</td>"
            f"<td>{escape(f['winner'] or '-')}</td></tr>"
            for f in recent_fixtures
        ]
    ) or '<tr><td colspan="3">No completed fixtures yet.</td></tr>'

    return f"""
    <section class="grid three">
      <article class="card stat"><h3>Users</h3><p>{counts['users']}</p></article>
      <article class="card stat"><h3>Your Tips</h3><p>{counts['tips']}</p></article>
      <article class="card stat"><h3>Correct Tips</h3><p>{counts['correct_tips']}</p></article>
    </section>

    <section class="card">
      <h2>Welcome, {escape(user['display_name'])}</h2>
      <p>Current round: <strong>{current_round_text}</strong>. Use the <a href="/tips">Weekly Tips</a> page to submit picks.</p>
    </section>

    <section class="grid two">
      <article class="card">
        <h2>Upcoming Fixtures</h2>
        <table>
          <thead><tr><th>Match</th><th>Kickoff (Sydney)</th><th>Round</th></tr></thead>
          <tbody>{upcoming_rows}</tbody>
        </table>
      </article>
      <article class="card">
        <h2>Recent Results</h2>
        <table>
          <thead><tr><th>Result</th><th>Kickoff (Sydney)</th><th>Winner</th></tr></thead>
          <tbody>{recent_rows}</tbody>
        </table>
      </article>
    </section>
    """


def render_tips(
    user: Row,
    round_number: int | None,
    season_year: int,
    fixtures: list[Row],
    tips_by_fixture: dict[int, Row],
    selectable_rounds: list[int],
    current_round: int | None,
) -> str:
    round_options = "".join(
        [
            f'<option value="{round_value}" {"selected" if round_value == round_number else ""}>Round {round_value}</option>'
            for round_value in selectable_rounds
        ]
    )
    current_round_text = (
        f"Current round: {current_round}. Tips lock {TIP_LOCK_MINUTES} minutes before kickoff. "
        "If you miss a locked game, the underdog is auto-picked."
        if current_round is not None
        else (
            f"Current round is not set yet. Tips lock {TIP_LOCK_MINUTES} minutes before kickoff. "
            "If you miss a locked game, the underdog is auto-picked."
        )
    )
    round_picker_html = ""
    if selectable_rounds:
        round_picker_html = f"""
      <form method="get" action="/tips" class="inline-form">
        <label>Round:
          <select name="round" onchange="this.form.submit()">{round_options}</select>
        </label>
      </form>
      <p class="tip-round-note">{escape(current_round_text)}</p>
    """

    if round_number is None:
        return (
            "<section class=\"card\">"
            "<h2>Weekly Tips</h2>"
            f"{round_picker_html}"
            "<p>No fixtures available yet. Ask an admin to run data sync.</p>"
            "</section>"
        )

    now = sydney_now()
    cards = []
    for fixture in fixtures:
        fixture_id = int(fixture["id"])
        raw_home = str(fixture["home_team"])
        raw_away = str(fixture["away_team"])
        home = escape(raw_home)
        away = escape(raw_away)
        kickoff = display_sydney(fixture["start_time_utc"])
        selected_row = tips_by_fixture.get(fixture_id)
        selected = str(selected_row["tip_team"]) if selected_row is not None else None
        locked = is_tip_locked(fixture["start_time_utc"], now=now, lock_minutes=TIP_LOCK_MINUTES)
        disabled_attr = "disabled" if locked else ""
        lock_label = (
            "<span class='tip-lock-pill locked'>Locked</span>"
            if locked
            else "<span class='tip-lock-pill open'>Open</span>"
        )
        stadium_name = str(fixture["stadium_name"]).strip() if fixture["stadium_name"] else ""
        stadium_city = str(fixture["stadium_city"]).strip() if fixture["stadium_city"] else ""
        stadium_text = ""
        if stadium_name and stadium_city:
            stadium_text = f"{stadium_name}, {stadium_city}"
        elif stadium_name:
            stadium_text = stadium_name
        elif stadium_city:
            stadium_text = stadium_city
        stadium_html = (
            f"<div class='tip-match-stadium'>{escape(stadium_text)}</div>"
            if stadium_text
            else ""
        )

        home_price = f"{float(fixture['home_price']):.2f}" if fixture["home_price"] is not None else "-"
        away_price = f"{float(fixture['away_price']):.2f}" if fixture["away_price"] is not None else "-"
        home_logo = fixture["home_logo_url"]
        away_logo = fixture["away_logo_url"]
        home_initials = "".join(part[:1] for part in raw_home.split()[:2]).upper() or "H"
        away_initials = "".join(part[:1] for part in raw_away.split()[:2]).upper() or "A"

        home_logo_html = (
            f'<img src="{escape(str(home_logo))}" alt="{home} logo" class="tip-team-logo">'
            if home_logo
            else f'<div class="tip-team-logo tip-team-logo-placeholder">{escape(home_initials)}</div>'
        )
        away_logo_html = (
            f'<img src="{escape(str(away_logo))}" alt="{away} logo" class="tip-team-logo">'
            if away_logo
            else f'<div class="tip-team-logo tip-team-logo-placeholder">{escape(away_initials)}</div>'
        )

        cards.append(
            f"""
            <article class="tip-match-card {'locked-match' if locked else ''}">
              <div class="tip-match-meta">
                <span class="tip-match-kickoff">{kickoff}</span>
                {lock_label}
              </div>
              {stadium_html}
              <div class="tip-match-main">
                <div class="tip-edge">
                  <input class="tip-radio-input" id="tip_{fixture_id}_home" type="radio" name="tip_{fixture_id}" value="{home}" {"checked" if selected == raw_home else ""} {disabled_attr}>
                  <label class="tip-radio-label" for="tip_{fixture_id}_home" aria-label="Pick {home}"></label>
                </div>
                <div class="tip-team left-team">
                  <div class="tip-team-name">{home}</div>
                  {home_logo_html}
                  <div class="tip-team-odds">Odds {home_price}</div>
                </div>
                <div class="tip-versus">v</div>
                <div class="tip-team right-team">
                  <div class="tip-team-name">{away}</div>
                  {away_logo_html}
                  <div class="tip-team-odds">Odds {away_price}</div>
                </div>
                <div class="tip-edge">
                  <input class="tip-radio-input" id="tip_{fixture_id}_away" type="radio" name="tip_{fixture_id}" value="{away}" {"checked" if selected == raw_away else ""} {disabled_attr}>
                  <label class="tip-radio-label" for="tip_{fixture_id}_away" aria-label="Pick {away}"></label>
                </div>
              </div>
            </article>
            """
        )

    card_html = "".join(cards) or "<p>No fixtures for this round.</p>"
    save_disabled = "disabled" if not fixtures else ""
    return f"""
    <section class="card">
      <h2>Weekly Tips: Season {season_year}, Round {round_number}</h2>
      {round_picker_html}
      <div class="tips-round-strip">Round {round_number} selections</div>
      <form method="post" action="/tips/save">
        <input type="hidden" name="round" value="{round_number}">
        <input type="hidden" name="season_year" value="{season_year}">
        <div class="tip-cards">{card_html}</div>
        <button type="submit" {save_disabled}>Save tips</button>
      </form>
    </section>
    """


def _short_team_name(name: str) -> str:
    parts = name.split()
    if not parts:
        return name
    if len(parts) == 1:
        return parts[0]
    return " ".join(parts[-2:])


def _pick_logo_for_fixture(fixture: Row, tip_team: str) -> str | None:
    if tip_team == fixture["home_team"]:
        return fixture["home_logo_url"]
    if tip_team == fixture["away_team"]:
        return fixture["away_logo_url"]
    return None


def render_tipsheet(
    user: Row,
    season_year: int,
    round_number: int | None,
    round_numbers: list[int],
    fixtures: list[Row],
    participants: list[dict[str, Any]],
    tips_by_user_fixture: dict[tuple[int, int], Row],
    all_submitted: bool,
    total_required: int,
) -> str:
    if round_number is None:
        return '<section class="card"><h2>Tipsheet</h2><p>No fixtures available yet.</p></section>'

    round_options = "".join(
        [
            f'<option value="{r}" {"selected" if r == round_number else ""}>Round {r}</option>'
            for r in round_numbers
        ]
    )

    submitted_count = sum(1 for p in participants if p["has_submitted"])
    status_html = (
        f"<p class='tipsheet-status ok'>All submissions received ({submitted_count}/{len(participants)}). Everyone's tips are now visible.</p>"
        if all_submitted
        else f"<p class='tipsheet-status pending'>Submissions: {submitted_count}/{len(participants)}. Tips unlock after all users submit.</p>"
    )

    pending_names = [p["display_name"] for p in participants if not p["has_submitted"]]
    pending_html = ""
    if pending_names:
        pending_html = "<p class='tipsheet-pending'>Pending: " + ", ".join(escape(name) for name in pending_names) + "</p>"

    header_cols = []
    for game_num, fixture in enumerate(fixtures, start=1):
        header_cols.append(
            f'<th class="tipsheet-fixture-col">G{game_num}</th>'
        )
    header_html = "".join(header_cols) if header_cols else "<th>No fixtures</th>"

    row_html = []
    for participant in participants:
        uid = int(participant["id"])
        first_name = participant["display_name"].split()[0] if participant["display_name"] else "User"
        avatar_url = str(participant["avatar_url"]) if participant.get("avatar_url") else None
        avatar_html = _avatar_html(participant["display_name"], avatar_url, "ts-avatar")
        cells = []
        for fixture in fixtures:
            key = (uid, int(fixture["id"]))
            tip = tips_by_user_fixture.get(key)
            if not all_submitted:
                cell = "<td class='tipsheet-cell locked-cell'>-</td>"
            elif tip is None:
                cell = "<td class='tipsheet-cell empty-cell'>-</td>"
            else:
                tip_team = tip["tip_team"]
                logo_url = _pick_logo_for_fixture(fixture, tip_team)
                result_class = ""
                if fixture["status"] == "completed" and tip["points_awarded"] is not None:
                    result_class = " correct-pick" if int(tip["points_awarded"]) == 1 else " wrong-pick"
                logo_html = (
                    f"<img src=\"{escape(str(logo_url))}\" alt=\"{escape(str(tip_team))}\" class=\"pick-logo\">"
                    if logo_url
                    else f"<div class='pick-team'>{escape(str(tip_team))}</div>"
                )
                cell = f"<td class='tipsheet-cell{result_class}'>{logo_html}</td>"
            cells.append(cell)

        row_html.append(
            f"""
            <tr>
              <td class="tipster-col">
                {avatar_html}
                <div class="ts-name">{escape(first_name)}</div>
              </td>
              {''.join(cells)}
            </tr>
            """
        )

    body_html = "".join(row_html) if row_html else "<tr><td>No users found.</td></tr>"
    return f"""
    <section class="card">
      <h2>Tipsheet: Season {season_year}, Round {round_number}</h2>
      <form method="get" action="/tipsheet" class="inline-form">
        <label>Round:
          <select name="round" onchange="this.form.submit()">{round_options}</select>
        </label>
      </form>
      {status_html}
      {pending_html}
      <div class="tipsheet-wrap">
        <table class="tipsheet-table">
          <thead>
            <tr>
              <th class="tipster-col"></th>
              {header_html}
            </tr>
          </thead>
          <tbody>
            {body_html}
          </tbody>
        </table>
      </div>
    </section>
    """


def render_leaderboard(
    user: Row,
    players: list[dict[str, Any]],
    round_numbers: list[int],
    season_year: int,
) -> str:
    if not players:
        return '<section class="card"><h2>Leaderboard</h2><p>No players yet.</p></section>'

    # --- Podium (top 3) ---
    podium_order = [1, 0, 2]  # 2nd, 1st, 3rd
    podium_html = ""
    top3 = players[:3]
    if top3:
        podium_items = []
        for display_idx in podium_order:
            if display_idx >= len(top3):
                podium_items.append('<div class="podium-slot empty"></div>')
                continue
            p = top3[display_idx]
            rank = display_idx + 1
            avatar = _avatar_html(p["display_name"], p["avatar_url"], "podium-avatar")
            height_class = f"podium-{rank}"
            podium_items.append(f"""
              <div class="podium-slot {height_class}">
                {avatar}
                <div class="podium-name">{escape(p["display_name"])}</div>
                <div class="podium-points">{p["total_points"]} pts</div>
                <div class="podium-bar">
                  <span class="podium-rank">#{rank}</span>
                </div>
              </div>
            """)
        podium_html = f'<div class="podium-wrap">{"".join(podium_items)}</div>'

    # --- Round-by-round scores table ---
    round_headers = "".join(f"<th>R{rn}</th>" for rn in round_numbers)
    rows_html = []
    for idx, p in enumerate(players, start=1):
        round_cells = "".join(
            f"<td>{p['round_points'].get(rn, 0)}</td>" for rn in round_numbers
        )
        avatar = _avatar_html(p["display_name"], p["avatar_url"], "lb-avatar")
        rows_html.append(
            f"<tr>"
            f"<td class='lb-rank'>{idx}</td>"
            f"<td class='lb-player'>{avatar}<span>{escape(p['display_name'])}</span></td>"
            f"{round_cells}"
            f"<td class='lb-total'>{p['total_points']}</td>"
            f"</tr>"
        )
    table_body = "".join(rows_html)

    return f"""
    <section class="card">
      <h2>Leaderboard &mdash; Season {season_year}</h2>
      {podium_html}
      <div class="lb-table-wrap">
        <table class="lb-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Player</th>
              {round_headers}
              <th>Total</th>
            </tr>
          </thead>
          <tbody>{table_body}</tbody>
        </table>
      </div>
    </section>
    """


def render_ladder(ladder: list[dict[str, Any]], season_year: int) -> str:
    if not ladder:
        return '<section class="card"><h2>NRL Ladder</h2><p>No completed fixtures yet for this season.</p></section>'

    rows_html = []
    for idx, team in enumerate(ladder, start=1):
        logo_html = (
            f'<img src="{escape(str(team["logo_url"]))}" alt="" class="ladder-logo">'
            if team["logo_url"]
            else ""
        )
        finals_class = " finals-spot" if idx <= 8 else ""
        diff = team["point_diff"]
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        rows_html.append(
            f"<tr class='ladder-row{finals_class}'>"
            f"<td class='ladder-pos'>{idx}</td>"
            f"<td class='ladder-team'>{logo_html}<span>{escape(str(team['team']))}</span></td>"
            f"<td>{team['played']}</td>"
            f"<td>{team['won']}</td>"
            f"<td>{team['lost']}</td>"
            f"<td>{team['drawn']}</td>"
            f"<td>{team['points_for']}</td>"
            f"<td>{team['points_against']}</td>"
            f"<td class='ladder-diff'>{diff_str}</td>"
            f"<td class='ladder-pts'>{team['comp_points']}</td>"
            f"</tr>"
        )

    return f"""
    <section class="card">
      <h2>NRL Ladder &mdash; {season_year}</h2>
      <div class="ladder-wrap">
        <table class="ladder-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Team</th>
              <th>P</th>
              <th>W</th>
              <th>L</th>
              <th>D</th>
              <th>PF</th>
              <th>PA</th>
              <th>PD</th>
              <th>Pts</th>
            </tr>
          </thead>
          <tbody>{"".join(rows_html)}</tbody>
        </table>
      </div>
      <p class="ladder-note">Top 8 teams qualify for finals.</p>
    </section>
    """


def render_admin(
    user: Row,
    last_sync: str | None,
    latest_summary: str | None,
    facebook_check: dict[str, Any] | None = None,
) -> str:
    if int(user["is_admin"]) != 1:
        return '<section class="card"><h2>Admin</h2><p>Admin access required.</p></section>'
    summary_block = f"<pre>{escape(latest_summary)}</pre>" if latest_summary else "<p>No sync run in this session yet.</p>"
    sync_time = display_sydney(last_sync) if last_sync else "never"
    fb_status_html = ""
    if facebook_check is not None:
        status_text = "Configured" if facebook_check.get("enabled") else "Not configured"
        callback_url = str(facebook_check.get("callback_url") or "")
        graph_version = str(facebook_check.get("graph_version") or "")
        scopes = str(facebook_check.get("scopes") or "")
        app_id_text = str(facebook_check.get("app_id_display") or "missing")
        app_secret_status = str(facebook_check.get("app_secret_status") or "missing")
        missing = facebook_check.get("missing") or []
        missing_lines = "".join(f"<li><code>{escape(str(item))}</code></li>" for item in missing)
        missing_html = (
            f"<p><strong>Missing values:</strong></p><ul>{missing_lines}</ul>"
            if missing_lines
            else "<p><strong>Missing values:</strong> none</p>"
        )
        fb_status_html = f"""
      <h3>Facebook Login Check</h3>
      <p><strong>Status:</strong> {escape(status_text)}</p>
      <p><strong>App ID:</strong> <code>{escape(app_id_text)}</code></p>
      <p><strong>App Secret:</strong> {escape(app_secret_status)}</p>
      <p><strong>Graph Version:</strong> <code>{escape(graph_version)}</code></p>
      <p><strong>Scopes:</strong> <code>{escape(scopes)}</code></p>
      <p><strong>Callback URL:</strong> <code>{escape(callback_url)}</code></p>
      {missing_html}
        """
    return f"""
    <section class="card">
      <h2>Admin Tools</h2>
      <p>Last data sync (Sydney): <strong>{escape(sync_time)}</strong></p>
      <p><a href="/tipsheet">Open round tipsheet</a></p>
      <form method="post" action="/admin/sync" class="inline-form">
        <label>Season year <input type="number" name="season_year" min="2020" max="2100" value="{sydney_now().year}"></label>
        <button type="submit">Sync entire season</button>
      </form>
      <h3>Latest Sync Result</h3>
      {summary_block}
      {fb_status_html}
    </section>
    """
