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

    def test_broadcast_flags_are_removed_for_artwork_only(self):
        for variant in ("Harbor Heights [NEW]","[NEW] Harbor Heights",
                        "Harbor Heights (NEW)","Harbor Heights [NEW] [LIVE]",
                        "Harbor Heights [Series Premiere]"):
            with self.subTest(variant=variant):
                self.assertEqual(artwork.clean_artwork_title(variant),"Harbor Heights")
                self.assertEqual(artwork.normalize_title(variant),
                                 artwork.normalize_title("Harbor Heights"))
                with patch.object(artwork,"_get",return_value=[{"show":SHOW}]) as get:
                    result=artwork.find_tvmaze_artwork(variant)
                self.assertEqual(result["match"],"series")
                get.assert_called_once_with("/search/shows",{"q":"Harbor Heights"})

    def test_real_title_parts_remain_untouched(self):
        for title in ("New Girl","The Office (US)","[NEW]"):
            self.assertEqual(artwork.clean_artwork_title(title),title)

    def test_no_fuzzy_mismatch(self):
        with patch.object(artwork,"_get",return_value=[
            {"show":dict(SHOW,name="Harbor Other")}
        ]):
            self.assertIsNone(artwork.find_tvmaze_artwork("Harbor Heights"))

    def test_tmdb_tv_exact_title_poster_and_broadcast_tags(self):
        show={"id":124,"name":"Family Feud","original_name":"Family Feud",
              "poster_path":"/family_feud.jpg"}
        response=unittest.mock.Mock()
        response.json.return_value={"results":[show]}
        with patch.object(artwork.requests,"get",return_value=response) as get:
            result=artwork.find_tmdb_tv_artwork("Family Feud [NEW]","test-key")
        self.assertEqual(result["source"],"TMDB")
        self.assertEqual(result["match"],"series")
        self.assertEqual(result["image_url"],
                         "https://image.tmdb.org/t/p/w342/family_feud.jpg")
        self.assertEqual(result["credit_url"],"https://www.themoviedb.org/tv/124")
        self.assertEqual(get.call_args.args[0],artwork.TMDB_TV_API)
        self.assertEqual(get.call_args.kwargs["params"]["query"],"Family Feud")
        self.assertEqual(get.call_args.kwargs["params"]["api_key"],"test-key")

    def test_tmdb_tv_skips_ambiguous_or_unrelated_shows(self):
        exact={"id":124,"name":"Family Feud","poster_path":"/family_feud.jpg"}
        near={"id":125,"name":"Family Feud UK","poster_path":"/other.jpg"}
        response=unittest.mock.Mock()
        with patch.object(artwork.requests,"get",return_value=response):
            response.json.return_value={"results":[near]}
            self.assertIsNone(artwork.find_tmdb_tv_artwork("Family Feud","key"))
            response.json.return_value={"results":[exact,dict(exact,id=126)]}
            self.assertIsNone(artwork.find_tmdb_tv_artwork("Family Feud","key"))
            response.json.return_value={"results":[near,exact]}
            self.assertEqual(artwork.find_tmdb_tv_artwork("Family Feud","key")["match"],"series")
            response.json.return_value={"results":[dict(exact,poster_path="//bad.jpg")]}
            self.assertIsNone(artwork.find_tmdb_tv_artwork("Family Feud","key"))
        self.assertIsNone(artwork.find_tmdb_tv_artwork("Family Feud",None))

    def test_tmdb_movie_poster_exact_title_and_year(self):
        movie={"id":77,"title":"Cast Away","original_title":"Cast Away",
               "release_date":"2000-12-22","poster_path":"/poster_77.jpg"}
        response=unittest.mock.Mock()
        response.json.return_value={"results":[movie]}
        with patch.object(artwork.requests,"get",return_value=response) as get:
            result=artwork.find_tmdb_movie_artwork("Cast Away [NEW]",2000,"test-key")
        self.assertEqual(result["source"],"TMDB")
        self.assertEqual(result["image_url"],
                         "https://image.tmdb.org/t/p/w342/poster_77.jpg")
        self.assertEqual(get.call_args.kwargs["params"]["year"],2000)
        self.assertEqual(get.call_args.kwargs["params"]["api_key"],"test-key")
        self.assertNotIn("test-key",str(result))

    def test_tmdb_rejects_mismatch_ambiguous_releases_and_unsafe_images(self):
        movies=[
            {"id":10,"title":"Cast Away","release_date":"1999-01-01",
             "poster_path":"/wrong.jpg"},
            {"id":11,"title":"Cast Away","release_date":"2000-01-01",
             "poster_path":"/ok.jpg"},
            {"id":12,"title":"Another Film","release_date":"2000-01-01",
             "poster_path":"/other.jpg"}]
        response=unittest.mock.Mock()
        response.json.return_value={"results":movies}
        with patch.object(artwork.requests,"get",return_value=response):
            self.assertEqual(artwork.find_tmdb_movie_artwork("Cast Away",2000,"key")["credit_url"],
                             "https://www.themoviedb.org/movie/11")
            self.assertIsNone(artwork.find_tmdb_movie_artwork("Cast Away",None,"key"))
        self.assertIsNone(artwork.find_tmdb_movie_artwork("Cast Away",2000,None))
        self.assertFalse(artwork.safe_tmdb_image_url("https://evil.com/poster.jpg"))
        self.assertTrue(artwork.safe_tmdb_image_url("https://image.tmdb.org/t/p/w342/a.jpg"))

    def test_reject_untrusted_image_hosts(self):
        self.assertIsNone(artwork.safe_image_url("https://example.com/uploads/images/original/3/4.jpg"))
        self.assertIsNone(artwork.safe_image_url("http://static.tvmaze.com/uploads/images/original/3/4.jpg"))
        self.assertIsNone(artwork.safe_image_url("https://static.tvmaze.com.evil.net/uploads/images/original/3/4.jpg"))
        self.assertIsNotNone(artwork.safe_image_url("https://static.tvmaze.com/uploads/images/medium_landscape/1/2.jpg"))

if __name__=="__main__":
    unittest.main()
