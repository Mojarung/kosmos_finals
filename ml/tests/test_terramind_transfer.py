import numpy as np

from floodvalue.terramind_transfer import balanced_indices


def test_external_sampler_balances_events_instead_of_number_of_tiles():
    events = {"small": [0], "large": list(range(1, 96))}
    indices = np.asarray(balanced_indices(events, 10_000, np.random.default_rng(2026)))
    assert np.all((indices >= 0) & (indices < 96))
    assert .47 < np.mean(indices == 0) < .53
    assert len(set(indices)) == 96
