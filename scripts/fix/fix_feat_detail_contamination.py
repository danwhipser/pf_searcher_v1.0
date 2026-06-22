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
OUT_BOOK_FEATS = ROOT / 'result' / 'feats' / 'feat-book-feats-cleaned.json'

LEADING_PUNCT_RE = re.compile(r'^[\s\)\]\}:：:\|\-\uFF09\uFF1A\uFF5C]+')
URL_TAIL_RE = re.compile(r'(https?://\S+)')
HEADER_CN_EN_RE = re.compile(
    r'[\u4e00-\u9fff]{2,24}\s*[（(]\s*[A-Za-z][A-Za-z0-9\'"`\-\s,/&+]{2,}\s*[）)]\s*(?:〔[^〕]{1,12}〕)?'
)
STRAY_TOKEN_RE = re.compile(r'^(战斗|超魔|演武|神话增强专长|神话专长|专长|B[123]|CRB|APG|UC|UM|MA|OA)$')


def normalize_ws(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '')).strip()


def has_cn(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text or ''))


def truncate_next_header(text: str) -> tuple[str, bool]:
    t = text
    changed = False
    for m in HEADER_CN_EN_RE.finditer(t):
        # avoid cutting if match is near beginning (current feat title mention)
        if m.start() < 28:
            continue
        right = t[m.end() : m.end() + 64]
        # strong signals of next feat block begins here
        if any(k in right for k in ['先决条件', '专长效果', '通常状况', '特殊说明', '你', '该生物']):
            t = t[:m.start()].strip()
            changed = True
            break
        # or left side already seems a completed sentence
        left = t[max(0, m.start() - 24) : m.start()]
        if any(p in left for p in ['。', '；', '！', '？']):
            t = t[:m.start()].strip()
            changed = True
            break
    return t, changed


def clean_detail(detail: str, benefit: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    d = normalize_ws(detail)
    b = normalize_ws(benefit)
    if not d:
        return d, reasons

    nd = LEADING_PUNCT_RE.sub('', d).strip()
    if nd != d:
        reasons.append('trim_leading_punct')
        d = nd

    # remove translator/url tail garbage
    urlm = URL_TAIL_RE.search(d)
    if urlm and urlm.start() > 24:
        d = d[: urlm.start()].strip()
        reasons.append('cut_url_tail')

    if '译者' in d:
        pos = d.find('译者')
        if pos > 20:
            d = d[:pos].strip()
            reasons.append('cut_translator_tail')

    # trim appended next-feat header
    d2, cut = truncate_next_header(d)
    if cut:
        d = d2
        reasons.append('cut_next_feat_header')

    d = normalize_ws(d)

    # replace meaningless short detail by benefit summary
    if STRAY_TOKEN_RE.fullmatch(d or '') or (len(d) <= 7 and len(b) >= 8):
        if b:
            d = b
            reasons.append('replace_short_with_benefit')

    # english metadata contamination
    if re.search(r'\bSource\b|\bBenefit\b|\bMythic\b', d) and has_cn(b):
        d = b
        reasons.append('replace_english_meta_with_benefit')

    # if still starts with punct after operations
    nd = LEADING_PUNCT_RE.sub('', d).strip()
    if nd != d:
        d = nd
        reasons.append('trim_leading_punct_2')

    return normalize_ws(d), reasons


def main() -> None:
    parser = argparse.ArgumentParser(description='Fix contaminated feat detail_text in per-book JSON.')
    parser.add_argument('--book-feats', type=Path, default=IN_BOOK_FEATS)
    parser.add_argument('--output', type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument('--inplace', action='store_true')
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding='utf-8'))

    updated = 0
    by_reason: dict[str, int] = {}
    samples: list[dict[str, str]] = []

    for book, rows in data.items():
        for row in rows:
            old = normalize_ws(row.get('detail_text', ''))
            if not old:
                continue
            benefit = row.get('benefit_summary', '')
            new, reasons = clean_detail(old, benefit)
            if new != old and new:
                row['detail_text'] = new
                updated += 1
                for r in reasons:
                    by_reason[r] = by_reason.get(r, 0) + 1
                if len(samples) < 60:
                    samples.append({
                        'book': book,
                        'key': row.get('match_key', ''),
                        'old': old,
                        'new': new,
                        'reasons': ','.join(reasons),
                    })

    out = args.book_feats if args.inplace else args.output
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    report = {
        'updated_rows': updated,
        'reason_counts': by_reason,
        'samples': samples,
    }
    rep_path = ROOT / 'result' / 'feats' / 'detail_fix_report_latest.json'
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print('Done.')
    print(f'Updated rows: {updated}')
    for k, v in sorted(by_reason.items(), key=lambda x: (-x[1], x[0])):
        print(f'{k}: {v}')
    print(f'Output: {out}')
    print(f'Report: {rep_path}')


if __name__ == '__main__':
    main()