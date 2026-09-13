# -*- coding: utf-8 -*-
"""Optional richer background paragraphs (no spoilers)."""
from __future__ import annotations

BACKGROUND_OVERRIDES: dict[str, str] = {
    "berlin_wall_fall_1989": (
        "By autumn 1989 the German Democratic Republic was losing citizens daily through "
        "Berlin's open border crossings. Months of protest, Hungarian border openings, and "
        "Soviet reluctance to intervene had weakened the regime. On 9 November a confused "
        "press conference announcement suggested travel rules would ease immediately — within "
        "hours thousands gathered at Bornholmer Straße and other checkpoints while Stasi "
        "officers, army units, and party hardliners waited for orders from East Berlin and Moscow."
    ),
    "cuban_missile_1962": (
        "In October 1962 Cold War tensions were already acute after the failed Bay of Pigs "
        "invasion and Soviet military aid to Cuba. U-2 flights had photographed construction "
        "sites; the ExComm group assembled in Washington represented military, diplomatic, and "
        "intelligence voices with sharply different appetites for risk."
    ),
    "haitian_revolution_1801": (
        "Saint-Domingue was France's richest colony, built on enslaved labor and brutal "
        "plantation agriculture. Since 1791 successive waves of rebellion, royalist plots, "
        "British and Spanish intervention, and civil war among rebel factions had shattered "
        "colonial control. Toussaint Louverture emerged as the dominant military leader while "
        "Paris convulsed in its own revolution and Napoleon prepared to restore imperial order."
    ),
    "magna_carta_1215": (
        "By 1215 King John's heavy taxation, failed campaigns in France, and arbitrary justice "
        "had alienated the English baronage. Rebel lords held London and demanded written limits "
        "on royal power. Runnymede, a meadow beside the Thames, became the stage for "
        "negotiation between a bankrupt crown and armed nobles who remembered earlier charters "
        "and feared losing their feudal rights."
    ),
}


def background_for(enc) -> str:
    if enc.id in BACKGROUND_OVERRIDES:
        return BACKGROUND_OVERRIDES[enc.id]
    if getattr(enc, "background", ""):
        return enc.background
    return (
        f"Around {enc.era} in {enc.region}, this episode belongs to the wider story of "
        f"{enc.category.lower()}. Contemporary actors operated with incomplete intelligence, "
        f"limited resources, and rival factions — the outcome was not foreordained."
    )


def chat_background_for(enc) -> str:
    """Rich background for workspace chat only (no outcome spoilers)."""
    bg = background_for(enc)
    framing = (
        f"This episode unfolds in **{enc.region}** around **{enc.era}**. "
        f"It sits within the broader history of {enc.category.lower()} — "
        f"those present could not know how later generations would judge their choices."
    )
    return f"{bg}\n\n{framing}"
