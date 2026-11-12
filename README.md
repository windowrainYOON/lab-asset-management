# lab-asset-management

연구실 자원·업무 관리 도구 두 가지가 들어 있습니다. 둘 다 연구실 Google Drive의 같은 시트를 읽고 씁니다.

```
desktop/   Asset Manager — macOS 데스크톱 앱 (Python · PySide6). desktop/README.md
webapp/    연구실 모바일 웹앱 — NFC 출퇴근, 일정·예약·업무·공지·주문·예산 (Apps Script + GitHub Pages). webapp/README.md
```

## 저장소에 올리지 않는 것

이 저장소는 공개입니다. 프로그램 코드만 올리고, 아래는 `.gitignore`로 막아 둡니다.

- 로그인 토큰·쿠키·OAuth 클라이언트 (`state/`, `secrets/`, `client_secret*.json`, `google_token*.json`)
- 연구실 데이터 (`data/`, `*.xlsx`, `*.xls`, `*.csv`, `*.labproj`, `Asset Manager 설정.json`)
- 웹앱 서버의 비공개 값: 시트 파일 ID와 처음 비밀번호의 해시 (`webapp/private/`, 그것이 들어간 `webapp/paste/Code.gs`)
- 빌드 결과·백업·로그 (`build/`, `dist/`, `backups/`, `releases/`, `logs/`)

테스트에 나오는 사람 이름·이메일은 모두 가짜입니다.
