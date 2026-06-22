#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
IN_BOOK_FEATS = ROOT / 'result' / 'feats' / 'feat-book-feats.json'
IN_SUS = ROOT / 'result' / 'feats' / 'parse_error_suspects_high_confidence_latest.json'
OUT_BOOK_FEATS = ROOT / 'result' / 'feats' / 'feat-book-feats-cleaned-pass2.json'

HEADER_CN_EN_RE = re.compile(
    r'[\u4e00-\u9fff]{2,24}\s*[（(]\s*[A-Za-z][A-Za-z0-9\'"`\-\s,/&+]{2,}\s*[）)]\s*(?:〔[^〕]{1,12}〕)?'
)
LEADING_PUNCT_RE = re.compile(r'^[\s\)\]\}:：:\|\-\uFF09\uFF1A\uFF5C]+')


def normalize_ws(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '')).strip()


def truncate_at_next_header(detail: str) -> str:
    d = normalize_ws(detail)
    for m in HEADER_CN_EN_RE.finditer(d):
        if m.start() < 24:
            continue
        d = d[:m.start()].strip()
        break
    return normalize_ws(d)


def looks_like_header_only(text: str) -> bool:
    t = normalize_ws(text)
    if not t:
        return False
    if HEADER_CN_EN_RE.fullmatch(t):
        return True
    if t.endswith('（神话）') and len(t) <= 14:
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description='Second-pass fix on high-confidence contaminated feat details.')
    parser.add_argument('--book-feats', type=Path, default=IN_BOOK_FEATS)
    parser.add_argument('--suspects', type=Path, default=IN_SUS)
    parser.add_argument('--output', type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument('--inplace', action='store_true')
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding='utf-8'))
    sus_items = json.loads(args.suspects.read_text(encoding='utf-8')).get('items', [])

    sus_by_key: dict[str, set[str]] = {}
    for it in sus_items:
        k = normalize_ws(it.get('key', ''))
        if not k:
            continue
        sus_by_key.setdefault(k, set()).update(it.get('reasons') or [])

    updated = 0
    samples = []

    for rows in data.values():
        for row in rows:
            key = normalize_ws(row.get('match_key', ''))
            if key not in sus_by_key:
                continue
            reasons = sus_by_key[key]

            old = normalize_ws(row.get('detail_text', ''))
            if not old:
                continue
            benefit = normalize_ws(row.get('benefit_summary', ''))
            new = old
            local_reasons = []

            if 'embedded_next_feat_header' in reasons:
                t = truncate_at_next_header(new)
                if t and t != new:
                    new = t
                    local_reasons.append('truncate_next_header_pass2')

            if 'too_short_vs_benefit' in reasons and benefit:
                # Use benefit when detail is header-like or too terse.
                if looks_like_header_only(new) or (len(new) < max(12, int(len(benefit) * 0.8))):
                    if new != benefit:
                        new = benefit
                        local_reasons.append('replace_with_benefit_pass2')

            new = LEADING_PUNCT_RE.sub('', normalize_ws(new)).strip()

            if new and new != old:
                row['detail_text'] = new
                updated += 1
                if len(samples) < 80:
                    samples.append({'key': key, 'old': old, 'new': new, 'reasons': ','.join(local_reasons)})

    out = args.book_feats if args.inplace else args.output
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    rep = {
        'updated_rows': updated,
        'sample_count': len(samples),
        'samples': samples,
    }
    rep_path = ROOT / 'result' / 'feats' / 'detail_fix_report_pass2_latest.json'
    rep_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')

    print('Done.')
    print(f'Updated rows: {updated}')
    print(f'Output: {out}')
    print(f'Report: {rep_path}')


if __name__ == '__main__':
    main()