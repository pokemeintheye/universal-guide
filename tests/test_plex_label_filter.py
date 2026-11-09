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

    def test_exclude_denies_labeled_movies_or_shows(self):
        with patch.object(main,"PLEX_ALLOWED_LABEL","Restricted"), \
             patch.object(main,"PLEX_LABEL_MODE","exclude"):
            for kind in ("Video","Directory"):
                with patch.object(main,"plex_xml") as fetch:
                    self.assertFalse(main.plex_item_has_allowed_label(
                        item_xml(item_type=kind,labels=["restricted"])))
                    fetch.assert_not_called()

    def test_exclude_includes_unlabeled_with_verified_metadata(self):
        detail=ET.Element("MediaContainer")
        detail.append(item_xml(labels=["Other"]))
        with patch.object(main,"PLEX_ALLOWED_LABEL","Restricted"), \
             patch.object(main,"PLEX_LABEL_MODE","exclude"), \
             patch.object(main,"plex_xml",return_value=detail) as fetch:
            self.assertTrue(main.plex_item_has_allowed_label(item_xml()))
            fetch.assert_called_once_with("/library/metadata/123")

    def test_exclude_uses_detailed_labels_when_listing_empty(self):
        detail=ET.Element("MediaContainer")
        detail.append(item_xml(labels=["Restricted"]))
        with patch.object(main,"PLEX_ALLOWED_LABEL","Restricted"), \
             patch.object(main,"PLEX_LABEL_MODE","exclude"), \
             patch.object(main,"plex_xml",return_value=detail):
            self.assertFalse(main.plex_item_has_allowed_label(item_xml()))

    def test_exclude_does_not_assume_missing_metadata_is_safe(self):
        detail=ET.Element("MediaContainer")
        with patch.object(main,"PLEX_ALLOWED_LABEL","Restricted"), \
             patch.object(main,"PLEX_LABEL_MODE","exclude"), \
             patch.object(main,"plex_xml",return_value=detail):
            self.assertFalse(main.plex_item_has_allowed_label(item_xml()))

    def test_exclude_fails_sync_on_metadata_fetch_error(self):
        with patch.object(main,"PLEX_ALLOWED_LABEL","Restricted"), \
             patch.object(main,"PLEX_LABEL_MODE","exclude"), \
             patch.object(main,"plex_xml",side_effect=RuntimeError("Plex offline")):
            with self.assertRaisesRegex(RuntimeError,"Plex offline"):
                main.plex_item_has_allowed_label(item_xml())

    def test_no_label_means_no_filter_even_in_exclude_mode(self):
        with patch.object(main,"PLEX_ALLOWED_LABEL",""), \
             patch.object(main,"PLEX_LABEL_MODE","exclude"):
            self.assertTrue(main.plex_item_has_allowed_label(item_xml()))

    def test_invalid_key_cannot_be_fetched(self):
        with patch.object(main,"PLEX_ALLOWED_LABEL","Family Approved"):
            with patch.object(main,"plex_xml") as fetch:
                self.assertFalse(main.plex_item_has_allowed_label(item_xml(key="../123")))
                fetch.assert_not_called()


if __name__=="__main__":
    unittest.main()
