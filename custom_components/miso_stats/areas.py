"""Local balancing authority (LBA) names and the codes MISO's Emissions API expects.

The API's location lookup returns an LBA's full name ("Detroit Edison Company"), while its
event queries take the code ("DECO"). The pairs come from the MISO Emissions Dashboard's own map
data (miso.singularity.energy, October 2026). MISO's commercial pricing nodes use the same
codes as prefixes ("DECO.NODE"), which is how a node in the home's LBA is suggested.
"""

from __future__ import annotations

LBA_CODES: dict[str, str] = {
    "1803 Electric Cooperative": "LAGT",
    "AEP West": "AEP",
    "Allegheny Power": "AP",
    "Alliant East - Wisconsin Power and Light Company": "ALTE",
    "Alliant West - Interstate Power & Light": "ALTW",
    "Ameren Illinois": "AMIL",
    "Ameren Missouri": "AMMO",
    "Associated Electric Cooperative Inc.": "AECI",
    "Big Rivers Electric Corp": "BREC",
    "Board of Public Utilities Kansas City Kansas": "KACY",
    "Central Louisiana Electric Co. Inc.": "CLEC",
    "Columbia Missouri Water and Light Department": "CWLD",
    "Commonwealth Edison Co.": "CE",
    "Consumers Energy - METC": "CONS",
    "Dairyland Power Cooperative (GSE)": "DPC",
    "Dayton Power & Light Co.": "DPL",
    "Detroit Edison Company": "DECO",
    "Duke Energy Carolinas": "DUK",
    "Duke Energy Indiana": "CIN",
    "Duquesne Light Company": "DLCO",
    "East Kentucky Power Coop.": "EKPC",
    "Empire District Electric Co.": "EDE",
    "Entergy Arkansas": "EAI",
    "Entergy Louisiana": "EES",
    "Entergy Mississippi": "EMBA",
    "Grand River Dam Authority": "GRDA",
    "Great River Energy": "GRE",
    "Henderson Municipal Power": "HMPL",
    "Hoosier Energy Rural Elec.": "HE",
    "Indianapolis Power & Light": "IPL",
    "Kansas City Power & Light Co.": "KCPL",
    "Lafayette (City of)": "LAFA",
    "Lone Star Transmission": "GLH",
    "Louisiana Generating/Cajun Electric": "LAGN",
    "Louisville Gas & Electric/ Kentucky Utilities": "LGEE",
    "Madison Gas and Electric Company": "MGE",
    "Michigan Upper Peninsula": "MIUP",
    "Midamerican Energy Co.": "MEC",
    "Minnesota Power and Light Company": "MP",
    "Montana-Dakota Utilities Co.": "MDU",
    "Muscatine Power & Water": "MPW",
    "Northern Indiana Public Service": "NIPS",
    "Northern States Power Company": "NSP",
    "Otter Tail Power Company": "OTP",
    "Progress Energy Carolinas East": "CPLE",
    "South Mississippi Electric Power Association": "SME",
    "Southern Illinois Power Co-operative": "SIPC",
    "Southern Indiana Gas & Electric": "SIGE",
    "Southern MN Municipal Power Agency": "SMP",
    "Springfield Illinois - City Water Light & Power": "CWLP",
    "Upper Peninsula Power Company": "UPPC",
    "Wisconsin Electric Power Company": "WEC",
    "Wisconsin Public Service Corporation": "WPS",
}
_CODES = set(LBA_CODES.values())


def lba_code(lba: str | None) -> str | None:
    """The API code for an LBA name (or a code already); unknown names pass through unchanged."""
    if not lba:
        return None
    if lba in _CODES:
        return lba
    return LBA_CODES.get(lba) or next((c for n, c in LBA_CODES.items() if n.casefold() == lba.casefold()), lba)
