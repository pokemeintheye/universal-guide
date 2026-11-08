"""No external Philo network access: enforce verified handoff-only matching."""
import unittest

from app.philo_links import links_for


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
        for subtitle,season,episode in (("Not Pilot",1,1),("Pilot",2,1),("Pilot",1,2)):
            self.assertNotIn("episode",links_for(
                "TV Land","Everybody Loves Raymond",subtitle,season,episode))


if __name__=="__main__":
    unittest.main()
