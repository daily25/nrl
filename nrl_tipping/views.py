from __future__ import annotations

from html import escape
from sqlite3 import Row
from typing import Any

from nrl_tipping.config import TIP_LOCK_MINUTES
from nrl_tipping.utils import display_sydney, is_round_locked, is_tip_locked, sydney_now


def _push_subscribe_script() -> str:
    """Return JS that silently re-subscribes if permission already granted.

    Does NOT auto-prompt — the profile page has an explicit button for that.
    """
    return """
    <script>
    (async () => {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
      if (Notification.permission !== "granted") return;

      let vapidKey;
      try {
        const resp = await fetch("/api/push/vapid-key");
        const data = await resp.json();
        vapidKey = data.vapid_public_key;
        if (!vapidKey) return;
      } catch (e) { return; }

      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();

      if (sub) {
        await fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sub.toJSON()),
        }).catch(() => {});
        return;
      }

      function urlBase64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
        const raw = atob(base64);
        const arr = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        return arr;
      }

      try {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(vapidKey),
        });
        await fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sub.toJSON()),
        });
      } catch (e) {}
    })();
    </script>
    """


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
        <a href="/predict-ladder">Predict Ladder</a>
        <a href="/predictions">Predictions</a>
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
        "Predict the Ladder": "predict-ladder",
    }.get(title, "")
    tips_active = ' class="active"' if active_key == "tips" else ""
    predict_active = ' class="active"' if active_key == "predict-ladder" else ""
    tipsheet_active = ' class="active"' if active_key == "tipsheet" else ""
    leaderboard_active = ' class="active"' if active_key == "leaderboard" else ""
    return f"""
    <nav class="mobile-footer-nav" aria-label="Footer navigation">
      <a href="/tips"{tips_active}>My Tips</a>
      <a href="/predict-ladder"{predict_active}>Predict</a>
      <a href="/tipsheet"{tipsheet_active}>Tipsheet</a>
      <a href="/leaderboard"{leaderboard_active}>Leaderboard</a>
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
  <main class="container">
    <header class="header">
      <div class="brand-wrap">
        <a href="/tips"><img src="/static/icon-512.png" alt="Aldo's Tipping Comp logo" class="app-logo"></a>
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
  {_push_subscribe_script() if user else ""}
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

    <section class="card" id="push-section">
      <h3>Notifications</h3>
      <p>Get a reminder when tips are due before each round.</p>
      <div id="push-status"><p style="color:var(--muted)">Checking...</p></div>
      <button type="button" id="push-toggle-btn" style="display:none">Enable notifications</button>
    </section>
    <script>
    (function() {{
      var statusEl = document.getElementById("push-status");
      var btn = document.getElementById("push-toggle-btn");

      var isBrave = navigator.brave && typeof navigator.brave.isBrave === "function";
      if (isBrave) {{
        statusEl.innerHTML = "<p style='color:var(--muted)'>Push notifications are not supported in Brave browser. Try Chrome or Samsung Browser instead.</p>";
        return;
      }}
      if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {{
        statusEl.innerHTML = "<p style='color:var(--muted)'>Push notifications are not supported on this device/browser.</p>";
        return;
      }}

      function urlB64(base64String) {{
        var padding = "=".repeat((4 - base64String.length % 4) % 4);
        var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
        var raw = atob(base64);
        var arr = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        return arr;
      }}

      var vapidKey = null;

      function getVapidKey() {{
        return fetch("/api/push/vapid-key")
          .then(function(r) {{ return r.json(); }})
          .then(function(d) {{ vapidKey = d.vapid_public_key || null; return vapidKey; }})
          .catch(function() {{ return null; }});
      }}

      function ensureSW() {{
        return navigator.serviceWorker.register("/service-worker.js").then(function(reg) {{
          if (reg.active) return reg;
          return new Promise(function(resolve) {{
            var sw = reg.installing || reg.waiting;
            if (!sw) {{ console.warn("ensureSW: no installing/waiting worker"); return resolve(null); }}
            if (sw.state === "activated") return resolve(reg);
            console.log("ensureSW: waiting for state:", sw.state);
            var t = setTimeout(function() {{
              console.warn("ensureSW: timed out in state:", sw.state);
              resolve(null);
            }}, 10000);
            sw.addEventListener("statechange", function() {{
              console.log("ensureSW: statechange:", sw.state);
              if (sw.state === "activated") {{ clearTimeout(t); resolve(reg); }}
              else if (sw.state === "redundant") {{ clearTimeout(t); resolve(null); }}
            }});
          }});
        }}).catch(function(e) {{
          console.error("ensureSW error:", e);
          return null;
        }});
      }}

      function showEnabled() {{
        statusEl.innerHTML = "<p style='color:#2e7d32'>Notifications are <strong>enabled</strong>.</p>";
        btn.textContent = "Disable notifications";
        btn.style.display = "";
        btn.disabled = false;
        btn.onclick = disablePush;
      }}

      function showOff() {{
        statusEl.innerHTML = "<p>Notifications are <strong>off</strong>.</p>";
        btn.textContent = "Enable notifications";
        btn.style.display = "";
        btn.disabled = false;
        btn.onclick = enablePush;
      }}

      function showError(msg) {{
        statusEl.innerHTML = "<p style='color:#c62828'>" + msg + "</p>";
        btn.textContent = "Try again";
        btn.style.display = "";
        btn.disabled = false;
        btn.onclick = enablePush;
      }}

      function updateUI() {{
        var perm = Notification.permission;
        if (perm === "denied") {{
          statusEl.innerHTML = "<p style='color:var(--muted)'>Notifications are blocked. To fix: open your browser settings &rarr; Site settings &rarr; Notifications &rarr; find this site and change to Allow. Then refresh this page.</p>";
          btn.style.display = "none";
          return Promise.resolve();
        }}

        return getVapidKey().then(function(key) {{
          if (!key) {{
            statusEl.innerHTML = "<p style='color:var(--muted)'>Push notifications are not configured on this server.</p>";
            btn.style.display = "none";
            return;
          }}
          return ensureSW().then(function(reg) {{
            if (!reg) {{
              showOff();
              return;
            }}
            return reg.pushManager.getSubscription().then(function(sub) {{
              if (perm === "granted" && sub) {{
                showEnabled();
              }} else {{
                showOff();
              }}
            }});
          }});
        }}).catch(function() {{
          showOff();
        }});
      }}

      function enablePush() {{
        btn.disabled = true;
        btn.textContent = "Enabling...";
        statusEl.innerHTML = "<p style='color:var(--muted)'>Requesting permission...</p>";
        Notification.requestPermission().then(function(perm) {{
          if (perm !== "granted") {{
            return updateUI();
          }}
          statusEl.innerHTML = "<p style='color:var(--muted)'>Setting up service worker...</p>";
          return getVapidKey().then(function(key) {{
            if (!key) return showError("Server VAPID key not available.");
            return ensureSW().then(function(reg) {{
              if (!reg) {{
                var dbg = "no reg";
                try {{
                  var r2 = navigator.serviceWorker.controller;
                  dbg = "controller=" + (r2 ? r2.state : "null");
                }} catch(x) {{}}
                return showError("Service worker failed to activate (" + dbg + "). Open browser DevTools &rarr; Application &rarr; Service Workers for details.");
              }}
              statusEl.innerHTML = "<p style='color:var(--muted)'>Subscribing to push...</p>";
              return reg.pushManager.subscribe({{
                userVisibleOnly: true,
                applicationServerKey: urlB64(key),
              }}).then(function(sub) {{
                statusEl.innerHTML = "<p style='color:var(--muted)'>Saving subscription...</p>";
                return fetch("/api/push/subscribe", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify(sub.toJSON()),
                }}).then(function(resp) {{
                  if (!resp.ok) return showError("Server rejected subscription (HTTP " + resp.status + ").");
                  return updateUI();
                }});
              }});
            }});
          }});
        }}).catch(function(e) {{
          showError("Error: " + (e.message || String(e)));
        }});
      }}

      function disablePush() {{
        btn.disabled = true;
        btn.textContent = "Disabling...";
        ensureSW().then(function(reg) {{
          if (!reg) return updateUI();
          return reg.pushManager.getSubscription().then(function(sub) {{
            if (!sub) return updateUI();
            var endpoint = sub.endpoint;
            return sub.unsubscribe().then(function() {{
              return fetch("/api/push/unsubscribe", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ endpoint: endpoint }}),
              }}).catch(function() {{}});
            }}).then(function() {{
              return updateUI();
            }});
          }});
        }}).catch(function(e) {{
          showError("Error disabling: " + (e.message || String(e)));
        }});
      }}

      updateUI();
    }})();
    </script>
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
        f"Current round: {current_round}. All tips lock when the first game of the round starts. "
        "If you haven't tipped by then, the underdog is auto-picked for all games."
        if current_round is not None
        else (
            "Current round is not set yet. All tips lock when the first game of the round starts. "
            "If you haven't tipped by then, the underdog is auto-picked for all games."
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
    round_locked = is_round_locked(fixtures, now=now, lock_minutes=TIP_LOCK_MINUTES)
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
        locked = round_locked
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
    round_locked: bool = False,
    current_user_id: int | None = None,
) -> str:
    if round_number is None:
        return '<section class="card"><h2>Tipsheet</h2><p>No fixtures available yet.</p></section>'

    round_options = "".join(
        [
            f'<option value="{r}" {"selected" if r == round_number else ""}>Round {r}</option>'
            for r in round_numbers
        ]
    )

    # Tips are visible once the round is locked (first game has started)
    tips_visible = round_locked

    submitted_count = sum(1 for p in participants if p["has_submitted"])
    if tips_visible:
        status_html = f"<p class='tipsheet-status ok'>Round locked — all tips are now visible. Submissions: {submitted_count}/{len(participants)}.</p>"
    else:
        status_html = f"<p class='tipsheet-status pending'>Tips are hidden until the first game of the round starts. Submissions: {submitted_count}/{len(participants)}.</p>"

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
        is_own_row = current_user_id is not None and uid == current_user_id
        for fixture in fixtures:
            key = (uid, int(fixture["id"]))
            tip = tips_by_user_fixture.get(key)
            if not tips_visible and not is_own_row:
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
    # Prize calculation: $20 entry, 70% tipping pool, 80%/20% split
    total_pool = len(players) * 20
    tipping_pool = total_pool * 0.70
    prize_1st = tipping_pool * 0.80
    prize_2nd = tipping_pool * 0.20
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
            prize_html = ""
            if rank == 1:
                prize_html = f'<div class="podium-prize">${prize_1st:,.2f}</div>'
            elif rank == 2:
                prize_html = f'<div class="podium-prize">${prize_2nd:,.2f}</div>'
            podium_items.append(f"""
              <div class="podium-slot {height_class}">
                {avatar}
                <div class="podium-name">{escape(p["display_name"])}</div>
                <div class="podium-points">{p["total_points"]} pts</div>
                <div class="podium-bar">
                  <span class="podium-rank">#{rank}</span>
                  {prize_html}
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


