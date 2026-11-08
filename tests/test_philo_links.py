"""No external Philo network access: enforce verified handoff-only matching."""
import unittest

from app.philo_links import links_for, channel_catalog


class VerifiedPhiloHandoffs(unittest.TestCase):
    def test_office(self):
        links=links_for("Comedy Central","The Office")
        self.assertIn("show",links)
        self.assertNotIn("channel",links)
        self.assertNotIn("episode",links)

    def test_other_shows_cannot_open_office(self):
        office_id="U2hvdzo2MDg1NDg4OTk2NDg0MzkyODg"
        for title in ("The Simpsons","Office Space","The Office UK","Everybody Loves Raymond"):
            link=links_for("Comedy Central",title).get("show")
            self.assertTrue(link is None or office_id not in link["url"])

    def test_food_network_channel_only(self):
        links=links_for("Food Network","Random Program")
        self.assertIn("channel",links)
        self.assertNotIn("show",links)
        self.assertNotIn("episode",links)

    def test_pilot_exact(self):
        links=links_for("TV Land","Everybody Loves Raymond","Pilot",1,1)
        self.assertIn("episode",links)
        self.assertIn("show",links)
        self.assertEqual(links["episode"]["autoplay"],False)

    def test_episode_mismatch(self):
        for subtitle,season,episode in (("Not Pilot",1,1),("Pilot",2,1),("Pilot",1,2),
                                        ("Pilot",None,1),("Pilot",1,None)):
            self.assertNotIn("episode",links_for(
                "TV Land","Everybody Loves Raymond",subtitle,season,episode))

    def test_hgtv_stays_accessible_without_epg(self):
        channels={item["name"]:item for item in channel_catalog()}
        self.assertIn("HGTV",channels)
        self.assertEqual(channels["HGTV"]["verification"],"public-listed")
        self.assertTrue(channels["HGTV"]["url"].startswith(
            "https://www.philo.com/player/channel/"))

    def test_publicly_discovered_channel_links(self):
        for network in ("AMC","HGTV","HISTORY","Discovery Channel","TLC","TV Land"):
            link=links_for(network,"Unknown TV program").get("channel")
            self.assertIsNotNone(link,network)
            self.assertEqual(link["verification"],"public-listed")
            self.assertFalse(link["autoplay"])

    def test_large_public_channel_catalog_has_no_wrong_reuse(self):
        channels=channel_catalog()
        self.assertGreaterEqual(len(channels),75)
        self.assertEqual(len(channels),len({c["name"] for c in channels}))
        self.assertEqual(len(channels),len({c["url"] for c in channels}))
        for name in ("Animal Planet","Lifetime","Nick Jr.","MeTV","MeTV Toons",
                     "MTV2","Science Channel","Travel Channel","Paramount Network",
                     "Cooking Channel","Discovery Family","Magnolia Network",
                     "TeenNick","Smithsonian Channel","Crime + Investigation"):
            link=links_for(name,"A completely unrelated show").get("channel")
            self.assertIsNotNone(link,name)
            self.assertIn(link["verification"],("public-listed","iphone-tested"))
            self.assertTrue(link["url"].startswith(
                "https://www.philo.com/player/channel/"))
        for name in ("Food Network","A&E","Hallmark Channel","Nickelodeon"):
            self.assertEqual(links_for(name,"Any show")["channel"]["verification"],
                             "iphone-tested")
        for name in ("MTV",):
            self.assertNotIn("channel",links_for(name,"Some show"))

        self.assertNotIn("channel",links_for("The Wrong Channel","The Office"))
        self.assertNotIn("channel",links_for("Hallmark","An unknown show"))

    def test_publicly_discovered_show_links(self):
        for show in ("The Kitchen","House Hunters"):
            link=links_for("HGTV",show).get("show")
            self.assertIsNotNone(link,show)
            self.assertEqual(link["verification"],"public-listed")

    def test_user_supplied_catchy_comedy_live_handoff(self):
        links=links_for("Catchy Comedy","A random live program")
        self.assertIn("channel",links)
        self.assertEqual(links["channel"]["url"],
                         "https://www.philo.com/player/channel/Q2hhbm5lbDo2MDg1NDg4OTk2NDg0Mzk2OTU")
        self.assertEqual(links["channel"]["verification"],"public-listed")
        self.assertFalse(links["channel"]["autoplay"])
        comedy=links_for("Comedy Central","A random live program").get("channel")
        self.assertIsNotNone(comedy)
        self.assertEqual(comedy["url"],
                         "https://www.philo.com/player/channel/Q2hhbm5lbDo2MDg1NDg4OTk2NDg0Mzg0OTk")
        self.assertEqual(comedy["verification"],"public-listed")
        self.assertFalse(comedy["autoplay"])

    def test_show_fallbacks_for_unmapped_popular_channels(self):
        for channel,title in (("Comedy Central","South Park"),
                              ("Catchy Comedy","Cheers"),
                              ("Catchy Comedy","Taxi"),
                              ("Catchy Comedy","Full House"),
                              ("Catchy Comedy","Hot in Cleveland")):
            links=links_for(channel,title)
            self.assertIn("show",links)
            self.assertEqual(links["show"]["verification"],"public-listed")
            self.assertIn("channel",links)
        self.assertNotIn("show",links_for("Catchy Comedy","Unknown Show"))
        self.assertIn("channel",links_for("Comedy Central","South Park"))

    def test_exact_kitchen_episode(self):
        links=links_for("Food Network","The Kitchen","World on a Plate",41,1)
        self.assertIn("episode",links)
        self.assertNotIn("episode",links_for("Food Network","The Kitchen","World on a Plate",40,1))


if __name__=="__main__":
    unittest.main()
