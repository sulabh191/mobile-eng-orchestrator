"""Allow `python -m orchestrator` as an alias for the `orc` console script."""

from orchestrator.cli.app import main

if __name__ == "__main__":  # pragma: no cover - thin wrapper
    main()
