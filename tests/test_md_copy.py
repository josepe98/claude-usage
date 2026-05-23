"""Tests for per-table 'Copy as markdown' buttons in the dashboard."""
import re
import unittest

import dashboard


HTML = dashboard.HTML_TEMPLATE


class TestMarkdownHelperShape(unittest.TestCase):
    """The generic _tableToMarkdown helper exists and produces valid GFM."""

    def test_helper_defined(self):
        self.assertIn("function _tableToMarkdown(", HTML)

    def test_helper_signature(self):
        # tableId, headers, rowsData
        self.assertRegex(
            HTML,
            r"function\s+_tableToMarkdown\s*\(\s*tableId\s*,\s*headers\s*,\s*rowsData\s*\)",
        )

    def test_helper_builds_header_separator_body(self):
        # Body of helper must produce the canonical GFM shape: "| ... |\n|---|---|\n| ... |"
        m = re.search(
            r"function _tableToMarkdown\([^)]*\)\s*\{(.+?)\n\}", HTML, re.DOTALL
        )
        self.assertIsNotNone(m, "helper body not found")
        body = m.group(1)
        # Header row joined with " | " and wrapped in "| ... |"
        self.assertIn("'| '", body)
        self.assertIn("' | '", body)
        # Separator row uses '---'
        self.assertIn("'---'", body)
        # Joins each row with newline
        self.assertIn("'\\n'", body)

    def test_md_escape_present(self):
        # Pipe and newline escaping protects table integrity.
        self.assertIn("_mdEscape", HTML)


class TestPerTableButtons(unittest.TestCase):
    """Every data table should have a visible MD copy button."""

    EXPECTED_HANDLERS = [
        "copyModelMD",
        "copySessionsMD",
        "copyProjectsMD",
        "copyProjectBranchMD",
    ]

    def test_md_button_class_present(self):
        self.assertIn("md-btn", HTML)

    def test_handler_per_table_defined(self):
        for fn in self.EXPECTED_HANDLERS:
            with self.subTest(handler=fn):
                self.assertIn(f"function {fn}(", HTML)

    def test_handler_wired_to_button(self):
        for fn in self.EXPECTED_HANDLERS:
            with self.subTest(handler=fn):
                self.assertIn(f'onclick="{fn}()"', HTML)

    def test_clipboard_icon_present(self):
        # The clipboard glyph (U+1F4CB) appears on each MD button label.
        self.assertEqual(HTML.count("&#x1f4cb; MD"), len(self.EXPECTED_HANDLERS))


class TestClipboardAndFallback(unittest.TestCase):
    """Primary path uses navigator.clipboard; fallback path uses textarea + execCommand."""

    def test_uses_navigator_clipboard(self):
        self.assertIn("navigator.clipboard", HTML)
        self.assertIn("writeText", HTML)

    def test_fallback_helper_defined(self):
        self.assertIn("function _fallbackCopy(", HTML)

    def test_fallback_uses_textarea_and_execCommand(self):
        m = re.search(r"function _fallbackCopy\([^)]*\)\s*\{(.+?)\n\}", HTML, re.DOTALL)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("createElement('textarea')", body)
        self.assertIn("execCommand('copy')", body)


class TestToastNotification(unittest.TestCase):
    """A short-lived toast confirms the copy action."""

    def test_toast_element_present(self):
        self.assertIn('id="md-toast"', HTML)
        self.assertIn("Copied as markdown!", HTML)

    def test_toast_css_present(self):
        self.assertIn(".md-toast", HTML)
        self.assertIn(".md-toast.show", HTML)

    def test_toast_auto_hides_after_2s(self):
        # The toast should be removed after 2000ms.
        self.assertRegex(HTML, r"setTimeout\([^,]+,\s*2000\s*\)")

    def test_toast_helper_defined(self):
        self.assertIn("function _showToast(", HTML)


if __name__ == "__main__":
    unittest.main()
