from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

PDF_PATH = Path("landing page test file.pdf")
OUTPUT_DIR = Path("docs")

LEVELS = ["醫學中心", "區域醫院", "地區醫院", "衛生所", "診所"]


@dataclass
class Hospital:
    district: str
    level: str
    name: str
    address: str
    phone: str
    vaccine_stock: str | None


@dataclass
class ExtractedData:
    hospitals: List[Hospital]
    source_note: str


def read_pdf_text() -> str:
    pdf = PDF_PATH.read_bytes()
    objects: Dict[int, bytes] = {}
    for match in re.finditer(rb"(\d+)\s+(\d+)\s+obj\s", pdf):
        obj_id = int(match.group(1))
        start = match.end()
        end = pdf.find(b"endobj", start)
        if end == -1:
            continue
        objects[obj_id] = pdf[start:end]

    def get_stream(obj_bytes: bytes) -> bytes | None:
        if b"stream" not in obj_bytes:
            return None
        stream_start = obj_bytes.find(b"stream")
        stream_data = obj_bytes[stream_start + len(b"stream") :]
        end = stream_data.find(b"endstream")
        stream_data = stream_data[:end]
        return stream_data.strip(b"\r\n")

    def parse_cmap(cmap_bytes: bytes) -> Dict[int, str]:
        text = cmap_bytes.decode("latin1")
        lines = [line.strip() for line in text.splitlines()]
        mapping: Dict[int, str] = {}
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.endswith("beginbfchar"):
                count = int(line.split()[0])
                i += 1
                for _ in range(count):
                    parts = lines[i].split()
                    if len(parts) >= 2:
                        src = int(parts[0].strip("<>"), 16)
                        dst = parts[1].strip("<>")
                        if len(dst) % 4 == 0:
                            chars = "".join(
                                chr(int(dst[j : j + 4], 16))
                                for j in range(0, len(dst), 4)
                            )
                        else:
                            chars = chr(int(dst, 16))
                        mapping[src] = chars
                    i += 1
                continue
            if line.endswith("beginbfrange"):
                count = int(line.split()[0])
                i += 1
                for _ in range(count):
                    parts = lines[i].split()
                    if len(parts) >= 3:
                        start = int(parts[0].strip("<>"), 16)
                        end = int(parts[1].strip("<>"), 16)
                        third = parts[2]
                        if third.startswith("["):
                            arr_line = third
                            while not arr_line.endswith("]"):
                                i += 1
                                arr_line += " " + lines[i]
                            arr_items = re.findall(r"<([0-9A-Fa-f]+)>", arr_line)
                            for offset, item in enumerate(arr_items):
                                chars = "".join(
                                    chr(int(item[j : j + 4], 16))
                                    for j in range(0, len(item), 4)
                                )
                                mapping[start + offset] = chars
                        else:
                            base = int(third.strip("<>"), 16)
                            for code in range(start, end + 1):
                                mapping[code] = chr(base + (code - start))
                    i += 1
                continue
            i += 1
        return mapping

    font_resource_map = {1: 6, 2: 13, 3: 18, 4: 20}
    cmap_map = {6: 1109, 13: 1112, 20: 1116}
    font_maps: Dict[int, Dict[int, str]] = {}
    for font_obj, cmap_obj in cmap_map.items():
        stream = get_stream(objects[cmap_obj])
        if not stream:
            continue
        dec = zlib.decompress(stream)
        font_maps[font_obj] = parse_cmap(dec)

    content_stream = zlib.decompress(get_stream(objects[5]))

    pattern = re.compile(
        rb"/F(\d+)\s+[0-9.]+\s+Tf|\[(.*?)\]\s*TJ|<([0-9A-Fa-f]+)>\s*Tj|\((.*?)\)\s*Tj",
        re.S,
    )

    current_font = None
    texts: List[str] = []

    for match in pattern.finditer(content_stream):
        if match.group(1):
            current_font = font_resource_map.get(int(match.group(1)))
            continue
        if match.group(2) is not None:
            arr = match.group(2)
            parts: List[tuple[str, str | bytes]] = []
            idx = 0
            while idx < len(arr):
                if arr[idx : idx + 1] == b"<":
                    end = arr.find(b">", idx + 1)
                    if end == -1:
                        break
                    hexstr = arr[idx + 1 : end].decode("ascii")
                    parts.append(("hex", hexstr))
                    idx = end + 1
                    continue
                if arr[idx : idx + 1] == b"(":
                    idx += 1
                    buf = b""
                    depth = 1
                    while idx < len(arr) and depth > 0:
                        ch = arr[idx : idx + 1]
                        if ch == b"\\":
                            if idx + 1 < len(arr):
                                buf += arr[idx + 1 : idx + 2]
                                idx += 2
                                continue
                        if ch == b"(":
                            depth += 1
                        elif ch == b")":
                            depth -= 1
                            if depth == 0:
                                idx += 1
                                break
                        buf += ch
                        idx += 1
                    parts.append(("lit", buf))
                    continue
                idx += 1

            text_piece = ""
            for kind, value in parts:
                if kind == "hex":
                    mapping = font_maps.get(current_font or 0, {})
                    chars = []
                    for j in range(0, len(value), 4):
                        code = int(value[j : j + 4], 16)
                        chars.append(mapping.get(code, ""))
                    text_piece += "".join(chars)
                else:
                    text_piece += value.decode("latin1")
            if text_piece:
                texts.append(text_piece)
            continue
        if match.group(3):
            hexstr = match.group(3).decode("ascii")
            mapping = font_maps.get(current_font or 0, {})
            chars = []
            for j in range(0, len(hexstr), 4):
                code = int(hexstr[j : j + 4], 16)
                chars.append(mapping.get(code, ""))
            text_piece = "".join(chars)
            if text_piece:
                texts.append(text_piece)
            continue
        if match.group(4) is not None:
            text_piece = match.group(4).decode("latin1")
            if text_piece:
                texts.append(text_piece)
            continue

    return "".join(texts)


