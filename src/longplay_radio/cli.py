"""Small command line surface for validating and running one DJ job."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from longplay_radio import __version__
from longplay_radio.config import ConfigurationError, Settings
from longplay_radio.contract import ContractError, DJJob
from longplay_radio.pipeline import PipelineError, run_dj_job
from longplay_radio.storage import StorageError, store_from_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="longplay-radio",
        description="Render an approval-carrying LONGPLAY RADIO line with Genblaze and B2 provenance.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate", help="validate a job without rendering")
    validate.add_argument("job", type=Path)
    subcommands.add_parser("doctor", help="show non-secret selected configuration")
    run = subcommands.add_parser("run", help="render, store, verify, and print a receipt")
    run.add_argument("job", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            job = DJJob.from_path(args.job)
            print(
                json.dumps(
                    {
                        "status": "validated",
                        "schema_version": job.schema_version,
                        "job_id": str(job.job_id),
                        "job_payload_sha256": job.payload_sha256,
                        "approved_script_sha256": job.approval.script_sha256,
                    },
                    sort_keys=True,
                    indent=2,
                )
            )
            return
        settings = Settings.from_env()
        if args.command == "doctor":
            print(json.dumps({"status": "ready", **settings.public_status()}, indent=2))
            return
        job = DJJob.from_path(args.job)
        store = store_from_settings(settings)
        try:
            receipt = run_dj_job(job, settings, store)
        finally:
            store.close()
        print(receipt.to_json())
    except (ConfigurationError, ContractError, PipelineError, StorageError) as exc:
        print(f"longplay-radio: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
