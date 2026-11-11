"""Demo catalog: real card names/types/costs in Scryfall's card format. IDs and PRICES ARE SAMPLE VALUES."""
import json, uuid
U = lambda s: str(uuid.uuid5(uuid.NAMESPACE_URL, "mtgstack-demo/" + s))
cards = []
LEG = {"commander": "legal", "legacy": "legal", "vintage": "legal"}
def card(name, type_line, cost, cmc, colors, rarity, sets, text="", ci=None):
    """sets: list of (set_code, set_name, cn, finishes, prices{usd,usd_foil,usd_etched}, released, border)"""
    for s in sets:
        code, sname, cn, fins, pr, rel = s[:6]
        border = s[6] if len(s) > 6 else "black"
        cards.append({"object": "card", "id": U(f"{code}/{cn}"), "oracle_id": U("o/" + name), "name": name,
                      "lang": "en", "released_at": rel, "set": code, "set_name": sname, "collector_number": cn,
                      "rarity": rarity, "type_line": type_line, "mana_cost": cost, "cmc": cmc, "colors": colors,
                      "color_identity": ci if ci is not None else colors, "oracle_text": text, "finishes": fins,
                      "border_color": border, "frame": "2015", "frame_effects": [], "full_art": False,
                      "promo": False, "promo_types": [], "tcgplayer_id": None, "legalities": LEG,
                      "prices": {k: (f"{v:.2f}" if v else None) for k, v in pr.items()}})
NF = ["nonfoil", "foil"]
C21 = ("c21", "Commander 2021"); CMM = ("cmm", "Commander Masters"); CMR = ("cmr", "Commander Legends")
M21 = ("m21", "Core Set 2021"); DMU = ("dmu", "Dominaria United"); MKM = ("mkc", "Murders at Karlov Manor Commander")
def S(setpair, cn, pr, rel, fins=NF, border="black"): return (setpair[0], setpair[1], cn, fins, pr, rel, border)
p = lambda u, f=None, e=None: {"usd": u, "usd_foil": f, "usd_etched": e}

card("Atraxa, Praetors' Voice", "Legendary Creature — Phyrexian Angel Horror", "{G}{W}{U}{B}", 4, ["W","U","B","G"], "mythic",
     [S(("c16","Commander 2016"), "28", p(11.5, 38), "2016-11-11"), S(("2x2","Double Masters 2022"), "190", p(8.0, 12.5), "2022-07-08")],
     "Flying, vigilance, deathtouch, lifelink. At the beginning of your end step, proliferate.")
