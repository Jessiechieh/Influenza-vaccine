#!/usr/bin/env python
from __future__ import annotations

import html
import re
import zlib
from dataclasses import dataclass
from pathlib import Path

PDF_PATH = Path("landing page test file.pdf")
OUTPUT_DIR = Path("docs")

LEVELS = ["醫學中心", "區域醫院", "地區醫院", "診所", "衛生所"]


@dataclass
class Hospital:
    district: str
    level: str
    name: str
    address: str
    phone: str
    stock: str = "未提供"


def extract_pdf_text(pdf_path: Path) -> str:
    pdf = pdf_path.read_bytes()
    obj_re = re.compile(rb"(\d+)\s+\d+\s+obj(.*?)endobj", re.S)
    objects = {int(m.group(1)): m.group(2).strip() for m in obj_re.finditer(pdf)}

    page_obj = objects[4]
    font_dict = re.search(rb"/Font\s*<<([^>]*)>>", page_obj, re.S).group(1)
    font_map = {
        name.decode(): int(ref)
        for name, ref in re.findall(rb"/(F\d+)\s+(\d+)\s+0\s+R", font_dict)
    }

    def extract_cmap(font_name: str) -> str | None:
        fobj = objects[font_map[font_name]]
        match = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", fobj)
        if not match:
            return None
        ref = int(match.group(1))
        stream = re.search(rb"stream\r?\n(.*?)endstream", objects[ref], re.S).group(1)
        try:
            return zlib.decompress(stream).decode("latin1")
        except Exception:
            return stream.decode("latin1")

    def parse_cmap(cmap: str) -> dict[str, str]:
        mapping: dict[str, str] = {}
        lines = [line.strip() for line in cmap.splitlines() if line.strip()]
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            bfchar_match = re.match(r"(\d+)\s+beginbfchar", line)
            if bfchar_match:
                count = int(bfchar_match.group(1))
                for j in range(1, count + 1):
                    src_dst = re.findall(r"<([0-9A-Fa-f]+)>", lines[idx + j])
                    if len(src_dst) >= 2:
                        src, dst = src_dst[0], src_dst[1]
                        mapping[src.upper()] = chr(int(dst, 16))
                idx += count + 1
                continue
            bfrange_match = re.match(r"(\d+)\s+beginbfrange", line)
            if bfrange_match:
                count = int(bfrange_match.group(1))
                for j in range(1, count + 1):
                    entries = re.findall(r"<([0-9A-Fa-f]+)>", lines[idx + j])
                    if len(entries) >= 3:
                        start, end, dst = entries[:3]
                        start_i = int(start, 16)
                        end_i = int(end, 16)
                        dst_i = int(dst, 16)
                        for offset, code in enumerate(range(start_i, end_i + 1)):
                            mapping[f"{code:04X}"] = chr(dst_i + offset)
                idx += count + 1
                continue
            idx += 1
        return mapping

    font_maps = {}
    for font_name in font_map:
        cmap = extract_cmap(font_name)
        if cmap:
            font_maps[font_name] = parse_cmap(cmap)

    content_ref = int(re.search(rb"/Contents\s+(\d+)\s+0\s+R", page_obj).group(1))
    content_obj = objects[content_ref]
    stream = re.search(rb"stream\r?\n(.*?)endstream", content_obj, re.S).group(1)
    content = zlib.decompress(stream).decode("latin1")

    text_parts: list[str] = []
    current_font = None
    for line in content.split("\n"):
        font_match = re.search(r"/(F\d+)\s+\d+(?:\.\d+)?\s+Tf", line)
        if font_match:
            current_font = font_match.group(1)
        for literal in re.findall(r"\((.*?)\)", line):
            text_parts.append(literal)
        for hexstr in re.findall(r"<([0-9A-Fa-f]+)>", line):
            mapping = font_maps.get(current_font, {})
            step = 4
            if mapping:
                step = len(next(iter(mapping)))
            if len(hexstr) % step != 0:
                step = 2
            chars: list[str] = []
            for idx in range(0, len(hexstr), step):
                chunk = hexstr[idx : idx + step].upper()
                if chunk in mapping:
                    chars.append(mapping[chunk])
                else:
                    chars.append(chr(int(chunk, 16)))
            text_parts.append("".join(chars))

    return "".join(text_parts)


