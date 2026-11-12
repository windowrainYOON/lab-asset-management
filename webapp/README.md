# 연구실 모바일 웹앱 (코드)

NFC 태그로 출근을 기록하고, 일정·장비 예약·업무·공지사항·주문 요청·예산을 휴대폰에서 보는 웹앱입니다.
장비 예약·공지사항·주문 요청은 휴대폰에서 만들고 고칠 수도 있습니다.

```
server/      Apps Script 서버 (파일별 원본). 고칠 때는 여기를 고친다.
private/     이 연구실의 비공개 값 (시트 파일 ID, 처음 비밀번호의 해시). gitignore. private.example.gs를 복사해 채운다.
paste/       Apps Script 편집기에 붙여 넣을 한 파일 판 (Code.gs + appsscript.json). node tools/bundle.js 로 만든다.
             Code.gs에는 private/ 값이 들어가므로 gitignore.
web/         GitHub Pages에 올릴 화면 (index.html, css/, js/). 키·파일 ID·개인정보 없음.
tests/       node tests/run.js  — 서버 규칙 테스트 (가짜 데이터, 구글 서비스 흉내)
             node tests/devserver.js  — 화면 미리보기 http://localhost:8787 (비밀번호 test-pin-42, 가짜 구성원)
tools/       bundle.js
```

## 어디에 무엇이 올라가나
| 무엇 | 어디 | 공개 |
|---|---|---|
| `web/` | GitHub Pages (`.github/workflows/pages.yml`이 main의 `webapp/web/`을 배포) | 공개. `js/config.js`에는 서버 주소(…/exec)만 |
| `server/` + `private/` → `paste/Code.gs` | 연구실 Google 계정의 Apps Script 프로젝트 | `server/`만 공개. `private/`와 `paste/Code.gs`는 저장소에 올리지 않는다 |

## 스크립트 속성 (Apps Script → 프로젝트 설정 → 스크립트 속성)
| 이름 | 값 | 누가 |
|---|---|---|
| `DB_SHEET_ID` | `setup()`이 자동으로 넣음 | 자동 |
| `ENTRY_SECRET` | `setup()`이 자동으로 넣음 (입장 열쇠 서명) | 자동 |
| `CONFIG_FILE_ID` | 비워 두면 `Asset Manager 설정.json` 기본 ID | 선택 |
| `ALLOW_PLAIN_TAP` | `1` = 태그 서명 없이도 출근 기록 (태그 설정 전 시험용). 태그를 붙이면 지운다 | 사용자 |
| `SDM_KEY` | 태그 열쇠 32자리 16진수. **사용자가 직접 입력** (채팅·파일에 적지 않음) | 사용자, 태그 단계 |
| `SDM_MAC_KEY` | 태그의 파일 읽기 열쇠가 다를 때만 | 사용자, 선택 |

## 동작 요약
- 요청: `POST` (Content-Type `text/plain`, JSON 본문 `{action, device, key, member, …}`) → `{ok, …}`.
- 비밀번호: 설정 JSON `webapp.pin` (PBKDF2-SHA256, 2000회). 없으면 서버에 든 처음 값의 해시. 휴대폰마다 10분에 5번까지.
- 입장 열쇠: `기기ID.버전.서명`. 데스크톱에서 "계속 열어 두기"를 끄고 바꾸면 버전이 올라가 모두 다시 입력.
- 출근: 태그 URL `?picc=…&cmac=…` → AN12196 방식 검사(카운터 증가 확인, 처음 보는 태그는 서명이 맞으면 `태그` 탭에 자동 등록) → 오늘 칸이 비어 있을 때만 `HH:MM`.
- 쓰기: 시트 칸 단위, 쓰기 직전 `ID`로 행 다시 찾기, 스크립트 잠금, 모든 쓰기는 `감사로그`. 남의 항목은 화면에서 한 번 확인.
- 읽기 캐시 30초, 쓰기하면 그 파일 캐시는 바로 버림.
- 매일 밤 백업: `installBackup()`을 한 번 실행하면 03시에 4개 시트 사본을 `Lab Web App/백업`에 (14일 보관).

## 시험 결과 (2026-11-11)
- `node tests/run.js` 29/29 통과 (AN12196 태그 예제, Python과 같은 PBKDF2, 예약 규칙, 월 탭·열 자동 생성 등).
- 데스크톱 `tests/test_webapp_pin.py` 6/6 통과.
- 화면: Playwright(iPhone 크기, 라이트·다크)로 전 화면과 쓰기 흐름 확인, 콘솔 오류 없음.
