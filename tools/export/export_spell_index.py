from pathlib import Path
import json

from table_chm import parse_hhc_toc


def traverse(nodes, keyword):
    stack = [(node, [node.get("title", "")]) for node in nodes]
    results = []
    while stack:
        node, path = stack.pop()
        title = node.get("title", "")
        local = node.get("local", "")
        normalized = title.strip()
        if keyword in normalized or keyword in local:
            if local:
                results.append(
                    {
                        "title": normalized,
                        "path": " / ".join(filter(None, path)),
                        "local": local,
                    }
                )
        for child in node.get("children", []):
            stack.append((child, path + [child.get("title", "")]))
    return results


def main():
    toc_path = Path("Pathfinder v2.14 SC/Pathfinder v2.14 SC.hhc")
    nodes = parse_hhc_toc(toc_path)
    spells = traverse(nodes, "法术")
    if not spells:
        print("未在目录中找到含 法术 的节点")
        return
    out = Path("result/spell-index.md")
    lines = ["# 法术目录", ""]
    for entry in spells:
        title = entry["title"] or entry["local"]
        path = entry["path"]
        link = entry["local"]
        lines.append(f"- [{title}]({link})  \n  `{path}`")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()







