def parse_entries(text: str) -> ExtractedData:
    columns = "行政區醫院層級單位名稱地址電話號碼疫苗庫存"
    if text.startswith(columns):
        text = text[len(columns) :]

    parts = text.split("⩗")
    entries = parts[:-1]
    source_note = parts[-1] if parts else ""

    hospitals: List[Hospital] = []
    for entry in entries:
        level_index = min(
            (entry.find(level) for level in LEVELS if entry.find(level) != -1),
            default=-1,
        )
        if level_index == -1:
            continue
        district = entry[:level_index]
        if not district.endswith("區"):
            district = f"{district}區"
        rest = entry[level_index:]
        level = next((lv for lv in LEVELS if rest.startswith(lv)), "")
        rest = rest[len(level) :]
        address_start = rest.find("高雄市")
        if address_start == 0:
            second_address = rest.find("高雄市", len("高雄市"))
            if second_address != -1:
                name = rest[:second_address]
                address_start = second_address
            else:
                name = ""
        else:
            name = rest[:address_start]
        address_phone = rest[address_start:]
        phone_match = re.search(r"0\d-\d{7,8}", address_phone)
        if phone_match:
            address = address_phone[: phone_match.start()]
            phone = phone_match.group(0)
        else:
            address = address_phone
            phone = ""
        hospitals.append(
            Hospital(
                district=district,
                level=level,
                name=name,
                address=address,
                phone=phone,
                vaccine_stock=None,
            )
        )

    return ExtractedData(hospitals=hospitals, source_note=source_note)


def slugify(value: str) -> str:
    value = re.sub(r"\s+", "-", value.strip())
    value = re.sub(r"[^\w\-\u4e00-\u9fff]", "", value)
    return value


