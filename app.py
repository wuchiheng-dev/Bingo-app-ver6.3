from flask import Flask, jsonify, render_template
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import time
import requests

app = Flask(__name__)

# 台灣彩券官方 API（由台彩官網前端使用）
OFFICIAL_PAGE = "https://www.taiwanlottery.com/lotto/result/bingo_bingo/"
API_BASE = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery/"
LATEST_API = API_BASE + "LatestBingoResult"
HISTORY_API = API_BASE + "BingoResult"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BingoBingoStats/6.2.1; +https://www.taiwanlottery.com/)",
    "Accept": "application/json",
    "Referer": OFFICIAL_PAGE,
}
SESSION = requests.Session()
CACHE = {"updated": 0.0, "draws": []}
CACHE_SECONDS = 60
TAIPEI = ZoneInfo("Asia/Taipei")


class LotteryAPIError(RuntimeError):
    """Official API or response-format error."""


def _request_json(url, params=None):
    try:
        response = SESSION.get(url, params=params, headers=HEADERS, timeout=15)
    except requests.RequestException as exc:
        raise LotteryAPIError(f"連線台灣彩券官方 API 失敗：{exc}") from exc

    if response.status_code != 200:
        raise LotteryAPIError(f"台灣彩券官方 API HTTP {response.status_code}")

    content_type = response.headers.get("Content-Type", "").lower()
    # Prevent HTML error pages from surfacing as the browser's
    # "Unexpected token '<'" JSON parsing error.
    body = response.text.lstrip()
    if "json" not in content_type and not body.startswith("{"):
        raise LotteryAPIError(
            "官方 API 回傳的不是 JSON（可能是網站錯誤頁或暫時性阻擋）。"
            f"Content-Type={content_type or 'unknown'}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise LotteryAPIError("官方 API 回應內容不是有效 JSON。") from exc

    if not isinstance(payload, dict):
        raise LotteryAPIError("官方 API JSON 格式錯誤：最外層不是物件。")
    if payload.get("rtCode") != 0:
        raise LotteryAPIError(
            f"台灣彩券官方 API 回報錯誤：{payload.get('rtMsg') or payload.get('rtCode')}"
        )
    content = payload.get("content")
    if not isinstance(content, dict):
        raise LotteryAPIError("官方 API JSON 缺少 content 物件。")
    return content


def _int_number(value, field):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise LotteryAPIError(f"官方資料欄位 {field} 不是有效整數。")
    if not 1 <= number <= 80:
        raise LotteryAPIError(f"官方資料欄位 {field} 超出 01–80 範圍。")
    return number


def _normalize_numbers(values, field):
    if not isinstance(values, list) or len(values) != 20:
        raise LotteryAPIError(f"官方資料欄位 {field} 應有20個號碼。")
    numbers = [_int_number(v, field) for v in values]
    if len(set(numbers)) != 20:
        raise LotteryAPIError(f"官方資料欄位 {field} 出現重複號碼。")
    return numbers


def _parse_bingo_item(item, draw_date, draw_time=""):
    if not isinstance(item, dict):
        raise LotteryAPIError("歷史開獎資料項目不是物件。")

    term = item.get("drawTerm")
    if term is None:
        raise LotteryAPIError("歷史開獎資料缺少 drawTerm。")

    sorted_numbers = _normalize_numbers(item.get("bigShowOrder"), "bigShowOrder")
    draw_order = _normalize_numbers(item.get("openShowOrder"), "openShowOrder")

    if set(sorted_numbers) != set(draw_order):
        raise LotteryAPIError("bigShowOrder 與 openShowOrder 的號碼集合不一致。")

    # 台彩資料中超級獎號為第20個開出的號碼。
    super_number = item.get("bullEyeTop")
    if super_number is not None:
        super_number = _int_number(super_number, "bullEyeTop")
        if super_number != draw_order[-1]:
            raise LotteryAPIError("官方資料的 bullEyeTop 與第20個開出號碼不一致。")

    return {
        "term": str(term),
        "date": draw_date,
        "time": draw_time or str(item.get("drawTime") or ""),
        "numbers": sorted(sorted_numbers),
        "draw_order": draw_order,
        "super_number": super_number,
    }


def fetch_latest_draw():
    content = _request_json(LATEST_API)
    post = content.get("lotteryBingoLatestPost")
    if not isinstance(post, dict):
        raise LotteryAPIError("最新開獎 API 缺少 lotteryBingoLatestPost。")

    date_time = post.get("dDate")
    if not isinstance(date_time, str) or len(date_time) < 16:
        raise LotteryAPIError("最新開獎 API 的 dDate 日期時間格式不正確。")

    prize_num = post.get("prizeNum") or {}
    latest_item = {
        "drawTerm": post.get("drawTerm"),
        "bigShowOrder": post.get("bigShowOrder"),
        "openShowOrder": post.get("openShowOrder"),
        "bullEyeTop": prize_num.get("bullEye") if isinstance(prize_num, dict) else None,
    }
    return _parse_bingo_item(
        latest_item,
        draw_date=date_time[:10],
        draw_time=date_time[11:16],
    )


def fetch_draws(force=False):
    now = time.time()
    if not force and CACHE["draws"] and now - CACHE["updated"] < CACHE_SECONDS:
        return CACHE["draws"], ""

    latest = fetch_latest_draw()
    latest_term = int(latest["term"])
    latest_date = datetime.strptime(latest["date"], "%Y-%m-%d").date()

    # The official history endpoint accepts openDate, pageNum, pageSize.
    # Query the latest draw date first, then earlier dates only if necessary.
    by_term = {latest["term"]: latest}
    for day_offset in range(0, 4):
        if len(by_term) >= 10:
            break
        query_date = (latest_date - timedelta(days=day_offset)).isoformat()
        content = _request_json(
            HISTORY_API,
            params={
                "openDate": query_date,
                "pageNum": 1,
                "pageSize": 250,
            },
        )
        items = content.get("bingoQueryResult")
        if items is None:
            items = []
        if not isinstance(items, list):
            raise LotteryAPIError("歷史 API 的 bingoQueryResult 不是陣列。")

        for item in items:
            draw = _parse_bingo_item(item, draw_date=query_date)
            # Keep only current or earlier draws and avoid duplicate latest item.
            if int(draw["term"]) <= latest_term:
                by_term[draw["term"]] = draw

    draws = sorted(by_term.values(), key=lambda d: int(d["term"]), reverse=True)[:10]
    if len(draws) != 10:
        raise LotteryAPIError(
            f"官方歷史 API 目前只回傳 {len(draws)} 期有效開獎資料，尚不足10期；"
            "系統不會使用其他網站或虛構資料補齊。"
        )

    # Validate every returned draw again before storing it.
    for draw in draws:
        if len(draw["numbers"]) != 20 or len(set(draw["numbers"])) != 20:
            raise LotteryAPIError(f"第 {draw['term']} 期號碼驗證失敗。")

    CACHE["updated"] = now
    CACHE["draws"] = draws
    return draws, ""


def _build_grid_lines():
    """Build 8 horizontal rows, 10 vertical columns and 45-degree diagonals."""
    rows = [[r * 10 + c + 1 for c in range(10)] for r in range(8)]
    cols = [[r * 10 + c + 1 for r in range(8)] for c in range(10)]
    diagonals = []

    # Down-right diagonals, starting from the top edge and left edge.
    for start_col in range(10):
        line = []
        r, c = 0, start_col
        while r < 8 and c < 10:
            line.append(r * 10 + c + 1)
            r += 1
            c += 1
        if len(line) >= 4:
            diagonals.append(line)
    for start_row in range(1, 8):
        line = []
        r, c = start_row, 0
        while r < 8 and c < 10:
            line.append(r * 10 + c + 1)
            r += 1
            c += 1
        if len(line) >= 4:
            diagonals.append(line)

    # Down-left diagonals, starting from the top edge and right edge.
    for start_col in range(10):
        line = []
        r, c = 0, start_col
        while r < 8 and c >= 0:
            line.append(r * 10 + c + 1)
            r += 1
            c -= 1
        if len(line) >= 4:
            diagonals.append(line)
    for start_row in range(1, 8):
        line = []
        r, c = start_row, 9
        while r < 8 and c >= 0:
            line.append(r * 10 + c + 1)
            r += 1
            c -= 1
        if len(line) >= 4:
            diagonals.append(line)

    return rows, cols, diagonals


def analyze_patterns(draws):
    """Measure recent horizontal, vertical and diagonal concentration.

    A Bingo draw is random; this is a descriptive pattern score, not a claim
    that a past line makes the next draw more likely to follow that line.
    """
    if len(draws) != 10:
        return {"rows": [], "columns": [], "diagonals": [], "top_patterns": []}

    rows, cols, diagonals = _build_grid_lines()
    weights = [1.60 - i * 0.09 for i in range(10)]  # newest draw has most weight
    total_weight = sum(weights)

    def score_lines(lines, category, label_prefix):
        results = []
        for idx, line in enumerate(lines, start=1):
            per_draw = [len(set(draw["numbers"]) & set(line)) for draw in draws]
            weighted_hits = sum(hits * weights[i] for i, hits in enumerate(per_draw))
            expected_hits = total_weight * len(line) * 0.25  # 20 of 80 per draw
            ratio = weighted_hits / expected_hits if expected_hits else 1.0
            recent_hits = sum(per_draw[:3])
            results.append({
                "category": category,
                "label": f"{label_prefix}{idx}",
                "numbers": line,
                "length": len(line),
                "weighted_hits": round(weighted_hits, 2),
                "expected_hits": round(expected_hits, 2),
                "ratio": round(ratio, 2),
                "recent3_hits": recent_hits,
                "hit_counts": per_draw,
            })
        return results

    row_stats = score_lines(rows, "row", "橫列 ")
    col_stats = score_lines(cols, "column", "直行 ")
    diag_stats = score_lines(diagonals, "diagonal", "斜線 ")
    all_stats = row_stats + col_stats + diag_stats
    # Rank by relative activity, but require at least a modest excess over chance.
    top = sorted(all_stats, key=lambda x: (x["ratio"], x["recent3_hits"]), reverse=True)[:8]

    def public_line(item):
        return {
            "category": item["category"],
            "label": item["label"],
            "numbers": item["numbers"],
            "length": item["length"],
            "ratio": item["ratio"],
            "recent3_hits": item["recent3_hits"],
            "hit_counts": item["hit_counts"],
        }

    return {
        "rows": [public_line(x) for x in row_stats],
        "columns": [public_line(x) for x in col_stats],
        "diagonals": [public_line(x) for x in diag_stats],
        "top_patterns": [public_line(x) for x in top],
    }


def predict(draws):
    """Rank 20 candidates using frequency, recency and grid-pattern features."""
    if len(draws) != 10:
        return []

    counts = Counter()
    weighted_frequency = {n: 0.0 for n in range(1, 81)}
    weights = [1.60 - i * 0.09 for i in range(10)]
    for index, draw in enumerate(draws):
        for number in draw["numbers"]:
            counts[number] += 1
            weighted_frequency[number] += weights[index]

    pattern_data = analyze_patterns(draws)
    line_groups = {
        "row": (pattern_data["rows"], 0.18),
        "column": (pattern_data["columns"], 0.18),
        "diagonal": (pattern_data["diagonals"], 0.12),
    }
    pattern_bonus = {n: 0.0 for n in range(1, 81)}
    pattern_signals = {n: [] for n in range(1, 81)}

    for category, (lines, category_weight) in line_groups.items():
        # Each number receives only its strongest line signal in each direction,
        # preventing many overlapping diagonals from dominating the ranking.
        best_for_number = {n: (0.0, None) for n in range(1, 81)}
        for line in lines:
            # Ignore ordinary/random fluctuations; only reward activity above baseline.
            excess = max(0.0, min(1.5, line["ratio"] - 1.0))
            recent_factor = 1.0 + min(line["recent3_hits"], 8) / 20.0
            contribution = excess * recent_factor
            for number in line["numbers"]:
                if contribution > best_for_number[number][0]:
                    best_for_number[number] = (contribution, line)
        for number, (contribution, line) in best_for_number.items():
            pattern_bonus[number] += contribution * category_weight
            if line and contribution > 0:
                pattern_signals[number].append(line["label"])

    # Normalize frequency so the pattern bonus is meaningful but remains secondary.
    mean_frequency = sum(weighted_frequency.values()) / 80.0
    ranked_items = []
    for number in range(1, 81):
        frequency_component = weighted_frequency[number] / mean_frequency if mean_frequency else 0.0
        # Small count tie-break; structure should never overwhelm observed frequency.
        score = frequency_component + pattern_bonus[number]
        ranked_items.append((number, score))

    ranked = sorted(ranked_items, key=lambda item: (item[1], counts[item[0]], -item[0]), reverse=True)
    selected = sorted(number for number, _ in ranked[:20])
    score_map = dict(ranked_items)
    return [
        {
            "number": number,
            "count": counts[number],
            "score": round(score_map[number], 3),
            "signals": pattern_signals[number],
        }
        for number in selected
    ]


def build_response(force=False):
    draws, _ = fetch_draws(force=force)
    predictions = predict(draws)
    pattern_analysis = analyze_patterns(draws)
    return {
        "ok": True,
        "source_url": OFFICIAL_PAGE,
        "api_latest": LATEST_API,
        "updated_at": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
        "draws": draws,
        "prediction": predictions,
        "pattern_analysis": pattern_analysis,
        "sample_size": len(draws),
        "prediction_ready": len(predictions) == 20,
        "error": "",
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data", methods=["GET"])
def api_data():
    try:
        return jsonify(build_response(force=False)), 200
    except Exception as exc:
        app.logger.exception("取得官方開獎資料失敗")
        return jsonify({
            "ok": False,
            "source_url": OFFICIAL_PAGE,
            "updated_at": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
            "draws": [],
            "prediction": [],
            "pattern_analysis": {"rows": [], "columns": [], "diagonals": [], "top_patterns": []},
            "sample_size": 0,
            "prediction_ready": False,
            "error": str(exc),
        }), 502


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        return jsonify(build_response(force=True)), 200
    except Exception as exc:
        app.logger.exception("手動更新官方開獎資料失敗")
        return jsonify({
            "ok": False,
            "source_url": OFFICIAL_PAGE,
            "updated_at": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
            "draws": [],
            "prediction": [],
            "pattern_analysis": {"rows": [], "columns": [], "diagonals": [], "top_patterns": []},
            "sample_size": 0,
            "prediction_ready": False,
            "error": str(exc),
        }), 502


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({
        "ok": True,
        "version": "6.3.0",
        "message": "Flask API is running",
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
