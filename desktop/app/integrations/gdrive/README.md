# Google Drive access (not built yet)

Options, simplest first:
1. Read the local Google Drive for desktop folder — no API permission needed.
2. Drive API + OAuth (desktop client) with `drive.file`: only files the user
   picks. Add `spreadsheets.readonly` to import Google Sheets directly.
3. `drive.readonly` for browsing the whole Drive (restricted scope; needs
   Google verification if distributed outside a test-user list).

Tokens would be stored in the macOS Keychain.
