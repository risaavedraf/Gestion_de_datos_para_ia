"""
Run individual pipeline stages
"""
import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

from config.logging_config import setup_logging

logger = setup_logging("run_stage")


def run_stage(stage: str, sample_size: int = None):
    """Run a specific pipeline stage"""
    from src.ingestion import ingest
    from src.cleaning import clean
    from src.validation import validate
    from src.loader import load

    stages = {
        "bronze": lambda: ingest(sample_size=sample_size),
        "silver": lambda: clean(sample_size=sample_size),
        "gold": lambda: validate(sample_size=sample_size),
        "load": lambda: load(sample_size=sample_size)
    }

    if stage not in stages:
        print(f"Error: Unknown stage '{stage}'")
        print(f"Valid stages: {', '.join(stages.keys())}")
        sys.exit(1)

    print(f"Running stage: {stage}")
    result = stages[stage]()

    print(f"\nResult:")
    for key, value in result.items():
        if key != "transformations":
            print(f"  {key}: {value}")

    if "transformations" in result:
        print(f"  transformations:")
        for t in result["transformations"]:
            print(f"    - {t}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run individual pipeline stage")
    parser.add_argument("stage", choices=["bronze", "silver", "gold", "load"], help="Stage to run")
    parser.add_argument("--sample", type=int, default=None, help="Sample size")

    args = parser.parse_args()
    run_stage(args.stage, sample_size=args.sample)
