# Influenza-vaccine

本專案會從 `landing page test file.pdf` 解析高雄市流感疫苗合約院所資料，並輸出 GitHub Pages 靜態網站：

- `docs/index.html`：院所總覽清單
- `docs/hospitals/*/index.html`：每一家院所的獨立網站

## 產生網站

```bash
python scripts/generate_sites.py
```

執行後會在 `docs/` 產生所有頁面與樣式檔，可直接設定 GitHub Pages 來源為 `docs/`。
