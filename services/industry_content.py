"""
Industry-specific content for the network social proof report.

The network report shows non-subscribers what en place would have caught
at peer organizations. Content needs to feel native to the reader's
industry; a service care provider seeing restaurant cautionary tales
breaks the demo immediately.

Indexed by `industry` (matches organizations.industry column):
  - 'restaurant'   (default, original en place vertical)
  - 'service_care' (IDD/HCBS, home health, behavioral health, group homes,
                    remote support services)

Adding a new industry:
  1. Define <INDUSTRY>_TALES list (~25-30 stories, same dict shape)
  2. Define <INDUSTRY>_CATEGORY_SUMMARIES list (5 entries)
  3. Add both to the lookup dicts at the bottom
  4. No changes needed in dashboard_service.py
"""


# ════════════════════════════════════════════════════════════════════════════
# RESTAURANT
# ════════════════════════════════════════════════════════════════════════════
# Lifted verbatim from dashboard_service.py _generate_network_report.

RESTAURANT_TALES = [
    {
        "name": "Snookie's Cookies",
        "location": "Atlantic City, NJ",
        "category": "harassment",
        "story": "Staff had been anonymously journaling concerns about an inappropriate relationship between the GM and assistant manager for six straight months. The signals were consistent, escalating, and clear to anyone looking.",
        "result": "Both managers fired, restaurant now facing a $40,000 sexual harassment lawsuit, and the whole team demoralized.",
        "lead_time": "six months"
    },
    {
        "name": "Bayou Brew House",
        "location": "New Orleans, LA",
        "category": "theft",
        "story": "Repeated anonymous notes about a bartender skimming cash on late shifts. The pattern was clear across multiple staff check-ins.",
        "result": "$18,000 missing, police report filed, team trust destroyed.",
        "lead_time": "four months"
    },
    {
        "name": "Harbor Light Oyster Bar",
        "location": "Portland, ME",
        "category": "bullying",
        "story": "New server repeatedly targeted with hostile 'hazing' by senior staff. The pattern showed up in her check-ins and her coworkers' notes.",
        "result": "Best server walked, posted a viral Glassdoor review that tanked hiring for months.",
        "lead_time": "three months"
    },
    {
        "name": "Mesa Verde Cantina",
        "location": "Austin, TX",
        "category": "harassment",
        "story": "Multiple mentions of a manager making sexually suggestive comments to BOH staff. The pattern repeated across different employees.",
        "result": "Title VII lawsuit, $65,000 settlement, restaurant rebrand attempt.",
        "lead_time": "five months"
    },
    {
        "name": "Iron Skillet Tavern",
        "location": "Pittsburgh, PA",
        "category": "theft",
        "story": "Consistent notes about missing product and one closer 'helping himself.' Multiple staff noticed independently.",
        "result": "$22,000 in inventory shrinkage, two staff terminated, owner had to absorb the loss.",
        "lead_time": "five months"
    },
    {
        "name": "Crescent Moon Bistro",
        "location": "Charleston, SC",
        "category": "substance",
        "story": "Repeated complaints of a cook showing up impaired on weekend closes. Coworkers were worried but didn't know who to tell.",
        "result": "Kitchen fire that closed the restaurant for three weeks, insurance investigation flagged staffing decisions.",
        "lead_time": "four months"
    },
    {
        "name": "Beachcomber Grill",
        "location": "Miami, FL",
        "category": "bullying",
        "story": "Assistant GM verbally berating hosts on the floor — same names showing up in check-ins every week.",
        "result": "Three host resignations in a month, owner had to bring in temp hosts and pay overtime, customer service ratings dropped 20%.",
        "lead_time": "two months"
    },
    {
        "name": "Sakura House",
        "location": "San Francisco, CA",
        "category": "threats",
        "story": "Sushi chef repeatedly threatening BOH staff during high-volume service. The escalation was visible in the notes.",
        "result": "Physical altercation in walk-in, police called, restaurant lost half its kitchen team in a week.",
        "lead_time": "three months"
    },
    {
        "name": "Stovepipe Diner",
        "location": "Denver, CO",
        "category": "theft",
        "story": "Pattern of tip-pool money coming up short on closing manager's shifts. Staff noticed but felt powerless.",
        "result": "$11,000 in tip theft confirmed, Department of Labor investigation, owner personally liable for back wages.",
        "lead_time": "six months"
    },
    {
        "name": "Hearthstone Pub",
        "location": "Boston, MA",
        "category": "bullying",
        "story": "Senior line cook targeting new hires with aggressive 'kitchen culture.' The new hires wrote about it. The veterans normalized it.",
        "result": "Six new hires quit within their first three months, hiring costs alone exceeded $18,000 for the year.",
        "lead_time": "four months"
    },
    {
        "name": "Tideline Seafood",
        "location": "Seattle, WA",
        "category": "substance",
        "story": "Bartender repeatedly overserving themselves on shift. Other staff mentioned it but management never saw the notes.",
        "result": "DUI accident on the way home from a shift, civil suit against the restaurant for failing to intervene, $200,000 settlement.",
        "lead_time": "four months"
    },
    {
        "name": "Goldenrod Grill",
        "location": "Nashville, TN",
        "category": "harassment",
        "story": "GM making inappropriate advances toward servers after close. The pattern was documented for half a year.",
        "result": "Three former servers filed a joint complaint, mandatory training, GM terminated, restaurant on the front page of the local paper.",
        "lead_time": "six months"
    },
    {
        "name": "Cobblestone Cafe",
        "location": "Philadelphia, PA",
        "category": "theft",
        "story": "Repeated notes about cash drawer discrepancies tied to one closer. The math didn't add up and staff knew it.",
        "result": "Closer arrested, $13,500 recovered, but restaurant lost three months while owner negotiated insurance reimbursement.",
        "lead_time": "five months"
    },
    {
        "name": "Tumbleweed Roadhouse",
        "location": "Phoenix, AZ",
        "category": "bullying",
        "story": "Kitchen lead bullying dish team nightly. The dish crew wrote about it constantly. Nobody with authority ever read it.",
        "result": "Entire dish team walked out on a Saturday night, restaurant had to close mid-service, lost $8,000 in covers.",
        "lead_time": "three months"
    },
    {
        "name": "Ridgepoint Brewery",
        "location": "Asheville, NC",
        "category": "bullying",
        "story": "Manager favoring certain staff with shifts while freezing others out. The fairness complaints escalated into something worse.",
        "result": "Three staff filed wrongful termination claims, $35,000 in legal fees, restaurant culture took two years to recover.",
        "lead_time": "four months"
    },
    {
        "name": "Five Anchors Tavern",
        "location": "Baltimore, MD",
        "category": "substance",
        "story": "Closer showing up impaired multiple weekends. Coworkers covered for them until they couldn't.",
        "result": "Robbery during closing shift, employee couldn't operate the alarm correctly while impaired, $25,000 stolen.",
        "lead_time": "three months"
    },
    {
        "name": "Magnolia Smokehouse",
        "location": "Memphis, TN",
        "category": "harassment",
        "story": "Repeated harassment of female servers by a senior cook. Everyone in the kitchen knew. Management didn't.",
        "result": "Two servers filed Title VII complaints, $90,000 settlement, kitchen completely restructured, restaurant lost a Michelin recognition bid.",
        "lead_time": "six months"
    },
    {
        "name": "Old Pier Pizza",
        "location": "San Diego, CA",
        "category": "theft",
        "story": "Repeated notes about food orders not matching POS rings. One employee was always on shift when it happened.",
        "result": "Comp ring theft confirmed at $14,000, employee terminated, owner had to invest $9,000 in new POS audit tools.",
        "lead_time": "four months"
    },
    {
        "name": "Whitepine Steakhouse",
        "location": "Chicago, IL",
        "category": "bullying",
        "story": "Front-of-house manager publicly humiliating servers in pre-shift. Pattern repeated across check-ins for months.",
        "result": "Two senior servers quit within the same week, restaurant struggled with weekend coverage for a quarter.",
        "lead_time": "five months"
    },
    {
        "name": "Sunset Boulevard Bistro",
        "location": "Los Angeles, CA",
        "category": "theft",
        "story": "Notes flagging that one expo always seemed to have 'extras' going to a friend at the bar. Staff suspected comping fraud.",
        "result": "Internal audit found $7,200 in unauthorized comps over four months, expo terminated, bar staff retrained.",
        "lead_time": "four months"
    },
    {
        "name": "Cedar Bluff Grill",
        "location": "Knoxville, TN",
        "category": "threats",
        "story": "Tension between two line cooks escalating in notes. Multiple staff mentioned the verbal threats during service.",
        "result": "One cook pulled a knife during service, restaurant evacuated, both terminated, ServSafe certifications under review.",
        "lead_time": "two months"
    },
    {
        "name": "Veranda Wine Bar",
        "location": "Sonoma, CA",
        "category": "substance",
        "story": "Multiple staff noting that the sommelier was tasting heavily during shifts. Pattern documented across weekly check-ins.",
        "result": "Sommelier crashed company car after a shift, $40,000 in damages plus civil liability, license suspended.",
        "lead_time": "three months"
    },
    {
        "name": "Ironwood Tavern",
        "location": "Madison, WI",
        "category": "bullying",
        "story": "Bartenders 'icing out' a new hire from training and tip pool fairness. The new hire's check-ins documented it for weeks.",
        "result": "New hire quit and reported the tip pool issues to DOL, $12,000 in back wages owed, two bartenders terminated.",
        "lead_time": "two months"
    },
    {
        "name": "Salty Dog Diner",
        "location": "Annapolis, MD",
        "category": "theft",
        "story": "Patterns of inventory loss tied to one specific shift's closer. Multiple staff noted the discrepancies independently.",
        "result": "$8,400 in product theft confirmed via security footage, employee terminated and charged.",
        "lead_time": "four months"
    },
    {
        "name": "Burnt Cork Brewery",
        "location": "Richmond, VA",
        "category": "harassment",
        "story": "Brewer making comments about a server's appearance in front of other staff. Pattern noted by multiple coworkers.",
        "result": "Server filed hostile work environment claim, $30,000 settlement, brewery now requires HR-led harassment training quarterly.",
        "lead_time": "three months"
    },
    {
        "name": "Mariana's Trattoria",
        "location": "Houston, TX",
        "category": "bullying",
        "story": "Sous chef screaming at line cooks during peak service nightly. Notes flagged it for months.",
        "result": "Two line cooks resigned with formal complaints, sous chef demoted, restaurant lost three weeks of dinner service to retraining.",
        "lead_time": "five months"
    },
    {
        "name": "Lighthouse Lobster Co.",
        "location": "Bar Harbor, ME",
        "category": "theft",
        "story": "Multiple anonymous notes about cash sales that never made it to the bank deposit. One specific manager always closed those nights.",
        "result": "$22,000 stolen over six months, manager arrested, restaurant absorbed insurance deductible plus loss.",
        "lead_time": "six months"
    },
    {
        "name": "Twin Oaks Bistro",
        "location": "Atlanta, GA",
        "category": "substance",
        "story": "Notes from coworkers about a closing manager 'self-medicating' from the bar. Pattern across multiple Friday/Saturday closes.",
        "result": "Manager fell during inventory and broke his wrist, workers comp investigation revealed BAC at 0.18, claim denied, civil suit pending.",
        "lead_time": "two months"
    },
    {
        "name": "Bridgewater Tavern",
        "location": "Minneapolis, MN",
        "category": "bullying",
        "story": "Manager humiliating BOH staff in front of customers when food was sent back. Pattern showed up in three different staff's notes.",
        "result": "Two cooks walked mid-shift on a Friday, restaurant comped 80% of dinner covers, owner lost $11,000 that night alone.",
        "lead_time": "two months"
    },
    {
        "name": "Mesa Verde Cantina",
        "location": "Santa Fe, NM",
        "category": "theft",
        "story": "Pattern of missing cash tied to one manager's closing shifts. The team talked. Management didn't listen.",
        "result": "$15,000 embezzled, cantina lost half its night staff overnight.",
        "lead_time": "three months"
    }
]


