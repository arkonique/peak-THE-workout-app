import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.food_search import (
    FoodProductNotFound,
    get_food_product,
    reset_food_search_state,
)


ROOT_DIR = Path(__file__).resolve().parents[1]


class FoodProductTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_food_search_state()

    def tearDown(self) -> None:
        reset_food_search_state()

    @patch("backend.food_search._get_product_api")
    def test_exact_product_is_cached(self, get_api: Mock) -> None:
        product = {
            "code": "0038259110157",
            "product_name": "100% wheat grain thin spaghetti",
            "nutriments": {"energy-kcal_100g": 350},
        }
        get_api.return_value.product.get.return_value = product

        first = get_food_product("0038259110157")
        second = get_food_product("0038259110157")

        self.assertEqual(first["product"], product)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        get_api.return_value.product.get.assert_called_once_with("0038259110157")

    @patch("backend.food_search._get_product_api")
    def test_missing_product_has_a_specific_error(self, get_api: Mock) -> None:
        get_api.return_value.product.get.return_value = None

        with self.assertRaisesRegex(FoodProductNotFound, "No Open Food Facts product"):
            get_food_product("9999999999999")

    def test_product_code_must_be_numeric(self) -> None:
        with self.assertRaisesRegex(ValueError, "digits only"):
            get_food_product("bread")


class FoodCodeLookupPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.page = (ROOT_DIR / "frontend" / "food-code-lookup.html").read_text(encoding="utf-8")

    def test_lookup_runs_on_submit_not_typing(self) -> None:
        self.assertIn('form.addEventListener("submit"', self.page)
        self.assertNotIn('addEventListener("input"', self.page)
        self.assertIn("/api/food-product", self.page)

    def test_example_product_codes_are_available(self) -> None:
        for code in ("5020580007034", "5060108457583", "0028400718271"):
            self.assertIn(f'data-code="{code}"', self.page)

    def test_canadian_label_sections_are_present(self) -> None:
        self.assertIn("Nutrition Facts", self.page)
        self.assertIn("Valeur nutritive", self.page)
        self.assertIn("Ingredients / Ingrédients", self.page)


if __name__ == "__main__":
    unittest.main()
