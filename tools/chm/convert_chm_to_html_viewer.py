#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def load_bs4():
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("缺少 beautifulsoup4，请先执行: pip install beautifulsoup4") from exc
    return BeautifulSoup


def decode_bytes(raw: bytes) -> str:
    m = re.search(br"charset=([0-9A-Za-z_\-]+)", raw[:1024], re.I)
    if m:
        enc = m.group(1).decode("ascii", errors="ignore")
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "gb18030", "gbk", "cp936", "big5", "latin1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def normalize_rel_path(value: str) -> str:
    path = unquote(value).replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    return path.lstrip("./").strip()


def is_external_url(value: str) -> bool:
    if not value:
        return True
    v = value.strip().lower()
    if v.startswith(("http://", "https://", "data:", "mailto:", "javascript:")):
        return True
    return False


def data_uri(path: Path) -> str:
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "application/octet-stream"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def decompile_chm(chm_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    hh_exe = Path(r"C:\Windows\hh.exe")
    if not hh_exe.exists():
        raise SystemExit("未找到 C:\\Windows\\hh.exe，无法自动解包 CHM。")
    cmd = [str(hh_exe), "-decompile", str(target_dir), str(chm_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        stderr = proc.stderr.strip()
        raise SystemExit(f"hh.exe 解包失败，退出码={proc.returncode}\n{stderr}")


def find_hhc(extracted_dir: Path, chm_path: Path | None) -> Path:
    candidates = sorted(extracted_dir.glob("*.hhc"))
    if candidates:
        return candidates[0]
    if chm_path:
        likely = extracted_dir / f"{chm_path.stem}.hhc"
        if likely.exists():
            return likely
    raise SystemExit(f"在 {extracted_dir} 下未找到 .hhc 目录文件。")


def parse_hhc(hhc_path: Path) -> list[dict]:
    text = decode_bytes(hhc_path.read_bytes())
    nodes = parse_hhc_manual(text)
    if nodes:
        return nodes
    # 兜底：若手工解析失败，再回退到 bs4
    BeautifulSoup = load_bs4()
    soup = BeautifulSoup(text, "html.parser")
    root_ul = soup.find("ul")
    if root_ul is None:
        return []
    return parse_ul(root_ul)


def parse_hhc_manual(text: str) -> list[dict]:
    token_re = re.compile(
        r"(?is)<object\s+type\s*=\s*['\"]text/sitemap['\"].*?</object>|</?ul\b[^>]*>"
    )
    param_re = re.compile(
        r"(?is)<param[^>]*name\s*=\s*['\"](?P<name>[^'\"]+)['\"][^>]*value\s*=\s*['\"](?P<value>[^'\"]*)['\"][^>]*>"
    )

    root: list[dict] = []
    stack: list[list[dict]] = [root]
    pending_node: dict | None = None

    for m in token_re.finditer(text):
        token = m.group(0)
        lower = token.lower()

        if lower.startswith("<object"):
            title = ""
            local = ""
            image_number = ""
            for pm in param_re.finditer(token):
                name = pm.group("name").strip().lower()
                value = pm.group("value").strip()
                if name == "name":
                    title = value
                elif name == "local":
                    local = value
                elif name == "imagenumber":
                    image_number = value
            node = {"title": title, "local": local, "image_number": image_number, "children": []}
            stack[-1].append(node)
            pending_node = node
            continue

        if lower.startswith("<ul"):
            # 只有刚解析完一个节点时，后续 UL 才表示该节点的 children
            if pending_node is not None:
                stack.append(pending_node["children"])
                pending_node = None
            continue

        if lower.startswith("</ul"):
            if len(stack) > 1:
                stack.pop()
            pending_node = None

    return root


def parse_ul(ul_tag) -> list[dict]:
    nodes: list[dict] = []
    for li in ul_tag.find_all("li", recursive=False):
        obj = li.find("object")
        title = ""
        local = ""
        if obj is not None:
            for p in obj.find_all("param"):
                name = (p.get("name") or "").strip().lower()
                value = (p.get("value") or "").strip()
                if name == "name":
                    title = value
                elif name == "local":
                    local = value
        child_ul = li.find("ul", recursive=False)
        children = parse_ul(child_ul) if child_ul is not None else []
        nodes.append({"title": title, "local": local, "children": children})
    return nodes


def flatten_first_page(nodes: list[dict]) -> str:
    for n in nodes:
        local = (n.get("local") or "").strip()
        if local:
            return local
        sub = flatten_first_page(n.get("children", []))
        if sub:
            return sub
    return ""


class Inliner:
    CSS_URL_RE = re.compile(r"url\((['\"]?)(.*?)\1\)")

    def __init__(self, extracted_dir: Path):
        self.extracted_dir = extracted_dir.resolve()
        self.asset_cache: dict[Path, str] = {}
        self.page_cache: dict[str, str] = {}
        self.page_paths: dict[str, Path] = {}
        self.bs4 = load_bs4()
        self._build_page_index()

    def _build_page_index(self) -> None:
        for p in self.extracted_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self.extracted_dir).as_posix()
            key = rel.lower()
            self.page_paths[key] = p

    def resolve_from_ref(self, base_file: Path, ref: str) -> Path | None:
        if not ref or is_external_url(ref):
            return None
        parts = urlsplit(ref)
        path_only = normalize_rel_path(parts.path)
        if not path_only:
            return None
        target = (base_file.parent / path_only).resolve()
        try:
            target.relative_to(self.extracted_dir)
        except ValueError:
            return None
        if not target.exists() or not target.is_file():
            return None
        return target

    def _data_uri_cached(self, path: Path) -> str:
        p = path.resolve()
        if p in self.asset_cache:
            return self.asset_cache[p]
        uri = data_uri(p)
        self.asset_cache[p] = uri
        return uri

    def inline_css(self, css_text: str, css_file: Path) -> str:
        def repl(match: re.Match[str]) -> str:
            raw_ref = match.group(2).strip()
            if not raw_ref or is_external_url(raw_ref) or raw_ref.startswith("#"):
                return match.group(0)
            asset = self.resolve_from_ref(css_file, raw_ref)
            if not asset:
                return match.group(0)
            return f"url('{self._data_uri_cached(asset)}')"

        return self.CSS_URL_RE.sub(repl, css_text)

    def inline_page(self, page_path: Path) -> str:
        page_rel = page_path.relative_to(self.extracted_dir).as_posix().lower()
        if page_rel in self.page_cache:
            return self.page_cache[page_rel]

        soup = self.bs4(decode_bytes(page_path.read_bytes()), "html.parser")

        # 内联 stylesheet
        for link in list(soup.find_all("link")):
            rel = " ".join(link.get("rel", [])).lower()
            href = (link.get("href") or "").strip()
            if "stylesheet" not in rel or not href:
                continue
            css_path = self.resolve_from_ref(page_path, href)
            if not css_path:
                continue
            css_text = decode_bytes(css_path.read_bytes())
            style = soup.new_tag("style")
            style.string = self.inline_css(css_text, css_path)
            link.replace_with(style)

        # 内联 script src
        for script in list(soup.find_all("script")):
            src = (script.get("src") or "").strip()
            if not src:
                continue
            js_path = self.resolve_from_ref(page_path, src)
            if not js_path:
                continue
            script.attrs.pop("src", None)
            script.string = decode_bytes(js_path.read_bytes())

        # 内联常见 src/href 资源
        attr_map = [
            ("img", "src"),
            ("source", "src"),
            ("audio", "src"),
            ("video", "src"),
            ("track", "src"),
            ("embed", "src"),
            ("object", "data"),
            ("input", "src"),
            ("link", "href"),
        ]
        for tag_name, attr in attr_map:
            for tag in soup.find_all(tag_name):
                val = (tag.get(attr) or "").strip()
                if not val or is_external_url(val) or val.startswith("#"):
                    continue
                target = self.resolve_from_ref(page_path, val)
                if not target:
                    continue
                if target.suffix.lower() in (".htm", ".html"):
                    continue
                tag[attr] = self._data_uri_cached(target)

        # style 内联 url(...)
        for tag in soup.find_all(style=True):
            original = tag.get("style", "")
            tag["style"] = self.inline_css(original, page_path)

        # 页面内链接转换为 postMessage 导航
        for a in soup.find_all("a"):
            href = (a.get("href") or "").strip()
            if not href or is_external_url(href) or href.startswith("#"):
                continue
            target = self.resolve_from_ref(page_path, href)
            if not target:
                continue
            if target.suffix.lower() not in (".htm", ".html"):
                continue
            key = target.relative_to(self.extracted_dir).as_posix()
            a["href"] = f"#/__page__/{key}"
            a["data-page-key"] = key

        # 注入页面内导航桥接脚本
        nav_script = soup.new_tag("script")
        nav_script.string = """
document.addEventListener("click", function (e) {
  const a = e.target && e.target.closest ? e.target.closest("a[data-page-key]") : null;
  if (!a) return;
  e.preventDefault();
  const key = a.getAttribute("data-page-key");
  if (!key) return;
  try {
    parent.postMessage({ type: "open-page", key: key }, "*");
  } catch (err) {}
});
"""
        if soup.body is not None:
            soup.body.append(nav_script)
        elif soup.html is not None:
            soup.html.append(nav_script)
        else:
            soup.append(nav_script)

        page_html = str(soup)
        self.page_cache[page_rel] = page_html
        return page_html

    def build_all_pages(self) -> dict[str, str]:
        result: dict[str, str] = {}
        html_files = sorted(
            p for p in self.extracted_dir.rglob("*") if p.is_file() and p.suffix.lower() in (".htm", ".html")
        )
        total = len(html_files)
        for i, p in enumerate(html_files, start=1):
            if i % 200 == 0 or i == total:
                print(f"  - 内嵌页面进度: {i}/{total}")
            key = p.relative_to(self.extracted_dir).as_posix()
            result[key] = self.inline_page(p)
        return result


def render_tree(nodes: list[dict]) -> str:
    parts = ["<ul>"]
    for n in nodes:
        title = html.escape((n.get("title") or "").strip() or "(无标题)")
        local = normalize_rel_path((n.get("local") or "").strip())
        children = n.get("children", [])
        if local:
            item = (
                f'<span class="toc-link" data-key="{html.escape(local)}" '
                f'title="{html.escape(local)}">{title}</span>'
            )
        else:
            item = f'<span class="toc-title">{title}</span>'
        parts.append(f"<li>{item}")
        if children:
            parts.append(render_tree(children))
        parts.append("</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def build_viewer_html(book_title: str, toc_html: str, first_page: str, pages: dict[str, str]) -> str:
    safe_title = html.escape(book_title)
    first_key = normalize_rel_path(first_page)
    pages_json = json.dumps(pages, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title} - 全内嵌 CHM 阅读器</title>
  <style>
    html, body {{ margin: 0; height: 100%; font-family: Arial, "Microsoft YaHei", sans-serif; }}
    .layout {{ display: grid; grid-template-columns: 360px 1fr; height: 100vh; }}
    .sidebar {{ border-right: 1px solid #ddd; overflow: auto; padding: 10px 14px; box-sizing: border-box; background: #fafafa; }}
    .content {{ height: 100vh; }}
    iframe {{ width: 100%; height: 100%; border: none; background: white; }}
    h1 {{ margin: 0 0 10px; font-size: 16px; }}
    .meta {{ margin: 0 0 10px; color: #666; font-size: 12px; }}
    ul {{ margin: 0; padding-left: 16px; }}
    li {{ margin: 4px 0; line-height: 1.4; }}
    .toc-link {{ color: #0b57d0; cursor: pointer; text-decoration: none; }}
    .toc-link:hover {{ text-decoration: underline; }}
    .toc-title {{ color: #333; }}
    .bar {{ display: flex; gap: 8px; margin-bottom: 8px; }}
    .bar input {{ flex: 1; padding: 4px 6px; }}
    .bar button {{ padding: 4px 8px; cursor: pointer; }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <h1>{safe_title}</h1>
      <p class="meta">全内嵌单文件：目录、正文、图片/CSS/JS 全部打包。</p>
      <div class="bar">
        <input id="q" type="text" placeholder="过滤目录文字...">
        <button id="clearBtn" type="button">清空</button>
      </div>
      <div id="toc">{toc_html}</div>
    </aside>
    <main class="content">
      <iframe id="frame"></iframe>
    </main>
  </div>
  <script id="pages-data" type="application/json">{pages_json}</script>
  <script>
    const toc = document.getElementById("toc");
    const q = document.getElementById("q");
    const clearBtn = document.getElementById("clearBtn");
    const frame = document.getElementById("frame");
    const pages = JSON.parse(document.getElementById("pages-data").textContent || "{{}}");
    let currentKey = "{html.escape(first_key)}";

    function renderPage(key) {{
      if (!key) return;
      const page = pages[key];
      if (!page) {{
        frame.srcdoc = "<h2 style='font-family:Arial;padding:16px'>页面不存在: " + key + "</h2>";
        return;
      }}
      currentKey = key;
      frame.srcdoc = page;
    }}

    toc.addEventListener("click", (e) => {{
      const t = e.target;
      if (!(t instanceof HTMLElement)) return;
      if (!t.classList.contains("toc-link")) return;
      const key = t.getAttribute("data-key");
      if (!key) return;
      renderPage(key);
    }});

    window.addEventListener("message", (evt) => {{
      const data = evt.data || {{}};
      if (data.type !== "open-page") return;
      if (!data.key) return;
      renderPage(data.key);
    }});

    function filterTree(keyword) {{
      const kw = keyword.trim().toLowerCase();
      const items = toc.querySelectorAll("li");
      items.forEach((li) => {{
        const text = (li.textContent || "").toLowerCase();
        li.style.display = !kw || text.includes(kw) ? "" : "none";
      }});
    }}

    q.addEventListener("input", () => filterTree(q.value));
    clearBtn.addEventListener("click", () => {{
      q.value = "";
      filterTree("");
    }});

    renderPage(currentKey);
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将 CHM（或已解包目录）转换为真正全内嵌的单文件 HTML。"
    )
    parser.add_argument(
        "--chm",
        type=Path,
        default=Path(r"assets\raw\Pathfinder v2.14 SC.chm"),
        help="CHM 文件路径（默认 Pathfinder v2.14 SC.chm）",
    )
    parser.add_argument(
        "--extracted-dir",
        type=Path,
        default=Path(r"Pathfinder v2.14 SC"),
        help="CHM 解包目录（默认 Pathfinder v2.14 SC）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(r"result\pathfinder-v2.14-sc-viewer-embedded.html"),
        help="输出 HTML 文件路径",
    )
    parser.add_argument(
        "--force-decompile",
        action="store_true",
        help="强制重新解包 CHM",
    )
    args = parser.parse_args()

    extracted_dir = args.extracted_dir.resolve()
    chm_path = args.chm.resolve()
    need_decompile = args.force_decompile or not extracted_dir.exists()

    if need_decompile:
        if not chm_path.exists():
            raise SystemExit(f"CHM 文件不存在: {chm_path}")
        print(f"[1/4] 解包 CHM: {chm_path} -> {extracted_dir}")
        decompile_chm(chm_path, extracted_dir)
    else:
        print(f"[1/4] 复用已存在目录: {extracted_dir}")

    hhc_path = find_hhc(extracted_dir, chm_path if chm_path.exists() else None)
    print(f"[2/4] 解析目录: {hhc_path.name}")
    nodes = parse_hhc(hhc_path)
    if not nodes:
        raise SystemExit("HHC 目录解析结果为空。")

    print("[3/4] 内嵌页面与资源（可能较慢）...")
    inliner = Inliner(extracted_dir)
    pages = inliner.build_all_pages()
    print(f"  - 页面总数: {len(pages)}")

    first_page = flatten_first_page(nodes) or "about:blank"
    toc_html = render_tree(nodes)
    html_text = build_viewer_html(chm_path.stem or extracted_dir.name, toc_html, first_page, pages)

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[4/4] 已生成: {output_path}")
    print(f"  - 文件大小: {size_mb:.2f} MB")
    print("完成：该文件可单独分发，不依赖外部 html/css/image。")


if __name__ == "__main__":
    sys.exit(main())

