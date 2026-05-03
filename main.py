"""CLI entry point for the OwnersHub Ad & Page Optimiser.

Usage:
    python main.py --mode full
    python main.py --mode light
    python main.py --mode full --dry-run
    python main.py --mode full --config /path/to/config.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def _install_walltime_guard(seconds: int = 600) -> None:
    """Hard wall-clock cap so a hung run can't sit forever under cron/systemd."""
    if not hasattr(__import__("signal"), "SIGALRM"):
        return  # POSIX-only; Windows dev runs simply skip this guard
    import signal

    def _handler(signum, frame):  # noqa: ARG001
        raise SystemExit(f"wall-clock limit ({seconds}s) exceeded; aborting run")

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OwnersHub Ad & Page Optimiser")
    parser.add_argument("--mode", choices=["full", "light", "content", "serve"], required=True)
    parser.add_argument(
        "--config",
        default=os.environ.get("ADS_OPTIMIZER_CONFIG") or str(PROJECT_ROOT / "config.yaml"),
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline but skip the Claude call. Useful for plumbing tests.",
    )
    parser.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=600,
        help="Wall-clock cap on the whole run (POSIX only). Default 600s.",
    )
    args = parser.parse_args(argv)

    _load_dotenv()
    if args.mode != "serve":
        _install_walltime_guard(args.max_runtime_seconds)

    from ads_optimizer.config_loader import ConfigError, load_config
    from ads_optimizer.logging_setup import setup_logging
    from ads_optimizer import runner

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return 2

    log_path = PROJECT_ROOT / config["storage"]["logs_file"]
    setup_logging(
        log_path,
        max_bytes=int(config["storage"].get("log_max_bytes", 5 * 1024 * 1024)),
        backup_count=int(config["storage"].get("log_backup_count", 5)),
    )
    logger = logging.getLogger("main")
    logger.info("Starting run: mode=%s dry_run=%s config=%s", args.mode, args.dry_run, args.config)

    try:
        if args.mode == "full":
            runner.run_full(config, PROJECT_ROOT, dry_run=args.dry_run)
        elif args.mode == "light":
            runner.run_light(config, PROJECT_ROOT, dry_run=args.dry_run)
        elif args.mode == "content":
            runner.run_content(config, PROJECT_ROOT, dry_run=args.dry_run)
        else:
            from ads_optimizer.hitl.app import create_app
            port = int(config.get("hitl", {}).get("port", 8080))
            logger.info("Starting Henry approval server on port %d", port)
            app = create_app(config, PROJECT_ROOT)
            app.run(host="0.0.0.0", port=port, debug=False)
    except Exception:
        logger.exception("Run failed")
        return 1

    logger.info("Run finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
