"""Create a consistent SQLite snapshot, including committed WAL contents."""
import argparse
from pathlib import Path
import sqlite3

parser=argparse.ArgumentParser()
parser.add_argument("source")
parser.add_argument("destination")
args=parser.parse_args()
source=Path(args.source).resolve()
destination=Path(args.destination).resolve()
if not source.is_file():
    raise SystemExit("Source database does not exist.")
if destination.exists():
    raise SystemExit("Choose a new destination; existing backups are not overwritten.")
destination.parent.mkdir(parents=True,exist_ok=True)
with sqlite3.connect(source.as_uri()+"?mode=ro",uri=True) as src, sqlite3.connect(destination) as dest:
    src.backup(dest)
print("Backup created:",destination)
