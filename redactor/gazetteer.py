"""Word lists that encode the project's *scope policy*.

These lists are the difference between a redactor that works on a prospectus
and one that turns it into unreadable soup. They are plain data so that a
reviewer can audit the policy without reading any logic, and so that adapting
the tool to a new document domain is an edit here rather than a code change.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ORGANISATION scope policy
# ---------------------------------------------------------------------------
# Decision: public bodies, regulators, exchanges, depositories and statutes are
# NOT personally identifying. "SEBI" tells you nothing about who is involved in
# this transaction -- every Indian offer document names it. Redacting them
# would destroy the document's meaning for zero privacy gain, and would tank
# precision against any sensible ground truth. Commercial counterparties
# (issuer, bankers, auditors, law firms, vendors) ARE redacted.

PUBLIC_BODIES = {
    "securities and exchange board of india",
    "sebi",
    "reserve bank of india",
    "rbi",
    "bse limited",
    "bse",
    "national stock exchange of india limited",
    "nse",
    "stock exchanges",
    "registrar of companies",
    "roc",
    "ministry of corporate affairs",
    "government of india",
    "income tax department",
    "central board of direct taxes",
    "national securities depository limited",
    "nsdl",
    "central depository services (india) limited",
    "cdsl",
    "depositories",
    "insurance regulatory and development authority",
    "irdai",
    "competition commission of india",
    "supreme court of india",
    "high court",
    "national company law tribunal",
    "nclt",
    "employees provident fund organisation",
    "epfo",
    "unique identification authority of india",
    "uidai",
    "international monetary fund",
    "world bank",
    "united nations",
}

#: Statutes and regulations -- named laws, never PII.
LEGISLATION_MARKERS = {
    "act",
    "regulations",
    "rules",
    "notification",
    "circular",
    "amendment",
    "code",
    "scheme",
    "policy",
    "guidelines",
    "convention",
    "treaty",
}

#: Suffixes strong enough to identify a company on their own. These are legal
#: forms -- a phrase ending in "Private Limited" is a registered entity.
#:
#: Deliberately EXCLUDED: Bank, Capital, Securities, Industries, Holdings,
#: Ventures, Solutions. Those are real parts of company names, but in an offer
#: document they far more often end a *defined term* -- "Refund Bank",
#: "Escrow Collection Bank", "Venture Capital", "Public Offer Account Bank".
#: Nothing is lost by excluding them, because the companies that use those
#: words in this document also carry a legal form ("HDFC Bank Limited"), and
#: the alias mechanism below re-registers the short form from the long one.
STRONG_ORG_SUFFIXES = [
    "private limited", "pvt. ltd.", "pvt ltd", "limited", "ltd.", "ltd",
    "llp", "inc.", "inc", "corporation", "corp.", "incorporated", "plc",
    "gmbh", "s.a.", "b.v.", "n.v.", "co., ltd.", "& co.", "and co.",
    "& sons", "& associates", "and associates", "& partners", "and partners",
    "family trust", "chartered accountants",
]

#: Single generic nouns that must not stand alone before a suffix: "Bank
#: Limited" is a fragment, "Vedanta Limited" is a company.
GENERIC_HEAD_NOUNS = {
    "bank", "account", "company", "corporation", "trust", "fund", "group",
    "escrow", "refund", "syndicate", "collection", "investment", "advisory",
    "sponsor", "designated", "registrar", "underwriter", "co", "term",
    "branch", "office", "division", "unit", "entity", "firm", "party",
}

#: Full suffix list, used only for stripping aliases off harvested names.
ORG_SUFFIXES = [
    "private limited",
    "pvt. ltd.",
    "pvt ltd",
    "limited",
    "ltd.",
    "ltd",
    "llp",
    "inc.",
    "inc",
    "corporation",
    "corp.",
    "incorporated",
    "plc",
    "gmbh",
    "s.a.",
    "b.v.",
    "n.v.",
    "co., ltd.",
    "& co.",
    "and co.",
    "& sons",
    "& associates",
    "and associates",
    "& partners",
    "and partners",
    "family trust",
    "trust",
    "bank",
    "securities",
    "capital",
    "holdings",
    "ventures",
    "industries",
    "enterprises",
    "technologies",
    "solutions",
    "consultants",
    "chartered accountants",
]

# ---------------------------------------------------------------------------
# PERSON scope policy
# ---------------------------------------------------------------------------

HONORIFICS = [
    "mr.", "mr", "mrs.", "mrs", "ms.", "ms", "dr.", "dr", "prof.", "prof",
    "shri", "smt.", "smt", "sri", "kum.", "justice", "hon'ble",
]

#: Job titles / roles that sit next to a person's name in offer documents.
#: Used as a positive context signal when harvesting name candidates.
PERSON_CONTEXT_KEYWORDS = [
    "contact person", "company secretary", "compliance officer",
    "managing director", "whole-time director", "whole time director",
    "independent director", "executive director", "non-executive director",
    "chairman", "chairperson", "director", "chief executive officer",
    "chief financial officer", "chief operating officer", "ceo", "cfo", "coo",
    "promoter", "promoters", "partner", "proprietor", "signatory",
    "authorised signatory", "authorized signatory", "key managerial personnel",
    "senior management", "shareholder", "nominee", "trustee", "auditor",
    "relationship manager", "grievance officer", "investor relations",
    "s/o", "d/o", "w/o", "son of", "daughter of", "wife of",
    "aged", "years old", "resident of", "residing at",
]

#: If ANY token of a capitalised candidate phrase appears here, the phrase is
#: not a person's name. This is the primary precision guard for the PERSON
#: recognizer in a document where domain jargon is heavily title-cased.
NON_PERSON_TOKENS = {
    # Offer-document jargon
    "offer", "offers", "offering", "prospectus", "herring", "red", "draft",
    "equity", "share", "shares", "shareholder", "shareholders", "stock",
    "issue", "issuer", "fresh", "sale", "bid", "bidder", "bidders", "bidding",
    "anchor", "investor", "investors", "institutional", "retail", "individual",
    "qualified", "buyers", "allotment", "allottee", "book", "building",
    "running", "lead", "manager", "managers", "registrar", "syndicate",
    "underwriter", "underwriting", "escrow", "banker", "bankers", "refund",
    "price", "floor", "cap", "band", "face", "value", "premium", "discount",
    "lot", "size", "proceeds", "objects", "utilisation", "utilization",
    "company", "companies", "limited", "ltd", "private", "public", "board",
    "committee", "meeting", "resolution", "articles", "association",
    "memorandum", "certificate", "incorporation", "registered", "corporate",
    "office", "act", "regulations", "rules", "section", "regulation",
    "schedule", "annexure", "chapter", "clause", "part", "page", "note",
    "notes", "financial", "statements", "statement", "restated", "audited",
    "consolidated", "standalone", "profit", "loss", "balance", "sheet",
    "cash", "flow", "revenue", "operations", "ebitda", "margin", "ratio",
    "risk", "factors", "management", "discussion", "analysis", "business",
    "industry", "overview", "history", "certain", "matters", "capital",
    "structure", "dividend", "policy", "taxation", "legal", "proceedings",
    "regulatory", "statutory", "disclosures", "terms", "procedure",
    "description", "rights", "general", "information", "definitions",
    "abbreviations", "presentation", "currency", "exchange", "rates",
    "forward", "looking", "summary", "document", "basis", "capitalisation",
    "capitalization", "dilution", "peer", "group", "comparison",
    "subsidiary", "subsidiaries", "associate", "joint", "venture", "group",
    "promoter", "promoters", "selling", "trust", "trusts",
    # Geography that would otherwise look like surnames
    "india", "indian", "maharashtra", "pune", "mumbai", "bombay", "delhi",
    "gujarat", "karnataka", "bengaluru", "bangalore", "chennai", "kolkata",
    "hyderabad", "madhya", "pradesh", "bhopal", "chakan", "baner", "khed",
    "state", "states", "district", "taluka", "village", "road", "street",
    "lane", "nagar", "colony", "society", "apartment", "tower", "centre",
    "center", "park", "phase", "sector", "plot", "floor", "building",
    "united", "kingdom", "america", "states", "europe", "china", "asia",
    # Calendar
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "fiscal", "year",
    # Institutions that are allowlisted anyway
    "securities", "board", "reserve", "national", "stock", "exchange",
    "government", "ministry", "department", "authority", "tribunal", "court",
    # Job titles and table headers -- these sit *next to* names, so without
    # them the context harvester happily learns "Managing Director" as a person.
    "contact", "person", "officer", "officers", "compliance", "secretary",
    "executive", "managing", "independent", "chief", "key", "managerial",
    "personnel", "chairman", "chairperson", "auditor", "auditors", "statutory",
    "designated", "intermediaries", "intermediary", "sponsor", "banks",
    "banker", "syndicate", "member", "members", "trustee", "nominee",
    "relationship", "grievance", "redressal", "signatory", "advisor",
    "advisory", "consultant", "counsel", "solicitor", "employee", "employees",
    "staff", "workforce", "team", "personnel",
    # Section headings and boilerplate that is title-cased or shouted
    "requirements", "requirement", "responsibility", "absolute", "disclosure",
    "developments", "litigation", "outstanding", "material", "report",
    "reports", "registration", "number", "newspaper", "daily", "working",
    "days", "day", "data", "market", "facility", "facilities", "unit",
    "units", "plant", "contracts", "contract", "agreement", "agreements",
    "deed", "capacity", "products", "product", "customers", "customer",
    "suppliers", "supplier", "properties", "property", "insurance",
    "employees", "awards", "accreditations", "objects", "restated",
    "director", "directors", "whole", "time", "term", "tenure", "sitting",
    "fees", "fee", "remuneration", "commission", "salary", "compensation",
    "website", "websites", "email", "e-mail", "telephone", "phone", "fax",
    "address", "contact", "media", "finalizing", "upi", "circulars",
    "circular", "reservation", "among", "bidding", "revision", "withdrawal",
    "acquisition", "amount", "per", "based", "payment", "expense", "closing",
    "opening", "date", "dates", "weighted", "average", "cost", "value",
    "gymkhana", "farms", "chowk", "wadi", "peth", "pimpri", "chinchwad",
    # Acronyms of allowlisted bodies -- they cling to the end of a real name
    # when a table cell runs two fields together.
    "sebi", "bse", "nse", "rbi", "roc", "nsdl", "cdsl", "irdai", "mca",
    "fema", "icdr", "lodr", "sci", "nclt", "gst", "pan", "tan", "din", "cin",
    "chartered", "accountants", "accountant", "advocates", "advocate",
    "practising", "practicing", "firm", "associates", "consultancy",
}

#: Tokens that mark a capitalised phrase as a company even when it carries no
#: legal suffix -- "Kushal Electricals", "Waterloo Motors", "Parijat
#: Foundation". Without these the person harvester claims them, which redacts
#: the right characters under the wrong label and corrupts per-type metrics.
ORG_INDICATOR_TOKENS = {
    "electricals", "electronics", "motors", "foundation", "engineering",
    "exports", "imports", "traders", "trading", "steel", "metals", "auto",
    "pharma", "infra", "projects", "realty", "estates", "logistics",
    "packaging", "plastics", "textiles", "chemicals", "polymers", "wires",
    "cables", "components", "instruments", "machinery", "equipments",
    "mills", "works", "foods", "agro", "labs", "laboratories", "healthcare",
    "hospitals", "resorts", "hotels", "developers", "builders", "constructions",
}

#: Tokens that must not immediately precede a company suffix. Without this,
#: "Equity Share capital" and "Issue of Capital" are read as company names,
#: because "capital" is a legitimate suffix in "Northwind Capital".
ORG_LEADING_STOPWORDS = {
    "equity", "share", "shares", "issue", "issued", "offer", "offered",
    "offering", "indian", "india", "private", "public", "paid", "post",
    "pre", "respective", "websites", "website", "accordance", "total", "net",
    "gross", "aggregate", "entire", "foreign", "domestic", "listed",
    "unlisted", "relevant", "applicable", "material", "said", "such",
    "certain", "various", "other", "above", "below", "working", "share",
    "authorised", "authorized", "subscribed", "voting", "preference",
}

#: Words that a match may pick up at the start of a company name.
ORG_LEADING_NOISE = {"formerly", "erstwhile", "namely", "viz", "and", "or", "of", "to",
                     "the", "our", "its", "their", "by", "with", "from", "for", "at", "in",
                     "company", "issuer", "between", "among", "including", "such", "as"}

#: Indian state / union territory names, used to bound address spans.
INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal", "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry",
    "Chandigarh", "Andaman and Nicobar Islands", "Lakshadweep", "Daman",
]

#: Tokens that commonly open or appear inside an Indian postal address.
ADDRESS_MARKERS = [
    "road", "street", "lane", "marg", "nagar", "colony", "society", "chowk",
    "cross", "sector", "phase", "block", "plot", "survey", "s. no", "s.no",
    "gat no", "village", "taluka", "tehsil", "district", "po", "p.o",
    "apartment", "apartments", "bunglow", "bungalow", "tower", "building",
    "complex", "centre", "center", "estate", "industrial", "area", "park",
    "floor", "wing", "opposite", "near", "behind", "off",
]
