import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as bingo


def make_draw(term):
    nums = list(range(1, 21))
    return {
        "term": str(term),
        "date": "2026-11-04",
        "time": "",
        "numbers": nums,
        "draw_order": nums,
        "super_number": 20,
    }


def test_normalize_api_draw():
    item = {
        "drawTerm": 115062385,
        "bigShowOrder": [f"{n:02d}" for n in range(1, 21)],
        "openShowOrder": [f"{n:02d}" for n in range(1, 21)],
        "bullEyeTop": "20",
    }
    draw = bingo._parse_bingo_item(item, "2026-11-04")
    assert draw["term"] == "115062385"
    assert len(draw["numbers"]) == 20
    assert draw["super_number"] == 20


def test_prediction_requires_ten_draws():
    assert bingo.predict([make_draw(115062385)]) == []
    result = bingo.predict([make_draw(115062385 - i) for i in range(10)])
    assert len(result) == 20
    assert all(1 <= item["number"] <= 80 for item in result)


def test_api_error_is_json(monkeypatch):
    monkeypatch.setattr(
        bingo,
        "fetch_draws",
        lambda force=False: (_ for _ in ()).throw(bingo.LotteryAPIError("test error")),
    )
    client = bingo.app.test_client()
    response = client.get("/api/data")
    assert response.status_code == 502
    assert response.is_json
    assert response.get_json()["error"] == "test error"


def test_health_endpoint():
    response = bingo.app.test_client().get("/api/health")
    assert response.status_code == 200
    assert response.get_json()["version"] == "6.3.0"


def test_pattern_analysis_has_rows_columns_and_diagonals():
    draws = [make_draw(115062385 - i) for i in range(10)]
    analysis = bingo.analyze_patterns(draws)
    assert len(analysis["rows"]) == 8
    assert len(analysis["columns"]) == 10
    assert len(analysis["diagonals"]) > 0
    assert len(analysis["top_patterns"]) == 8


def test_prediction_includes_pattern_signals():
    draws = [make_draw(115062385 - i) for i in range(10)]
    result = bingo.predict(draws)
    assert len(result) == 20
    assert all("signals" in item for item in result)