def parse_hospitals(text: str) -> list[Hospital]:
    rows = [row for row in text.split("⩗") if row]
    hospitals: list[Hospital] = []
    for row in rows:
        if row.startswith("114年度"):
            continue
        if row.startswith("行政區"):
            row = row.replace("行政區醫院層級單位名稱地址電話號碼疫苗庫存", "")
            row = row.strip()
        if not row:
            continue

        phone_match = re.search(r"0\d{1,2}-\d{6,8}", row)
        phone = phone_match.group(0) if phone_match else ""
        phone_start = phone_match.start() if phone_match else len(row)

        district_match = re.match(r"(.+?區)", row)
        if not district_match:
            continue
        district = district_match.group(1)
        level = next((lvl for lvl in LEVELS if row[district_match.end() :].startswith(lvl)), "")
        if not level:
            continue
        level_end = district_match.end() + len(level)

        address_start = row.rfind("高雄市", 0, phone_start)
        if address_start == -1:
            continue
        name = row[level_end:address_start]
        address = row[address_start:phone_start]

        hospitals.append(
            Hospital(
                district=district,
                level=level,
                name=name,
                address=address,
                phone=phone,
            )
        )

    return hospitals


def slugify(name: str, index: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", name)
    safe = safe.strip("-")
    if not safe:
        return f"hospital-{index:03d}"
    return f"{safe.lower()}-{index:03d}"


def render_hospital_page(hospital: Hospital, slug: str) -> str:
    title = f"{hospital.name} | 高雄市流感疫苗合約院所"
    maps_query = html.escape(hospital.address)
    return f"""<!doctype html>
<html lang=\"zh-Hant\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <link rel=\"stylesheet\" href=\"../../style.css\" />
</head>
<body>
  <header class=\"site-header\">
    <div class=\"container\">
      <a class=\"back-link\" href=\"../../index.html\">← 返回總覽</a>
      <h1>{html.escape(hospital.name)}</h1>
      <p class=\"subtitle\">高雄市流感疫苗合約院所｜{html.escape(hospital.district)} · {html.escape(hospital.level)}</p>
    </div>
  </header>

  <main class=\"container\">
    <section class=\"card\">
      <h2>院所資訊</h2>
      <dl class=\"info-grid\">
        <div>
          <dt>行政區</dt>
          <dd>{html.escape(hospital.district)}</dd>
        </div>
        <div>
          <dt>醫院層級</dt>
          <dd>{html.escape(hospital.level)}</dd>
        </div>
        <div>
          <dt>地址</dt>
          <dd>{html.escape(hospital.address)}</dd>
        </div>
        <div>
          <dt>電話</dt>
          <dd><a href=\"tel:{html.escape(hospital.phone)}\">{html.escape(hospital.phone)}</a></dd>
        </div>
        <div>
          <dt>疫苗庫存</dt>
          <dd>{html.escape(hospital.stock)}</dd>
        </div>
      </dl>
      <div class=\"actions\">
        <a class=\"button\" href=\"https://www.google.com/maps/search/?api=1&query={maps_query}\" target=\"_blank\" rel=\"noreferrer\">查看地圖</a>
        <a class=\"button secondary\" href=\"../../index.html\">返回院所清單</a>
      </div>
    </section>
  </main>

  <footer class=\"site-footer\">
    <div class=\"container\">
      <p>資料來源：114 年度公費流感疫苗合約院所清冊（高雄市）</p>
      <p>最後更新：115/1/19</p>
    </div>
  </footer>
</body>
</html>
"""


def render_index(hospitals: list[Hospital], entries: list[tuple[str, Hospital]]) -> str:
    cards = "\n".join(
        f"""
      <a class=\"hospital-card\" href=\"{html.escape(slug)}/index.html\">
        <h3>{html.escape(hospital.name)}</h3>
        <p>{html.escape(hospital.district)} · {html.escape(hospital.level)}</p>
        <span>{html.escape(hospital.address)}</span>
        <span>{html.escape(hospital.phone)}</span>
      </a>
        """
        for slug, hospital in entries
    )
    return f"""<!doctype html>
<html lang=\"zh-Hant\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>高雄市流感疫苗合約院所</title>
  <link rel=\"stylesheet\" href=\"style.css\" />
</head>
<body>
  <header class=\"site-header\">
    <div class=\"container\">
      <h1>高雄市流感疫苗合約院所</h1>
      <p class=\"subtitle\">共 {len(hospitals)} 家院所，依醫院層級與行政區整理。</p>
      <p class=\"meta\">資料來源：114 年度公費流感疫苗合約院所清冊（高雄市）</p>
    </div>
  </header>

  <main class=\"container\">
    <section class=\"grid\">
{cards}
    </section>
  </main>

  <footer class=\"site-footer\">
    <div class=\"container\">
      <p>最後更新：115/1/19</p>
    </div>
  </footer>
</body>
</html>
"""


def render_styles() -> str:
    return """
:root {
  color-scheme: light;
  --bg: #f6f7fb;
  --card: #ffffff;
  --text: #182033;
  --muted: #5f6b7a;
  --accent: #3366ff;
  --border: #e0e6f1;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif;
  background: var(--bg);
  color: var(--text);
}

.container {
  width: min(1100px, 92vw);
  margin: 0 auto;
}

.site-header {
  padding: 48px 0 32px;
  background: linear-gradient(135deg, #ecf2ff, #ffffff);
  border-bottom: 1px solid var(--border);
}

.site-header h1 {
  margin: 0 0 12px;
  font-size: 2.4rem;
}

.subtitle {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 1.1rem;
}

.meta {
  margin: 0;
  color: var(--muted);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 18px;
  padding: 32px 0 48px;
}

.hospital-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 20px;
  border-radius: 16px;
  background: var(--card);
  text-decoration: none;
  color: inherit;
  border: 1px solid var(--border);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.hospital-card h3 {
  margin: 0;
  font-size: 1.1rem;
}

.hospital-card span,
.hospital-card p {
  margin: 0;
  color: var(--muted);
  font-size: 0.95rem;
}

.hospital-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 20px rgba(20, 38, 92, 0.1);
}

.card {
  background: var(--card);
  padding: 24px;
  border-radius: 18px;
  border: 1px solid var(--border);
  margin: 32px 0 48px;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin: 16px 0 0;
}

.info-grid dt {
  font-weight: 600;
  color: var(--muted);
  margin-bottom: 4px;
}

.info-grid dd {
  margin: 0;
  font-size: 1rem;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 20px;
}

.button {
  background: var(--accent);
  color: #fff;
  padding: 10px 18px;
  border-radius: 999px;
  text-decoration: none;
  font-weight: 600;
}

.button.secondary {
  background: #eef1f8;
  color: var(--text);
}

.back-link {
  display: inline-flex;
  margin-bottom: 12px;
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
}

.site-footer {
  border-top: 1px solid var(--border);
  padding: 20px 0 40px;
  color: var(--muted);
  font-size: 0.9rem;
}
"""


def main() -> None:
    text = extract_pdf_text(PDF_PATH)
    hospitals = parse_hospitals(text)
    hospitals.sort(key=lambda h: (h.district, h.level, h.name))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "hospitals").mkdir(parents=True, exist_ok=True)

    entries: list[tuple[str, Hospital]] = []
    for idx, hospital in enumerate(hospitals, start=1):
        slug = slugify(hospital.name, idx)
        hospital_dir = OUTPUT_DIR / "hospitals" / slug
        hospital_dir.mkdir(parents=True, exist_ok=True)
        (hospital_dir / "index.html").write_text(
            render_hospital_page(hospital, slug), encoding="utf-8"
        )
        entries.append((f"hospitals/{slug}", hospital))

    (OUTPUT_DIR / "index.html").write_text(
        render_index(hospitals, entries), encoding="utf-8"
    )
    (OUTPUT_DIR / "style.css").write_text(render_styles().lstrip(), encoding="utf-8")


if __name__ == "__main__":
    main()
