from tools import fmt


def test_num_widens_precision_below_10():
    assert fmt.num(86433.1) == "86,433.10" and fmt.num(1.3112345) == "1.3112" and fmt.num(-0.0215) == "-0.0215"
    assert fmt.num(0.0) == "0.00" and fmt.num(1.31, 1) == "1.3" and fmt.num(None) == "n/a"
    assert fmt.num(float("nan")) == "n/a"