RESTAURANT_CATEGORY_SUMMARIES = [
    {
        "category": "Harassment",
        "signals_prevented": 31,
        "example": "Multiple staff mentioned uncomfortable behavior from a closing manager → Issue addressed before formal complaint filed"
    },
    {
        "category": "Bullying",
        "signals_prevented": 26,
        "example": "New hire repeatedly targeted by senior staff → Manager intervention stopped resignation and rebuilt team trust"
    },
    {
        "category": "Theft",
        "signals_prevented": 19,
        "example": "Pattern of tip-pool discrepancies flagged across check-ins → Investigation recovered $2,400"
    },
    {
        "category": "Threats",
        "signals_prevented": 14,
        "example": "Escalating tension between BOH staff detected → Schedules adjusted before physical altercation"
    },
    {
        "category": "Substance Concerns",
        "signals_prevented": 10,
        "example": "Corroborated notes about impaired coworker on closes → Employee connected to support resources"
    }
]


# ════════════════════════════════════════════════════════════════════════════
# SERVICE CARE
# ════════════════════════════════════════════════════════════════════════════
# IDD/HCBS providers, home health, behavioral health support, group homes,
# remote support services. Categories shifted from the restaurant set to
# reflect what actually matters in this vertical: client-directed abuse and
# neglect are the highest-stakes incident types and don't map to the
# restaurant categories cleanly.

