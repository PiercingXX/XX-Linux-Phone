from main import parse_args


def test_cli_parsing():
    args = parse_args(["--send", "a.jpg", "b.jpg"])
    assert args.send == ["a.jpg", "b.jpg"]
    assert args.service is False
    args = parse_args(["--service"])
    assert args.service is True
    assert parse_args([]).send == []
