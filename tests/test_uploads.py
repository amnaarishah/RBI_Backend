"""Upload safety: size limit, real content type, safe file names."""
import asyncio
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException, UploadFile  # noqa: E402
from app.core.uploads import read_upload, safe_filename, DOCUMENT_TYPES, SPREADSHEET_TYPES, MB  # noqa: E402


def up(name, data):
    return UploadFile(filename=name, file=io.BytesIO(data))


def run(coro):
    return asyncio.run(coro)


class Uploads(unittest.TestCase):
    def test_valid_files_pass(self):
        run(read_upload(up("a.pdf", b"%PDF-1.7 data"), DOCUMENT_TYPES, MB))
        run(read_upload(up("a.xlsx", b"PK\x03\x04rest"), SPREADSHEET_TYPES, MB))
        run(read_upload(up("a.csv", b"tag,date\n1,2"), SPREADSHEET_TYPES, MB))

    def rejected(self, name, data, allowed, limit=MB, code=400):
        with self.assertRaises(HTTPException) as cm:
            run(read_upload(up(name, data), allowed, limit))
        self.assertEqual(cm.exception.status_code, code)

    def test_wrong_extension(self):
        self.rejected("evil.exe", b"MZ....", DOCUMENT_TYPES)

    def test_content_must_match_extension(self):
        self.rejected("fake.pdf", b"MZ\x90\x00 not a pdf", DOCUMENT_TYPES)
        self.rejected("fake.xlsx", b"%PDF-1.4", SPREADSHEET_TYPES)
        self.rejected("bin.csv", b"a\x00b\x00", SPREADSHEET_TYPES)

    def test_size_limit_and_empty(self):
        self.rejected("big.pdf", b"%PDF-" + b"0" * (2 * MB), DOCUMENT_TYPES, limit=MB, code=413)
        self.rejected("empty.pdf", b"", DOCUMENT_TYPES)

    def test_safe_filename(self):
        self.assertEqual(safe_filename("../../etc/passwd.pdf"), "passwd.pdf")
        self.assertEqual(safe_filename("C:" + chr(92) + "x" + chr(92) + "My Report (final).PDF"), "My_Report_final.pdf")
        self.assertEqual(safe_filename(""), "file")
        self.assertLessEqual(len(safe_filename("a" * 300 + ".pdf")), 80)


if __name__ == "__main__":
    unittest.main()
