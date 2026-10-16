"""Run the app from a checkout: PYTHONPATH=src python3 -m xx_contacts."""

import sys


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if "--service" in args:
        from xx_contacts.service import main as service_main

        raise SystemExit(service_main(args))
    from xx_contacts.main import main as gtk_main

    gtk_main()


if __name__ == "__main__":
    main()
