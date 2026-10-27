"""Structural smoke test for the bundled VS Code extension.

Keeps future contributors honest: any rename / move under `vscode-extension/`
that breaks the install instructions will fail this test, without needing
Node or vsce locally.
"""
import json
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "vscode-extension"


class TestVscodeExtensionLayout(unittest.TestCase):
    def test_required_files_exist(self):
        required = [
            "package.json",
            "tsconfig.json",
            "README.md",
            ".vscodeignore",
            "src/extension.ts",
            "src/pricing.ts",
            "src/__tests__/extension.test.ts",
        ]
        missing = [p for p in required if not (EXT / p).exists()]
        self.assertEqual(missing, [], f"missing files: {missing}")

    def test_package_json_is_valid_and_complete(self):
        pkg_path = EXT / "package.json"
        self.assertTrue(pkg_path.exists(), "vscode-extension/package.json missing")
        with pkg_path.open("r", encoding="utf-8") as f:
            pkg = json.load(f)

        # Top-level required fields (per task spec).
        self.assertEqual(pkg.get("displayName"), "Claude Usage")
        self.assertIn("name", pkg)
        self.assertIn("main", pkg)

        # engines.vscode must be ^1.75 per spec.
        engines = pkg.get("engines") or {}
        self.assertEqual(engines.get("vscode"), "^1.75.0")

        # activationEvents must contain onStartupFinished.
        self.assertIn("onStartupFinished", pkg.get("activationEvents") or [])

        # The "build" script must run tsc.
        scripts = pkg.get("scripts") or {}
        self.assertIn("build", scripts)
        self.assertIn("tsc", scripts["build"])

        # Settings keys the README and extension code rely on.
        cfg = (
            pkg.get("contributes", {})
            .get("configuration", {})
            .get("properties", {})
        )
        self.assertIn("claudeUsage.dashboardUrl", cfg)
        self.assertIn("claudeUsage.refreshSeconds", cfg)
        self.assertEqual(cfg["claudeUsage.dashboardUrl"].get("default"), "http://localhost:8080")
        self.assertEqual(cfg["claudeUsage.refreshSeconds"].get("default"), 30)

    def test_tsconfig_is_valid_json(self):
        # tsconfig allows comments in TS tooling, but ours is plain JSON;
        # if a contributor introduces comments they should also update this
        # test (and the build pipeline) intentionally.
        with (EXT / "tsconfig.json").open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn("compilerOptions", cfg)
        self.assertEqual(cfg["compilerOptions"].get("outDir"), "out")


if __name__ == "__main__":
    unittest.main()
