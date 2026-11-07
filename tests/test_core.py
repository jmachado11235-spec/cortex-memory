"""Minimal test suite for Cortex core functionality."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure cortex package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CORTEX_VAULT"] = str(Path(__file__).resolve().parent.parent / "examples" / "vault")


class TestScoring(unittest.TestCase):
    """Test the scoring algorithm."""

    def setUp(self):
        from cortex.smart_loader import score_atom
        self.score = score_atom

    def test_name_match_scores_highest(self):
        atom = {"name": "Deploy freeze", "tags": [], "project": "ops",
                "description": "", "path": "atoms/deploy.md", "status": "active",
                "updated": "2020-01-01"}
        score = self.score(atom, ["deploy"])
        self.assertGreater(score, 0)

    def test_no_match_scores_zero(self):
        atom = {"name": "Unrelated atom", "tags": ["other"], "project": "misc",
                "description": "nothing here", "path": "atoms/other.md",
                "status": "active", "updated": "2020-01-01"}
        score = self.score(atom, ["deploy"])
        self.assertEqual(score, 0.0)

    def test_multi_keyword_bonus(self):
        atom = {"name": "Deploy risk analysis", "tags": ["deploy", "risk"],
                "project": "ops", "description": "", "path": "atoms/deploy.md",
                "status": "active", "updated": "2020-01-01"}
        score_multi = self.score(atom, ["deploy", "risk"])
        score_single = self.score(atom, ["deploy"])
        # Multi-keyword with bonus should be more than double single
        self.assertGreater(score_multi, score_single * 2)

    def test_archived_penalty(self):
        base = {"name": "Deploy freeze", "tags": ["deploy"], "project": "ops",
                "description": "", "path": "atoms/deploy.md", "updated": "2020-01-01"}
        active = {**base, "status": "active"}
        archived = {**base, "status": "archived"}
        self.assertGreater(self.score(active, ["deploy"]),
                           self.score(archived, ["deploy"]))

    def test_project_match_in_multi_keyword(self):
        atom = {"name": "Rate limit finding", "tags": ["api"],
                "project": "ops", "description": "", "path": "insights/rate.md",
                "status": "active", "updated": "2020-01-01"}
        # "ops" matches only via project, "api" via tag
        score = self.score(atom, ["ops", "api"])
        # Should get multi-keyword bonus since both match
        self.assertGreater(score, 0)


class TestTemporalLayer(unittest.TestCase):
    """Test temporal layer classification."""

    def test_hot_layer(self):
        from cortex.smart_loader import temporal_layer
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(temporal_layer(today), "hot")

    def test_cold_layer(self):
        from cortex.smart_loader import temporal_layer
        self.assertEqual(temporal_layer("2020-01-01"), "cold")

    def test_invalid_date(self):
        from cortex.smart_loader import temporal_layer
        self.assertEqual(temporal_layer("not-a-date"), "cold")


class TestFrontmatterParsing(unittest.TestCase):
    """Test frontmatter parsing."""

    def test_parse_valid_frontmatter(self):
        from cortex.index_builder import parse_frontmatter
        text = "---\nname: Test\ntype: rule\ntags: [a, b, c]\n---\nBody"
        fm = parse_frontmatter(text)
        self.assertEqual(fm["name"], "Test")
        self.assertEqual(fm["type"], "rule")
        self.assertEqual(fm["tags"], ["a", "b", "c"])

    def test_parse_no_frontmatter(self):
        from cortex.index_builder import parse_frontmatter
        fm = parse_frontmatter("Just a regular markdown file")
        self.assertEqual(fm, {})


class TestMarkerUpdate(unittest.TestCase):
    """Test marker-based file updates."""

    def test_update_replaces_marker_content(self):
        from cortex.moc_refresher import update_file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Before\n<!-- AUTO:test -->\nold content\n<!-- /AUTO:test -->\nAfter\n")
            f.flush()
            path = Path(f.name)

        try:
            result = update_file(path, {"test": "new content"})
            self.assertTrue(result.changed)
            self.assertIn("test", result.markers_found)
            text = path.read_text()
            self.assertIn("new content", text)
            self.assertNotIn("old content", text)
            self.assertIn("Before", text)
            self.assertIn("After", text)
        finally:
            path.unlink()

    def test_missing_marker_reported(self):
        from cortex.moc_refresher import update_file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("No markers here\n")
            f.flush()
            path = Path(f.name)

        try:
            result = update_file(path, {"missing": "content"})
            self.assertFalse(result.changed)
            self.assertIn("missing", result.markers_missing)
        finally:
            path.unlink()


class TestConfigFallbackParser(unittest.TestCase):
    """Test the TOML fallback parser handles edge cases."""

    def test_inline_comments_stripped(self):
        from cortex.config import _parse_toml
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write("[layers]\nhot = 2  # days\nwarm = 7  # days\n")
            f.flush()
            path = Path(f.name)

        try:
            config = _parse_toml(path)
            self.assertEqual(config["layers"]["hot"], 2)
            self.assertEqual(config["layers"]["warm"], 7)
        finally:
            path.unlink()

    def test_negative_numbers(self):
        from cortex.config import _parse_toml
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write("[test]\nval = -5\n")
            f.flush()
            path = Path(f.name)

        try:
            config = _parse_toml(path)
            self.assertEqual(config["test"]["val"], -5)
        finally:
            path.unlink()


class TestAtomProjectValidation(unittest.TestCase):
    """Project routing must be explicit and personal must be deliberate."""

    def test_unknown_project_rejected(self):
        from unittest.mock import patch
        from cortex.atom_writer import validate_project
        with patch("cortex.atom_writer.get_valid_projects", return_value=["d2g", "personal"]):
            with self.assertRaises(ValueError):
                validate_project("made-up")

    def test_personal_requires_confirmation(self):
        from unittest.mock import patch
        from cortex.atom_writer import validate_project
        with patch("cortex.atom_writer.get_valid_projects", return_value=["d2g", "personal"]):
            with self.assertRaises(ValueError):
                validate_project("personal")
            validate_project("personal", allow_personal=True)

    def test_named_project_accepted(self):
        from unittest.mock import patch
        from cortex.atom_writer import validate_project
        with patch("cortex.atom_writer.get_valid_projects", return_value=["d2g", "personal"]):
            validate_project("d2g")


class TestExampleVault(unittest.TestCase):
    """Test that the example vault works end-to-end."""

    def test_index_builder_runs(self):
        from cortex.index_builder import scan_vault, build_indexes
        atoms = scan_vault()
        self.assertGreater(len(atoms), 0)
        indexes = build_indexes(atoms)
        self.assertIn("by_project", indexes)
        self.assertIn("graph", indexes)

    def test_smart_loader_search(self):
        from cortex.smart_loader import load_manifest, search_keywords
        manifest = load_manifest()
        if not manifest:
            # Build indexes first
            from cortex.index_builder import scan_vault, build_indexes, write_indexes
            atoms = scan_vault()
            indexes = build_indexes(atoms)
            write_indexes(indexes, atoms)
            manifest = load_manifest()
        results = search_keywords(manifest, ["deploy"], top_n=5)
        self.assertGreater(len(results), 0)
        self.assertIn("deploy", results[0]["name"].lower())




class TestFullTextIndex(unittest.TestCase):
    """The FTS5 shard is what makes note bodies searchable at all."""

    def setUp(self):
        from cortex import fts_index
        if not fts_index.fts5_available():
            self.skipTest("sqlite3 built without FTS5")
        self.tmp = tempfile.TemporaryDirectory()
        vault = Path(self.tmp.name)
        (vault / "atoms").mkdir()
        (vault / "atoms" / "note_one.md").write_text(
            "---\nname: Loan engine rebuild\ntype: event\nproject: demo\n"
            "status: active\ntags: [engine]\nupdated: 2020-01-01\n"
            "description: Rebuilt the engine\n---\n"
            "The tabla de amortizacion template is copied per borrower.\n"
            "Two payments were reconciled against Klar.\n",
            encoding="utf-8")
        (vault / "atoms" / "note_two.md").write_text(
            "---\nname: Unrelated\ntype: event\nproject: demo\nstatus: active\n"
            "tags: []\nupdated: 2020-01-01\ndescription: Nothing to see\n---\n"
            "This note is about something else entirely.\n",
            encoding="utf-8")

        from cortex import index_builder
        self.patches = []
        for mod, attrs in ((fts_index, ("VAULT", "INDEX_DIR", "DB_PATH")),
                           (index_builder, ("VAULT",))):
            for attr in attrs:
                self.patches.append((mod, attr, getattr(mod, attr)))
        fts_index.VAULT = vault
        fts_index.INDEX_DIR = vault / "index"
        fts_index.DB_PATH = vault / "index" / "fts.db"
        index_builder.VAULT = vault
        self.fts = fts_index
        self.assertEqual(fts_index.build(), 2)

    def tearDown(self):
        for mod, attr, val in self.patches:
            setattr(mod, attr, val)
        self.tmp.cleanup()

    def test_strip_frontmatter(self):
        body = self.fts.strip_frontmatter("---\nname: X\n---\nReal body\n")
        self.assertEqual(body.strip(), "Real body")
        self.assertEqual(self.fts.strip_frontmatter("No frontmatter"), "No frontmatter")

    def test_body_only_term_is_found(self):
        """'borrower' appears in no frontmatter field -- the whole point."""
        hits = self.fts.search(["borrower"])
        self.assertEqual(len(hits), 1)
        path = next(iter(hits))
        self.assertIn("note_one", path)
        self.assertIn("borrower", hits[path]["body_hits"])

    def test_accents_are_folded(self):
        """Query without accents must reach text written with them, and back."""
        self.assertTrue(self.fts.search(["amortizacion"]))
        self.assertTrue(self.fts.search(["amortizaci\u00f3n"]))

    def test_stemming_matches_plural(self):
        self.assertTrue(self.fts.search(["payment"]))
        self.assertTrue(self.fts.search(["payments"]))

    def test_no_match_returns_nothing(self):
        self.assertEqual(self.fts.search(["quetzalcoatl"]), {})

    def test_missing_index_degrades_quietly(self):
        self.fts.DB_PATH.unlink()
        self.assertFalse(self.fts.available())
        self.assertEqual(self.fts.search(["borrower"]), {})


class TestBodyScoring(unittest.TestCase):
    """Body hits add score without displacing curated frontmatter matches."""

    def setUp(self):
        from cortex.smart_loader import score_atom
        self.score = score_atom
        self.atom = {"name": "Unrelated atom", "tags": [], "project": "demo",
                     "description": "nothing here", "path": "atoms/x.md",
                     "status": "active", "updated": "2020-01-01"}

    def test_body_hit_lifts_an_otherwise_invisible_atom(self):
        self.assertEqual(self.score(self.atom, ["borrower"]), 0.0)
        hit = {"body_hits": {"borrower"}, "norm": 1.0}
        self.assertGreater(self.score(self.atom, ["borrower"], hit), 0.0)

    def test_description_still_outranks_body(self):
        desc_atom = {**self.atom, "description": "all about borrowers"}
        body_only = self.score(self.atom, ["borrower"],
                               {"body_hits": {"borrower"}, "norm": 0.0})
        in_desc = self.score(desc_atom, ["borrower"])
        self.assertGreater(in_desc, body_only)

    def test_scoring_without_fts_hit_is_unchanged(self):
        atom = {"name": "Deploy freeze", "tags": ["deploy"], "project": "ops",
                "description": "", "path": "atoms/deploy.md",
                "status": "active", "updated": "2020-01-01"}
        self.assertEqual(self.score(atom, ["deploy"]),
                         self.score(atom, ["deploy"], None))


if __name__ == "__main__":
    unittest.main()