def render_predict_ladder(
    user: Row,
    teams: list[dict[str, Any]],
    existing_prediction: list[dict[str, Any]],
    leaderboard: list[dict[str, Any]],
    actual_ladder: list[dict[str, Any]],
    season_year: int,
) -> str:
    # Build ordered team list: use existing prediction order, or default alphabetical
    if existing_prediction:
        ordered = existing_prediction
        team_lookup = {t["team"]: t for t in teams}
        team_items = []
        for pred in ordered:
            t = team_lookup.get(pred["team"], {})
            logo = t.get("logo_url") or ""
            team_items.append({"team": pred["team"], "logo_url": logo})
    else:
        team_items = list(teams)

    total = len(team_items)
    list_html = []
    for idx, t in enumerate(team_items, start=1):
        logo_html = (
            f'<img src="{escape(str(t["logo_url"]))}" alt="" class="pldr-logo">'
            if t.get("logo_url")
            else ""
        )
        finals_cls = " pldr-finals" if idx <= 8 else ""
        # Insert zone headers
        if idx == 1:
            list_html.append('<li class="pldr-zone pldr-zone-finals">Finals (1&ndash;8)</li>')
        elif idx == 9:
            list_html.append('<li class="pldr-zone pldr-zone-elim">Eliminated (9&ndash;{0})</li>'.format(total))
        list_html.append(
            f'<li class="pldr-item{finals_cls}" data-team="{escape(t["team"])}">'
            f'<span class="pldr-pos">{idx}</span>'
            f'{logo_html}'
            f'<span class="pldr-name">{escape(t["team"])}</span>'
            f'<span class="pldr-handle">&#x2630;</span>'
            f'</li>'
        )

    has_prediction = bool(existing_prediction)
    status_text = "Your prediction is saved." if has_prediction else "Drag teams into your predicted order, then save."

    # Leaderboard section
    lb_html = ""
    if leaderboard and actual_ladder:
        lb_rows = []
        for idx, entry in enumerate(leaderboard, start=1):
            avatar = _avatar_html(entry["display_name"], entry["avatar_url"], "pldr-lb-avatar")
            is_me = " class='pldr-lb-me'" if entry["user_id"] == int(user["id"]) else ""
            lb_rows.append(
                f"<tr{is_me}>"
                f"<td>{idx}</td>"
                f"<td class='pldr-lb-player'>{avatar}<span>{escape(entry['display_name'])}</span></td>"
                f"<td class='pldr-lb-diff'>{entry['total_diff']}</td>"
                f"</tr>"
            )
        lb_html = f"""
        <section class="card" style="margin-top:1rem">
          <h3>Ladder Prediction Standings</h3>
          <p class="pldr-note">Lower score = closer to actual ladder. Best possible score is 0.</p>
          <table class="pldr-lb-table">
            <thead><tr><th>#</th><th>Player</th><th>Diff</th></tr></thead>
            <tbody>{"".join(lb_rows)}</tbody>
          </table>
        </section>
        """

    return f"""
    <section class="card">
      <h2>Predict the Ladder &mdash; {season_year}</h2>
      <div class="pldr-countdown-wrap" id="pldr-countdown-wrap">
        <div class="pldr-countdown" id="pldr-countdown"></div>
      </div>
      <p class="pldr-status">{status_text}</p>
      <form method="post" action="/predict-ladder" id="pldr-form">
        <input type="hidden" name="season_year" value="{season_year}">
        <ol class="pldr-list" id="pldr-list">
          {"".join(list_html)}
        </ol>
        <input type="hidden" name="order" id="pldr-order" value="">
        <button type="submit" id="pldr-submit">Save Prediction</button>
      </form>
    </section>
    {lb_html}
    <script>
    (() => {{
      const list = document.getElementById("pldr-list");
      const orderInput = document.getElementById("pldr-order");
      if (!list || !orderInput) return;

      let dragItem = null;
      let touchStartY = 0;
      let touchOffsetY = 0;

      function updatePositions() {{
        // Remove old zone headers
        list.querySelectorAll(".pldr-zone").forEach(z => z.remove());
        const items = list.querySelectorAll(".pldr-item");
        const total = items.length;
        items.forEach((el, i) => {{
          const pos = i + 1;
          el.querySelector(".pldr-pos").textContent = pos;
          el.classList.toggle("pldr-finals", pos <= 8);
          // Insert zone headers
          if (pos === 1) {{
            const hdr = document.createElement("li");
            hdr.className = "pldr-zone pldr-zone-finals";
            hdr.innerHTML = "Finals (1&ndash;8)";
            list.insertBefore(hdr, el);
          }} else if (pos === 9) {{
            const hdr = document.createElement("li");
            hdr.className = "pldr-zone pldr-zone-elim";
            hdr.innerHTML = "Eliminated (9&ndash;" + total + ")";
            list.insertBefore(hdr, el);
          }}
        }});
        const teams = Array.from(items).map(el => el.dataset.team);
        orderInput.value = JSON.stringify(teams);
      }}

      // Desktop drag & drop
      list.addEventListener("dragstart", (e) => {{
        if (!e.target.classList.contains("pldr-item")) return;
        dragItem = e.target;
        e.target.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
      }});

      list.addEventListener("dragend", (e) => {{
        if (dragItem) dragItem.classList.remove("dragging");
        dragItem = null;
        updatePositions();
      }});

      list.addEventListener("dragover", (e) => {{
        e.preventDefault();
        const target = e.target.closest(".pldr-item");
        if (!target || target === dragItem) return;
        const rect = target.getBoundingClientRect();
        const mid = rect.top + rect.height / 2;
        if (e.clientY < mid) {{
          list.insertBefore(dragItem, target);
        }} else {{
          list.insertBefore(dragItem, target.nextSibling);
        }}
      }});

      // Touch drag & drop
      list.addEventListener("touchstart", (e) => {{
        const handle = e.target.closest(".pldr-handle");
        if (!handle) return;
        const item = handle.closest(".pldr-item");
        if (!item) return;
        dragItem = item;
        const touch = e.touches[0];
        const rect = item.getBoundingClientRect();
        touchStartY = touch.clientY;
        touchOffsetY = touch.clientY - rect.top;
        item.classList.add("dragging");
      }}, {{ passive: true }});

      list.addEventListener("touchmove", (e) => {{
        if (!dragItem) return;
        e.preventDefault();
        const touch = e.touches[0];
        const items = Array.from(list.querySelectorAll(".pldr-item:not(.dragging)"));
        for (const item of items) {{
          const rect = item.getBoundingClientRect();
          const mid = rect.top + rect.height / 2;
          if (touch.clientY < mid) {{
            list.insertBefore(dragItem, item);
            break;
          }} else if (item === items[items.length - 1]) {{
            list.insertBefore(dragItem, item.nextSibling);
          }}
        }}
      }}, {{ passive: false }});

      list.addEventListener("touchend", () => {{
        if (dragItem) {{
          dragItem.classList.remove("dragging");
          dragItem = null;
          updatePositions();
        }}
      }});

      // Make items draggable
      list.querySelectorAll(".pldr-item").forEach(el => {{
        el.setAttribute("draggable", "true");
      }});

      // Set initial order
      updatePositions();
    }})();

    // Countdown timer — deadline 12 March {season_year} 8pm Sydney (AEDT = UTC+11)
    (() => {{
      const DEADLINE = new Date("{season_year}-03-12T20:00:00+11:00").getTime();
      const countdownEl = document.getElementById("pldr-countdown");
      const wrapEl = document.getElementById("pldr-countdown-wrap");
      const form = document.getElementById("pldr-form");
      const submitBtn = document.getElementById("pldr-submit");
      if (!countdownEl) return;

      function pad(n) {{ return String(n).padStart(2, "0"); }}

      function tick() {{
        const now = Date.now();
        const diff = DEADLINE - now;
        if (diff <= 0) {{
          countdownEl.innerHTML = '<span class="pldr-cd-closed">Predictions are closed</span>';
          if (wrapEl) wrapEl.classList.add("pldr-cd-expired");
          if (submitBtn) {{ submitBtn.disabled = true; submitBtn.textContent = "Closed"; }}
          // Disable dragging
          document.querySelectorAll(".pldr-item").forEach(el => {{
            el.removeAttribute("draggable");
            el.style.cursor = "default";
          }});
          document.querySelectorAll(".pldr-handle").forEach(el => {{
            el.style.display = "none";
          }});
          return;
        }}
        const days = Math.floor(diff / 86400000);
        const hours = Math.floor((diff % 86400000) / 3600000);
        const mins = Math.floor((diff % 3600000) / 60000);
        const secs = Math.floor((diff % 60000) / 1000);
        countdownEl.innerHTML =
          '<span class="pldr-cd-label">Predictions close in</span>' +
          '<span class="pldr-cd-time">' +
          (days > 0 ? '<span class="pldr-cd-unit"><span class="pldr-cd-num">' + days + '</span>d</span>' : '') +
          '<span class="pldr-cd-unit"><span class="pldr-cd-num">' + pad(hours) + '</span>h</span>' +
          '<span class="pldr-cd-unit"><span class="pldr-cd-num">' + pad(mins) + '</span>m</span>' +
          '<span class="pldr-cd-unit"><span class="pldr-cd-num">' + pad(secs) + '</span>s</span>' +
          '</span>';
        setTimeout(tick, 1000);
      }}
      tick();
    }})();
    </script>
    """


