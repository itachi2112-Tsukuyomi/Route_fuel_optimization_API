from unittest import mock

from django.test import SimpleTestCase, override_settings

from fuel.services import geocoding
from fuel.services.geocoding import GeocodingError, geocode, lookup_city


class GeocodeTests(SimpleTestCase):
    def test_city_state_variants(self):
        for text in ("Chicago, IL", "chicago il", "Chicago, Illinois", "Chicago, IL, USA"):
            loc = geocode(text)
            self.assertEqual(loc.source, "city")
            self.assertAlmostEqual(loc.lat, 41.85, delta=0.3)
            self.assertAlmostEqual(loc.lng, -87.68, delta=0.3)

    def test_zip_code(self):
        loc = geocode("90210")
        self.assertEqual(loc.source, "zip")
        self.assertIn("Beverly Hills", loc.label)

    def test_coordinates(self):
        loc = geocode("39.7392, -104.9903")
        self.assertEqual((loc.lat, loc.lng, loc.source), (39.7392, -104.9903, "coordinates"))

    def test_saint_and_spacing_aliases(self):
        self.assertEqual(lookup_city("St. Louis", "MO"), lookup_city("Saint Louis", "MO"))
        self.assertIsNotNone(lookup_city("De Forest", "WI"))

    def test_outside_usa_rejected(self):
        with self.assertRaisesMessage(GeocodingError, "outside the USA"):
            geocode("48.85, 2.35")

    @override_settings(FUEL_PLANNER={**geocoding.settings.FUEL_PLANNER, "NOMINATIM_ENABLED": False})
    def test_unknown_place_rejected(self):
        with self.assertRaises(GeocodingError):
            geocode("Atlantis, ZZ")

    def test_free_text_falls_back_to_nominatim(self):
        fake = mock.Mock(status_code=200)
        fake.json.return_value = [{"lat": "36.1", "lon": "-115.17", "display_name": "Las Vegas Strip"}]
        with mock.patch.object(geocoding.requests, "get", return_value=fake) as get:
            loc = geocode("The Strip")
        get.assert_called_once()
        self.assertEqual(loc.source, "nominatim")
