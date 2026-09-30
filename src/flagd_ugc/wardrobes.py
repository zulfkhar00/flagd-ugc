from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WardrobeDefinition:
    key: str
    label: str
    prompt: str


WARDROBES = {
    item.key: item
    for item in (
        WardrobeDefinition(
            key="charcoal-hoodie",
            label="Charcoal hoodie",
            prompt="an ordinary charcoal-grey pullover hoodie with no visible branding",
        ),
        WardrobeDefinition(
            key="washed-navy-tee",
            label="Navy T-shirt",
            prompt="an ordinary navy crew-neck T-shirt with no visible branding",
        ),
        WardrobeDefinition(
            key="forest-sweatshirt",
            label="Forest-green sweatshirt",
            prompt="an ordinary forest-green crew-neck sweatshirt with no visible branding",
        ),
        WardrobeDefinition(
            key="olive-street-layers",
            label="Olive jacket and cream T-shirt",
            prompt=(
                "an ordinary olive utility jacket worn open over an off-white crew-neck "
                "T-shirt, with no visible branding"
            ),
        ),
        WardrobeDefinition(
            key="cream-knit",
            label="Cream knit sweater",
            prompt="an ordinary cream knit sweater with no visible branding",
        ),
        WardrobeDefinition(
            key="burgundy-long-sleeve",
            label="Burgundy long-sleeve top",
            prompt="an ordinary burgundy cotton long-sleeve top with no visible branding",
        ),
        WardrobeDefinition(
            key="heather-lilac-tee",
            label="Lilac T-shirt",
            prompt="an ordinary heather-lilac crew-neck T-shirt with no visible branding",
        ),
        WardrobeDefinition(
            key="black-zip-hoodie",
            label="Black zip hoodie",
            prompt="an ordinary black zip hoodie worn open over a plain off-white top, with no visible branding",
        ),
        WardrobeDefinition(
            key="rust-striped-tee",
            label="Muted rust striped T-shirt",
            prompt=(
                "an ordinary muted-rust T-shirt with thin cream stripes and no visible "
                "branding"
            ),
        ),
    )
}


def get_wardrobe(key: str) -> WardrobeDefinition:
    try:
        return WARDROBES[key]
    except KeyError as exc:
        raise ValueError(
            f"unknown wardrobe {key!r}; choose from {', '.join(WARDROBES)}"
        ) from exc


def wardrobe_keys() -> tuple[str, ...]:
    return tuple(WARDROBES)
