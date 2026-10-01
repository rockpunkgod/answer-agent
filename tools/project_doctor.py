"""Default inspection never runs tests or changes runtime state."""
import argparse
import json
from pathlib import Path
from helpdesk.project_status import project_doctor


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--state', default='.agent/project-state.json')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps(project_doctor(args.root, args.state), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