SERVICE_CARE_TALES = [
    {
        "name": "Bridgepoint Living",
        "location": "Akron, OH",
        "category": "substance",
        "story": "Multiple anonymous notes about a senior DSP smelling like alcohol on overnight shifts. Coworkers raised concerns repeatedly across three months.",
        "result": "Client found wandering at 3am while DSP slept on shift. State investigation, agency placed on corrective action plan, DSP terminated.",
        "lead_time": "three months"
    },
    {
        "name": "Cornerstone Pathways",
        "location": "Albuquerque, NM",
        "category": "bullying",
        "story": "Pattern of one House Manager always assigning best shifts to her two friends. Other DSPs flagged it across check-ins for over four months.",
        "result": "Three top performers transferred to a competitor agency, retention crisis at the site, hiring costs ballooned $14,000 in one quarter.",
        "lead_time": "four months"
    },
    {
        "name": "Magnolia Care Group",
        "location": "Birmingham, AL",
        "category": "theft",
        "story": "RN's medication count discrepancies were noted by relief nurses repeatedly. Pattern was clear in shift handoff notes.",
        "result": "Diversion of controlled substances confirmed. Felony charges, license revoked, DEA investigation of the agency's full medication program.",
        "lead_time": "five months"
    },
    {
        "name": "Hilltop Community Services",
        "location": "Boise, ID",
        "category": "bullying",
        "story": "New DSP repeatedly assigned the most behaviorally complex client by senior staff. Comments about 'earning her stripes' appeared in multiple notes.",
        "result": "New DSP quit after eight weeks, posted a detailed account on Reddit, agency lost two prospective hires when they saw it during interview prep.",
        "lead_time": "two months"
    },
    {
        "name": "Riverside Independent Living",
        "location": "Charleston, SC",
        "category": "theft",
        "story": "Client's family flagged missing items. Pattern of one DSP being on shift each time something disappeared appeared across multiple staff notes.",
        "result": "$2,800 in stolen client property recovered, DSP charged with felony exploitation of a vulnerable adult, agency settled with family for $25,000.",
        "lead_time": "three months"
    },
    {
        "name": "Oakhaven Residential",
        "location": "Cincinnati, OH",
        "category": "abuse",
        "story": "DSPs repeatedly noted that one coworker spoke to a non-verbal client in demeaning tones and used physical 'redirection' beyond protocol.",
        "result": "Hidden camera footage confirmed the pattern. Felony abuse charges, agency lost Medicaid certification at that site for 90 days.",
        "lead_time": "six months"
    },
    {
        "name": "Maple Ridge Supports",
        "location": "Des Moines, IA",
        "category": "theft",
        "story": "Multiple DSPs flagged that one staff member was pre-filling daily progress notes hours before shift end. Pattern documented in coworker observations.",
        "result": "State audit caught it, agency fined $35,000, all backdated records had to be reconstructed and re-billed to Medicaid.",
        "lead_time": "four months"
    },
    {
        "name": "Vista Behavioral Health",
        "location": "Eugene, OR",
        "category": "harassment",
        "story": "Notes from multiple DSPs flagged a coordinator using racial slurs about a Black client when supervisors weren't present. Pattern visible across check-ins.",
        "result": "Coordinator fired, EEOC complaint filed by three witnessing DSPs, agency settled for $80,000 plus mandatory anti-bias training.",
        "lead_time": "four months"
    },
    {
        "name": "Compass Care",
        "location": "Fargo, ND",
        "category": "abuse",
        "story": "DSPs noticed one coworker exchanging personal phone numbers with a high-functioning client and arranging visits outside of shifts. Pattern repeated.",
        "result": "Investigation found inappropriate relationship with a vulnerable adult. DSP terminated, mandatory state reporting, family pursued civil action.",
        "lead_time": "three months"
    },
    {
        "name": "Beaconwood Services",
        "location": "Greensboro, NC",
        "category": "bullying",
        "story": "Notes consistently flagged mandation patterns where the same DSPs were forced into 16-hour double shifts every week. Burnout signals were unmissable.",
        "result": "DSP fell asleep on shift, client had unmonitored seizure, ER visit. State mandated staffing ratio review, agency required to hire 12 additional DSPs.",
        "lead_time": "four months"
    },
    {
        "name": "Ironwood Group Homes",
        "location": "Honolulu, HI",
        "category": "neglect",
        "story": "Overnight shift handoffs repeatedly mentioned one DSP 'always' being asleep at the 3am rounds. Pattern across multiple weeks.",
        "result": "Client medical emergency went unnoticed for 90 minutes. State citation, agency required to install monitoring systems at five sites.",
        "lead_time": "six weeks"
    },
    {
        "name": "Fielding Pathways",
        "location": "Indianapolis, IN",
        "category": "theft",
        "story": "Coworker comments about one DSP 'leaving early but logging full shifts' appeared across multiple weekly notes.",
        "result": "Time card audit recovered $14,000 in fraudulent wages over 18 months. Termination plus civil restitution, agency had to overhaul time tracking.",
        "lead_time": "five months"
    },
    {
        "name": "Hearthstone Homes",
        "location": "Jacksonville, FL",
        "category": "bullying",
        "story": "After a DSP reported witnessing client neglect, manager began assigning her undesirable shifts and writing her up for minor things. Pattern visible in her notes and others'.",
        "result": "EEOC retaliation complaint, $40,000 settlement, manager terminated, mandatory whistleblower protection training across all sites.",
        "lead_time": "two months"
    },
    {
        "name": "Whitepine Living",
        "location": "Kansas City, MO",
        "category": "substance",
        "story": "Three different DSPs across two months noted a coworker's slurred speech and alcohol smell on weekend overnight shifts. Concerns went up but never down.",
        "result": "DSP on shift when client fell and broke a hip. Toxicology confirmed alcohol. Termination, lawsuit, agency premiums increased 22% the following year.",
        "lead_time": "two months"
    },
    {
        "name": "Liberty Bell Supports",
        "location": "Lansing, MI",
        "category": "abuse",
        "story": "Multiple BCBA observation notes flagged that one DSP was not following the prescribed behavior plan, escalating client behaviors instead of de-escalating.",
        "result": "Three client elopement incidents traced to the same DSP's shifts. Termination, retraining mandate from state, behavior plan refresher required for the entire agency.",
        "lead_time": "three months"
    },
    {
        "name": "Sunridge Community",
        "location": "Madison, WI",
        "category": "harassment",
        "story": "Several female DSPs separately noted unwanted comments and touching from a male coworker on overnight shifts. Pattern emerged from independent notes.",
        "result": "Hostile work environment lawsuit, $120,000 settlement, mandatory harassment training across all sites, two female DSPs left the field entirely.",
        "lead_time": "four months"
    },
    {
        "name": "Old Mill Residential",
        "location": "Norfolk, VA",
        "category": "abuse",
        "story": "Notes from multiple staff flagged improper restraint use by one DSP that escalated to bruising on a client. Pattern across three incidents.",
        "result": "Felony abuse charges, mandatory CPI re-certification for the entire agency, revocation of one site's license.",
        "lead_time": "two months"
    },
    {
        "name": "Pacific Crossing",
        "location": "Olympia, WA",
        "category": "substance",
        "story": "DSPs reported missing pills from medication counts that always coincided with one staff member's shifts. Pattern documented across two months.",
        "result": "Personal use of client medications confirmed via testing. License revoked, criminal charges, agency reviewed all medication management systems.",
        "lead_time": "three months"
    },
    {
        "name": "Quarry Hill Services",
        "location": "Pittsburgh, PA",
        "category": "bullying",
        "story": "Site Coordinator created a hostile environment for new DSPs through public criticism, impossible expectations, and exclusion. Pattern visible across many staff notes.",
        "result": "Annual turnover hit 184% at that site. Coordinator demoted, retention specialist hired, retention recovered over six months but agency lost a major contract.",
        "lead_time": "six months"
    },
    {
        "name": "Redbud Living",
        "location": "Raleigh, NC",
        "category": "harassment",
        "story": "DSP repeatedly posted photos of group home activities on personal social media. Other DSPs flagged this multiple times across notes.",
        "result": "HIPAA violation reported, mandatory training across all sites, $7,500 individual fine for the DSP, federal complaint filed.",
        "lead_time": "two months"
    },
    {
        "name": "Shore Light Behavioral",
        "location": "St. Louis, MO",
        "category": "bullying",
        "story": "Senior DSPs intentionally giving newest hires the most physically demanding clients without proper training or warning. Pattern documented across notes.",
        "result": "New hire injured during a behavior incident, workers comp claim, OSHA complaint, mandatory training reorganization across the agency.",
        "lead_time": "three months"
    },
    {
        "name": "Tall Pines Group",
        "location": "Tampa, FL",
        "category": "theft",
        "story": "Items repeatedly missing from clients' rooms. One DSP's shifts always coincided with the disappearances. Multiple staff noticed independently.",
        "result": "Stolen client property worth $4,200 found in DSP's car. Felony charges, license revoked, families pursued civil action against the agency.",
        "lead_time": "four months"
    },
    {
        "name": "Underwood Care",
        "location": "Tucson, AZ",
        "category": "harassment",
        "story": "Assistant Program Director making sexual comments to female DSPs in private meetings. Pattern emerged across notes from three different employees.",
        "result": "Multiple Title VII complaints, $200,000 settlement, executive director also terminated for inaction, agency board overhaul.",
        "lead_time": "five months"
    },
    {
        "name": "Vinland Services",
        "location": "Virginia Beach, VA",
        "category": "bullying",
        "story": "Pattern of one DSP being given desk-coverage shifts (low difficulty) while others got mandation. Romantic relationship with the scheduler suspected, noted by multiple staff.",
        "result": "Internal investigation confirmed the undisclosed relationship. Both terminated, scheduling system overhauled with full audit logs.",
        "lead_time": "three months"
    },
    {
        "name": "Whitewater Living",
        "location": "Wichita, KS",
        "category": "bullying",
        "story": "Two DSPs verbally arguing in front of clients during shifts, escalating tensions. Pattern of conflicts documented across notes.",
        "result": "One client's behavioral regression noted in BCBA reports tied directly to specific staff conflicts. Mandatory conflict resolution, both DSPs reassigned.",
        "lead_time": "two months"
    },
    {
        "name": "Cypress Hollow Services",
        "location": "Atlanta, GA",
        "category": "theft",
        "story": "DSPs noted that one supervisor was signing off on training records without staff actually completing the modules. Pattern flagged across multiple notes.",
        "result": "State found expired CPR certifications during a routine audit. Agency cited, all staff required to complete back training, supervisor terminated.",
        "lead_time": "five months"
    },
    {
        "name": "Riverbend Care",
        "location": "Denver, CO",
        "category": "theft",
        "story": "Petty cash for client community outings consistently came up short. Notes from multiple DSPs identified one common factor in the shifts.",
        "result": "$9,400 in client funds tracked back to one DSP over 18 months. Felony exploitation charges, civil suit, mandatory state notification of all client families.",
        "lead_time": "six months"
    },
    {
        "name": "Northern Lights Behavioral",
        "location": "Minneapolis, MN",
        "category": "harassment",
        "story": "Multiple DSPs flagged a House Manager making comments about pregnancy and fertility to female staff. Pattern across check-ins.",
        "result": "Gender discrimination lawsuit, $75,000 settlement, manager terminated, EEOC training required for the entire management chain.",
        "lead_time": "four months"
    }
]


