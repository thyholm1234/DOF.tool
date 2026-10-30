from datetime import date

from portal.services.finds import cluster, migration

D1, D2, D3 = date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)
SKAGEN = (57.74, 10.63)


def _n(roots):
    return len(set(roots))


def test_migration_codes():
    assert migration("N") == (True, 0.0)
    assert migration("R SV") == (True, 225.0)
    assert migration("T HØ") == (True, None)
    assert migration("TF") == (False, None)  # trækforsøg er ikke træk
    assert migration("N R") == (False, None)
    assert migration(None) == (False, None)


def test_staying_and_migrating_are_separate():
    pts = [(D1, *SKAGEN, 1, False, None), (D1, *SKAGEN, 1, True, 0.0)]
    assert _n(cluster(pts)) == 2


def test_migrating_same_place_other_day_is_new_bird():
    pts = [(D1, *SKAGEN, 1, True, 0.0), (D2, *SKAGEN, 1, True, 0.0)]
    assert _n(cluster(pts)) == 2


def test_migrating_same_place_same_day_is_one():
    pts = [(D1, *SKAGEN, 1, True, 0.0), (D1, SKAGEN[0] + 0.005, SKAGEN[1], 2, True, None)]
    assert _n(cluster(pts)) == 1


def test_migrating_follows_direction():
    south = (SKAGEN[0] - 0.25, SKAGEN[1])  # ca. 28 km mod syd
    assert _n(cluster([(D1, *SKAGEN, 1, True, 180.0), (D2, *south, 2, True, 180.0)])) == 1
    # næste dag mod nord, men stedet ligger syd for: ikke samme fugl
    assert _n(cluster([(D1, *SKAGEN, 1, True, 0.0), (D2, *south, 2, True, 0.0)])) == 2
    # modsat retning
    assert _n(cluster([(D1, *SKAGEN, 1, True, 180.0), (D2, *south, 2, True, 0.0)])) == 2


def test_unknown_direction_cannot_bridge_opposite_directions():
    pts = [(D1, *SKAGEN, 1, True, 0.0), (D1, *SKAGEN, 2, True, None), (D1, *SKAGEN, 3, True, 180.0)]
    assert _n(cluster(pts)) == 2


def test_staying_chain_is_capped():
    # en kæde mod øst i hop på ca. 12 km om dagen: over 30 km deles den
    pts = [(date(2026, 5, d), 56.0, 10.0 + 0.19 * (d - 1), d, False, None) for d in range(1, 6)]
    roots = cluster(pts)
    assert _n(roots) == 2
    assert roots[0] == roots[1] == roots[2]


def test_site_gap_still_links_staying():
    pts = [(D1, *SKAGEN, 1, False, None), (D3, 55.0, 12.0, 9, False, None), (date(2026, 5, 7), *SKAGEN, 1, False, None)]
    roots = cluster(pts)
    assert roots[0] == roots[2] and roots[1] != roots[0]
