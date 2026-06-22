import argparse
import html
import re
from pathlib import Path
from typing import Iterable

import importlib


def _load_bs4():
    try:
        module = importlib.import_module("bs4")
        return module.BeautifulSoup
    except ImportError as exc:
        raise SystemExit("缺少 beautifulsoup4，请运行 pip install beautifulsoup4") from exc


def detect_encoding(data: bytes) -> str:
    m = re.search(br"charset=([0-9a-zA-Z_-]+)", data[:512], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore")
        try:
            data.decode(enc)
            return enc
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8", "gb18030", "gbk", "gb2312", "cp936", "latin1"):
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8"


def dump_text_from_body(body) -> Iterable[str]:
    for element in body.find_all(["p", "h1", "h2", "h3", "h4", "li"]):
        text = element.get_text(" ", strip=True)
        if text:
            yield text


def html_to_markdown(path: Path, BeautifulSoup) -> str:
    raw_bytes = path.read_bytes()
    encoding = detect_encoding(raw_bytes)
    raw = raw_bytes.decode(encoding, errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")
    title_tag = soup.title
    title = title_tag.get_text(strip=True) if title_tag else path.stem
    if soup.body:
        segments = list(dump_text_from_body(soup.body))
    else:
        segments = [soup.get_text(" ", strip=True)]
    md_lines = [f"## {title}", f"**Source:** `{path.name}`", ""]
    for segment in segments:
        md_lines.append(segment)
        md_lines.append("")
    return "\n".join(md_lines).strip()


def main():
    parser = argparse.ArgumentParser(description="将 spell 目录下 HTML 汇总为 Markdown")
    parser.add_argument(
        "--spell-dir",
        default="spell",
        help="输入 HTML 目录（默认 spell/）",
    )
    parser.add_argument(
        "--output",
        default="result/spell-content.md",
        help="输出的 Markdown 文件（默认 result/spell-content.md）。",
    )
    args = parser.parse_args()
    spell_dir = Path(args.spell_dir)
    if not spell_dir.is_dir():
        raise SystemExit(f"{spell_dir} 不存在，请先准备 spell 目录")
    BeautifulSoup = _load_bs4()
    entries = []
    for file in sorted(spell_dir.glob("*.htm*")):
        entries.append(html_to_markdown(file, BeautifulSoup))
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n\n".join(entries) + "\n", encoding="utf-8")
    print(f"已生成 {out_path}，包含 {len(entries)} 个 HTML 段落")


if __name__ == "__main__":
    main()