def render_all_predictions(
    user: Row,
    predictions_by_user: dict[int, dict[str, Any]],
    teams: list[dict[str, Any]],
    season_year: int,
    season_started: bool,
    actual_ladder: list[dict[str, Any]],
    leaderboard: list[dict[str, Any]],
) -> str:
    if not season_started:
        return f"""
        <section class="card">
          <h2>Ladder Predictions &mdash; {season_year}</h2>
          <p>Predictions will be visible once the first game of the season starts.</p>
          <p><a href="/predict-ladder">Submit your prediction &rarr;</a></p>
        </section>
        """

    if not predictions_by_user:
        return f"""
        <section class="card">
          <h2>Ladder Predictions &mdash; {season_year}</h2>
          <p>No predictions have been submitted yet.</p>
          <p><a href="/predict-ladder">Submit your prediction &rarr;</a></p>
        </section>
        """

    team_logos = {t["team"]: t.get("logo_url") for t in teams}

    # Leaderboard section
    lb_html = ""
    if leaderboard and actual_ladder:
        lb_rows = []
        for idx, entry in enumerate(leaderboard, start=1):
            avatar = _avatar_html(entry["display_name"], entry["avatar_url"], "pldr-lb-avatar")
            is_me = " class='pldr-lb-me'" if entry["user_id"] == int(user["id"]) else ""
            lb_rows.append(
                f"<tr{is_me}>"
                f"<td>{idx}</td>"
                f"<td class='pldr-lb-player'>{avatar}<span>{escape(entry['display_name'])}</span></td>"
                f"<td class='pldr-lb-diff'>{entry['total_diff']}</td>"
                f"</tr>"
            )
        lb_html = f"""
        <section class="card" style="margin-top:1rem">
          <h3>Ladder Prediction Standings</h3>
          <p class="pldr-note">Lower score = closer to actual ladder. Best possible score is 0.</p>
          <table class="pldr-lb-table">
            <thead><tr><th>#</th><th>Player</th><th>Diff</th></tr></thead>
            <tbody>{"".join(lb_rows)}</tbody>
          </table>
        </section>
        """

    # Build prediction columns
    users_data = list(predictions_by_user.values())
    num_positions = max((len(u["predictions"]) for u in users_data), default=0)

    header_cols = []
    for u_data in users_data:
        avatar = _avatar_html(u_data["display_name"], u_data["avatar_url"], "ts-avatar")
        first_name = u_data["display_name"].split()[0] if u_data["display_name"] else "User"
        header_cols.append(
            f'<th class="tipster-col">{avatar}<div class="ts-name">{escape(first_name)}</div></th>'
        )

    body_rows = []
    for pos in range(1, num_positions + 1):
        zone = ""
        if pos == 1:
            zone = f'<tr><td class="pldr-zone pldr-zone-finals" colspan="{len(users_data) + 1}">Finals (1&ndash;8)</td></tr>'
        elif pos == 9:
            zone = f'<tr><td class="pldr-zone pldr-zone-elim" colspan="{len(users_data) + 1}">Eliminated (9&ndash;{num_positions})</td></tr>'

        cells = [f'<td class="pred-pos">{pos}</td>']
        for u_data in users_data:
            preds = u_data["predictions"]
            team = preds[pos - 1]["team"] if pos <= len(preds) else "-"
            logo = team_logos.get(team)
            if logo:
                cells.append(f'<td class="tipsheet-cell"><img src="{escape(str(logo))}" alt="" class="pick-logo"><div class="pick-team">{escape(team)}</div></td>')
            else:
                cells.append(f'<td class="tipsheet-cell"><div class="pick-team">{escape(team)}</div></td>')

        body_rows.append(f"{zone}<tr>{''.join(cells)}</tr>")

    return f"""
    <section class="card">
      <h2>Ladder Predictions &mdash; {season_year}</h2>
      <p><a href="/predict-ladder">Edit your prediction &rarr;</a></p>
      <div class="tipsheet-wrap">
        <table class="tipsheet-table">
          <thead>
            <tr>
              <th class="pred-pos-hdr">#</th>
              {"".join(header_cols)}
            </tr>
          </thead>
          <tbody>
            {"".join(body_rows)}
          </tbody>
        </table>
      </div>
    </section>
    {lb_html}
    """


