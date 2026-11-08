"""HGTV fallback is limited to a confirmed US station and degrades safely."""
import gzip
import unittest
from unittest.mock import patch
from app.hgtv_fallback import _channel_score, fetch_hgtv


class Response:
    def __init__(self,body):self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def raise_for_status(self):pass
    def iter_content(self,chunk_size=65536):yield self.body


def date_convert(raw):
    from datetime import datetime,timezone
    return datetime.strptime(raw[:14],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()


class HgtvTests(unittest.TestCase):
    def test_us_east_only(self):
        self.assertEqual(_channel_score(["HGTV USA - Eastern Feed"],"x.us2"),100)
        self.assertEqual(_channel_score(["HGTV - Pacific Feed"],"x.us2"),0)
        self.assertEqual(_channel_score(["HGTV Canada"],"x.ca2"),0)
        self.assertEqual(_channel_score(["Food Network"],"x.us2"),0)

    def test_parse_valid_channel(self):
        from datetime import datetime,timedelta,timezone
        start=(datetime.now(timezone.utc)+timedelta(minutes=10)).strftime("%Y%m%d%H%M%S")
        stop=(datetime.now(timezone.utc)+timedelta(minutes=70)).strftime("%Y%m%d%H%M%S")
        xml=('<tv><channel id="HGTV.USA.-.Eastern.Feed.us2">'
             '<display-name>HGTV USA - Eastern Feed</display-name></channel>'
             '<programme channel="HGTV.USA.-.Eastern.Feed.us2" start="'+start+
             '" stop="'+stop+'"><title>House Hunters</title>'
             '<sub-title>Moving Day</sub-title></programme></tv>').encode()
        with patch("app.hgtv_fallback.requests.get",return_value=Response(gzip.compress(xml))):
            rows,status=fetch_hgtv(date_convert,lambda node:"series",lambda node:(None,None))
        self.assertEqual(status["programs"],1)
        self.assertEqual(rows[0][0],"HGTV")
        self.assertEqual(rows[0][3],"House Hunters")

    def test_missing_channel_does_not_generate_schedule(self):
        body=gzip.compress(b'<tv><channel id="x"><display-name>Food Network</display-name></channel></tv>')
        with patch("app.hgtv_fallback.requests.get",return_value=Response(body)):
            rows,status=fetch_hgtv(date_convert,lambda node:"unknown",lambda node:(None,None))
        self.assertEqual(rows,[])
        self.assertIn("not found",status["error"])


if __name__=="__main__":
    unittest.main()
