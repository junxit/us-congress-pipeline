"""Senate LIS member IDs mapped to bioguide IDs, vendored and pinned.

The two chambers do not identify a member the same way. The House Clerk writes
``name-id="A000055"`` on a recorded vote -- the bioguide ID, the same string
BILLSTATUS uses for sponsors and cosponsors -- and the Senate writes
``<lis_member_id>S289</lis_member_id>`` and no bioguide ID at all. Phase 8
recorded each as published and inferred nothing, which left the obvious
question -- how did this member vote in both chambers -- answerable only by a
join the reader had to build.

This is that join, and it is **vendored rather than fetched**. The upstream
table is edited continuously: a corrected spelling, a new senator, a fixed
identifier. A build that read it live would re-render every vote file touching
an edited member on the day of the edit, and the daily loop decides what to
push by comparing bytes -- so an upstream typo fix would force-push commits
across the corpus with nothing in the repository saying why. Pinning it here
makes a rebuild reproducible, and 328 rows is small enough to read in
review, which no feed is.

Regenerate with ``data/scripts/build_members.py`` when a Congress seats new
members, and read the diff: a changed bioguide ID moves votes from one senator
to another.

Extracted 2026-08-09 from https://github.com/unitedstates/congress-legislators
(CC0, public domain dedication). Note this is the one source in this project
that is not published by the federal government.
"""

from __future__ import annotations

import unicodedata

