"""Safe episode title aliases and scheduled multipart matching."""
import unittest
from app.main import (
    build_media_index, match, assign_scheduled_episode_parts,
    numbered_episode_part, episode_title_alias,
)


def item(title, key, show="Little House on the Prairie", season=1, episode=1):
    return {"kind":"episode","id":"episode:"+str(key),"title":title,
            "show_title":show,"season":season,"episode":episode,
            "normalized":show.casefold(),"year":1975}


def program(subtitle, start="2026-11-09T15:00:00+00:00",
            stop="2026-11-09T16:00:00+00:00",
            season=None, episode=None):
    return {"title":"Little House on the Prairie","subtitle":subtitle,
            "program_kind":"series","season":season,"episode":episode,
            "start":start,"stop":stop}


class EpisodeTitleVariants(unittest.TestCase):
    def test_leading_the_on_either_side(self):
        for plex,epg in (("The Journey","Journey"),("Journey","The Journey")):
            index=build_media_index([item(plex,123)])
            result=match(program(epg),index)
            self.assertEqual(result["rating_key"],"123")

    def test_leading_the_alias_requires_unique_match(self):
        index=build_media_index([item("Journey",123),
                                 item("The Journey",124,episode=2)])
        self.assertIsNone(match(program("Journey Home"),index))
        # Exact title still wins even when the alternative alias is ambiguous.
        self.assertEqual(match(program("Journey"),index)["rating_key"],"123")

    def test_multipart_guide_title_offers_both_when_order_unknown(self):
        index=build_media_index([
            item("Journey Home (1)",123),item("Journey Home (2)",124,episode=2)])
        result=match(program("Journey Home"),index)
        self.assertEqual([p["rating_key"] for p in result["parts"]],["123","124"])

    def test_adjacent_guide_slots_assign_part_1_and_part_2(self):
        index=build_media_index([
            item("Journey Home (1)",123),item("Journey Home (2)",124,episode=2)])
        first=program("Journey Home")
        second=program("Journey Home","2026-11-09T16:00:00+00:00",
                       "2026-11-09T17:00:00+00:00")
        for p in (first,second):p["plex"]=match(p,index)
        assign_scheduled_episode_parts([first,second])
        self.assertEqual(first["plex"]["rating_key"],"123")
        self.assertEqual(second["plex"]["rating_key"],"124")
        self.assertEqual(first["plex"]["scheduled_part"],1)
        self.assertEqual(second["plex"]["scheduled_part"],2)
        self.assertNotIn("parts",first["plex"])

    def test_nonadjacent_airings_remain_selectable(self):
        index=build_media_index([
            item("Journey Home (1)",123),item("Journey Home (2)",124,episode=2)])
        first=program("Journey Home")
        second=program("Journey Home","2026-11-09T17:00:00+00:00",
                       "2026-11-09T18:00:00+00:00")
        for p in (first,second):p["plex"]=match(p,index)
        assign_scheduled_episode_parts([first,second])
        self.assertEqual(len(first["plex"]["parts"]),2)
        self.assertEqual(len(second["plex"]["parts"]),2)

    def test_numbered_parts_are_not_assigned_if_single_episode(self):
        index=build_media_index([item("Journey Home (1)",123)])
        self.assertIsNone(match(program("Journey Home"),index))

    def test_only_trailing_parenthetical_parts_removed(self):
        self.assertEqual(numbered_episode_part("Journey Home (2)"),
                         ("journey home",2))
        self.assertIsNone(numbered_episode_part("Journey Home (Finale)"))
        self.assertEqual(episode_title_alias("The Journey Home"),"journey home")


if __name__=="__main__":
    unittest.main()
