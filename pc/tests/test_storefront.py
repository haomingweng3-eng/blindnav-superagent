from blindnav_pc.storefront import Door, Sign, ground, name_score, spatial_score


def _door(x1, y1, x2, y2):
    return Door((x1, y1, x2, y2))


def test_name_score_exact_alias_containment():
    assert name_score("FOREVER 21", "forever21") == 1.0
    assert name_score("KFC", "肯德基", ["KFC"]) == 0.9
    assert name_score("KFC 王府井店", "KFC") == 0.7
    assert name_score("CROSSROADS", "FOREVER 21") == 0.0


def test_spatial_score_needs_horizontal_overlap_and_sign_above():
    sign = Sign("KFC", (0.4, 0.6, 0.6, 0.7))
    under = _door(0.42, 0.2, 0.58, 0.58)
    beside = _door(0.75, 0.2, 0.9, 0.58)
    above = _door(0.42, 0.75, 0.58, 0.95)
    assert spatial_score(sign, under) > 0.5
    assert spatial_score(sign, beside) == 0.0
    assert spatial_score(sign, above) < spatial_score(sign, under)


def test_ground_picks_door_under_target_sign_not_nearest():
    signs = [Sign("BANK", (0.05, 0.6, 0.25, 0.7)), Sign("KFC", (0.6, 0.6, 0.8, 0.7))]
    near = _door(0.05, 0.1, 0.25, 0.3)          # small but close-looking
    target = _door(0.62, 0.1, 0.78, 0.55)
    result = ground([near, target], signs, "肯德基", ["KFC"])
    assert result.state == "confirmed"
    assert result.candidates[0].door is target


def test_ground_abstains_when_target_sign_missing():
    result = ground([_door(0.1, 0.1, 0.2, 0.5)], [Sign("BANK", (0.1, 0.6, 0.2, 0.7))], "KFC")
    assert result.state == "target_not_seen"
    assert result.reason == "target_name_absent"


def test_ground_abstains_on_tie():
    sign = Sign("KFC", (0.3, 0.6, 0.7, 0.7))
    a = _door(0.30, 0.1, 0.50, 0.55)
    b = _door(0.50, 0.1, 0.70, 0.55)
    result = ground([a, b], [sign], "KFC")
    assert result.state == "ambiguous"
    assert result.reason == "two_doors_tie"


def test_ground_reports_no_door_under_sign():
    sign = Sign("KFC", (0.0, 0.6, 0.1, 0.7))
    result = ground([_door(0.8, 0.1, 0.9, 0.5)], [sign], "KFC")
    assert result.state == "ambiguous"
    assert result.reason == "no_door_under_sign"


def _cand(cx, cy, w, h, conf=0.9):
    from blindnav_pc.last10m import DoorCandidate
    return DoorCandidate(0, cx, cy, w, h, conf, 5.0)


def test_propose_doors_for_target_prefers_owning_door():
    from blindnav_pc.last10m import propose_doors_for_target
    big = _cand(0.15, 0.3, 0.25, 0.5)      # old ranking would pick this
    target = _cand(0.70, 0.32, 0.12, 0.35)
    signs = [Sign("KFC", (0.66, 0.70, 0.76, 0.78))]
    proposal = propose_doors_for_target([big, target], signs, "KFC")
    assert proposal.refused is False
    assert proposal.reason == "target_confirmed"
    assert proposal.candidates[0] is target


def test_propose_doors_for_target_falls_back_when_target_absent():
    from blindnav_pc.last10m import propose_doors_for_target
    door = _cand(0.5, 0.3, 0.1, 0.4)
    proposal = propose_doors_for_target([door], [Sign("BANK", (0.45, 0.7, 0.55, 0.78))], "KFC")
    assert proposal.reason.startswith("fallback_")
    assert proposal.candidates[0] is door
