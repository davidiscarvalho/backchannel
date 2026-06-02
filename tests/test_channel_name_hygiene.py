"""Channel names reject control/null bytes (defense in depth for a bare
self-host console without a CSP), but HTML chars are allowed — escaping is the
render layer's job."""

import tempfile
import unittest
from pathlib import Path

from backchannel.store import APIError, BackchannelStore

OWNER = "owner_1"
KEY = "key_1"


class ChannelNameHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = BackchannelStore(Path(self.tempdir.name) / "names.db")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _create(self, name: str) -> dict:
        return self.store.create_channel({"name": name, "mode": "broadcast"}, owner_id=OWNER, key_id=KEY)

    def test_rejects_null_byte(self) -> None:
        with self.assertRaises(APIError) as ctx:
            self._create("evil\x00name")
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.error, "invalid_channel_name")

    def test_rejects_control_chars(self) -> None:
        for bad in ("line\nbreak", "tab\tname", "bell\x07", "del\x7f"):
            with self.assertRaises(APIError):
                self._create(bad)

    def test_rejects_overlong_name(self) -> None:
        with self.assertRaises(APIError):
            self._create("x" * 201)

    def test_allows_html_chars(self) -> None:
        # '<' is a legal channel-name character; XSS defense is escaping at render.
        ch = self._create("<script>alert(1)</script>")
        self.assertEqual(ch["name"], "<script>alert(1)</script>")

    def test_rejects_zero_width_and_bidi(self) -> None:
        # Invisible / bidirectional formatting chars enable display spoofing
        # (two names that look identical but differ) — reject them. Built from
        # explicit codepoints so the invisible chars don't get mangled in source.
        for cp in (0x200B, 0x200D, 0x202E, 0x2066, 0x200F, 0xFEFF, 0x00AD, 0x061C):
            with self.assertRaises(APIError) as ctx:
                self._create(f"team{chr(cp)}secret")
            self.assertEqual(ctx.exception.status, 422, f"U+{cp:04X} should be rejected")
            self.assertEqual(ctx.exception.error, "invalid_channel_name")

    def test_allows_normal_unicode(self) -> None:
        # Ordinary non-ASCII letters are fine — only invisible/bidi controls are barred.
        ch = self._create("café-canал-日本語")
        self.assertEqual(ch["name"], "café-canал-日本語")


if __name__ == "__main__":
    unittest.main()
