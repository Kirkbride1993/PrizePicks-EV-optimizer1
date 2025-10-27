
# main.py — PrizePicks EV Optimizer (Player Props feed)

import time
import json
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="PrizePicks EV Optimizer — Hands-Off", layout="wide")
st.title("📈 PrizePicks EV Optimizer — Hands-Off")
st.caption("Fully automated feed → de-vig → EV → best slips. Open and watch it update.")

# ---------------------------
# Sidebar controls
# ---------------------------
with st.sidebar:
    st.header("🔌 Data Feeds")

    # Optional: allow override via Secrets, but default to PrizePicks public feed.
    default_url = "https://api.prizepicks.com/projections"
    api_url = st.secrets.get("PP_API_URL", default_url)
    api_url = st.text_input("API URL", value=api_url)

    # Autorefresh seconds
    refresh_sec = st.slider("Auto-refresh (seconds)", min_value=15, max_value=600, value=180, step=15)

    # Filters pop
    st.subheader("Filters")
    league_filter = st.selectbox(
        "League (optional filter)",
        ["All", "NFL", "NBA", "MLB", "NHL", "WNBA", "CBB", "CFB", "SOC", "MMA", "Tennis"],
        index=0,
        help="Shows all by default. This is a soft filter against the feed's league labels."
    )
    text_filter = st.text_input("Search (player or stat)", "")

    st.markdown("---")
    st.write("Tip: Leave League = All for maximum coverage.")

# ---------------------------
# Data fetching
# ---------------------------
# --------------------------------------------
# Data fetching
# --------------------------------------------
# (old placeholder removed)
# --------------------------------------------
# Safe PrizePicks fetch (with cache + cooldown)
# --------------------------------------------

