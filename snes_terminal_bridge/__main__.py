import argparse
from .bridge import main

parser = argparse.ArgumentParser(description="SNES Terminal Bridge")
parser.add_argument(
    "--target", "-t",
    metavar="WINDOW",
    help="Override target window title pattern (default: value from keyboard_mappings.yaml)",
)
args = parser.parse_args()
main(target_override=args.target)