card("Sol Ring", "Artifact", "{1}", 1, [], "uncommon", [S(C21, "263", p(1.5), "2021-04-23", ["nonfoil"]), S(CMM, "410", p(1.2, 6.0), "2023-08-04"), S(CMM, "703", p(9.0, 20.0), "2023-08-04", border="borderless")], "{T}: Add {C}{C}.")
card("Arcane Signet", "Artifact", "{2}", 2, [], "common", [S(("eld","Throne of Eldraine"), "331", p(0.6, 3.0), "2019-10-04"), S(CMM, "386", p(0.4, 1.5), "2023-08-04")])
card("Fellwar Stone", "Artifact", "{2}", 2, [], "uncommon", [S(C21, "245", p(0.5), "2021-04-23", ["nonfoil"])])
card("Chromatic Lantern", "Artifact", "{3}", 3, [], "rare", [S(("grn","Guilds of Ravnica"), "233", p(3.4, 9.0), "2018-10-05")])
card("Solemn Simulacrum", "Artifact Creature — Golem", "{4}", 4, [], "rare", [S(CMM, "397", p(0.9, 3.5), "2023-08-04")])
card("Birds of Paradise", "Creature — Bird", "{G}", 1, ["G"], "rare", [S(M21, "171", p(7.5, 18.0), "2020-07-03")])
card("Llanowar Elves", "Creature — Elf Druid", "{G}", 1, ["G"], "common", [S(("fdn","Foundations"), "227", p(0.15, 0.5), "2024-11-15")])
card("Sakura-Tribe Elder", "Creature — Snake Shaman", "{1}{G}", 2, ["G"], "common", [S(CMM, "320", p(0.4, 1.8), "2023-08-04")])
card("Eternal Witness", "Creature — Human Shaman", "{1}{G}{G}", 3, ["G"], "uncommon", [S(("2xm","Double Masters"), "149", p(1.8, 5.0), "2020-08-07")])
card("Evolution Sage", "Creature — Elf Druid", "{2}{G}", 3, ["G"], "uncommon", [S(("war","War of the Spark"), "159", p(0.4, 1.2), "2019-05-03")])
card("Flux Channeler", "Creature — Human Wizard", "{2}{U}", 3, ["U"], "uncommon", [S(("war","War of the Spark"), "52", p(0.3, 1.0), "2019-05-03")])
card("Thrummingbird", "Creature — Phyrexian Bird Horror", "{1}{U}", 2, ["U"], "uncommon", [S(("one","Phyrexia: All Will Be One"), "79", p(0.2, 0.6), "2023-02-10")])
card("Deepglow Skate", "Creature — Fish", "{4}{U}", 5, ["U"], "rare", [S(("ncc","New Capenna Commander"), "215", p(1.6), "2022-04-29", ["nonfoil"])])
card("Viral Drake", "Creature — Phyrexian Drake", "{3}{U}", 4, ["U"], "uncommon", [S(("2xm","Double Masters"), "79", p(0.3, 0.9), "2020-08-07")])
card("Grateful Apparition", "Creature — Spirit", "{1}{W}", 2, ["W"], "uncommon", [S(("war","War of the Spark"), "17", p(0.3, 1.1), "2019-05-03")])
card("Vorinclex, Monstrous Raider", "Legendary Creature — Phyrexian Praetor", "{4}{G}{G}", 6, ["G"], "mythic", [S(("khm","Kaldheim"), "199", p(14.0, 22.0), "2021-02-05")])
card("Doubling Season", "Enchantment", "{4}{G}", 5, ["G"], "mythic", [S(("2x2","Double Masters 2022"), "143", p(39.0, 55.0), "2022-07-08")])
card("Hardened Scales", "Enchantment", "{G}", 1, ["G"], "rare", [S(("khc","Kaldheim Commander"), "78", p(1.4), "2021-02-05", ["nonfoil"])])
card("Inexorable Tide", "Enchantment", "{3}{U}{U}", 5, ["U"], "rare", [S(("ncc","New Capenna Commander"), "222", p(5.5), "2022-04-29", ["nonfoil"])])
card("Rhystic Study", "Enchantment", "{2}{U}", 3, ["U"], "rare", [S(("jmp","Jumpstart"), "169", p(31.0), "2020-07-17", ["nonfoil"]), S(("wot","Wilds of Eldraine: Enchanting Tales"), "21", p(42.0, 60.0), "2023-09-08")])
card("Phyrexian Arena", "Enchantment", "{1}{B}{B}", 3, ["B"], "rare", [S(("onc","Phyrexia: All Will Be One Commander"), "88", p(2.5), "2023-02-10", ["nonfoil"])])
card("Sylvan Library", "Enchantment", "{1}{G}", 2, ["G"], "rare", [S(("ema","Eternal Masters"), "187", p(28.0, 80.0), "2016-06-10")])
card("Mystic Remora", "Enchantment", "{U}", 1, ["U"], "uncommon", [S(("ice","Ice Age"), "87", p(6.0), "1995-06-01", ["nonfoil"])])
card("Guardian Project", "Enchantment", "{3}{G}", 4, ["G"], "rare", [S(("rna","Ravnica Allegiance"), "130", p(5.0, 9.0), "2019-01-25")])
card("Oath of Teferi", "Legendary Enchantment", "{3}{W}{U}", 5, ["W","U"], "rare", [S(("dom","Dominaria"), "200", p(1.0, 4.0), "2018-04-27")])
card("Narset, Parter of Veils", "Legendary Planeswalker — Narset", "{1}{U}{U}", 3, ["U"], "uncommon", [S(("war","War of the Spark"), "61", p(2.2, 6.0), "2019-05-03")])
card("Teferi, Master of Time", "Legendary Planeswalker — Teferi", "{2}{U}{U}", 4, ["U"], "mythic", [S(("m21","Core Set 2021"), "75", p(1.8, 4.5), "2020-07-03")])
card("Nissa, Who Shakes the World", "Legendary Planeswalker — Nissa", "{3}{G}{G}", 5, ["G"], "rare", [S(("war","War of the Spark"), "169", p(3.5, 9.0), "2019-05-03")])
card("Vraska, Golgari Queen", "Legendary Planeswalker — Vraska", "{2}{B}{G}", 4, ["B","G"], "mythic", [S(("grn","Guilds of Ravnica"), "213", p(2.0, 6.0), "2018-10-05")])
card("Swords to Plowshares", "Instant", "{W}", 1, ["W"], "uncommon", [S(("sta","Strixhaven Mystical Archive"), "10", p(2.5, 4.0, 7.0), "2021-04-23", ["nonfoil","foil","etched"])])
card("Path to Exile", "Instant", "{W}", 1, ["W"], "uncommon", [S(("2xm","Double Masters"), "25", p(3.0, 6.0), "2020-08-07")])
card("Counterspell", "Instant", "{U}{U}", 2, ["U"], "uncommon", [S(("mh2","Modern Horizons 2"), "267", p(1.0, 2.5, 3.5), "2021-06-18", ["nonfoil","foil","etched"]), S(("dmr","Dominaria Remastered"), "45", p(0.8, 1.2), "2023-01-13")])
card("Cyclonic Rift", "Instant", "{1}{U}", 2, ["U"], "rare", [S(("2xm","Double Masters"), "45", p(28.0, 45.0), "2020-08-07")])
card("Anguished Unmaking", "Instant", "{1}{W}{B}", 3, ["W","B"], "rare", [S(("soi","Shadows over Innistrad"), "242", p(2.0, 5.0), "2016-04-08")])
card("Assassin's Trophy", "Instant", "{B}{G}", 2, ["B","G"], "rare", [S(("grn","Guilds of Ravnica"), "152", p(2.8, 8.0), "2018-10-05")])
card("Beast Within", "Instant", "{2}{G}", 3, ["G"], "uncommon", [S(C21, "180", p(0.6), "2021-04-23", ["nonfoil"])])
card("Generous Gift", "Instant", "{2}{W}", 3, ["W"], "uncommon", [S(("mh1","Modern Horizons"), "11", p(1.5, 4.0), "2019-06-14")])
card("Lightning Bolt", "Instant", "{R}", 1, ["R"], "uncommon", [S(("2xm","Double Masters"), "117", p(1.8, 4.0), "2020-08-07")])
card("Toxic Deluge", "Sorcery", "{2}{B}", 3, ["B"], "rare", [S(("c13","Commander 2013"), "99", p(19.0), "2013-11-01", ["nonfoil"])])
card("Damnation", "Sorcery", "{2}{B}{B}", 4, ["B"], "rare", [S(("tsr","Time Spiral Remastered"), "106", p(14.0, 20.0), "2021-03-19")])
card("Cultivate", "Sorcery", "{2}{G}", 3, ["G"], "common", [S(C21, "182", p(0.3), "2021-04-23", ["nonfoil"])])
card("Kodama's Reach", "Sorcery — Arcane", "{2}{G}", 3, ["G"], "common", [S(C21, "193", p(0.3), "2021-04-23", ["nonfoil"])])
card("Farseek", "Sorcery", "{1}{G}", 2, ["G"], "common", [S(("m13","Magic 2013"), "170", p(0.8, 3.0), "2012-07-13")])
card("Rampant Growth", "Sorcery", "{1}{G}", 2, ["G"], "common", [S(("m21","Core Set 2021"), "198", p(0.2, 0.8), "2020-07-03")])
card("Contentious Plan", "Sorcery", "{1}{U}", 2, ["U"], "common", [S(("war","War of the Spark"), "46", p(0.2, 0.6), "2019-05-03")])
card("Tezzeret's Gambit", "Sorcery", "{3}{U/P}", 4, ["U"], "uncommon", [S(("kld","Kaladesh"), "68", p(0.5, 2.0), "2016-09-30")])
LAND = ("Land", "", 0, [])
card("Command Tower", *LAND, "common", [S(C21, "300", p(0.25), "2021-04-23", ["nonfoil"])], ci=[])
card("Exotic Orchard", *LAND, "rare", [S(C21, "302", p(0.4), "2021-04-23", ["nonfoil"])])
card("Karn's Bastion", *LAND, "rare", [S(("war","War of the Spark"), "248", p(1.2, 3.0), "2019-05-03")])
for nm, tl, ci, cn, pr in [("Breeding Pool", "Land — Forest Island", ["G","U"], "246", 13.0), ("Overgrown Tomb", "Land — Swamp Forest", ["B","G"], "253", 12.0),
                           ("Temple Garden", "Land — Forest Plains", ["G","W"], "258", 9.0), ("Watery Grave", "Land — Island Swamp", ["U","B"], "259", 14.0),
                           ("Hallowed Fountain", "Land — Plains Island", ["W","U"], "251", 11.0), ("Godless Shrine", "Land — Plains Swamp", ["W","B"], "248", 10.0)]:
    card(nm, tl, "", 0, [], "rare", [S(("rvr","Ravnica Remastered"), cn, p(pr, pr * 1.6), "2024-01-12")], ci=ci)
