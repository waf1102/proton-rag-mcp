"""Fuse bounded, independently validated message rankings without another model."""

from itertools import islice


def fuse_rankings(legacy, consensus, limit):
    """Favor agreement while retaining sharp matches; ties keep first-seen order.

    Each message votes once per list. Return the verified passage with the largest
    contribution, so ranking never manufactures or rewrites evidence.
    """
    scores, passages, contributions = {}, {}, {}
    for ranking, weight in ((legacy, 1), (consensus, 2)):
        seen = set()
        for hit in islice(ranking, 100):
            key = hit["metadata"]["docSource"]
            if key in seen:
                continue
            contribution = weight / (10 + len(seen))
            seen.add(key)
            scores[key] = scores.get(key, 0) + contribution
            if contribution > contributions.get(key, 0):
                passages[key], contributions[key] = hit, contribution
    ordered = sorted(scores, key=lambda key: -scores[key])
    return [passages[key] for key in ordered[:limit]]
