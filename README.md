# Influenza-vaccine

高雄市流感疫苗合約院所資料已整理成 GitHub Pages 靜態網站，並為每一家院所建立獨立頁面。

## 使用方式

1. 重新產生 GitHub Pages 檔案：
   ```bash
   python scripts/generate_pages.py
   ```
2. 產生的網站內容位於 `docs/`，可直接設定為 GitHub Pages 來源。

## 網站結構

- `docs/index.html`：院所總覽頁面
- `docs/hospitals/<slug>/index.html`：單一院所頁面
- `docs/style.css`：共用樣式
