import pytest

from app.budget.allocator import MAX_SCALE, MIN_SCALE, Demand, allocate, predicted_rate


def fleet(n=10, rate=20.0, **kw):
    return [Demand(i, rate, 1.0, **kw) for i in range(n)]


def test_the_budget_is_met():
    demands = fleet()
    scales = allocate(demands, 60.0)
    assert predicted_rate(demands, scales) == pytest.approx(60.0, rel=1e-3)
    # Identical robots get identical tolerances.
    assert len({round(s, 6) for s in scales.values()}) == 1


def test_a_robot_that_matters_more_gets_a_tighter_tolerance():
    demands = fleet()
    demands[0] = Demand(0, 20.0, 1.0, weight=4.0)
    scales = allocate(demands, 60.0)
    assert scales[0] < scales[1]
    # Weight 4 means 4 ** (1/3) times tighter, as the closed form says.
    assert scales[1] / scales[0] == pytest.approx(4 ** (1 / 3), rel=1e-3)
    assert predicted_rate(demands, scales) == pytest.approx(60.0, rel=1e-3)


def test_a_cheap_robot_is_watched_more_closely():
    demands = fleet()
    demands[0] = Demand(0, 4.0, 1.0)  # moves little: tight tolerances cost little
    scales = allocate(demands, 60.0)
    assert scales[0] < scales[1]


def test_demand_is_learned_at_the_scale_it_was_seen():
    # 6 per minute at scale 2 is the same robot as 18 per minute at scale 1
    # (beyond the heartbeat floor of 2): (18 - 2) = (6 - 2) * 4.
    assert Demand(1, 6.0, 2.0).c == pytest.approx(Demand(1, 18.0, 1.0).c)


def test_a_generous_budget_gives_the_tightest_tolerances():
    assert set(allocate(fleet(), 10_000.0).values()) == {MIN_SCALE}


def test_an_impossible_budget_gives_the_loosest():
    # Ten robots on a 30 s heartbeat send 20 a minute whatever their tolerance.
    assert set(allocate(fleet(), 10.0).values()) == {MAX_SCALE}


def test_nothing_to_allocate():
    assert allocate([], 60.0) == {}
