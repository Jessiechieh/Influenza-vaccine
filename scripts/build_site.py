#!/usr/bin/env python3
"""Generate static GitHub Pages site from the Kaohsiung hospital PDF."""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = ROOT / "landing page test file.pdf"
OUTPUT_DIR = ROOT
HOSPITALS_DIR = OUTPUT_DIR / "hospitals"


@dataclass
class HospitalRecord:
    district: str
    level: str
    name: str
    address: str
    phone: str
    stock: str


def extract_obj(data: bytes, num: int) -> bytes | None:
    match = re.search(rb"%d\s+0\s+obj\b" % num, data)
    if not match:
        return None
    start = match.end()
    end_match = re.search(rb"endobj", data[start:])
    if not end_match:
        return None
    return data[start : start + end_match.start()]


def parse_cmap(stream_bytes: bytes) -> dict[bytes, str]:
    text = stream_bytes.decode("latin1")
    mapping: dict[bytes, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.endswith("beginbfchar"):
            count = int(line.split()[0])
            for offset in range(1, count + 1):
                parts = lines[index + offset].strip().split()
                if len(parts) >= 2:
                    src = bytes.fromhex(parts[0].strip("<>"))
                    dst_hex = parts[1].strip("<>")
                    try:
                        uni = bytes.fromhex(dst_hex).decode("utf-16-be")
                    except Exception:
                        uni = "".join(
                            chr(int(dst_hex[pos : pos + 4], 16))
                            for pos in range(0, len(dst_hex), 4)
                        )
                    mapping[src] = uni
            index += count + 1
            continue
        if line.endswith("beginbfrange"):
            count = int(line.split()[0])
            for offset in range(1, count + 1):
                parts = lines[index + offset].strip().split()
                if len(parts) >= 3:
                    start_hex = parts[0].strip("<>")
                    end_hex = parts[1].strip("<>")
                    dst_hex = parts[2].strip("<>")
                    start = int(start_hex, 16)
                    end = int(end_hex, 16)
                    dst = int(dst_hex, 16)
                    src_len = len(start_hex) // 2
                    dst_len = len(dst_hex) // 2
                    for code in range(start, end + 1):
                        src = code.to_bytes(src_len, "big")
                        uni = (dst + (code - start)).to_bytes(dst_len, "big")
                        try:
                            uni_char = uni.decode("utf-16-be")
                        except Exception:
                            uni_char = "".join(
                                chr(int(dst_hex[pos : pos + 4], 16))
                                for pos in range(0, len(dst_hex), 4)
                            )
                        mapping[src] = uni_char
            index += count + 1
            continue
        index += 1
    return mapping


def tokenize(stream: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(stream):
        char = stream[index]
        if char.isspace():
            index += 1
            continue
        if char == "/":
            end = index + 1
            while end < len(stream) and not stream[end].isspace() and stream[end] not in "[]<>()/":
                end += 1
            tokens.append(stream[index:end])
            index = end
            continue
        if char == "(":
            end = index + 1
            while end < len(stream) and stream[end] != ")":
                end += 2 if stream[end] == "\\" else 1
            tokens.append(stream[index : end + 1])
            index = end + 1
            continue
        if char == "<":
            if index + 1 < len(stream) and stream[index + 1] == "<":
                tokens.append("<<")
                index += 2
                continue
            end = index + 1
            while end < len(stream) and stream[end] != ">":
                end += 1
            tokens.append(stream[index : end + 1])
            index = end + 1
            continue
        if char == ">":
            if index + 1 < len(stream) and stream[index + 1] == ">":
                tokens.append(">>")
                index += 2
                continue
            tokens.append(">")
            index += 1
            continue
        if char in "[]":
            tokens.append(char)
            index += 1
            continue
        end = index
        while end < len(stream) and not stream[end].isspace() and stream[end] not in "[]<>()/":
            end += 1
        tokens.append(stream[index:end])
        index = end
    return tokens


def decode_hex_text(hex_str: str, cmap: dict[bytes, str]) -> str:
    hex_str = hex_str.strip("<>")
    if len(hex_str) % 2 == 1:
        hex_str += "0"
    data = bytes.fromhex(hex_str)
    return "".join(cmap.get(data[index : index + 2], "") for index in range(0, len(data), 2))


def parse_pdf_records() -> tuple[str, list[HospitalRecord]]:
    data = PDF_PATH.read_bytes()
    font_name_to_obj = {"/F1": 6, "/F2": 13, "/F3": 18, "/F4": 20}
    font_to_unicode: dict[int, int] = {}
    for font_obj in font_name_to_obj.values():
        obj_data = extract_obj(data, font_obj)
        if not obj_data:
            continue
        match = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", obj_data)
        if match:
            font_to_unicode[font_obj] = int(match.group(1))

    font_cmaps: dict[int, dict[bytes, str]] = {}
    for font_obj, cmap_obj in font_to_unicode.items():
        cmap_data = extract_obj(data, cmap_obj)
        if not cmap_data:
            continue
        start = re.search(rb"stream\r?\n", cmap_data)
        end = re.search(rb"\r?\nendstream", cmap_data)
        if not start or not end:
            continue
        stream = cmap_data[start.end() : end.start()]
        font_cmaps[font_obj] = parse_cmap(zlib.decompress(stream))

    content_obj = extract_obj(data, 5)
    if not content_obj:
        raise RuntimeError("Unable to locate content stream")
    start = re.search(rb"stream\r?\n", content_obj)
    end = re.search(rb"\r?\nendstream", content_obj)
    if not start or not end:
        raise RuntimeError("Unable to parse content stream")
    stream = zlib.decompress(content_obj[start.end() : end.start()]).decode("latin1")

    tokens = tokenize(stream)
    current_font_obj: int | None = None
    operands: list[object] = []
    text_positions: list[tuple[float | None, float | None, str]] = []

    x_pos: float | None = None
    y_pos: float | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"Tf", "Tm", "Tj", "TJ"}:
            if token == "Tf":
                if len(operands) >= 2:
                    font_name = operands[-2]
                    current_font_obj = font_name_to_obj.get(str(font_name))
                operands = []
            elif token == "Tm":
                if len(operands) >= 6:
                    try:
                        x_pos = float(operands[-2])
                        y_pos = float(operands[-1])
                    except Exception:
                        pass
                operands = []
            elif token == "Tj":
                if operands:
                    text_token = operands[-1]
                    cmap = font_cmaps.get(current_font_obj or 0, {})
                    if isinstance(text_token, str) and text_token.startswith("<"):
                        text = decode_hex_text(text_token, cmap)
                    elif isinstance(text_token, str) and text_token.startswith("("):
                        text = text_token[1:-1]
                    else:
                        text = str(text_token)
                    text_positions.append((x_pos, y_pos, text))
                operands = []
            elif token == "TJ":
                if operands:
                    arr = operands[-1]
                    texts: list[str] = []
                    cmap = font_cmaps.get(current_font_obj or 0, {})
                    if isinstance(arr, list):
                        for item in arr:
                            if isinstance(item, str) and item.startswith("<"):
                                texts.append(decode_hex_text(item, cmap))
                            elif isinstance(item, str) and item.startswith("("):
                                texts.append(item[1:-1])
                    text_positions.append((x_pos, y_pos, "".join(texts)))
                operands = []
            index += 1
            continue
        if token == "[":
            array: list[str] = []
            index += 1
            while index < len(tokens) and tokens[index] != "]":
                array.append(tokens[index])
                index += 1
            operands.append(array)
            index += 1
            continue
        try:
            operands.append(float(token))
        except Exception:
            operands.append(token)
        index += 1

    rows: dict[float, list[tuple[float, str]]] = {}
    for x, y, text in text_positions:
        if text.strip() == "":
            continue
        if x is None or y is None:
            continue
        y_key = round(y, 2)
        rows.setdefault(y_key, []).append((x, text))

    sorted_rows = sorted(rows.items(), key=lambda item: -item[0])
    title = ""
    records: list[HospitalRecord] = []
    for y, items in sorted_rows:
        items_sorted = [text for _, text in sorted(items, key=lambda item: item[0])]
        if not title and len(items_sorted) == 1 and "流感疫苗" in items_sorted[0]:
            title = items_sorted[0]
            continue
        if items_sorted[:2] == ["行政區", "醫院層級"]:
            continue
        if len(items_sorted) < 5:
            continue
        if items_sorted[0] == "行政區":
            continue
        if len(items_sorted) < 6:
            items_sorted.append("")
        records.append(
            HospitalRecord(
                district=items_sorted[0],
                level=items_sorted[1],
                name=items_sorted[2],
                address=items_sorted[3],
                phone=items_sorted[4],
                stock=items_sorted[5],
            )
        )
    return title, records


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_site() -> None:
    title, records = parse_pdf_records()
    HOSPITALS_DIR.mkdir(parents=True, exist_ok=True)

    css = """
:root {
  color-scheme: light;
  font-family: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif;
  background: #f6f7fb;
  color: #1f2a37;
}

body {
  margin: 0;
  padding: 32px;
}

main {
  max-width: 1100px;
  margin: 0 auto;
}

header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 24px;
}

h1 {
  margin: 0;
  font-size: 28px;
}

.subtitle {
  color: #556070;
  font-size: 14px;
}

.hospital-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
}

.card {
  background: #ffffff;
  border-radius: 16px;
  padding: 16px;
  box-shadow: 0 10px 20px rgba(15, 23, 42, 0.08);
  border: 1px solid rgba(148, 163, 184, 0.3);
}

.card h2 {
  margin: 0 0 8px;
  font-size: 18px;
  line-height: 1.4;
}

.card p {
  margin: 4px 0;
  font-size: 14px;
  color: #334155;
}

.card a {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-size: 14px;
  color: #1d4ed8;
  text-decoration: none;
  font-weight: 600;
}

.card a:hover {
  text-decoration: underline;
}

.detail-list {
  list-style: none;
  padding: 0;
  margin: 16px 0 0;
}

.detail-list li {
  display: flex;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px dashed #e2e8f0;
}

.detail-list span {
  min-width: 88px;
  font-weight: 600;
  color: #0f172a;
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  color: #1d4ed8;
  text-decoration: none;
  font-weight: 600;
}
""".strip()

    write_file(OUTPUT_DIR / "styles.css", css)

    cards_html: list[str] = []
    for idx, record in enumerate(records, start=1):
        slug = f"hospital-{idx:03d}"
        detail_path = f"hospitals/{slug}/index.html"
        cards_html.append(
            "\n".join(
                [
                    "<article class=\"card\">",
                    f"  <h2>{record.name}</h2>",
                    f"  <p>{record.district}｜{record.level}</p>",
                    f"  <p>{record.address}</p>",
                    f"  <p>{record.phone}</p>",
                    f"  <a href=\"{detail_path}\">查看院所網站 →</a>",
                    "</article>",
                ]
            )
        )

        detail_html = "\n".join(
            [
                "<!doctype html>",
                "<html lang=\"zh-Hant\">",
                "<head>",
                "  <meta charset=\"utf-8\" />",
                "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />",
                f"  <title>{record.name}｜高雄市流感疫苗合約院所</title>",
                "  <link rel=\"stylesheet\" href=\"../../styles.css\" />",
                "</head>",
                "<body>",
                "  <main>",
                "    <header>",
                f"      <h1>{record.name}</h1>",
                "      <div class=\"subtitle\">高雄市流感疫苗合約院所獨立資訊頁</div>",
                "    </header>",
                "    <section class=\"card\">",
                "      <ul class=\"detail-list\">",
                f"        <li><span>行政區</span><div>{record.district}</div></li>",
                f"        <li><span>醫院層級</span><div>{record.level}</div></li>",
                f"        <li><span>地址</span><div>{record.address}</div></li>",
                f"        <li><span>電話</span><div>{record.phone}</div></li>",
                f"        <li><span>疫苗庫存</span><div>{record.stock}</div></li>",
                "      </ul>",
                "    </section>",
                "    <a class=\"back-link\" href=\"../../index.html\">← 回到總覽</a>",
                "  </main>",
                "</body>",
                "</html>",
            ]
        )
        write_file(HOSPITALS_DIR / slug / "index.html", detail_html)

    index_html = "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"zh-Hant\">",
            "<head>",
            "  <meta charset=\"utf-8\" />",
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />",
            "  <title>高雄市流感疫苗合約院所獨立網站</title>",
            "  <link rel=\"stylesheet\" href=\"styles.css\" />",
            "</head>",
            "<body>",
            "  <main>",
            "    <header>",
            "      <h1>高雄市流感疫苗合約院所獨立網站</h1>",
            f"      <div class=\"subtitle\">資料來源：{title}</div>",
            "    </header>",
            "    <section class=\"hospital-grid\">",
            "\n".join(cards_html),
            "    </section>",
            "  </main>",
            "</body>",
            "</html>",
        ]
    )

    write_file(OUTPUT_DIR / "index.html", index_html)


if __name__ == "__main__":
    build_site()
