r"""Audit 2026-10-20 P2-3 / P2-7: mirror URL whitelist and tail-cursor math.

P2-3: URL_RE claimed to be a whitelist but the old path tail admitted shell
metacharacters, and the value was interpolated unquoted into a remote
``bash -c``. The path charset is now strict; interpolation is additionally
shlex.quote'd (covered by TestBuildVllmInstallCommands in
test_deployment_service.py).

P2-7: the run_long / reattach poll advances a byte cursor over output
decoded with a replace fallback; U+FFFD re-encodes to 3 bytes for 1 raw
byte and must be corrected, or the cursor drifts and re-trips the boundary.
"""
import unittest

from backend.service.deployment_common import URL_RE, tail_offset


class TestUrlWhitelist(unittest.TestCase):
    def test_accepts_plain_mirror_urls(self):
        for url in (
            "https://mirrors.aliyun.com/pypi/simple/",
            "https://pypi.tuna.tsinghua.edu.cn/simple",
            "http://pypi.local:8080/simple/extra",
            # enterprise artifactory style (query strings are legal)
            "https://artifactory.local:8081/artifactory/api/pypi/simple/?api_key=abc",
        ):
            self.assertTrue(URL_RE.fullmatch(url), url)

    def test_rejects_shell_metachar_paths(self):
        for url in (
            "https://pypi.org/simple/;reboot",
            "https://pypi.org/simple/$(rm -rf /)",
            "https://pypi.org/simple/`id`",
            "https://pypi.org/simple/|nc",
            "https://pypi.org/simple/&reboot",
        ):
            self.assertFalse(URL_RE.fullmatch(url), url)

    def test_rejects_garbage(self):
        self.assertFalse(URL_RE.fullmatch("not-a-url"))
        self.assertFalse(
            URL_RE.fullmatch("https://pypi.org/simple/ with space"))

    def test_rejects_query_containing_space(self):
        # Audit 2026-10-22 REM-10: the query class was written ``[^\\s]`` in a
        # raw string — two backslashes, i.e. "not (literal backslash, letter
        # s)" — so a SPACE passed the "whitelist". It is now ``[^\s]``.
        for url in (
            "https://pypi.org/simple/?api key=abc",
            "https://pypi.org/simple/?key=abc def",
            "https://pypi.org/simple/?a=1 ;reboot",
        ):
            self.assertFalse(URL_RE.fullmatch(url), url)


class TestTailOffset(unittest.TestCase):
    def test_ascii_advances_by_byte_length(self):
        self.assertEqual(tail_offset("abc\n"), 4)

    def test_multibyte_utf8_counts_raw_bytes(self):
        # 2 CJK chars (3 bytes each) + '\n'
        self.assertEqual(tail_offset("中文\n"), 7)

    def test_replacement_char_corrected_to_raw_byte(self):
        # 1 'a' + 1 invalid byte + '\n' = 3 raw bytes; "a\ufffd\n"
        # re-encodes to 5, so subtract the 2-byte excess per U+FFFD.
        self.assertEqual(tail_offset("a\ufffd\n"), 3)

    def test_mixed(self):
        # "a" + invalid byte + "中" (3 bytes) = 5 raw bytes
        self.assertEqual(tail_offset("a\ufffd中"), 5)
