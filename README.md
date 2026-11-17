# 기름값 추적

GitHub Actions가 매일 2번(09:20, 18:20) 오피넷 유가를 받아 `data/prices.json`에 쌓고,
GitHub Pages(`index.html`)에서 그래프로 보여줍니다.

- 지역·유종 기본값: `config.json` (`region`: "전북", `fuel`: B027 휘발유 / D047 경유 / K015 LPG)
- 인증키: 저장소 Settings → Secrets and variables → Actions → `OPINET_KEY`
- 수동 실행: Actions 탭 → 유가 수집 → Run workflow
