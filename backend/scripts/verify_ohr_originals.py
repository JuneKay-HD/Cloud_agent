"""Verify separately restored OHR files against the delivery manifest."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2] / 'data/public_benchmark/ohr'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=root)
    parser.add_argument('--include-parquet', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((root / 'large_files_manifest.json').read_text(encoding='utf-8'))
    rows = [r for r in manifest['files'] if args.include_parquet or r['path'].endswith('.pdf')]
    problems = []
    for row in rows:
        path = args.root / row['path']
        if not path.is_file():
            problems.append({'path': row['path'], 'reason': 'missing'})
            continue
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != row['sha256'] or path.stat().st_size != row['bytes']:
            problems.append({'path': row['path'], 'reason': 'hash_or_size_mismatch'})
    print(json.dumps({'checked': len(rows), 'passed': len(rows)-len(problems),
                      'problems': problems}, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
