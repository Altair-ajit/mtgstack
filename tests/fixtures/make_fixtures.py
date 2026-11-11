"""Builds tests/fixtures/cards.json: Scryfall-shaped card objects with FAKE ids and prices.
Shapes follow https://scryfall.com/docs/api/cards ; values are for tests only."""
import json, uuid
def U(s): return str(uuid.uuid5(uuid.NAMESPACE_URL, s))
cards = []
def card(name, set_, set_name, cn, rarity, type_line, cost, cmc, colors, text, finishes, prices,
         tcg=None, border="black", frame="2015", effects=(), full_art=False, promo_types=(), released="2020-01-01",
         lang="en", legal=None, etched_tcg=None, arena=None):
    cards.append({"object":"card","id":U(f"{set_}/{cn}/{lang}"),"oracle_id":U("oracle/"+name),"name":name,"lang":lang,
      "released_at":released,"set":set_,"set_name":set_name,"collector_number":cn,"rarity":rarity,
      "type_line":type_line,"mana_cost":cost,"cmc":cmc,"colors":colors,"color_identity":colors,"oracle_text":text,
      "finishes":finishes,"border_color":border,"frame":frame,"frame_effects":list(effects),"full_art":full_art,
      "promo":bool(promo_types),"promo_types":list(promo_types),"tcgplayer_id":tcg,"tcgplayer_etched_id":etched_tcg,
      "arena_id":arena,
      "legalities":legal or {"commander":"legal","vintage":"legal","legacy":"legal","modern":"not_legal"},
      "image_uris":{"normal":f"https://example.invalid/{set_}/{cn}.jpg","art_crop":f"https://example.invalid/{set_}/{cn}_art.jpg"},
      "prices":prices})
SR=("Sol Ring","Artifact","{1}",1.0,[],"{T}: Add {C}{C}.")
card(*SR[:1],"c21","Commander 2021","263","uncommon",*SR[1:],["nonfoil"],{"usd":"1.50"},tcg=239000,released="2021-04-23",legal={"commander":"legal","vintage":"restricted","legacy":"banned"})
card(*SR[:1],"cmm","Commander Masters","410","uncommon",*SR[1:],["nonfoil","foil"],{"usd":"1.20","usd_foil":"6.00"},tcg=500100,released="2023-08-04",legal={"commander":"legal","vintage":"restricted","legacy":"banned"})
card(*SR[:1],"cmm","Commander Masters","703","uncommon",*SR[1:],["nonfoil","foil"],{"usd":"9.00","usd_foil":"20.00"},tcg=500200,border="borderless",released="2023-08-04",legal={"commander":"legal","vintage":"restricted","legacy":"banned"})
card(*SR[:1],"fdc","Foundations Jumpstart Starter","0057","uncommon",*SR[1:],["foil"],{"usd_foil":"3.00"},tcg=560000,released="2024-11-15",legal={"commander":"legal","vintage":"restricted","legacy":"banned"})
LB=("Lightning Bolt","Instant","{R}",1.0,["R"],"Lightning Bolt deals 3 damage to any target.")
card(*LB[:1],"m11","Magic 2011","149","common",*LB[1:],["nonfoil","foil"],{"usd":"2.00","usd_foil":"15.00"},tcg=37000,frame="2003",released="2010-07-16",legal={"commander":"legal","modern":"legal","legacy":"legal"})
card(*LB[:1],"sta","Strixhaven Mystical Archive","42","rare",*LB[1:],["nonfoil","foil","etched"],{"usd":"3.00","usd_foil":"5.00","usd_etched":"8.00"},tcg=234000,etched_tcg=234001,released="2021-04-23",arena=75000,legal={"commander":"legal","modern":"legal","legacy":"legal"})
card(*LB[:1],"2xm","Double Masters","117","uncommon",*LB[1:],["nonfoil","foil"],{"usd":"1.80","usd_foil":"4.00"},tcg=212000,released="2020-08-07",legal={"commander":"legal","modern":"legal","legacy":"legal"})
CS=("Counterspell","Instant","{U}{U}",2.0,["U"],"Counter target spell.")
card(*CS[:1],"mh2","Modern Horizons 2","267","uncommon",*CS[1:],["nonfoil","foil","etched"],{"usd":"1.00","usd_foil":"2.50","usd_etched":"3.50"},tcg=240000,released="2021-06-18")
card(*CS[:1],"dmr","Dominaria Remastered","45","common",*CS[1:],["nonfoil","foil"],{"usd":"0.80","usd_foil":"1.20"},tcg=470000,released="2023-01-13")
card("Llanowar Elves","dom","Dominaria","168","common","Creature — Elf Druid","{G}",1.0,["G"],"{T}: Add {G}.",["nonfoil","foil"],{"usd":"0.25","usd_foil":"1.00"},tcg=160000,released="2018-04-27",legal={"commander":"legal","modern":"legal","standard":"not_legal"})
card("Llanowar Elves","fdn","Foundations","227","common","Creature — Elf Druid","{G}",1.0,["G"],"{T}: Add {G}.",["nonfoil","foil"],{"usd":"0.15","usd_foil":"0.50"},tcg=559000,released="2024-11-15",legal={"commander":"legal","modern":"legal","standard":"legal"},arena=91000)
card("Swords to Plowshares","sta","Strixhaven Mystical Archive","10","uncommon","Instant","{W}",1.0,["W"],"Exile target creature. Its controller gains life equal to its power.",["nonfoil","foil","etched"],{"usd":"2.50","usd_foil":"4.00","usd_etched":"7.00"},tcg=234100,released="2021-04-23")
card("Atraxa, Praetors' Voice","c16","Commander 2016","28","mythic","Legendary Creature — Phyrexian Angel Horror","{G}{W}{U}{B}",4.0,["B","G","U","W"],"Flying, vigilance, deathtouch, lifelink\nAt the beginning of your end step, proliferate.",["nonfoil","foil"],{"usd":"12.00","usd_foil":"40.00"},tcg=121000,released="2016-11-11")
card("Atraxa, Praetors' Voice","2x2","Double Masters 2022","190","mythic","Legendary Creature — Phyrexian Angel Horror","{G}{W}{U}{B}",4.0,["B","G","U","W"],"Flying, vigilance, deathtouch, lifelink\nAt the beginning of your end step, proliferate.",["nonfoil","foil"],{"usd":"8.00","usd_foil":"12.00"},tcg=280000,released="2022-07-08",effects=["showcase"])
card("Arcane Signet","eld","Throne of Eldraine","331","common","Artifact","{2}",2.0,[],"{T}: Add one mana of any color in your commander's color identity.",["nonfoil","foil"],{"usd":"0.60","usd_foil":"3.00"},tcg=198000,released="2019-10-04")
card("Island","fdn","Foundations","275","common","Basic Land — Island","",0.0,[],"({T}: Add {U}.)",["nonfoil","foil"],{"usd":"0.05","usd_foil":"0.20"},tcg=559500,released="2024-11-15",full_art=True,arena=91500)
card("Island","fdn","Foundations","276","common","Basic Land — Island","",0.0,[],"({T}: Add {U}.)",["nonfoil","foil"],{"usd":"0.05","usd_foil":"0.20"},tcg=559501,released="2024-11-15")
card("Lightning Bolt","sta","Strixhaven Mystical Archive","42","rare",*LB[1:],["nonfoil","foil"],{"usd":"6.00"},tcg=None,released="2021-04-23",lang="ja")
json.dump(cards, open("tests/fixtures/cards.json","w"), indent=1, ensure_ascii=False)
print(len(cards))
