import unittest

from backend.service.model_service import _parse_env_vars, _strip_shell_comment


class TestStripShellComment(unittest.TestCase):
    def test_no_hash_returns_unchanged(self):
        self.assertEqual(_strip_shell_comment("FOO=bar"), "FOO=bar")

    def test_full_line_comment_stripped_to_empty(self):
        self.assertEqual(_strip_shell_comment("# FOO=bar"), "")

    def test_trailing_space_hash_comment_stripped(self):
        self.assertEqual(_strip_shell_comment("FOO=bar # note"), "FOO=bar")

    def test_trailing_hash_no_space_after_stripped(self):
        # '#' preceded by whitespace still starts a comment, even with no
        # space after it.
        self.assertEqual(_strip_shell_comment("FOO=bar #baz"), "FOO=bar")

    def test_hash_not_at_word_start_is_kept(self):
        # '#' inside a token (preceded by a non-space char) is not a comment.
        self.assertEqual(_strip_shell_comment("FOO=bar#baz"), "FOO=bar#baz")

    def test_hash_inside_double_quotes_is_kept(self):
        self.assertEqual(
            _strip_shell_comment('FOO="bar # baz"'), 'FOO="bar # baz"'
        )

    def test_hash_inside_single_quotes_is_kept(self):
        self.assertEqual(
            _strip_shell_comment("FOO='bar # baz'"), "FOO='bar # baz'"
        )

    def test_hash_after_closing_quote_stripped(self):
        # Quote closes before the '#', so the '#' is again at word start.
        self.assertEqual(
            _strip_shell_comment('FOO="bar" # note'), 'FOO="bar"'
        )


class TestParseEnvVars(unittest.TestCase):
    def test_basic_key_value(self):
        self.assertEqual(_parse_env_vars("KEY=VALUE"), [("KEY", "VALUE")])

    def test_export_prefix_is_dropped(self):
        self.assertEqual(_parse_env_vars("export FOO=bar"), [("FOO", "bar")])

    def test_multi_token_line_appends_to_value(self):
        # A token without '=' folds into the preceding value (space-joined).
        self.assertEqual(_parse_env_vars("FOO=bar baz"), [("FOO", "bar baz")])

    def test_multiple_pairs_on_one_line(self):
        self.assertEqual(
            _parse_env_vars("FOO=bar BAZ=qux"),
            [("FOO", "bar"), ("BAZ", "qux")],
        )

    def test_blank_lines_and_full_line_comments_skipped(self):
        self.assertEqual(_parse_env_vars("\n\n# only a comment\n"), [])

    def test_trailing_comment_stripped_from_value(self):
        self.assertEqual(_parse_env_vars("FOO=bar # note"), [("FOO", "bar")])

    def test_hash_not_at_word_start_preserved_in_value(self):
        self.assertEqual(_parse_env_vars("FOO=bar#baz"), [("FOO", "bar#baz")])

    def test_quoted_value_with_hash_preserved(self):
        # shlex strips the quotes; the '#' inside them stays in the value.
        self.assertEqual(
            _parse_env_vars('FOO="bar # baz"'), [("FOO", "bar # baz")]
        )

    def test_mixed_comment_and_value(self):
        lines = "# header\nexport A=1 # x\nB=two # y"
        self.assertEqual(_parse_env_vars(lines), [("A", "1"), ("B", "two")])

    def test_token_without_equals_first_raises(self):
        with self.assertRaises(ValueError):
            _parse_env_vars("bar FOO=qux")

    def test_invalid_key_raises(self):
        with self.assertRaises(ValueError):
            _parse_env_vars("1FOO=bar")

    def test_unparseable_quotes_raise(self):
        with self.assertRaises(ValueError):
            _parse_env_vars('FOO="unterminated')


if __name__ == "__main__":
    unittest.main()
