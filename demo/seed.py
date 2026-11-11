"""Builds the demo collection. Runs both here and inside the browser (Pyodide)."""
import json
from mtgstack import collection as col, db, decks
from mtgstack.catalog import Catalog, upsert_cards

ATRAXA = """Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
1 Arcane Signet
1 Fellwar Stone
1 Chromatic Lantern
1 Solemn Simulacrum
1 Birds of Paradise
1 Llanowar Elves
1 Sakura-Tribe Elder
1 Eternal Witness
1 Evolution Sage
1 Flux Channeler
1 Thrummingbird
1 Deepglow Skate
1 Viral Drake
1 Grateful Apparition
1 Vorinclex, Monstrous Raider
1 Rishkar, Peema Renegade
1 Ezuri, Claw of Progress
1 Fathom Mage
1 Esper Sentinel
1 Doubling Season
1 Hardened Scales
1 Inexorable Tide
1 Rhystic Study
1 Phyrexian Arena
1 Sylvan Library
1 Mystic Remora
1 Guardian Project
1 Smothering Tithe
1 Mirari's Wake
1 Oath of Teferi
1 Narset, Parter of Veils
1 Teferi, Master of Time
1 Nissa, Who Shakes the World
1 Vraska, Golgari Queen
1 Tamiyo, Field Researcher
1 Swords to Plowshares
1 Path to Exile
1 Counterspell
1 Arcane Denial
1 Swan Song
1 Cyclonic Rift
1 Anguished Unmaking
1 Kaya's Guile
1 Assassin's Trophy
1 Beast Within
1 Generous Gift
1 Brainstorm
1 Vampiric Tutor
1 Enlightened Tutor
1 Worldly Tutor
1 Toxic Deluge
1 Damnation
1 Cultivate
1 Kodama's Reach
1 Farseek
1 Rampant Growth
1 Contentious Plan
1 Tezzeret's Gambit
1 Ponder
1 Demonic Tutor
1 Command Tower
1 Exotic Orchard
1 Karn's Bastion
1 Breeding Pool
1 Overgrown Tomb
1 Temple Garden
1 Watery Grave
1 Hallowed Fountain
1 Godless Shrine
1 Indatha Triome
5 Plains
9 Island
5 Swamp
9 Forest

Maybeboard
1 Lightning Bolt
"""

SIMIC = """Deck
1 Sol Ring
1 Arcane Signet
1 Cultivate
1 Birds of Paradise
1 Eternal Witness
1 Llanowar Elves
1 Rampant Growth
1 Counterspell
12 Forest
10 Island
"""


def build(path, cards_json: str):
    conn = db.connect(path)
    upsert_cards(conn, json.loads(cards_json))
    cat = Catalog(conn, online=False)

    def own(name, qty=1, set_code=None, finish="nonfoil", cond="NM", tags=""):
        ps = [p for p in cat.prints_by_name(name, fetch=False) if not set_code or p["set_code"] == set_code]
        return col.add(conn, ps[0]["id"], qty, finish, cond, "en", tags, auto_allocate=False)

    # --- the collection as it stood before any deck existed
    for name, qty, s, fin, tags in [
        ("Sol Ring", 2, "c21", "nonfoil", "binder"), ("Sol Ring", 1, "cmm", "foil", "binder"),
        ("Swords to Plowshares", 2, None, "nonfoil", "binder"), ("Counterspell", 2, "dmr", "nonfoil", "binder"),
        ("Counterspell", 1, "mh2", "etched", "binder"), ("Cyclonic Rift", 1, None, "nonfoil", "binder"),
        ("Birds of Paradise", 2, None, "nonfoil", "binder"), ("Eternal Witness", 2, None, "nonfoil", "binder"),
        ("Doubling Season", 1, None, "foil", "binder"), ("Breeding Pool", 1, None, "nonfoil", "binder"),
        ("Overgrown Tomb", 1, None, "nonfoil", "binder"), ("Lightning Bolt", 4, None, "nonfoil", "binder"),
        ("Path to Exile", 1, None, "nonfoil", "binder"), ("Sylvan Library", 1, None, "nonfoil", "binder"),
        ("Arcane Signet", 2, "eld", "nonfoil", "box:bulk-1"), ("Cultivate", 3, None, "nonfoil", "box:bulk-1"),
        ("Kodama's Reach", 2, None, "nonfoil", "box:bulk-1"), ("Rampant Growth", 4, None, "nonfoil", "box:bulk-1"),
        ("Farseek", 1, None, "nonfoil", "box:bulk-1"), ("Beast Within", 2, None, "nonfoil", "box:bulk-1"),
        ("Evolution Sage", 1, None, "nonfoil", "box:bulk-1"), ("Flux Channeler", 1, None, "nonfoil", "box:bulk-1"),
        ("Thrummingbird", 1, None, "nonfoil", "box:bulk-1"), ("Llanowar Elves", 3, None, "nonfoil", "box:bulk-1"),
        ("Contentious Plan", 1, None, "nonfoil", "box:bulk-1"), ("Ponder", 1, None, "nonfoil", "box:bulk-1"),
        ("Island", 24, None, "nonfoil", "box:lands"), ("Forest", 26, None, "nonfoil", "box:lands"),
        ("Plains", 6, None, "nonfoil", "box:lands"), ("Swamp", 6, None, "nonfoil", "box:lands"),
    ]:
        own(name, qty, s, fin, tags=tags)
    own("Atraxa, Praetors' Voice", 1, "2x2", "nonfoil", cond="LP", tags="trade")

    # --- a built deck, fully allocated (pulls its cards out of the binder/box)
    decks.import_decklist(conn, cat, "Simic Ramp", SIMIC, "commander", slug="simic-ramp", status="built")
    decks.pull(conn, "simic-ramp", remove_tags=["binder", "box:bulk-1", "box:lands"])

    # --- the deck being built
    decks.import_decklist(conn, cat, "Atraxa Counters", ATRAXA, "commander", slug="atraxa", status="building")

    # --- cards that arrived AFTER the deck was set to in progress: auto-tagged in
    for name, s, fin in [("Rhystic Study", "jmp", "nonfoil"), ("Smothering Tithe", None, "nonfoil"),
                         ("Fathom Mage", None, "nonfoil"), ("Hardened Scales", None, "nonfoil"),
                         ("Watery Grave", None, "nonfoil"), ("Command Tower", None, "nonfoil"),
                         ("Island", None, "nonfoil"), ("Forest", None, "nonfoil")]:
        ps = [p for p in cat.prints_by_name(name, fetch=False) if not s or p["set_code"] == s]
        col.add(conn, ps[0]["id"], 4 if name in ("Island", "Forest") else 1, fin, "NM", "en", "box:new")
    conn.commit()
    return conn


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "..")
    c = build("/tmp/demo_seed.db", open("demo_cards.json").read())
    for d in decks.list_decks(c):
        print(d["slug"], d["status"], d["progress"], "to move", d["free"], "to buy", d["missing"], d["missing_cost"])
