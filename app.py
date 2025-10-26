
import os
import io
import time
import json
import math
import requests
import pandas as pd
import streamlit as st
from optimizer_core import compute_probabilities, best_lineups, lineup_rows

st.set_page_config(page_title="PrizePicks EV Optimizer — Hands‑Off", layout="wide")

st.title("📈 PrizePicks EV Optimizer — Hands‑Off")
st.caption("Fully automated feed → de‑vig → EV → best slips. Open and watch it update.")

with st.sidebar:
    st.header("🔌 Data Feeds")
    st.markdown("This app can run completely hands‑off using **TheOddsAPI**.")
    # Secrets or env vars
    default_key = st.secrets.get("THEODDS_API_KEY", os.getenv("THEODDS_API_KEY", ""))
    api_key = st.text_input("TheOddsAPI Key", type="password", value=default_key)
    sport_key = st.text_input("Sport key", value=st.secrets.get("THEODDS_SPORT_KEY", os.getenv("THEODDS_SPORT_KEY", "americanfootball_nfl")))
    regions = st.text_input("Regions", value=st.secrets.get("THEODDS_REGIONS", os.getenv("THEODDS_REGIONS", "us")))
    markets = st.text_area("Markets (comma‑sep)", value=st.secrets.get("THEODDS_MARKETS", os.getenv("THEODDS_MARKETS", "player_pass_tds,player_pass_yards,player_rush_yards,player_receiving_yards,receptions")))
    refresh_sec = st.slider("Auto‑refresh (seconds)", 15, 300, 60)

    st.markdown("---")
    st.header("🧠 Optimizer")
    top_k = st.slider("Search top K props", 10, 120, 60, step=5)
    allow_same_game = st.checkbox("Allow same‑game combos", value=False)
    st.markdown("---")
    st.caption("Tip: Set secrets on Streamlit Cloud so you never enter keys on the page.")

def theoddsapi_fetch_props(api_key: str, sport_key: str, regions: str, markets: str) -> pd.DataFrame:
    """
    Fetch props from TheOddsAPI v4 and aggregate per player+market+line:
    - averages over/under prices across books
    - returns required columns: player, market, line, over_odds, under_odds, team, game
    """
    if not api_key or not sport_key:
        return pd.DataFrame()

    base_url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "regions": regions,
        "markets": markets.replace(" ", ""),
        "oddsFormat": "american",
        "dateFormat": "iso",
        "apiKey": api_key,
    }
    r = requests.get(base_url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    rows = []
    for g in data:
        home = g.get("home_team","")
        away = g.get("away_team","")
        game_name = f"{away} @ {home}" if home and away else (home or away)
        bookmakers = g.get("bookmakers", [])
        # map: (player, mk_key, point) -> dict of lists for over/under prices, plus team
        bucket = {}
        for bk in bookmakers:
            for mk in bk.get("markets", []):
                mk_key = mk.get("key")
                # For player markets, outcomes list items may contain "name" (player + " Over"/" Under") or separate runner metadata.
                for out in mk.get("outcomes", []):
                    # Try to parse player name, side (Over/Under), and line (point)
                    name = out.get("name","")
                    price = out.get("price", None)
                    point = out.get("point", None)
                    description = out.get("description","")  # often player name
                    team = out.get("team","")
                    # Heuristics:
                    # - If description exists, it's typically the player name.
                    player = description or name.replace(" Over","").replace(" Under","")
                    side = "over" if "Over" in name else ("under" if "Under" in name else None)
                    if side is None and isinstance(price, (int,float)):
                        # Sometimes outcomes are "Player Name" and separate outcome labels; skip if unclear
                        if str(name).lower().endswith("over"): side="over"
                        elif str(name).lower().endswith("under"): side="under"
                    if not side or price is None or point is None or not player:
                        continue
                    key = (player.strip(), mk_key, float(point))
                    if key not in bucket:
                        bucket[key] = {"over": [], "under": [], "team": team, "game": game_name}
                    bucket[key][side].append(price)

        for (player, mk_key, point), rec in bucket.items():
            if not rec["over"] or not rec["under"]:
                continue  # need both sides to devig
            over_avg = sum(rec["over"]) / len(rec["over"])
            under_avg = sum(rec["under"]) / len(rec["under"])
            rows.append({
                "player": player,
                "team": rec["team"],
                "market": mk_key,
                "line": point,
                "over_odds": int(round(over_avg)),
                "under_odds": int(round(under_avg)),
                "game": rec["game"],
            })

    df = pd.DataFrame(rows)
    # Optional: normalize market naming
    mk_map = {
        "player_pass_tds": "PASS_TDS",
        "player_pass_yards": "PASS_YDS",
        "player_rush_yards": "RUSH_YDS",
        "player_receiving_yards": "REC_YDS",
        "receptions": "REC_RECS",
    }
    if not df.empty:
        df["market"] = df["market"].map(lambda k: mk_map.get(str(k), str(k).upper()))
    return df

# Auto-refresh: rerun every refresh_sec by changing query params
st.experimental_set_query_params(t=str(int(time.time()) // max(1, refresh_sec)))

# Load live feed
df_raw = None
error = None
try:
    df_raw = theoddsapi_fetch_props(api_key, sport_key, regions, markets)
except Exception as e:
    error = str(e)

st.markdown("### Live Data")
if error:
    st.error(f"Feed error: {error}")
elif df_raw is None or df_raw.empty:
    st.info("Waiting for live data. Confirm your API key, sport, regions, and markets.")
else:
    st.success(f"Loaded {len(df_raw)} props from TheOddsAPI")
    st.dataframe(df_raw.head(50))

if df_raw is not None and not df_raw.empty:
    # compute probabilities & edges
    try:
        df_proc = compute_probabilities(df_raw)
        st.markdown("### Value Table (sorted by Power‑3 edge)")
        st.dataframe(df_proc.sort_values("edge_vs_BE_power3", ascending=False).head(200))

        # best lineups
        results, df2 = best_lineups(df_proc, top_k=top_k, allow_same_game=allow_same_game)

        st.markdown("## Best POWER lineups (EV on 1 unit)")
        cols = st.columns(5)
        for i, n in enumerate([2,3,4,5,6]):
            r = results["power"][n]
            with cols[i if i < len(cols) else -1]:
                st.subheader(f"{n}-Pick")
                st.metric("EV", f"{r['ev']:.3f}")
                st.dataframe(lineup_rows(df2, r["combo_idxs"]))

        st.markdown("## Best FLEX lineups (EV on 1 unit)")
        cols2 = st.columns(4)
        for i, n in enumerate([3,4,5,6]):
            r = results["flex"][n]
            with cols2[i if i < len(cols2) else -1]:
                st.subheader(f"{n}-Pick")
                st.metric("EV", f"{r['ev']:.3f}")
                st.dataframe(lineup_rows(df2, r["combo_idxs"]))

        st.caption("Assumes independence between legs. Disable same‑game for safety or request a correlation penalty build.")
    except Exception as e:
        st.error(f"Processing error: {e}")
