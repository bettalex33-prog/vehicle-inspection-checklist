#!/usr/bin/env python3
"""Unit tests for Vehicle Maintenance Checklist CLI."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Import from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from checklist import (create_default_data, load_data, save_data,
                       set_complete, reset_items, calculate_progress)


class TestChecklist(unittest.TestCase):
    """Test suite for checklist functions."""

    def setUp(self):
        """Create temporary directory and data path for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temp_dir.name) / "checklist_data.json"

    def tearDown(self):
        """Clean up temporary files after tests complete."""
        super().tearDown()
        self.temp_dir.cleanup()

    # ==========================================================================
    # TEST: create_default_data creates correct structure with all items False
    # ==========================================================================

    def test_create_default_data_all_items_false(self):
        """Create default data structure and verify all items start at False."""
        data = create_default_data()

        self.assertEqual(len(data), 7)
        for item_id in ["oil", "coolant", "tires", "brakes", "lights",
                        "battery", "leaks"]:
            self.assertIn(item_id, data)
            self.assertFalse(data[item_id]["complete"])
            self.assertIn("name", data[item_id])

    # ==========================================================================
    # TEST: load_data creates file if missing, returns default on empty file
    # ==========================================================================

    def test_load_data_creates_default_if_missing(self):
        """Verify that load_data creates the file with defaults when missing."""
        # Remove any existing test file
        self.data_path.unlink(missing_ok=True)

        result = load_data(self.data_path)

        self.assertEqual(len(result), 7)
        for item_id in ["oil", "coolant", "tires", "brakes", "lights",
                        "battery", "leaks"]:
            self.assertIn(item_id, result)
            self.assertFalse(result[item_id]["complete"])


    # ==========================================================================
    # TEST: save_data writes valid JSON to file and can be loaded back
    # ==========================================================================

    def test_save_data_creates_valid_json(self):
        """Verify that save_data creates a valid JSON file."""
        data = create_default_data()
        set_complete(self.data_path, "oil")  # Set one item to True

        saved_data = {"modified": True}
        save_data(self.data_path, saved_data)

        loaded = load_data(self.data_path)
        self.assertTrue(loaded["modified"])


    # ==========================================================================
    # TEST: set_complete idempotently sets item to True on multiple calls
    # ==========================================================================

    def test_set_complete_idempotent_calls(self):
        """Verify that calling complete twice sets the same item correctly."""
        initial = load_data(self.data_path)

        first_call = set_complete(self.data_path, "tires")
        second_call = set_complete(self.data_path, "tires")

        final = load_data(self.data_path)

        # Both calls should result in same state
        self.assertEqual(initial["tires"]["complete"], False)
        self.assertEqual(final["tires"]["complete"], True)


    # ==========================================================================
    # TEST: set_complete handles invalid item ID with ValueError
    # ==========================================================================

    def test_set_complete_invalid_item_id(self):
        """Verify that invalid item IDs raise ValueError from set_complete."""
        initial = load_data(self.data_path)

        # Test that set_complete raises ValueError, not SystemExit
        with self.assertRaises(ValueError):
            set_complete(self.data_path, "invalid_item_xyz")

        # Invalid call shouldn't change state
        final = load_data(self.data_path)
        self.assertFalse(final["oil"]["complete"])


    # ==========================================================================
    # TEST: reset_items sets all items to False
    # ==========================================================================

    def test_reset_items_clears_all(self):
        """Verify that reset_items sets all items to not complete."""
        initial = load_data(self.data_path)

        # Manually set one item to True to test reset
        initial["tires"]["complete"] = True

        reset_items(self.data_path)
        final = load_data(self.data_path)

        for item_id in ["oil", "coolant", "tires", "brakes", "lights",
                        "battery", "leaks"]:
            self.assertFalse(final[item_id]["complete"])


    # ==========================================================================
    # TEST: progress calculation is accurate with various completion states
    # ==========================================================================

    def test_progress_calculation_various_states(self):
        """Test progress percentage for different completion counts."""
        items_order = ["oil", "coolant", "tires", "brakes", "lights",
                        "battery", "leaks"]

        tests = [
            (0, 0),
            (1, 14),
            (2, 29),
            (3, 43),
            (4, 57),
            (5, 71),
            (6, 86),
            (7, 100),
        ]

        for completed_count, expected_percentage in tests:
            # Create fresh data and set exactly the right number of items
            test_data = create_default_data()
            if completed_count == 0:
                percentage, _, _ = calculate_progress(test_data)
                self.assertEqual(percentage, expected_percentage)
            else:
                # Manually set all but don't save to JSON file
                for i in range(completed_count):
                    test_data[items_order[i]]["complete"] = True

                percentage, _, _ = calculate_progress(test_data)
                self.assertAlmostEqual(percentage, expected_percentage, delta=1)


    # ==========================================================================
    # TEST: progress bar chart formatting (7-cell format)
    # ==========================================================================

    def test_progress_bar_formatting(self):
        """Test that progress bar has correct width of 7 cells."""
        data = create_default_data()

        percentage, bar, progress_str = calculate_progress(data)

        # Bar should be exactly 7 characters
        self.assertEqual(len(bar), 7)
        # Should show 0/7 with 0% when nothing is complete
        self.assertEqual(progress_str, "0/7 (0%)")

        # Test with one item complete (should match example format)
        test_data = create_default_data()
        test_data["oil"]["complete"] = True

        pct, bar2, prog_str2 = calculate_progress(test_data)
        # Should show 1/7 with 14% - matching the example "Progress: [█░░░░░] 1/7 (14%)"
        self.assertEqual(len(bar2), 7)
        self.assertEqual(prog_str2, "1/7 (14%)")


    # ==========================================================================
    # TEST: persistence survives file reload after modification
    # ==========================================================================

    def test_persistence_after_modify_reload(self):
        """Verify data persists across save and load operations."""
        data = create_default_data()

        # Set multiple items to complete using set_complete (triggers save)
        for item_id in ["oil", "brakes"]:
            set_complete(self.data_path, item_id)

        # Load it back (should preserve state from the saved file)
        after_save = load_data(self.data_path)

        self.assertTrue(after_save["oil"]["complete"])
        self.assertTrue(after_save["brakes"]["complete"])


    # ==========================================================================
    # TEST: load_data handles non-existent file gracefully
    # ==========================================================================

    def test_load_data_handles_nonexistent_file(self):
        """Verify that load_data handles missing files without crashing."""
        # Remove the file if it exists
        self.data_path.unlink(missing_ok=True)

        # Should create default data without error
        result = load_data(self.data_path)
        self.assertEqual(len(result), 7)


if __name__ == "__main__":
    unittest.main()
