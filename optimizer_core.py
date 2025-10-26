
import math
import itertools
import pandas as pd
from typing import List, Tuple, Dict

# ---------------- Core math ----------------

POWER_MULT = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}
FLEX_TABLE = {
    3: {(3,): 3.0, (2,): 1.0},
    4: {(4,): 6.0, (3,): 1.5},
    5: {(5,): 10.0, (4,): 2.0, (3,): 0.4},
    6: {(6,): 25.0, (5,): 2.0, (4,): 0.4},
}

def amer_to_imp_prob(odds: int) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:
        return (-odds) / ((-odds) + 100.0)

def devig_two_way(imp_over: float, imp_under: float) -> Tuple[float, float]:
    s = imp_over + imp_under
    if s <= 0:
        return 0.5, 0.5
    return imp_over / s, imp_under / s

def ev_power(ps: List[float], n: int) -> float:
    p_win = 1.0
    for p in ps:
        p_win *= p
    return POWER_MULT[n] * p_win - 1.0

def ev_flex(ps: List[float], n: int) -> float:
    table = FLEX_TABLE[n]
    nlegs = len(ps)
    dp = [0.0] * (nlegs + 1)
    dp[0] = 1.0
    for p in ps:
        nxt = [0.0] * (nlegs + 1)
        for k in range(nlegs + 1):
            if dp[k] == 0: 
                continue
            nxt[k] += dp[k] * (1 - p)
            if k + 1 <= nlegs:
                nxt[k + 1] += dp[k] * p
        dp = nxt
    ev = 0.0
    for outcomes, mult in table.items():
        prob = sum(dp[k] for k in outcomes)
        ev += prob * mult
    return ev - 1.0

def breakeven_p_power(n: int) -> float:
    m = POWER_MULT[n]
    return (1.0 / m) ** (1.0 / n)

def compute_probabilities(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    p_over = []
    for _, r in df.iterrows():
        io = amer_to_imp_prob(int(r["over_odds"]))
        iu = amer_to_imp_prob(int(r["under_odds"]))
        p, _ = devig_two_way(io, iu)
        p_over.append(p)
    df["p_over"] = p_over
    for n in [2,3,4,5,6]:
        df[f"edge_vs_BE_power{n}"] = df["p_over"] - breakeven_p_power(n)
    return df

def best_lineups(df: pd.DataFrame, top_k: int = 32, allow_same_game: bool = True) -> Dict[str, Dict[int, Dict]]:
    df2 = df.copy().sort_values("edge_vs_BE_power3", ascending=False).head(top_k).reset_index(drop=True)
    # optional "game" column for same-game suppression
    def invalid_combo(idx_tuple):
        if allow_same_game or "game" not in df2.columns:
            return False
        games = [str(df2.iloc[i].get("game", "")) for i in idx_tuple]
        # If any duplicate game exists, block
        return len(set(games)) < len(games)

    results = {"power": {}, "flex": {}}
    idxs = list(df2.index)
    rows = list(df2.itertuples())

    for n in [2,3,4,5,6]:
        best_p = -1e9; best_c = None
        for combo in itertools.combinations(idxs, n):
            if invalid_combo(combo): 
                continue
            ps = [rows[i].p_over for i in combo]
            evp = ev_power(ps, n)
            if evp > best_p:
                best_p, best_c = evp, combo
        results["power"][n] = {"ev": best_p, "combo_idxs": best_c}
        if n >= 3:
            best_f = -1e9; best_cf = None
            for combo in itertools.combinations(idxs, n):
                if invalid_combo(combo): 
                    continue
                ps = [rows[i].p_over for i in combo]
                evf = ev_flex(ps, n)
                if evf > best_f:
                    best_f, best_cf = evf, combo
            results["flex"][n] = {"ev": best_f, "combo_idxs": best_cf}
    return results, df2

def lineup_rows(df_small: pd.DataFrame, combo_idxs):
    if combo_idxs is None:
        return pd.DataFrame()
    rows = []
    for i in combo_idxs:
        r = df_small.iloc[i]
        rows.append({
            "player": r.get("player"),
            "team": r.get("team", ""),
            "market": r.get("market"),
            "line": r.get("line"),
            "p_over(no-vig)": round(float(r.get("p_over", 0.0)), 4),
            "over_odds": int(r.get("over_odds")),
            "under_odds": int(r.get("under_odds")),
            "game": r.get("game","")
        })
    return pd.DataFrame(rows)