card("Indatha Triome", "Land — Plains Swamp Forest", "", 0, [], "rare", [S(("iko","Ikoria: Lair of Behemoths"), "248", p(6.0, 11.0), "2020-04-24")], ci=["W","B","G"])
for nm, cn, c in [("Plains", "272", "W"), ("Island", "275", "U"), ("Swamp", "278", "B"), ("Forest", "281", "G")]:
    card(nm, f"Basic Land — {nm}", "", 0, [], "common", [S(("fdn","Foundations"), cn, p(0.05, 0.2), "2024-11-15")], ci=[c])

for nm, tl, cost, cmc, cols, rar, sp, cn, pr, rel in [
    ("Ponder", "Sorcery", "{U}", 1, ["U"], "common", ("m12","Magic 2012"), "73", 2.0, "2011-07-15"),
    ("Brainstorm", "Instant", "{U}", 1, ["U"], "common", ("sta","Strixhaven Mystical Archive"), "13", 2.2, "2021-04-23"),
    ("Swan Song", "Instant", "{U}", 1, ["U"], "rare", ("ths","Theros"), "65", 12.0, "2013-09-27"),
    ("Arcane Denial", "Instant", "{1}{U}", 2, ["U"], "common", ("a25","Masters 25"), "41", 0.9, "2018-03-16"),
    ("Demonic Tutor", "Sorcery", "{1}{B}", 2, ["B"], "uncommon", ("uma","Ultimate Masters"), "93", 32.0, "2018-12-07"),
    ("Vampiric Tutor", "Instant", "{B}", 1, ["B"], "rare", ("eve","Eventide"), "180", 38.0, "2008-07-25"),
    ("Enlightened Tutor", "Instant", "{W}", 1, ["W"], "uncommon", ("sta","Strixhaven Mystical Archive"), "3", 11.0, "2021-04-23"),
    ("Worldly Tutor", "Instant", "{G}", 1, ["G"], "uncommon", ("sta","Strixhaven Mystical Archive"), "53", 16.0, "2021-04-23"),
    ("Smothering Tithe", "Enchantment", "{3}{W}", 4, ["W"], "rare", ("rna","Ravnica Allegiance"), "22", 14.0, "2019-01-25"),
    ("Esper Sentinel", "Artifact Creature — Human Soldier", "{W}", 1, ["W"], "rare", ("mh2","Modern Horizons 2"), "12", 9.0, "2021-06-18"),
    ("Tamiyo, Field Researcher", "Legendary Planeswalker — Tamiyo", "{1}{G}{W}{U}", 4, ["G","W","U"], "mythic", ("emn","Eldritch Moon"), "190", 3.0, "2016-07-22"),
    ("Kaya's Guile", "Instant", "{1}{W}{B}", 3, ["W","B"], "rare", ("mh1","Modern Horizons"), "205", 0.7, "2019-06-14"),
    ("Mirari's Wake", "Enchantment", "{3}{G}{W}", 5, ["G","W"], "mythic", ("cmr","Commander Legends"), "286", 4.5, "2020-11-20"),
    ("Rishkar, Peema Renegade", "Legendary Creature — Elf Druid", "{2}{G}", 3, ["G"], "rare", ("aer","Aether Revolt"), "132", 0.6, "2017-01-20"),
    ("Ezuri, Claw of Progress", "Legendary Creature — Phyrexian Elf Warrior", "{2}{G}{U}", 4, ["G","U"], "mythic", ("c15","Commander 2015"), "43", 1.5, "2015-11-13"),
    ("Fathom Mage", "Creature — Human Wizard", "{2}{G}{U}", 4, ["G","U"], "rare", ("rtr","Return to Ravnica"), "162", 0.5, "2012-10-05"),
]:
    card(nm, tl, cost, cmc, cols, rar, [S(sp, cn, p(pr, round(pr * 1.8, 2)), rel)])
json.dump(cards, open("demo_cards.json", "w"))
print(len(cards), "printings,", len({c["oracle_id"] for c in cards}), "cards")