def write_site(data: ExtractedData) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "assets").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "hospitals").mkdir(parents=True, exist_ok=True)

    css = (
        "body { font-family: 'Noto Sans TC', system-ui, sans-serif; margin: 0; background: #f7f8fb; color: #1f2937; }\n"
        ".container { max-width: 960px; margin: 0 auto; padding: 32px 20px 48px; }\n"
        ".hero { background: white; padding: 24px; border-radius: 16px; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08); }\n"
        ".hero h1 { margin-top: 0; }\n"
        ".grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); margin-top: 24px; }\n"
        ".card { background: white; padding: 16px; border-radius: 12px; border: 1px solid #e5e7eb; }\n"
        ".meta { color: #64748b; font-size: 14px; margin: 4px 0 0; }\n"
        ".tag { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 12px; margin-right: 6px; }\n"
        ".footer { margin-top: 32px; color: #6b7280; font-size: 14px; }\n"
        "a { color: #2563eb; text-decoration: none; }\n"
        "a:hover { text-decoration: underline; }\n"
        ".detail-list { list-style: none; padding: 0; }\n"
        ".detail-list li { margin: 8px 0; }\n"
    )
    (OUTPUT_DIR / "assets" / "styles.css").write_text(css, encoding="utf-8")

    cards_html = []
    for idx, hospital in enumerate(data.hospitals, start=1):
        slug = f"{idx:02d}-{slugify(hospital.name)}"
        page_dir = OUTPUT_DIR / "hospitals" / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        page_path = page_dir / "index.html"
        vaccine_stock = hospital.vaccine_stock or "未提供"

        page_html = f"""<!doctype html>
<html lang=\"zh-Hant\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>{hospital.name}｜高雄市流感疫苗合約院所</title>
    <link rel=\"stylesheet\" href=\"../../assets/styles.css\" />
  </head>
  <body>
    <div class=\"container\">
      <div class=\"hero\">
        <a href=\"../../index.html\">← 返回總覽</a>
        <h1>{hospital.name}</h1>
        <p class=\"meta\">{hospital.district} · {hospital.level}</p>
        <ul class=\"detail-list\">
          <li><strong>地址：</strong>{hospital.address}</li>
          <li><strong>電話：</strong>{hospital.phone}</li>
          <li><strong>疫苗庫存：</strong>{vaccine_stock}</li>
        </ul>
      </div>
      <div class=\"footer\">資料來源：{data.source_note}</div>
    </div>
  </body>
</html>
"""
        page_path.write_text(page_html, encoding="utf-8")
        cards_html.append(
            f"""
        <div class=\"card\">
          <span class=\"tag\">{hospital.district}</span>
          <span class=\"tag\">{hospital.level}</span>
          <h3>{hospital.name}</h3>
          <p class=\"meta\">{hospital.address}</p>
          <p class=\"meta\">{hospital.phone}</p>
          <a href=\"hospitals/{slug}/index.html\">前往醫院網站 →</a>
        </div>
"""
        )

    index_html = f"""<!doctype html>
<html lang=\"zh-Hant\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>高雄市流感疫苗合約院所</title>
    <link rel=\"stylesheet\" href=\"assets/styles.css\" />
  </head>
  <body>
    <div class=\"container\">
      <div class=\"hero\">
        <h1>高雄市流感疫苗合約院所</h1>
        <p>本頁整理高雄市流感疫苗合約院所名單，點選卡片即可進入各院所的獨立網站。</p>
        <p class=\"meta\">資料來源：{data.source_note}</p>
      </div>
      <div class=\"grid\">
        {"".join(cards_html)}
      </div>
      <div class=\"footer\">
        <p>提醒：疫苗庫存欄位於原始資料中未提供，因此各院所頁面顯示為「未提供」。</p>
      </div>
    </div>
  </body>
</html>
"""

    (OUTPUT_DIR / "index.html").write_text(index_html, encoding="utf-8")


if __name__ == "__main__":
    extracted_text = read_pdf_text()
    data = parse_entries(extracted_text)
    write_site(data)
    print(f"Generated {len(data.hospitals)} hospital pages.")