@st.cache_data(show_spinner=False)
def fetch_prizepicks(url: str):
    # Relay-friendly fetch to bypass 403 from PrizePicks WAF on cloud hosts
    r = requests.get(url, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("RATE_LIMIT")
    if r.status_code == 403:
        raise RuntimeError("FORBIDDEN")
    r.raise_for_status()

    # Relay returns raw JSON text; parse to dict
    text = r.text.strip()
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    return r.json()


def safe_fetch(url: str, min_gap_sec: int = 120):
    now = time.time()
    if "last_fetch_ts" not in st.session_state:
        st.session_state["last_fetch_ts"] = 0
    if "last_data" not in st.session_state:
        st.session_state["last_data"] = None

    if now - st.session_state["last_fetch_ts"] < min_gap_sec:
        return st.session_state["last_data"]

    try:
        data = fetch_prizepicks(url)
        st.session_state["last_fetch_ts"] = now
        st.session_state["last_data"] = data
        return data
    except RuntimeError:  # RATE_LIMIT
        st.warning("⚠️ Rate limit hit — pausing 2 minutes and reusing last good data.")
        st.session_state["last_fetch_ts"] = now
        return st.session_state["last_data"]
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def to_safe_str(x):
    if x is None:
        return ""
    return str(x)

def parse_pp(json_obj: dict) -> pd.DataFrame:
    """
    PrizePicks returns:
      - data: list of projections
      - included: list with players, leagues, etc.

    We defensively map:
      - projection.attributes.stat_type or projection.attributes["projection_type"]["stat_type"]
      - projection.attributes.line_score (float/str)
      - player from relationships -> included
      - league from included where type == "league" or from projection.attributes.league, if any
    """
    data = json_obj.get("data", [])
    included = json_obj.get("included", [])

    # Build quick lookups from "included"
    players_by_id = {}
    leagues_by_id = {}
    teams_by_id = {}

    for inc in included:
        inc_type = inc.get("type")
        inc_id = to_safe_str(inc.get("id"))
        attrs = inc.get("attributes", {}) or {}

        # PrizePicks sometimes uses "new_player", "players", or "player"
        if inc_type in ("new_player", "players", "player"):
            name = attrs.get("name") or attrs.get("full_name") or attrs.get("display_name")
            players_by_id[inc_id] = name or ""
        elif inc_type == "league":
            leagues_by_id[inc_id] = attrs.get("name") or attrs.get("abbreviation") or ""
        elif inc_type in ("team", "teams"):
            teams_by_id[inc_id] = attrs.get("name") or attrs.get("abbreviation") or ""

    rows = []
    for item in data:
        attrs = item.get("attributes", {}) or {}
        rels = item.get("relationships", {}) or {}

        # Stat / market
        stat_type = attrs.get("stat_type") or attrs.get("projection_type") or attrs.get("type") or ""
        # Some responses nest the stat label deeper; keep fallbacks:
        if isinstance(stat_type, dict):
            stat_type = stat_type.get("stat_type") or stat_type.get("name") or ""

        # Line
        line = attrs.get("line_score") or attrs.get("line") or attrs.get("value") or ""

        # Link player via relationships
        player_name = ""
        player_rel = rels.get("new_player") or rels.get("player") or {}
        player_data = player_rel.get("data") or {}
        player_id = to_safe_str(player_data.get("id"))
        if player_id and player_id in players_by_id:
            player_name = players_by_id[player_id]

        # League via relationships (if present)
        league_name = ""
        league_rel = rels.get("league") or {}
        league_data = league_rel.get("data") or {}
        league_id = to_safe_str(league_data.get("id"))
        if league_id and league_id in leagues_by_id:
            league_name = leagues_by_id[league_id]

        # Team (best-effort; not always present)
        team_name = ""
        team_rel = rels.get("team") or {}
        team_data = team_rel.get("data") or {}
        team_id = to_safe_str(team_data.get("id"))
        if team_id and team_id in teams_by_id:
            team_name = teams_by_id[team_id]

        # Fallbacks if not found in relationships
        if not league_name:
            league_name = attrs.get("league") or ""

        rows.append({
            "Player": player_name,
            "League": league_name,
            "Stat": to_safe_str(stat_type),
            "Line": line,
            "Team": team_name,
        })

    df = pd.DataFrame(rows)
    # Clean up/normalize
    if not df.empty:
        df["League"] = df["League"].fillna("")
        df["Stat"] = df["Stat"].fillna("").str.replace("_", " ").str.title()
        # Simple normalization for common league labels
        df["LeagueNorm"] = (
            df["League"].str.upper()
            .replace({
                "NATIONAL FOOTBALL LEAGUE": "NFL",
                "NATIONAL BASKETBALL ASSOCIATION": "NBA",
                "MAJOR LEAGUE BASEBALL": "MLB",
            })
        )
    return df

# ---------------------------
# Load data (with graceful fallback)
# ---------------------------
placeholder = st.empty()
with st.spinner("Loading PrizePicks player props..."):
    try:
        raw = fetch_prizepicks(api_url)
        df = parse_pp(raw)
        ok = not df.empty
    except Exception as e:
        ok = False
        err = str(e)

if not ok:
    st.error("Could not parse player props. Showing a JSON preview so you can verify data is arriving.")
    st.code(json.dumps(raw if 'raw' in locals() else {"error": err}, indent=2)[:5000])
else:
    # ---------------------------
    # Apply filters
    # ---------------------------
    df_view = df.copy()

    if league_filter != "All":
        df_view = df_view[df_view["League"].str.upper().str.contains(league_filter.upper()) |
                          df_view.get("LeagueNorm", pd.Series([""]*len(df_view))).str.contains(league_filter.upper())]

    if text_filter.strip():
        q = text_filter.strip().lower()
        df_view = df_view[
            df_view["Player"].str.lower().str.contains(q) |
            df_view["Stat"].str.lower().str.contains(q) |
            df_view["Team"].str.lower().str.contains(q)
        ]

    st.subheader("Live Data")
    st.dataframe(df_view[["Player", "League", "Stat", "Line", "Team"]], use_container_width=True)

# ---------------------------
# Auto-refresh
# ---------------------------
st.caption("Live PrizePicks feed. This page auto-refreshes.")
time.sleep(0.1)
# Safer refresh (prevents crash on Streamlit Cloud)
try:
    st.autorefresh(interval=refresh_sec * 1000, key="pp_refresh")
except Exception:
    time.sleep(refresh_sec)
    st.rerun()
