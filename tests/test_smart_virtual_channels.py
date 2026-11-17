"""Metadata-driven Plex virtual channel scheduling and migration tests."""
import tempfile
import json
import xml.etree.ElementTree as ET
import unittest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from app import channel_management as cm
from app import virtual_rules as vr
from app import main

class Request:
    headers={}

class SmartChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_db,old_store=main.DB,cm.STORE
        main.DB=Path(self.tmp.name)/"guide.db"
        cm.STORE=Path(self.tmp.name)/"virtual.db"
        self.addCleanup(setattr,main,"DB",old_db)
        self.addCleanup(setattr,cm,"STORE",old_store)
        main.initialize()
        with main.db() as c:
            c.executemany("""INSERT INTO media
                (id,kind,title,show_title,year,season,episode,genres,duration_ms)
                VALUES (?,?,?,?,?,?,?,?,?)""",[
                ("episode:100","episode","Pilot","The Office",2005,1,1,"Comedy,Workplace",1320000),
                ("episode:101","episode","Second","The Office",2005,1,2,"Comedy",1430000),
                ("episode:102","episode","Pilot","The Simpsons",1989,1,1,"Comedy,Animation",1320000),
                ("movie:200","movie","Space Movie","",1995,None,None,"Sci-Fi,Adventure",7200000),
                ("movie:201","movie","Another Film","",2012,None,None,"Comedy",6000000),
            ])

    def test_genre_show_year_and_type_filters(self):
        with main.db() as c:
            items=[dict(x) for x in c.execute("SELECT * FROM media")]
        r=vr.SmartRules(media_type="episodes",genres=["Comedy"],
             shows=["The Office"],year_min=2000,order="chronological")
        self.assertEqual([m["id"] for m in vr.matching_items(items,r)],
                         ["episode:100","episode:101"])
        self.assertEqual(vr.pool(items,r)["matching"],2)
        movies=vr.SmartRules(media_type="movies",genres=["Sci-Fi"],year_max=2000)
        self.assertEqual([m["id"] for m in vr.matching_items(items,movies)],["movie:200"])
        self.assertEqual(vr.duration_minutes(items[0],vr.SmartRules()),22)

    def test_full_matching_preview_pages_and_filtering(self):
        # Full results must be browsable rather than limited to 15 samples.
        with main.db() as c:
            c.executemany(
                "INSERT INTO media (id,kind,title,show_title,year,genres) VALUES (?,?,?,?,?,?)",
                [(f"movie:{1000+i}","movie",f"Comedy {i:03d}","",2000+i%20,"Comedy,Drama")
                 for i in range(122)])
        rule=vr.SmartRules(media_type="movies",genres=["Comedy"],order="chronological")
        first=cm.preview_matching_items(cm.VirtualPreviewPage(rules=rule,offset=0,limit=50),Request())
        second=cm.preview_matching_items(cm.VirtualPreviewPage(rules=rule,offset=50,limit=50),Request())
        third=cm.preview_matching_items(cm.VirtualPreviewPage(rules=rule,offset=100,limit=50),Request())
        self.assertEqual(first["matching"],123)
        self.assertEqual((len(first["items"]),len(second["items"]),len(third["items"])),(50,50,23))
        keys=[x["rating_key"] for x in first["items"]+second["items"]+third["items"]]
        self.assertEqual(len(keys),len(set(keys)))
        self.assertTrue(all("Comedy" in x["genres"] for x in first["items"]))
        only=cm.preview_matching_items(cm.VirtualPreviewPage(rules=rule,search="Comedy 004",kind="movie"),Request())
        self.assertEqual(only["filtered"],1)
        self.assertEqual(only["items"][0]["title"],"Comedy 004")
        noepisodes=cm.preview_matching_items(cm.VirtualPreviewPage(rules=rule,kind="episode"),Request())
        self.assertEqual(noepisodes["filtered"],0)
        self.assertEqual(noepisodes["matching"],123)
        with self.assertRaises(HTTPException):
            cm.preview_matching_items(cm.VirtualPreviewPage(rules=rule,kind="music"),Request())

    def test_match_preview_retains_genre_label_collection_metadata(self):
        with main.db() as c:
            c.execute("UPDATE media SET plex_labels=?,plex_tags=? WHERE id='movie:201'",
                      ('["Family"]','["Comedy Night"]'))
        rule=vr.SmartRules(media_type="movies",genres=["Comedy"])
        data=cm.preview_matching_items(cm.VirtualPreviewPage(rules=rule),Request())
        item=next(x for x in data["items"] if x["rating_key"]==201)
        self.assertEqual(item["genres"],["Comedy"])
        self.assertEqual(item["labels"],["family"])
        self.assertEqual(item["tags"],["comedy night"])

    def test_is_only_one_genre_or_exact_and_combination(self):
        with main.db() as c:
            c.executemany(
                "INSERT INTO media (id,kind,title,year,genres) VALUES (?,?,?,?,?)",
                [("movie:301","movie","Comedy",2020,"Comedy"),
                 ("movie:302","movie","Drama",2021,"Drama"),
                 ("movie:303","movie","Both",2022,"Comedy,Drama"),
                 ("movie:304","movie","Three genres",2023,"Comedy,Drama,Action"),
                 ("movie:305","movie","Action",2024,"Action"),
                 ("movie:306","movie","No genres",2025,"")])
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        comedy={"field":"genre","operator":"is_only","value":"Comedy"}
        drama={"field":"genre","operator":"is_only","value":"Drama"}
        def results(expr):
            return {x["id"] for x in vr.matching_items(
                catalog,vr.SmartRules(media_type="movies",logic=expr))}
        self.assertEqual(results(comedy),{"movie:201","movie:301"})
        self.assertEqual(results(drama),{"movie:302"})
        # The requested AND expression requires both genres exactly.
        together={"group":"all","children":[comedy,drama]}
        self.assertEqual(results(together),{"movie:303"})
        self.assertNotIn("movie:304",results(together))
        self.assertEqual(results({"group":"any","children":[comedy,drama]}),
                         {"movie:201","movie:301","movie:302"})
        self.assertEqual(results({"group":"all","children":[
            comedy,drama,{"field":"year","operator":"gte","value":2022}]}),
            {"movie:303"})
        self.assertEqual(results({"group":"none","children":[together]}),
            {"movie:200","movie:201","movie:301","movie:302","movie:304","movie:305","movie:306"})
        self.assertEqual(results({"field":"genre","operator":"is_only","value":"comedy"}),
                         {"movie:201","movie:301"})
        inverse={"field":"genre","operator":"is_only_not","value":"Comedy"}
        self.assertIn("movie:303",results(inverse))
        self.assertNotIn("movie:301",results(inverse))
        self.assertNotIn("movie:306",results(inverse))

    def test_saved_multi_genre_is_only_requires_exact_combination(self):
        # Previously saved array values still load, but now require exactly
        # that combination and not a subset of selected genres.
        with main.db() as c:
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        legacy={"field":"genre","operator":"is_only","value":["Comedy","Animation"]}
        matched={x["id"] for x in vr.matching_items(
            catalog,vr.SmartRules(media_type="all",logic=legacy))}
        self.assertEqual(matched,{"episode:102"})

    def test_exclusive_operators_restricted_to_genre_and_valid_values(self):
        invalid=[
            {"field":"genre","operator":"is_only","value":[]},
            {"field":"genre","operator":"is_only","value":""},
            {"field":"genre","operator":"is_only_not","value":[]},
            {"field":"genre","operator":"is_only","value":[""]},
            {"field":"year","operator":"is_only","value":2020},
            {"field":"label","operator":"is_only","value":["Family"]},
            {"field":"genre","operator":"is_only","value":["Drama"]*31},
        ]
        for expression in invalid:
            with self.subTest(expression=expression),self.assertRaises(HTTPException):
                vr.validate_rules(vr.SmartRules(media_type="movies",logic=expression))

    def test_nested_boolean_logic_and_exception_collection(self):
        with main.db() as c:
            c.execute("UPDATE media SET genres=?,plex_tags=? WHERE id='movie:200'",
                      ("Comedy,Action",'["Favorites"]'))
            c.execute("UPDATE media SET genres=?,plex_tags=? WHERE id='movie:201'",
                      ("Comedy,Action",'["No"]'))
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        # (Comedy AND NOT Action) OR in Favorites collection
        root={"group":"any","children":[
            {"group":"all","children":[
                {"field":"genre","operator":"is","value":"Comedy"},
                {"field":"genre","operator":"is_not","value":"Action"}]},
            {"field":"tag","operator":"is","value":"Favorites"}]}
        rule=vr.SmartRules(media_type="movies",logic=root,order="chronological")
        self.assertEqual({x["id"] for x in vr.matching_items(catalog,rule)},
                         {"movie:200"})
        # (Comedy OR Romance) AND NOT (Horror OR Action)
        root2={"group":"all","children":[
            {"group":"any","children":[
                {"field":"genre","operator":"is","value":"Comedy"},
                {"field":"genre","operator":"is","value":"Romance"}]},
            {"group":"none","children":[
                {"field":"genre","operator":"is","value":"Horror"},
                {"field":"genre","operator":"is","value":"Action"}]}]}
        rule2=vr.SmartRules(media_type="all",logic=root2)
        self.assertEqual({x["id"] for x in vr.matching_items(catalog,rule2)},
                         {"episode:100","episode:101","episode:102"})

    def test_boolean_logic_year_show_and_media_type(self):
        with main.db() as c:
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        r=vr.SmartRules(media_type="all",logic={"group":"all","children":[
            {"field":"kind","operator":"is","value":"episode"},
            {"field":"show","operator":"is","value":"The Office"},
            {"field":"year","operator":"gte","value":2000},
            {"field":"year","operator":"lte","value":2020}]})
        self.assertEqual({x["id"] for x in vr.matching_items(catalog,r)},
                         {"episode:100","episode:101"})
        self.assertEqual(vr.pool(catalog,r)["matching"],2)

    def test_bad_logic_expressions_rejected(self):
        predicate={"field":"genre","operator":"is","value":"Comedy"}
        for logic in [
            {"group":"all","children":[]},
            {"group":"xor","children":[predicate]},
            {"group":"all","children":[{"field":"genre","operator":"bad","value":"Comedy"}]},
            {"field":"genre","operator":"is","value":""},
            {"field":"year","operator":"gte","value":"2020"},
            {"field":"kind","operator":"is","value":"invalid"},
            {"group":"all","children":[{"group":"all","children":[
              {"group":"all","children":[{"group":"all","children":[
                {"group":"all","children":[{"group":"all","children":[predicate]}]}]}]}]}]}
        ]:
            with self.assertRaises(HTTPException):
                vr.validate_rules(vr.SmartRules(media_type="all",logic=logic))

    def test_legacy_filters_still_work_without_logic(self):
        with main.db() as c:
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        r=vr.SmartRules(media_type="episodes",shows=["The Office"],genres=["Comedy"])
        self.assertEqual({x["id"] for x in vr.matching_items(catalog,r)},
                         {"episode:100","episode:101"})
        self.assertIsNone(r.logic)

    def test_independent_tv_chronology_and_movie_shuffle(self):
        # Interleaving random movies must not shuffle episodes out of order.
        with main.db() as c:
            c.executemany(
                "INSERT INTO media (id,kind,title,show_title,year,season,episode,genres) VALUES (?,?,?,?,?,?,?,?)",
                [("movie:501","movie","Film A","",2001,None,None,"Drama"),
                 ("movie:502","movie","Film B","",2012,None,None,"Drama"),
                 ("movie:503","movie","Film C","",1999,None,None,"Drama"),
                 ("episode:504","episode","Later","The Office",2005,1,3,"Comedy")])
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        r=vr.SmartRules(media_type="all",episode_order="chronological",
                        movie_order="shuffle",seed=41)
        first=[item["id"] for item,_ in vr.build_cycle(catalog,r)]
        second=[item["id"] for item,_ in vr.build_cycle(list(reversed(catalog)),r)]
        self.assertEqual(first,second)
        self.assertEqual([x for x in first if x in ("episode:100","episode:101","episode:504")],
                         ["episode:100","episode:101","episode:504"])
        self.assertEqual(len(first),len(set(first)))
        self.assertTrue(any(x.startswith("movie:") for x in first))

    def test_plex_sync_persists_collection_and_actual_release_date(self):
        sections=ET.fromstring('<MediaContainer><Directory type="movie" key="8"/></MediaContainer>')
        movie=ET.fromstring(
            '<Video type="movie" ratingKey="901" title="Part One" '
            'year="2004" originallyAvailableAt="2004-04-02" duration="60000">'
            '<Collection tag="Film Series"/><Tag tag="Holiday">'
            '<Genre tag="Drama"/></Video>')
        with (patch.object(main,"PLEX_ALLOWED_LABEL",""),
              patch.object(main,"plex_xml",return_value=sections),
              patch.object(main,"pages",return_value=[movie])):
            result=main.sync_plex()
        self.assertEqual(result["media_count"],1)
        with main.db() as c:
            item=dict(c.execute("SELECT * FROM media WHERE id='movie:901'").fetchone())
        self.assertEqual(json.loads(item["plex_collections"]),["Film Series"])
        self.assertEqual(json.loads(item["plex_tags"]),["Film Series","Holiday"])
        self.assertEqual(item["release_date"],"2004-04-02")

    def test_movie_collections_play_contiguously_in_release_order(self):
        with main.db() as c:
            c.executemany(
                "INSERT INTO media (id,kind,title,year,genres,plex_tags,plex_collections) VALUES (?,?,?,?,?,?,?)",
                [("movie:601","movie","Saga Part Three",2008,"Adventure",'["tag"]','["Space Saga"]'),
                 ("movie:602","movie","Saga Part One",1994,"Adventure",'[]','["Space Saga"]'),
                 ("movie:603","movie","Saga Part Two",2001,"Adventure",'[]','["Space Saga"]'),
                 ("movie:604","movie","Standalone",2002,"Adventure",'["Space Saga"]','[]'),
                 ("movie:605","movie","Other Sequel",2010,"Adventure",'[]','["Other Saga"]'),
                 ("movie:606","movie","Other Original",1982,"Adventure",'[]','["Other Saga"]')])
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        for movie_order in ("shuffle","oldest","newest"):
            with self.subTest(movie_order=movie_order):
                r=vr.SmartRules(media_type="movies",movie_order=movie_order,
                                collection_order="release",seed=123)
                ids=[item["id"] for item,_ in vr.build_cycle(catalog,r)]
                saga=[ids.index("movie:"+str(x)) for x in (602,603,601)]
                self.assertEqual(saga,list(range(saga[0],saga[0]+3)))
                other=[ids.index("movie:"+str(x)) for x in (606,605)]
                self.assertEqual(other,[other[0],other[0]+1])
                # A generic Tag named like a collection must not join it.
                self.assertNotIn(ids.index("movie:604"),saga)
                self.assertEqual(ids,[item["id"] for item,_ in
                                      vr.build_cycle(list(reversed(catalog)),r)])
        oldest=vr.SmartRules(media_type="movies",movie_order="oldest",
                             collection_order="release")
        ids=[item["id"] for item,_ in vr.build_cycle(catalog,oldest)]
        self.assertLess(ids.index("movie:606"),ids.index("movie:602"))
        newest=vr.SmartRules(media_type="movies",movie_order="newest",
                             collection_order="release")
        ids=[item["id"] for item,_ in vr.build_cycle(catalog,newest)]
        self.assertLess(ids.index("movie:602"),ids.index("movie:606"))

    def test_same_year_collection_movies_follow_actual_release_dates(self):
        with main.db() as c:
            c.executemany(
                "INSERT INTO media (id,kind,title,year,genres,plex_collections,release_date) VALUES (?,?,?,?,?,?,?)",
                [("movie:670","movie","March",2020,"Drama",'["Trilogy"]',"2020-03-02"),
                 ("movie:671","movie","January",2020,"Drama",'["Trilogy"]',"2020-01-10"),
                 ("movie:672","movie","December",2020,"Drama",'["Trilogy"]',"2020-12-25")])
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        r=vr.SmartRules(media_type="movies",movie_order="oldest",collection_order="release")
        ids=[item["id"] for item,_ in vr.build_cycle(catalog,r)]
        self.assertEqual([x for x in ids if x in ("movie:670","movie:671","movie:672")],
                         ["movie:671","movie:670","movie:672"])

    def test_mixed_collection_block_does_not_interrupt_movie_series(self):
        with main.db() as c:
            c.executemany(
                "INSERT INTO media (id,kind,title,year,genres,plex_collections) VALUES (?,?,?,?,?,?)",
                [("movie:650","movie","Part 2",2010,"Drama",'["Series"]'),
                 ("movie:651","movie","Part 1",2000,"Drama",'["Series"]')])
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        r=vr.SmartRules(media_type="all",episode_order="chronological",
                        movie_order="shuffle",collection_order="release")
        ids=[item["id"] for item,_ in vr.build_cycle(catalog,r)]
        self.assertEqual(ids.index("movie:651")+1,ids.index("movie:650"))
        self.assertLess(ids.index("episode:100"),ids.index("episode:101"))

    def test_order_validation_and_legacy_compatibility(self):
        with main.db() as c:
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        legacy=vr.SmartRules(media_type="all",order="shuffle",seed=222)
        self.assertEqual([x["id"] for x in vr.ordered_programming(catalog,legacy)],
                         [x["id"] for x in vr.matching_items(catalog,legacy)])
        for kw in ({"episode_order":"bad"},{"movie_order":"bad"},
                   {"collection_order":"bad"}):
            with self.subTest(kw=kw),self.assertRaises(HTTPException):
                vr.validate_rules(vr.SmartRules(**kw))

    def test_mixed_movies_and_selected_shows_match_independently(self):
        with main.db() as c:
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        # Previously the show filter rejected every movie, and a movie-only
        # genre filter also excluded the requested TV episodes.
        rules=vr.SmartRules(media_type="all",shows=["The Office"],
                            genres=["Sci-Fi"],order="chronological")
        matches=vr.matching_items(catalog,rules)
        self.assertEqual({x["id"] for x in matches},
                         {"episode:100","episode:101","movie:200"})
        preview=vr.pool(catalog,rules)
        self.assertEqual((preview["matching"],preview["movies"],preview["episodes"]),
                         (3,1,2))
        # Don't unintentionally include the entire movie library if only
        # shows were selected.
        show_only=vr.SmartRules(media_type="all",shows=["The Office"])
        self.assertEqual({x["id"] for x in vr.matching_items(catalog,show_only)},
                         {"episode:100","episode:101"})
        # No shows selected preserves the original metadata behavior.
        genre_only=vr.SmartRules(media_type="all",genres=["Sci-Fi"])
        self.assertEqual({x["id"] for x in vr.matching_items(catalog,genre_only)},
                         {"movie:200"})
        # A deterministic mixed schedule actually includes both media types.
        cycle=vr.build_cycle(catalog,rules)
        self.assertEqual(len(cycle),3)
        self.assertTrue(all(minutes>0 for _,minutes in cycle))

    def test_labels_and_tags_filter_independently_and_in_combination(self):
        with main.db() as c:
            c.execute("UPDATE media SET plex_labels=?, plex_tags=? WHERE id='episode:100'",
                      (json.dumps(["Family, Favorite", "Kids"]),json.dumps(["Sitcoms","Comedy Night"])))
            c.execute("UPDATE media SET plex_labels=?, plex_tags=? WHERE id='episode:101'",
                      (json.dumps(["Kids"]),json.dumps(["Sitcoms"])))
            c.execute("UPDATE media SET plex_labels=?, plex_tags=? WHERE id='movie:200'",
                      (json.dumps(["Family, Favorite"]),json.dumps(["Sci-Fi Classics"])))
            catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
        only_label=vr.SmartRules(media_type="all",labels=["family, favorite"])
        self.assertEqual({x["id"] for x in vr.matching_items(catalog,only_label)},
                         {"episode:100","movie:200"})
        only_tag=vr.SmartRules(media_type="all",tags=["Sitcoms"])
        self.assertEqual({x["id"] for x in vr.matching_items(catalog,only_tag)},
                         {"episode:100","episode:101"})
        combined=vr.SmartRules(media_type="all",labels=["Family, Favorite"],
                               tags=["Comedy Night"],genres=["Comedy"])
        self.assertEqual([x["id"] for x in vr.matching_items(catalog,combined)],
                         ["episode:100"])
        choices=cm.rule_filters(Request())
        self.assertIn("family, favorite",choices["labels"])
        self.assertIn("sitcoms",choices["tags"])
        self.assertNotIn("Sitcoms",choices["labels"])
        self.assertNotIn("Kids",choices["tags"])

    def test_plex_element_metadata_keeps_labels_separate_from_tags(self):
        item=ET.fromstring('''<Video ratingKey="100">
            <Label tag="Family, Favorite"/><Label tag="Kid Safe"/>
            <Collection tag="Halloween"/><Tag tag="Broadcast Block"/>
            <Genre tag="Comedy"/>
        </Video>''')
        self.assertEqual(main.plex_metadata_tags(item,("Label",)),
                         ["Family, Favorite","Kid Safe"])
        self.assertEqual(main.plex_metadata_tags(item,("Collection","Tag")),
                         ["Broadcast Block","Halloween"])
        self.assertEqual(main.plex_item_labels(item),
                         {"family, favorite","kid safe"})

    def test_label_detail_cache_retains_metadata_omitted_from_listing(self):
        listing=ET.fromstring('<Video ratingKey="200" type="movie" title="Space Movie"/>')
        detail=ET.fromstring('<MediaContainer><Video ratingKey="200" type="movie" title="Space Movie"><Label tag="Family Approved"/></Video></MediaContainer>')
        cache={}
        with (patch.object(main,"PLEX_ALLOWED_LABEL","Family Approved"),
              patch.object(main,"PLEX_LABEL_MODE","allow"),
              patch.object(main,"plex_xml",return_value=detail) as fetch):
            self.assertTrue(main.plex_item_has_allowed_label(listing,cache))
        fetch.assert_called_once_with("/library/metadata/200")
        self.assertEqual(main.plex_metadata_tags(cache["200"],("Label",)),
                         ["Family Approved"])

    def test_smart_rules_persist_and_drive_schedule(self):
        with main.db() as c:
            c.execute("UPDATE media SET plex_labels=?,plex_tags=? WHERE id='episode:100'",
                      ('["Kids"]','["Comedy Night"]'))
            c.execute("UPDATE media SET plex_labels=?,plex_tags=? WHERE id='episode:101'",
                      ('["Kids"]','["Other"]'))
        rule=vr.SmartRules(media_type="episodes",labels=["Kids"],tags=["Comedy Night"])
        created=cm.create_virtual(cm.VirtualUpdate(name="Tagged",
                        mode="smart",rules=rule),Request())
        definition=cm.virtual_definitions()[0]
        self.assertEqual(definition["rules"]["labels"],["Kids"])
        self.assertEqual(definition["rules"]["tags"],["Comedy Night"])
        start=datetime(2026,11,15,13,tzinfo=timezone.utc)
        guide=cm.virtual_guide(start,start+timedelta(hours=2))
        self.assertEqual({p["plex"]["rating_key"] for p in guide[0]["programs"]},{"100"})
        cm.delete_virtual(created["id"],Request())

    def test_stable_shuffle_across_reloads(self):
        with main.db() as c:
            items=[dict(x) for x in c.execute("SELECT * FROM media")]
        rule=vr.SmartRules(media_type="episodes",order="shuffle",seed=500)
        first=[x["id"] for x in vr.matching_items(items,rule)]
        second=[x["id"] for x in vr.matching_items(list(reversed(items)),rule)]
        self.assertEqual(first,second)

    def test_smart_crud_and_deterministic_guide(self):
        rule=vr.SmartRules(media_type="episodes",shows=["The Office"],
                           order="chronological")
        result=cm.create_virtual(cm.VirtualUpdate(name="Office TV",
                                  mode="smart",rules=rule),Request())
        channel_id=result["id"]
        saved=next(x for x in cm.virtual_definitions() if x["id"]==channel_id)
        self.assertEqual(saved["mode"],"smart")
        self.assertEqual(saved["rules"]["shows"],["The Office"])
        start=datetime(2026,11,15,13,tzinfo=timezone.utc)
        first=cm.virtual_guide(start,start+timedelta(hours=6))
        second=cm.virtual_guide(start,start+timedelta(hours=6))
        self.assertEqual(first,second)
        self.assertTrue(all(p["plex"]["rating_key"] in ("100","101") for p in first[0]["programs"]))
        self.assertGreater(len(first[0]["programs"]),5)
        cm.update_virtual(channel_id,cm.VirtualUpdate(name="Movies",
                mode="smart",rules=vr.SmartRules(media_type="movies")),Request())
        self.assertEqual(cm.virtual_definitions()[0]["rules"]["media_type"],"movies")
        cm.delete_virtual(channel_id,Request())
        self.assertEqual(cm.virtual_definitions(),[])

    def test_smart_channel_manual_includes_and_excludes_survive_reopen(self):
        rule=vr.SmartRules(media_type="episodes",shows=["The Office"],
                           order="chronological")
        body=cm.VirtualUpdate(
            name="Mixed overrides",mode="smart",rules=rule,
            items=[cm.VirtualItem(rating_key=200,duration_minutes=115)],
            exclude_rating_keys=[101])
        created=cm.create_virtual(body,Request())
        channel_id=created["id"]
        saved=cm.virtual_definitions()[0]
        self.assertEqual(saved["exclude_rating_keys"],[101])
        self.assertEqual(saved["items"][0]["rating_key"],200)
        start=datetime(2026,11,15,13,tzinfo=timezone.utc)
        guide=cm.virtual_guide(start,start+timedelta(hours=8))
        ids={p["plex"]["rating_key"] for p in guide[0]["programs"]}
        self.assertEqual(ids,{"100","200"})
        # Updating the same channel preserves the underlying channel ID,
        # but overrides change dynamically at the next guide refresh.
        cm.update_virtual(channel_id,cm.VirtualUpdate(
            name="Mixed overrides",mode="smart",rules=rule,
            items=[cm.VirtualItem(rating_key=201,duration_minutes=100)],
            exclude_rating_keys=[100]),Request())
        again=cm.virtual_guide(start,start+timedelta(hours=8))
        self.assertEqual({p["plex"]["rating_key"] for p in again[0]["programs"]},
                         {"101","201"})
        cm.delete_virtual(channel_id,Request())
        self.assertEqual(cm.virtual_definitions(),[])

    def test_saved_exclusions_resolve_plex_titles_by_id(self):
        # Saved items must resolve independently of the current search query.
        result=cm.resolve_plex_items(Request(),ids="100,200,999999")
        names={x["rating_key"]:(x["show_title"],x["title"])
               for x in result["items"]}
        self.assertEqual(names[100],("The Office","Pilot"))
        self.assertEqual(names[200],("","Space Movie"))
        self.assertNotIn(999999,names)
        self.assertEqual(cm.resolve_plex_items(Request(),ids="")["items"],[])
        with self.assertRaises(HTTPException):
            cm.resolve_plex_items(Request(),ids="100,not-an-id")
        with self.assertRaises(HTTPException):
            cm.resolve_plex_items(Request(),ids=",".join(str(i) for i in range(1,502)))

    def test_manual_include_cannot_also_be_excluded(self):
        with self.assertRaises(HTTPException):
            cm.create_virtual(cm.VirtualUpdate(name="Conflicting",
                mode="smart",rules=vr.SmartRules(),
                items=[cm.VirtualItem(rating_key=200,duration_minutes=120)],
                exclude_rating_keys=[200]),Request())

    def test_existing_manual_playlist_remains_supported(self):
        body=cm.VirtualUpdate(name="Manual",items=[cm.VirtualItem(rating_key=200,duration_minutes=90)])
        cm.create_virtual(body,Request())
        row=cm.virtual_definitions()[0]
        self.assertEqual(row["mode"],"manual")
        start=datetime(2026,11,15,13,tzinfo=timezone.utc)
        schedule=cm.virtual_guide(start,start+timedelta(hours=2))
        self.assertTrue(all(p["plex"]["rating_key"]=="200" for p in schedule[0]["programs"]))

    def test_invalid_filters_rejected(self):
        for rule in [
            vr.SmartRules(media_type="movies",shows=["The Office"]),
            vr.SmartRules(year_min=2020,year_max=1990),
        ]:
            with self.assertRaises(HTTPException):
                vr.validate_rules(rule)

    def test_metadata_schema_upgrade_from_old_database(self):
        # Reinitialize old media table independently of current test DB.
        with main.db() as c:
            c.execute("DROP TABLE media")
            c.execute("""CREATE TABLE media(id TEXT PRIMARY KEY,kind TEXT,title TEXT,
                      year INT,normalized TEXT,show_title TEXT,season INT,episode INT,guids TEXT)""")
        main.initialize()
        with main.db() as c:
            columns={x["name"] for x in c.execute("PRAGMA table_info(media)")}
        self.assertIn("genres",columns)
        self.assertIn("duration_ms",columns)
        self.assertIn("plex_labels",columns)
        self.assertIn("plex_tags",columns)
        self.assertIn("plex_collections",columns)
        self.assertIn("release_date",columns)

if __name__=="__main__":
    unittest.main()
