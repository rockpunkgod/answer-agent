"""Save a previously acquired native clipboard record; no desktop action or counting."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.chat_text_archive import archive_clipboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path, required=True, help='Windows-MCP Clipboard get result JSON')
    parser.add_argument('--group', required=True, help='Observed group name, not a verified platform ID')
    parser.add_argument('--output', type=Path, default=Path('data/private/chat-text-records'))
    args = parser.parse_args()
    print(archive_clipboard(args.result, args.output, observed_group=args.group))


if __name__ == '__main__':
    main()
