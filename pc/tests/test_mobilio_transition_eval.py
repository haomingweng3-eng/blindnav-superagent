import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from mobilio_transition_eval import analyze

def test_unknown_is_not_counted_as_warning_success():
    rows = [
        {"condition":"production","session":"s","frame":"1","arm":"baseline_split_c",
         "danger_any":"True","confirmed_clear":"False","effective_command":"STRAIGHT","safety_state":"CLEAR"},
        {"condition":"production","session":"s","frame":"1","arm":"mobilio",
         "danger_any":"True","confirmed_clear":"False","effective_command":"UNKNOWN","safety_state":"UNKNOWN"},
    ]
    result = analyze(rows)["production"]
    assert result["candidate_unknown_on_danger"] == 1
    assert result.get("candidate_warning_on_danger", 0) == 0
