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
            label="Worn charcoal hoodie",
            prompt=(
                "a slightly oversized, well-used charcoal-grey pullover hoodie with "
                "natural bunching, minor lint and no visible branding"
            ),
        ),
        WardrobeDefinition(
            key="washed-navy-tee",
            label="Loose washed navy T-shirt",
            prompt=(
                "a loose washed-navy crew-neck T-shirt with a slightly stretched collar, "
                "soft wrinkles and no visible branding"
            ),
        ),
        WardrobeDefinition(
            key="forest-sweatshirt",
            label="Faded forest sweatshirt",
            prompt=(
                "a faded forest-green crew-neck sweatshirt with soft pilling, uneven folds "
                "and no visible branding"
            ),
        ),
        WardrobeDefinition(
            key="olive-street-layers",
            label="Olive jacket and cream T-shirt",
            prompt=(
                "a lightweight dark-olive utility jacket worn open over a faded off-white "
                "crew-neck T-shirt, both ordinary, slightly creased and unbranded"
            ),
        ),
        WardrobeDefinition(
            key="cream-knit",
            label="Relaxed cream knit",
            prompt=(
                "a relaxed oatmeal-cream knit jumper with subtle pilling, slightly uneven "
                "cuffs and no visible branding"
            ),
        ),
        WardrobeDefinition(
            key="burgundy-long-sleeve",
            label="Faded burgundy long-sleeve",
            prompt=(
                "a faded burgundy cotton long-sleeve top with slightly stretched cuffs, "
                "ordinary wrinkles and no visible branding"
            ),
        ),
        WardrobeDefinition(
            key="heather-lilac-tee",
            label="Washed lilac T-shirt",
            prompt=(
                "a loose heather-lilac crew-neck T-shirt with a slightly worn collar, "
                "ordinary wrinkles and no visible branding"
            ),
        ),
        WardrobeDefinition(
            key="black-zip-hoodie",
            label="Faded black zip hoodie",
            prompt=(
                "a faded black zip hoodie worn open over a plain off-white vest, with "
                "soft pilling, uneven folds and no visible branding"
            ),
        ),
        WardrobeDefinition(
            key="rust-striped-tee",
            label="Muted rust striped T-shirt",
            prompt=(
                "a relaxed muted-rust T-shirt with thin faded cream stripes, a softly "
                "stretched collar, natural creases and no visible branding"
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