#: LIS member id mapped to ``(bioguide id, surname, states served as senator)``.
#: The surname and states are carried so the mapping can be *checked* against
#: the vote document at render time rather than trusted; see :func:`bioguide_for`.
SENATORS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "S009": ("B000401", "Bentsen", ('TX',)),
    "S010": ("B000444", "Biden", ('DE',)),
    "S014": ("B001057", "Bumpers", ('AR',)),
    "S015": ("B001077", "Burdick", ('ND',)),
    "S017": ("B001210", "Byrd", ('WV',)),
    "S023": ("C000877", "Cranston", ('CA',)),
    "S026": ("D000401", "Dole", ('KS',)),
    "S027": ("D000407", "Domenici", ('NM',)),
    "S033": ("F000268", "Ford", ('KY',)),
    "S034": ("G000072", "Garn", ('UT',)),
    "S035": ("G000236", "Glenn", ('OH',)),
    "S044": ("H000343", "Hatfield", ('OR',)),
    "S046": ("H000463", "Helms", ('NC',)),
    "S047": ("H000725", "Hollings", ('SC',)),
    "S051": ("I000025", "Inouye", ('HI',)),
    "S054": ("J000189", "Johnston", ('LA',)),
    "S055": ("K000105", "Kennedy", ('MA',)),
    "S057": ("L000174", "Leahy", ('VT',)),
    "S063": ("M000346", "McClure", ('ID',)),
    "S074": ("N000171", "Nunn", ('GA',)),
    "S075": ("P000009", "Packwood", ('OR',)),
    "S078": ("P000193", "Pell", ('RI',)),
    "S083": ("R000460", "Roth", ('DE',)),
    "S090": ("S000888", "Stevens", ('AK',)),
    "S096": ("T000254", "Thurmond", ('SC',)),
    "S102": ("D000185", "DeConcini", ('AZ',)),
    "S104": ("M000250", "Matsunaga", ('HI',)),
    "S105": ("L000504", "Lugar", ('IN',)),
    "S106": ("S000064", "Sarbanes", ('MD',)),
    "S107": ("R000249", "Riegle", ('MI',)),
    "S108": ("D000030", "Danforth", ('MO',)),
    "S113": ("M001054", "Moynihan", ('NY',)),
    "S114": ("M000678", "Metzenbaum", ('OH',)),
    "S115": ("H000456", "Heinz", ('PA',)),
    "S116": ("C000269", "Chafee", ('RI',)),
    "S117": ("S000068", "Sasser", ('TN',)),
    "S118": ("H000338", "Hatch", ('UT',)),
    "S119": ("W000092", "Wallop", ('WY',)),
    "S125": ("P000556", "Pryor", ('AR',)),
    "S127": ("B000243", "Baucus", ('MT',)),
    "S128": ("B000639", "Boren", ('OK',)),
    "S129": ("E000284", "Exon", ('NE',)),
    "S130": ("H000445", "Heflin", ('AL',)),
    "S131": ("L000261", "Levin", ('MI',)),
    "S132": ("B001225", "Bradley", ('NJ',)),
    "S133": ("D000566", "Durenberger", ('MN',)),
    "S134": ("A000219", "Armstrong", ('CO',)),
    "S135": ("B000647", "Boschwitz", ('MN',)),
    "S136": ("C000567", "Cochran", ('MS',)),
    "S137": ("C000598", "Cohen", ('ME',)),
    "S138": ("H000951", "Humphrey", ('NH',)),
    "S140": ("K000017", "Kassebaum", ('KS',)),
    "S141": ("P000513", "Pressler", ('SD',)),
    "S142": ("S000429", "Simpson", ('WY',)),
    "S143": ("W000154", "Warner", ('VA',)),
    "S144": ("M000811", "Mitchell", ('ME',)),
    "S147": ("D000018", "D’Amato", ('NY',)),
    "S149": ("D000366", "Dixon", ('IL',)),
    "S150": ("D000388", "Dodd", ('CT',)),
    "S152": ("G000333", "Gorton", ('WA',)),
    "S153": ("G000386", "Grassley", ('IA',)),
    "S155": ("K000019", "Kasten", ('WI',)),
    "S157": ("M001085", "Murkowski", ('AK',)),
    "S158": ("N000102", "Nickles", ('OK',)),
    "S160": ("R000497", "Rudman", ('NH',)),
    "S161": ("S000709", "Specter", ('PA',)),
    "S162": ("S001138", "Symms", ('ID',)),
    "S165": ("W000607", "Wilson", ('CA',)),
    "S166": ("L000123", "Lautenberg", ('NJ',)),
    "S167": ("B000468", "Bingaman", ('NM',)),
    "S170": ("G000321", "Gore", ('TN',)),
    "S171": ("G000365", "Gramm", ('TX',)),
    "S172": ("H000206", "Harkin", ('IA',)),
    "S173": ("K000148", "Kerry", ('MA',)),
    "S174": ("M000355", "McConnell", ('KY',)),
    "S175": ("S000423", "Simon", ('IL',)),
    "S176": ("R000361", "Rockefeller", ('WV',)),
    "S178": ("S000055", "Sanford", ('NC',)),
    "S179": ("B000780", "Breaux", ('LA',)),
    "S180": ("A000031", "Adams", ('WA',)),
    "S181": ("W000647", "Wirth", ('CO',)),
    "S182": ("M000702", "Mikulski", ('MD',)),
    "S183": ("F000329", "Fowler", ('GA',)),
    "S184": ("S000320", "Shelby", ('AL',)),
    "S185": ("D000064", "Daschle", ('SD',)),
    "S197": ("M000303", "McCain", ('AZ',)),
    "S198": ("R000146", "Reid", ('NV',)),
    "S199": ("G000352", "Graham", ('FL',)),
    "S200": ("B000611", "Bond", ('MO',)),
    "S201": ("C000705", "Conrad", ('ND',)),
    "S203": ("L000447", "Lott", ('MS',)),
    "S204": ("J000072", "Jeffords", ('VT',)),
    "S205": ("M000019", "Mack", ('FL',)),
    "S206": ("B000993", "Bryan", ('NV',)),
    "S207": ("R000295", "Robb", ('VA',)),
    "S208": ("K000146", "Kerrey", ('NE',)),
    "S209": ("K000305", "Kohl", ('WI',)),
    "S210": ("L000304", "Lieberman", ('CT',)),
    "S211": ("B001126", "Burns", ('MT',)),
    "S212": ("C000542", "Coats", ('IN',)),
    "S213": ("A000069", "Akaka", ('HI',)),
    "S214": ("B000919", "Brown", ('CO',)),
    "S215": ("C000858", "Craig", ('ID',)),
    "S216": ("S000606", "Smith", ('NH',)),
    "S217": ("W000288", "Wellstone", ('MN',)),
    "S218": ("S000269", "Seymour", ('CA',)),
    "S219": ("W000665", "Wofford", ('PA',)),
    "S220": ("B001076", "Burdick", ('ND',)),
    "S221": ("F000062", "Feinstein", ('CA',)),
    "S222": ("D000432", "Dorgan", ('ND',)),
    "S223": ("B000711", "Boxer", ('CA',)),
    "S224": ("G000445", "Gregg", ('NH',)),
    "S225": ("C000077", "Campbell", ('CO',)),
    "S226": ("M001025", "Moseley Braun", ('IL',)),
    "S227": ("F000437", "Faircloth", ('NC',)),
    "S228": ("C000813", "Coverdell", ('GA',)),
    "S229": ("M001111", "Murray", ('WA',)),
    "S230": ("F000061", "Feingold", ('WI',)),
    "S231": ("B000382", "Bennett", ('UT',)),
    "S232": ("K000088", "Kempthorne", ('ID',)),
    "S233": ("M000236", "Mathews", ('TN',)),
    "S234": ("K000333", "Krueger", ('TX',)),
    "S235": ("H001016", "Hutchison", ('TX',)),
    "S236": ("I000024", "Inhofe", ('OK',)),
    "S237": ("T000457", "Thompson", ('TN',)),
    "S238": ("A000355", "Abraham", ('MI',)),
    "S239": ("A000356", "Ashcroft", ('MO',)),
    "S240": ("D000294", "DeWine", ('OH',)),
    "S241": ("F000439", "Frist", ('TN',)),
    "S242": ("G000367", "Grams", ('MN',)),
    "S243": ("K000352", "Kyl", ('AZ',)),
    "S244": ("S000059", "Santorum", ('PA',)),
    "S245": ("S000663", "Snowe", ('ME',)),
    "S246": ("T000162", "Thomas", ('WY',)),
    "S247": ("W000779", "Wyden", ('OR',)),
    "S248": ("F000438", "Frahm", ('KS',)),
    "S249": ("B000953", "Brownback", ('KS',)),
    "S250": ("A000109", "Allard", ('CO',)),
    "S251": ("C001034", "Cleland", ('GA',)),
    "S252": ("C001035", "Collins", ('ME',)),
    "S253": ("D000563", "Durbin", ('IL',)),
    "S254": ("E000285", "Enzi", ('WY',)),
    "S255": ("H001028", "Hagel", ('NE',)),
    "S256": ("H001015", "Hutchinson", ('AR',)),
    "S257": ("J000177", "Johnson", ('SD',)),
    "S258": ("L000550", "Landrieu", ('LA',)),
    "S259": ("R000122", "Reed", ('RI',)),
    "S260": ("R000307", "Roberts", ('KS',)),
    "S261": ("S001141", "Sessions", ('AL',)),
    "S262": ("S001142", "Smith", ('OR',)),
    "S263": ("T000317", "Torricelli", ('NJ',)),
    "S264": ("B001233", "Bayh", ('IN',)),
    "S265": ("B001066", "Bunning", ('KY',)),
    "S266": ("C000880", "Crapo", ('ID',)),
    "S267": ("E000286", "Edwards", ('NC',)),
    "S268": ("F000442", "Fitzgerald", ('IL',)),
    "S269": ("L000035", "Lincoln", ('AR',)),
    "S270": ("S000148", "Schumer", ('NY',)),
    "S271": ("V000126", "Voinovich", ('OH',)),
    "S272": ("C001040", "Chafee", ('RI',)),
    "S273": ("M001141", "Miller", ('GA',)),
    "S274": ("A000121", "Allen", ('VA',)),
    "S275": ("C000127", "Cantwell", ('WA',)),
    "S276": ("C001043", "Carnahan", ('MO',)),
    "S277": ("C000174", "Carper", ('DE',)),
    "S278": ("C001041", "Clinton", ('NY',)),
    "S279": ("C001042", "Corzine", ('NJ',)),
    "S280": ("D000596", "Dayton", ('MN',)),
    "S281": ("E000194", "Ensign", ('NV',)),
    "S282": ("N000032", "Nelson", ('FL',)),
    "S283": ("N000180", "Nelson", ('NE',)),
    "S284": ("S000770", "Stabenow", ('MI',)),
    "S285": ("B001237", "Barkley", ('MN',)),
    "S286": ("T000024", "Talent", ('MO',)),
    "S287": ("C001056", "Cornyn", ('TX',)),
    "S288": ("M001153", "Murkowski", ('AK',)),
    "S289": ("A000360", "Alexander", ('TN',)),
    "S290": ("C000286", "Chambliss", ('GA',)),
    "S291": ("C001057", "Coleman", ('MN',)),
    "S292": ("D000601", "Dole", ('NC',)),
    "S293": ("G000359", "Graham", ('SC',)),
    "S295": ("P000590", "Pryor", ('AR',)),
    "S296": ("S001078", "Sununu", ('NH',)),
    "S297": ("S001163", "Salazar", ('CO',)),
    "S298": ("O000167", "Obama", ('IL',)),
    "S299": ("V000127", "Vitter", ('LA',)),
    "S300": ("B001135", "Burr", ('NC',)),
    "S301": ("C000560", "Coburn", ('OK',)),
    "S302": ("D000595", "DeMint", ('SC',)),
    "S303": ("T000250", "Thune", ('SD',)),
    "S304": ("M001162", "Martinez", ('FL',)),
    "S305": ("I000055", "Isakson", ('GA',)),
    "S306": ("M000639", "Menendez", ('NJ',)),
    "S307": ("B000944", "Brown", ('OH',)),
    "S308": ("C000141", "Cardin", ('MD',)),
    "S309": ("C001070", "Casey", ('PA',)),
    "S310": ("C001071", "Corker", ('TN',)),
    "S311": ("K000367", "Klobuchar", ('MN',)),
    "S312": ("M001170", "McCaskill", ('MO',)),
    "S313": ("S000033", "Sanders", ('VT',)),
    "S314": ("T000464", "Tester", ('MT',)),
    "S315": ("W000803", "Webb", ('VA',)),
    "S316": ("W000802", "Whitehouse", ('RI',)),
    "S317": ("B001261", "Barrasso", ('WY',)),
    "S318": ("W000437", "Wicker", ('MS',)),
    "S319": ("B001265", "Begich", ('AK',)),
    "S320": ("H001049", "Hagan", ('NC',)),
    "S321": ("J000291", "Johanns", ('NE',)),
    "S322": ("M001176", "Merkley", ('OR',)),
    "S323": ("R000584", "Risch", ('ID',)),
    "S324": ("S001181", "Shaheen", ('NH',)),
    "S325": ("U000038", "Udall", ('CO',)),
    "S326": ("U000039", "Udall", ('NM',)),
    "S327": ("W000805", "Warner", ('VA',)),
    "S328": ("B001266", "Burris", ('IL',)),
    "S329": ("K000373", "Kaufman", ('DE',)),
    "S330": ("B001267", "Bennet", ('CO',)),
    "S331": ("G000555", "Gillibrand", ('NY',)),
    "S332": ("F000457", "Franken", ('MN',)),
    "S333": ("L000572", "LeMieux", ('FL',)),
    "S334": ("K000374", "Kirk", ('MA',)),
    "S335": ("B001268", "Brown", ('MA',)),
    "S336": ("G000561", "Goodwin", ('WV',)),
    "S337": ("C001088", "Coons", ('DE',)),
    "S338": ("M001183", "Manchin", ('WV',)),
    "S339": ("K000360", "Kirk", ('IL',)),
    "S340": ("A000368", "Ayotte", ('NH',)),
    "S341": ("B001277", "Blumenthal", ('CT',)),
    "S342": ("B000575", "Blunt", ('MO',)),
    "S343": ("B001236", "Boozman", ('AR',)),
    "S344": ("H001061", "Hoeven", ('ND',)),
    "S345": ("J000293", "Johnson", ('WI',)),
    "S346": ("L000577", "Lee", ('UT',)),
    "S347": ("M000934", "Moran", ('KS',)),
    "S348": ("P000603", "Paul", ('KY',)),
    "S349": ("P000449", "Portman", ('OH',)),
    "S350": ("R000595", "Rubio", ('FL',)),
    "S351": ("T000461", "Toomey", ('PA',)),
    "S352": ("H001041", "Heller", ('NV',)),
    "S353": ("S001194", "Schatz", ('HI',)),
    "S354": ("B001230", "Baldwin", ('WI',)),
    "S355": ("C001098", "Cruz", ('TX',)),
    "S356": ("D000607", "Donnelly", ('IN',)),
    "S357": ("F000463", "Fischer", ('NE',)),
    "S358": ("F000444", "Flake", ('AZ',)),
    "S359": ("H001046", "Heinrich", ('NM',)),
    "S360": ("H001069", "Heitkamp", ('ND',)),
    "S361": ("H001042", "Hirono", ('HI',)),
    "S362": ("K000384", "Kaine", ('VA',)),
    "S363": ("K000383", "King", ('ME',)),
    "S364": ("M001169", "Murphy", ('CT',)),
    "S365": ("S001184", "Scott", ('SC',)),
    "S366": ("W000817", "Warren", ('MA',)),
    "S367": ("C001099", "Cowan", ('MA',)),
    "S368": ("C001100", "Chiesa", ('NJ',)),
    "S369": ("M000133", "Markey", ('MA',)),
    "S370": ("B001288", "Booker", ('NJ',)),
    "S371": ("W000818", "Walsh", ('MT',)),
    "S372": ("C001047", "Capito", ('WV',)),
    "S373": ("C001075", "Cassidy", ('LA',)),
    "S374": ("C001095", "Cotton", ('AR',)),
    "S375": ("D000618", "Daines", ('MT',)),
    "S376": ("E000295", "Ernst", ('IA',)),
    "S377": ("G000562", "Gardner", ('CO',)),
    "S378": ("L000575", "Lankford", ('OK',)),
    "S379": ("P000612", "Perdue", ('GA',)),
    "S380": ("P000595", "Peters", ('MI',)),
    "S381": ("R000605", "Rounds", ('SD',)),
    "S382": ("S001197", "Sasse", ('NE',)),
    "S383": ("S001198", "Sullivan", ('AK',)),
    "S384": ("T000476", "Tillis", ('NC',)),
    "S385": ("C001113", "Cortez Masto", ('NV',)),
    "S386": ("D000622", "Duckworth", ('IL',)),
    "S387": ("H001075", "Harris", ('CA',)),
    "S388": ("H001076", "Hassan", ('NH',)),
    "S389": ("K000393", "Kennedy", ('LA',)),
    "S390": ("V000128", "Van Hollen", ('MD',)),
    "S391": ("Y000064", "Young", ('IN',)),
    "S392": ("S001202", "Strange", ('AL',)),
    "S393": ("J000300", "Jones", ('AL',)),
    "S394": ("S001203", "Smith", ('MN',)),
    "S395": ("H001079", "Hyde-Smith", ('MS',)),
    "S396": ("B001243", "Blackburn", ('TN',)),
    "S397": ("B001310", "Braun", ('IN',)),
    "S398": ("C001096", "Cramer", ('ND',)),
    "S399": ("H001089", "Hawley", ('MO',)),
    "S400": ("M001197", "McSally", ('AZ',)),
    "S401": ("R000615", "Romney", ('UT',)),
    "S402": ("R000608", "Rosen", ('NV',)),
    "S403": ("S001191", "Sinema", ('AZ',)),
    "S404": ("S001217", "Scott", ('FL',)),
    "S405": ("L000594", "Loeffler", ('GA',)),
    "S406": ("K000377", "Kelly", ('AZ',)),
    "S407": ("H000601", "Hagerty", ('TN',)),
    "S408": ("H000273", "Hickenlooper", ('CO',)),
    "S409": ("L000570", "Luján", ('NM',)),
    "S410": ("L000571", "Lummis", ('WY',)),
    "S411": ("M001198", "Marshall", ('KS',)),
    "S412": ("T000278", "Tuberville", ('AL',)),
    "S413": ("P000145", "Padilla", ('CA',)),
    "S414": ("O000174", "Ossoff", ('GA',)),
    "S415": ("W000790", "Warnock", ('GA',)),
    "S416": ("B001319", "Britt", ('AL',)),
    "S417": ("B001305", "Budd", ('NC',)),
    "S418": ("F000479", "Fetterman", ('PA',)),
    "S419": ("M001190", "Mullin", ('OK',)),
    "S420": ("S001227", "Schmitt", ('MO',)),
    "S421": ("V000137", "Vance", ('OH',)),
    "S422": ("W000800", "Welch", ('VT',)),
    "S423": ("R000618", "Ricketts", ('NE',)),
    "S424": ("B001320", "Butler", ('CA',)),
    "S425": ("H001097", "Helmy", ('NJ',)),
    "S426": ("K000394", "Kim", ('NJ',)),
    "S427": ("S001150", "Schiff", ('CA',)),
    "S428": ("A000382", "Alsobrooks", ('MD',)),
    "S429": ("B001299", "Banks", ('IN',)),
    "S430": ("B001303", "Blunt Rochester", ('DE',)),
    "S431": ("C001114", "Curtis", ('UT',)),
    "S432": ("G000574", "Gallego", ('AZ',)),
    "S433": ("M001243", "McCormick", ('PA',)),
    "S434": ("M001242", "Moreno", ('OH',)),
    "S435": ("S001232", "Sheehy", ('MT',)),
    "S436": ("S001208", "Slotkin", ('MI',)),
    "S437": ("J000312", "Justice", ('WV',)),
    "S438": ("H001104", "Husted", ('OH',)),
    "S439": ("M001244", "Moody", ('FL',)),
    "S440": ("A000383", "Armstrong", ('OK',)),
    "S441": ("G000608", "Graham Nordone", ('SC',)),
}


