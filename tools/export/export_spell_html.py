import json
import shutil
from pathlib import Path
from urllib.parse import unquote


def find_spell_node(nodes):
    for node in nodes:
        if node.get("title") == "法术":
            return node
        child = find_spell_node(node.get("children", []))
        if child:
            return child
    return None


def collect_locals(node):
    locals = set()
    for child in node.get("children", []):
        local = child.get("local", "")
        if local:
            decoded = unquote(local)
            if decoded.endswith((".htm", ".html")):
                locals.add(decoded)
    return locals


def main():
    toc_path = Path("result/toc.json")
    if not toc_path.exists():
        raise SystemExit("请先生成 result/toc.json（用 table_chm）")
    data = json.loads(toc_path.read_text(encoding="utf-8"))
    spell_node = find_spell_node(data)
    if not spell_node:
        raise SystemExit("toc.json 中未找到 名称为 法术 的节点")
    html_files = collect_locals(spell_node)
    target_dir = Path("spell")
    target_dir.mkdir(exist_ok=True)
    base = Path("Pathfinder v2.14 SC")
    for relative in sorted(html_files):
        src = base / relative
        if not src.exists():
            print("缺少源文件：", relative)
            continue
        dest = target_dir / Path(relative).name
        shutil.copy(src, dest)
        print("复制", src, "->", dest)


if __name__ == "__main__":
    main()