def render_ladder_adjust(
    user: Row,
    predictions: list[dict[str, Any]],
    teams: list[dict[str, Any]],
    completed_rounds: list[int],
    used_rounds: set[int],
    season_year: int,
) -> str:
    """Render the adjustment section shown below the main prediction."""
    if not predictions:
        return ""

    # Find available rounds (completed but not yet used)
    available_rounds = [r for r in completed_rounds if r not in used_rounds]
    if not available_rounds:
        used_list = ", ".join(f"R{r}" for r in sorted(used_rounds)) if used_rounds else "none"
        return f"""
        <section class="card" style="margin-top:1rem">
          <h3>Round Adjustment</h3>
          <p>No completed rounds available for adjustment right now.</p>
          <p style="font-size:.85rem;color:var(--muted)">Moves used: {used_list}</p>
        </section>
        """

    team_logos = {t["team"]: t.get("logo_url") for t in teams}

    # Build team options
    team_options = []
    for p in predictions:
        logo = team_logos.get(p["team"])
        logo_attr = f' data-logo="{escape(str(logo))}"' if logo else ""
        team_options.append(
            f'<option value="{escape(p["team"])}"{logo_attr}>{p["position"]}. {escape(p["team"])}</option>'
        )

    round_options = []
    for r in available_rounds:
        round_options.append(f'<option value="{r}">Round {r}</option>')

    used_list = ", ".join(f"R{r}" for r in sorted(used_rounds)) if used_rounds else "none yet"

    return f"""
    <section class="card" style="margin-top:1rem">
      <h3>Round Adjustment</h3>
      <p>After each completed round, you may move <strong>one team</strong> up or down by <strong>one position</strong>.</p>
      <p style="font-size:.85rem;color:var(--muted)">Moves used: {used_list}</p>
      <form method="post" action="/predict-ladder/adjust" class="inline-form">
        <input type="hidden" name="season_year" value="{season_year}">
        <label>Round:
          <select name="round_number">{"".join(round_options)}</select>
        </label>
        <label>Team:
          <select name="team">{"".join(team_options)}</select>
        </label>
        <label>Direction:
          <select name="direction">
            <option value="up">&#9650; Move Up</option>
            <option value="down">&#9660; Move Down</option>
          </select>
        </label>
        <button type="submit">Apply Move</button>
      </form>
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
      <p><a href="/admin/users">Manage Users</a></p>
    </section>
    """


