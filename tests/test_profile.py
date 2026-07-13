from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.profile import ProfileInputError, complete_onboarding, update_profile


class ProfileValidationTests(unittest.TestCase):
    @patch("backend.profile._select_profile")
    @patch("backend.profile.get_supabase_client")
    def test_update_normalizes_all_editable_fields(self, get_client, select_profile) -> None:
        query = MagicMock()
        query.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])
        get_client.return_value.table.return_value = query
        select_profile.return_value = {"username": "new_name"}
        result = update_profile(
            "internal-user-id",
            {
                "username": " New_Name ",
                "display_name": "  New Name  ",
                "pace_gender": "female",
                "goals": ["Build strength", "build strength", "Mobility"],
                "workout_experience": "intermediate",
                "cuisine_preferences": ["Indian", "Japanese"],
                "dietary_preferences": ["High protein"],
                "preferred_units": "metric",
                "bio": "  Training consistently.  ",
            },
        )
        values = query.update.call_args.args[0]
        self.assertEqual(values["username"], "new_name")
        self.assertEqual(values["display_name"], "New Name")
        self.assertEqual(values["pace_gender"], "female")
        self.assertEqual(values["goals"], ["Build strength", "Mobility"])
        self.assertEqual(result["username"], "new_name")

    def test_update_rejects_unknown_and_invalid_values(self) -> None:
        with self.assertRaises(ProfileInputError):
            update_profile("internal-user-id", {"id": "cannot-change"})
        with self.assertRaises(ProfileInputError):
            update_profile("internal-user-id", {"workout_experience": "expert"})
        with self.assertRaises(ProfileInputError):
            update_profile("internal-user-id", {"goals": "not-a-list"})
        with self.assertRaises(ProfileInputError):
            update_profile("internal-user-id", {"onboarding_completed": True})

    @patch("backend.profile._select_profile")
    @patch("backend.profile.get_supabase_client")
    def test_complete_onboarding_requires_pace_and_sets_internal_flag(
        self, get_client, select_profile
    ) -> None:
        query = MagicMock()
        query.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])
        get_client.return_value.table.return_value = query
        select_profile.return_value = {"onboarding_completed": True, "pace_gender": "male"}

        result = complete_onboarding("internal-user-id", {"pace_gender": "male"})
        values = query.update.call_args.args[0]
        self.assertTrue(values["onboarding_completed"])
        self.assertEqual(values["pace_gender"], "male")
        self.assertTrue(result["onboarding_completed"])

        with self.assertRaises(ProfileInputError):
            complete_onboarding("internal-user-id", {"display_name": "No avatar"})


if __name__ == "__main__":
    unittest.main()
