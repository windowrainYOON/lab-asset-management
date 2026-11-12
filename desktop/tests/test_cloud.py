import pytest

from app.integrations.gdrive.drive import DriveError, parse_folder_id

FOLDER = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def test_parse_folder_id():
    link = f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing"
    assert parse_folder_id(link) == FOLDER
    assert parse_folder_id(FOLDER) == FOLDER
    assert parse_folder_id(f"https://drive.google.com/open?id={FOLDER}") == FOLDER
    with pytest.raises(DriveError):
        parse_folder_id("https://example.com/nothing")



def test_version_ignores_metadata_only_changes():
    from app.integrations.gdrive.drive import RemoteFile
    a = RemoteFile.from_api({"id": "f", "version": "5", "headRevisionId": "rev1"})
    b = RemoteFile.from_api({"id": "f", "version": "7", "headRevisionId": "rev1"})
    assert a.version == b.version == "rev1"
