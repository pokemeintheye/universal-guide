"""Regression tests for release years embedded in movie EPG titles."""
import unittest
from app.main import build_media_index,match,movie_match_key

def movie(title,year,key):
    return {"id":"movie:"+str(key),"kind":"movie","title":title,
            "normalized":title.casefold(),"year":year,
            "show_title":"","season":None,"episode":None}

def airing(title,year=None,kind="movie"):
    return {"title":title,"year":year,"program_kind":kind,
            "subtitle":"","season":None,"episode":None}

class MovieYearTitleTests(unittest.TestCase):
    def setUp(self):
        self.index=build_media_index([
            movie("Star Wars: Episode VI - Return of the Jedi",1983,501),
            movie("The Empire Strikes Back",1980,502),
            movie("Return of the Jedi",2019,503),
            movie("Back to the Future",1985,504)])

    def test_guide_title_with_trailing_release_year(self):
        result=match(airing("Star Wars: Return of the Jedi (1983)"),self.index)
        self.assertIsNotNone(result)
        self.assertEqual(result["rating_key"],"501")

    def test_episode_vi_title_variant(self):
        result=match(airing("Star Wars: Episode VI - Return of the Jedi (1983)"),self.index)
        self.assertEqual(result["rating_key"],"501")

    def test_release_year_can_appear_in_metadata_instead_of_title(self):
        self.assertEqual(match(airing("Star Wars: Return of the Jedi",1983),self.index)["rating_key"],"501")

    def test_regular_movie_year_suffix(self):
        self.assertEqual(match(airing("Back to the Future (1985)"),self.index)["rating_key"],"504")

    def test_conflicting_year_refused(self):
        self.assertIsNone(match(airing("Star Wars: Return of the Jedi (1983)",2019),self.index))

    def test_known_alias_requires_year_when_multiple_editions_exist(self):
        self.assertIsNone(match(airing("Return of the Jedi"),self.index))

    def test_does_not_match_unrelated_star_wars_movies(self):
        self.assertIsNone(match(airing("Star Wars: The Empire Strikes Back (1980)"),self.index))
        self.assertIsNone(match(airing("Star Wars: A New Hope (1977)"),self.index))

    def test_non_movie_not_matched_to_movies(self):
        self.assertIsNone(match(airing("Star Wars: Return of the Jedi (1983)",kind="series"),self.index))

    def test_only_final_parenthesized_year_is_removed(self):
        self.assertEqual(movie_match_key("Back to the Future (1985)"),("back to the future",1985))
        self.assertEqual(movie_match_key("The Movie (Director's Cut)"),("the movie director s cut",None))

if __name__=="__main__":
    unittest.main()
