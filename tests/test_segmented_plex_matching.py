"""Safely match a half-hour cartoon EPG slot to separately stored Plex episodes."""
import unittest
from app.main import build_media_index, match

def episode(title,number,key,show="SpongeBob SquarePants",season=1):
    return {"id":"episode:"+str(key),"kind":"episode","title":title,
            "normalized":title.casefold(),"show_title":show,
            "season":season,"episode":number,"year":1999}

def program(subtitle,show="SpongeBob SquarePants",season=None,number=None):
    return {"title":show,"program_kind":"series","subtitle":subtitle,
            "season":season,"episode":number,"year":1999}

class SegmentedEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.index=build_media_index([
            episode("Help Wanted",1,101),
            episode("Reef Blower",2,102),
            episode("Tea at the Treedome",3,103),
            episode("Pizza Delivery",5,105),
            episode("Home Sweet Pineapple",6,106),
            episode("Help Wanted",1,501,"Another Show")
        ])

    def test_two_segments(self):
        result=match(program("Help Wanted / Reef Blower"),self.index)
        self.assertIsNotNone(result)
        self.assertEqual([p["rating_key"] for p in result["parts"]],["101","102"])
        self.assertEqual(result["rating_key"],"101")

    def test_three_segments(self):
        result=match(program("Help Wanted; Reef Blower; Tea at the Treedome"),self.index)
        self.assertEqual(len(result["parts"]),3)

    def test_ampersand_combination(self):
        result=match(program("Pizza Delivery & Home Sweet Pineapple"),self.index)
        self.assertEqual([p["rating_key"] for p in result["parts"]],["105","106"])

    def test_no_cross_show_or_partial_match(self):
        self.assertIsNone(match(program("Help Wanted / Missing Episode"),self.index))
        self.assertIsNone(match(program("Help Wanted / Reef Blower",show="Another Show"),self.index))
        self.assertIsNone(match(program("Unknown Episode / Reef Blower"),self.index))

    def test_ambiguous_title_fails_closed(self):
        idx=build_media_index([episode("Help Wanted",1,101),
                               episode("Help Wanted",50,151),
                               episode("Reef Blower",2,102)])
        self.assertIsNone(match(program("Help Wanted / Reef Blower"),idx))

    def test_single_matching_episode_preserved(self):
        result=match(program("Help Wanted",season=1,number=1),self.index)
        self.assertEqual(result["rating_key"],"101")
        self.assertNotIn("parts",result)

    def test_bad_delimiters_do_not_guess(self):
        self.assertIsNone(match(program("Help Wanted/Reef Blower"),self.index))
        self.assertIsNone(match(program("Help Wanted / Reef Blower / Missing"),self.index))

if __name__=="__main__":
    unittest.main()
