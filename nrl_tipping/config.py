from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = ROOT_DIR / "static"
AVATAR_UPLOAD_DIR = STATIC_DIR / "avatars"
DB_PATH = DATA_DIR / "nrl_tipping.db"

ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
NRL_SPORT_KEY = os.getenv("NRL_SPORT_KEY", "rugbyleague_nrl")
DEFAULT_REGION = os.getenv("ODDS_REGION", "au")
SESSION_DURATION_HOURS = int(os.getenv("SESSION_DURATION_HOURS", "720"))  # 30 days
MAX_AVATAR_BYTES = int(os.getenv("MAX_AVATAR_BYTES", str(5 * 1024 * 1024)))  # 5 MB
TIP_LOCK_MINUTES = int(os.getenv("TIP_LOCK_MINUTES", "5"))
AUTO_SCORE_UPDATER_ENABLED = os.getenv("AUTO_SCORE_UPDATER_ENABLED", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
AUTO_SCORE_CHECK_INTERVAL_SECONDS = int(os.getenv("AUTO_SCORE_CHECK_INTERVAL_SECONDS", "900"))
AUTO_SCORE_MIN_AGE_HOURS = float(os.getenv("AUTO_SCORE_MIN_AGE_HOURS", "2"))

SSL_CERTFILE = DATA_DIR / "localhost.pem"
SSL_KEYFILE = DATA_DIR / "localhost-key.pem"

DEFAULT_ADMIN_EMAIL = os.getenv("NRL_ADMIN_EMAIL", "admin@nrltips.local")
DEFAULT_ADMIN_NAME = os.getenv("NRL_ADMIN_NAME", "Admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("NRL_ADMIN_PASSWORD", "ChangeMe123!")

FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID", "").strip()
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "").strip()
FACEBOOK_GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "v19.0").strip()
FACEBOOK_OAUTH_SCOPES = os.getenv("FACEBOOK_OAUTH_SCOPES", "email,public_profile").strip()

APP_ENV_PATH = ROOT_DIR / ".env"

NFTEAMS_ENV_PATH = Path(
    os.getenv(
        "NFTEAMS_ENV_PATH",
        r"C:\Users\steoo\.gemini\antigravity\scratch\NFTeams_automation\.env",
    )
)


def get_env_value_from_file(path: Path, key: str) -> str | None:
    if not path.exists():
        return None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        left, right = line.split("=", 1)
        if left.strip() == key:
            return right.strip().strip('"').strip("'")
    return None


def get_odds_api_key() -> str | None:
    direct = os.getenv("ODDS_API_KEY")
    if direct:
        return direct
    return get_env_value_from_file(NFTEAMS_ENV_PATH, "ODDS_API_KEY")


def _get_config_value(key: str, default: str = "") -> str:
    direct = os.getenv(key)
    if direct and direct.strip():
        return direct.strip()
    from_project = get_env_value_from_file(APP_ENV_PATH, key)
    if from_project and from_project.strip():
        return from_project.strip()
    from_nfteams = get_env_value_from_file(NFTEAMS_ENV_PATH, key)
    if from_nfteams and from_nfteams.strip():
        return from_nfteams.strip()
    return default


def get_facebook_oauth_config() -> dict[str, str]:
    return {
        "app_id": _get_config_value("FACEBOOK_APP_ID", FACEBOOK_APP_ID),
        "app_secret": _get_config_value("FACEBOOK_APP_SECRET", FACEBOOK_APP_SECRET),
        "graph_version": _get_config_value("FACEBOOK_GRAPH_VERSION", FACEBOOK_GRAPH_VERSION or "v19.0"),
        "oauth_scopes": _get_config_value(
            "FACEBOOK_OAUTH_SCOPES",
            FACEBOOK_OAUTH_SCOPES or "email,public_profile",
        ),
    }