SERVICE_CARE_CATEGORY_SUMMARIES = [
    {
        "category": "Client Abuse",
        "signals_prevented": 28,
        "example": "Multiple DSPs noted improper restraint use during behavior incidents → Issue addressed before client injury and state report filed"
    },
    {
        "category": "Theft & Exploitation",
        "signals_prevented": 24,
        "example": "Pattern of small discrepancies in client petty cash logs → Investigation recovered $1,800 in client funds before total exceeded $5,000"
    },
    {
        "category": "Harassment",
        "signals_prevented": 19,
        "example": "Multiple staff separately noted unwanted comments from a senior coordinator → Issue addressed before formal EEOC complaint filed"
    },
    {
        "category": "Bullying",
        "signals_prevented": 16,
        "example": "New DSP repeatedly given the most demanding assignments without proper training → Manager intervention stopped resignation and rebuilt team trust"
    },
    {
        "category": "Substance Concerns",
        "signals_prevented": 13,
        "example": "Corroborated notes about impaired DSP on overnight shifts → Employee connected to support resources before any client incident"
    }
]


# ════════════════════════════════════════════════════════════════════════════
# LOOKUP
# ════════════════════════════════════════════════════════════════════════════

_TALES_BY_INDUSTRY = {
    'restaurant': RESTAURANT_TALES,
    'service_care': SERVICE_CARE_TALES,
}

_CATEGORY_SUMMARIES_BY_INDUSTRY = {
    'restaurant': RESTAURANT_CATEGORY_SUMMARIES,
    'service_care': SERVICE_CARE_CATEGORY_SUMMARIES,
}


def get_cautionary_tales(industry: str) -> list:
    """Return the cautionary tales pool for the given industry.

    Falls back to restaurant tales for any unknown industry value, since
    that's the legacy default and produces sensible (if mismatched) content
    rather than an empty pool.
    """
    return _TALES_BY_INDUSTRY.get(industry, RESTAURANT_TALES)


def get_category_summaries(industry: str) -> list:
    """Return the network-stats category summaries for the given industry.

    Same fallback behavior as get_cautionary_tales.
    """
    return _CATEGORY_SUMMARIES_BY_INDUSTRY.get(industry, RESTAURANT_CATEGORY_SUMMARIES)