def render_admin_users(users: list, flash_password: dict | None = None) -> str:
    rows = []
    for u in users:
        uid = int(u["id"])
        name = escape(str(u["display_name"]))
        email = escape(str(u["email"]))
        role = "Admin" if int(u["is_admin"]) == 1 else "User"
        provider = escape(str(u["auth_provider"] or "local"))
        avatar_url = str(u["avatar_url"]) if u["avatar_url"] else None
        avatar_html = _avatar_html(str(u["display_name"]), avatar_url, "admin-user-avatar")

        # Password flash for this user (after reset)
        pw_flash = ""
        if flash_password and flash_password.get("user_id") == uid:
            temp_pw = escape(str(flash_password["password"]))
            pw_flash = f"""
            <div class="flash ok" style="margin:.5rem 0;font-size:.85rem">
              New password: <code style="user-select:all;font-weight:bold">{temp_pw}</code>
              — share this with the user, it won't be shown again.
            </div>
            """

        rows.append(f"""
        <article class="card admin-user-card">
          <div class="admin-user-header">
            {avatar_html}
            <div>
              <strong>{name}</strong><br>
              <span class="admin-user-meta">{email} · {role} · {provider}</span>
            </div>
          </div>
          {pw_flash}
          <div class="admin-user-actions">
            <form method="post" action="/admin/users/{uid}/reset-password" class="inline-form"
                  onsubmit="return confirm('Reset password for {name}?')">
              <button type="submit" class="btn-sm btn-secondary">Reset Password</button>
            </form>
            <form method="post" action="/admin/users/{uid}/avatar" class="inline-form"
                  enctype="multipart/form-data">
              <input type="file" name="avatar" accept="image/*" required style="max-width:180px">
              <button type="submit" class="btn-sm">Upload Photo</button>
            </form>
            <form method="post" action="/admin/users/{uid}/delete" class="inline-form"
                  onsubmit="return confirm('Permanently delete {name} and ALL their data? This cannot be undone.')">
              <button type="submit" class="btn-sm btn-danger">Delete</button>
            </form>
          </div>
        </article>
        """)

    return f"""
    <section class="card">
      <h2>User Management</h2>
      <p><a href="/admin">&larr; Back to Admin</a></p>
      <p>{len(users)} registered user(s)</p>
      <div class="admin-users-list">
        {"".join(rows)}
      </div>
    </section>
    """