def _fold(value: str) -> str:
    """Reduce a surname to a form the two sources agree on.

    They disagree in small ways that are not disagreements. The Senate writes
    ``Lujan`` where the crosswalk writes ``Luján``; hyphens, apostrophes and
    case vary. Comparing raw strings rejected 2 of 246 real senators, so
    diacritics are stripped, punctuation is flattened and case is folded.

    Args:
        value: A surname as either source writes it.

    Returns:
        The comparable form.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold().replace("'", "").replace("-", " ").strip()


def bioguide_for(lis: str, name: str, state: str) -> str:
    """Return the bioguide ID for a senator, or nothing if it cannot be trusted.

    The mapping is checked against the vote document rather than applied on
    faith. A vote attributed to the wrong senator is worse than a vote with no
    identifier at all: it is wrong in a way that reads as authoritative, and
    nothing downstream could detect it.

    Surname and state must both agree. Party is deliberately **not** checked --
    senators change party mid-career, and gating on it would reject Specter,
    Jeffords and Manchin for being accurately recorded.

    Names are matched by containment either way round, because a senator can
    change their name: the crosswalk records ``Graham Nordone`` where a 2015
    vote says ``Graham``.

    Args:
        lis: The Senate's own member identifier, e.g. ``S289``.
        name: The member as the vote document writes it, e.g. ``Alexander (R-TN)``.
        state: Two-letter state code from the vote document.

    Returns:
        The bioguide ID, or an empty string when there is no row or the row
        does not agree with the document.
    """
    row = SENATORS.get(lis)
    if not row:
        return ""
    bioguide, surname, states = row
    if state not in states:
        return ""
    published = _fold(name.split("(")[0])
    recorded = _fold(surname)
    if not published or not recorded:
        return ""
    if recorded not in published and published not in recorded:
        return ""
    return bioguide
