from arbfinder.validation import judge, deflate, purged_split


def test_arbitrage_immer_confirmed_ohne_overfitting():
    v = judge(in_sample_edge=2.0, out_of_sample_edge=None, n_trials=50,
              requires_validation=False)
    assert v.status == "confirmed"


def test_kein_signal_wird_verworfen():
    v = judge(in_sample_edge=0.0, out_of_sample_edge=None, n_trials=1)
    assert v.status == "rejected"


def test_vielversprechend_aber_wenig_daten_wird_geparkt_nicht_verworfen():
    v = judge(in_sample_edge=3.0, out_of_sample_edge=None, n_trials=10,
              requires_validation=True, min_samples=500, n_samples=20)
    assert v.status == "parked"  # NICHT rejected -> Einwand entschaerft


def test_robust_in_und_oos_wird_bestaetigt():
    v = judge(in_sample_edge=3.0, out_of_sample_edge=2.4, n_trials=5,
              requires_validation=True, min_samples=100, n_samples=800)
    assert v.status == "confirmed"


def test_deflation_ist_mild():
    # 20 Versuche duerfen einen 3%-Edge nicht auf <0 druecken
    assert deflate(3.0, 20) > 0


def test_purged_split_entfernt_testindizes_aus_training():
    splits = purged_split(100, k=5)
    for train, test in splits:
        assert not (set(train) & set(test))  # kein Leakage
