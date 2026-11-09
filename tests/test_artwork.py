"""No-key external artwork fallback must be exact, safe and optional."""
import unittest
from unittest.mock import patch
from app import artwork

SHOW={"id":42,"name":"Harbor Heights","url":"https://www.tvmaze.com/shows/42/harbor-heights",
      "image":{"original":"https://static.tvmaze.com/uploads/images/original/3/4.jpg"}}
EPISODE={"image":{"original":"https://static.tvmaze.com/uploads/images/original/4/5.jpg"}}

class ArtworkTests(unittest.TestCase):
    def test_exact_show_and_episode_artwork(self):
        with patch.object(artwork,"_get",side_effect=[
            [{"show":SHOW}],EPISODE
        ]) as get:
            image=artwork.find_tvmaze_artwork("Harbor Heights",2,5)
        self.assertEqual(image["match"],"episode")
        self.assertEqual(image["image_url"],EPISODE["image"]["original"])
        self.assertEqual(get.call_args.args[0],"/shows/42/episodebynumber")

    def test_series_fallback_when_episode_has_no_image(self):
        with patch.object(artwork,"_get",side_effect=[[{"show":SHOW}],{"image":None}]):
            image=artwork.find_tvmaze_artwork("Harbor Heights",1,3)
        self.assertEqual(image["match"],"series")

    def test_no_fuzzy_mismatch(self):
        with patch.object(artwork,"_get",return_value=[
            {"show":dict(SHOW,name="Harbor Other")}
        ]):
            self.assertIsNone(artwork.find_tvmaze_artwork("Harbor Heights"))

    def test_reject_untrusted_image_hosts(self):
        self.assertIsNone(artwork.safe_image_url("https://example.com/uploads/images/original/3/4.jpg"))
        self.assertIsNone(artwork.safe_image_url("http://static.tvmaze.com/uploads/images/original/3/4.jpg"))
        self.assertIsNone(artwork.safe_image_url("https://static.tvmaze.com.evil.net/uploads/images/original/3/4.jpg"))
        self.assertIsNotNone(artwork.safe_image_url("https://static.tvmaze.com/uploads/images/medium_landscape/1/2.jpg"))

if __name__=="__main__":
    unittest.main()
