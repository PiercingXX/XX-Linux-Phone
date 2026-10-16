"""Run the app from a checkout: PYTHONPATH=src python3 -m xx_calendar."""
import sys


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if "--service" in args:
        from xx_calendar.service import main as service_main

        raise SystemExit(service_main(args))
    from xx_calendar.main import main as gtk_main

    raise SystemExit(gtk_main())


if __name__ == "__main__":
    main()
