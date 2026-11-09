"""Plex label allow-list validation without contacting a Plex server."""
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from app import main


def item_xml(item_type="Video",key="123",title="Example",labels=()):
    node=ET.Element(item_type,{"ratingKey":key,"title":title})
    for label in labels:
        ET.SubElement(node,"Label",{"tag":label})
    return node


class PlexLabelFilterTests(unittest.TestCase):
    def test_no_filter_preserves_previous_behavior(self):
        with patch.object(main,"PLEX_ALLOWED_LABEL",""):
            self.assertTrue(main.plex_item_has_allowed_label(item_xml()))

    def test_movie_label_matches_case_insensitively(self):
        with patch.object(main,"PLEX_ALLOWED_LABEL","Family Approved"):
            with patch.object(main,"plex_xml") as fetch:
                self.assertTrue(main.plex_item_has_allowed_label(
                    item_xml(labels=["family approved"])))
                fetch.assert_not_called()

    def test_full_metadata_lookup_when_list_omits_label(self):
        node=item_xml("Directory",key="456")
        detail=ET.Element("MediaContainer")
        detail.append(item_xml("Directory",key="456",labels=["Family Approved"]))
        with patch.object(main,"PLEX_ALLOWED_LABEL","family approved"):
            with patch.object(main,"plex_xml",return_value=detail) as fetch:
                self.assertTrue(main.plex_item_has_allowed_label(node))
                fetch.assert_called_once_with("/library/metadata/456")

    def test_different_label_denied(self):
        detail=ET.Element("MediaContainer")
        detail.append(item_xml(labels=["Not Shared"]))
        with patch.object(main,"PLEX_ALLOWED_LABEL","Family Approved"):
            with patch.object(main,"plex_xml",return_value=detail):
                self.assertFalse(main.plex_item_has_allowed_label(
                    item_xml(labels=["Not Shared"])))

    def test_missing_or_mismatched_metadata_denied(self):
        detail=ET.Element("MediaContainer")
        detail.append(item_xml(key="999",labels=["Family Approved"]))
        with patch.object(main,"PLEX_ALLOWED_LABEL","Family Approved"):
            with patch.object(main,"plex_xml",return_value=detail):
                self.assertFalse(main.plex_item_has_allowed_label(item_xml(key="123")))

    def test_invalid_key_cannot_be_fetched(self):
        with patch.object(main,"PLEX_ALLOWED_LABEL","Family Approved"):
            with patch.object(main,"plex_xml") as fetch:
                self.assertFalse(main.plex_item_has_allowed_label(item_xml(key="../123")))
                fetch.assert_not_called()


if __name__=="__main__":
    unittest.main()