def render_privacy() -> str:
    return """
    <section class="card">
      <h2>Privacy Policy</h2>
      <p><strong>Last updated:</strong> February 2026</p>

      <h3>What we collect</h3>
      <p>When you create an account or sign in with Facebook, we store your name, email address,
         and profile picture. We also store the tipping selections you make each round.</p>

      <h3>How we use it</h3>
      <p>Your information is used solely to run the tipping competition — displaying your name
         on the leaderboard, tracking your tips, and showing your avatar. We do not sell, share,
         or use your data for advertising.</p>

      <h3>Facebook data</h3>
      <p>If you sign in with Facebook, we receive your public profile and email address.
         We do not post to your timeline or access your friends list.</p>

      <h3>Data storage</h3>
      <p>Your data is stored securely on our server. Passwords are hashed using PBKDF2 and
         are never stored in plain text.</p>

      <h3>Data deletion</h3>
      <p>You can request deletion of your account and all associated data at any time.
         See our <a href="/remove">Data Deletion</a> page for instructions.</p>

      <h3>Contact</h3>
      <p>For privacy questions, contact the site administrator.</p>
    </section>
    """


def render_data_deletion() -> str:
    return """
    <section class="card">
      <h2>Data Deletion Instructions</h2>

      <p>If you would like to delete your account and all associated data from Aldo's Tipping Comp,
         please follow these steps:</p>

      <ol>
        <li>Contact the site administrator and request account deletion.</li>
        <li>Provide the email address associated with your account.</li>
        <li>Your account, tip history, and any stored personal data will be permanently deleted.</li>
      </ol>

      <h3>What gets deleted</h3>
      <ul>
        <li>Your user account (name, email, password)</li>
        <li>Your profile picture</li>
        <li>All tipping selections and history</li>
        <li>Your Facebook connection (if applicable)</li>
        <li>Any active sessions</li>
      </ul>

      <p>Deletion is typically completed within 7 days of receiving your request.</p>

      <h3>Facebook users</h3>
      <p>If you signed in with Facebook, you can also remove this app from your
         Facebook settings under <strong>Settings → Apps and Websites</strong>.
         This will revoke the app's access to your Facebook data.</p>
    </section>
    """

