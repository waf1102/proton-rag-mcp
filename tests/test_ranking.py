from proton_rag.ranking import fuse_rankings


def hit(key, text=None):
    return {"metadata": {"docSource": key}, "text": text or key}


def test_agreement_promotes_known_evidence_and_keeps_best_supported_passage():
    legacy = [hit("sharp"), hit("other"), hit("gold", "legacy chunk")]
    consensus = [hit("gold", "semantic chunk"), hit("other"), hit("third")]
    result = fuse_rankings(legacy, consensus, 4)
    assert [h["metadata"]["docSource"] for h in result] == ["gold", "other", "third", "sharp"]
    assert result[0]["text"] == "semantic chunk"


def test_repeated_chunks_do_not_add_votes_and_ties_are_stable():
    unique = [hit("a"), hit("b"), hit("c")]
    repeated = [hit("a"), hit("a", "duplicate"), hit("b"), hit("c")]
    assert fuse_rankings(repeated, [], 3) == fuse_rankings(unique, [], 3)
    assert fuse_rankings([], unique, 2) == unique[:2]


def test_independent_shortlists_and_result_size_are_bounded():
    items = [hit(str(i)) for i in range(150)]
    result = fuse_rankings(items, [], 150)
    assert len(result) == 100
    assert len(fuse_rankings(items, list(reversed(items)), 7)) == 7


def test_equal_contributions_keep_first_seen_order():
    result = fuse_rankings([hit("a")], [hit(str(i)) for i in range(10)] + [hit("b")], 12)
    assert [h["metadata"]["docSource"] for h in result[-2:]] == ["a", "b"]
