"""Génération dynamique des icônes de la barre d'état (System Tray) avec Pillow.

Phase 7 du projet AirPlayWindows.
Produit les icônes aux couleurs des différents états (gris, orange, vert, rouge)
sans dépendance à des fichiers binaires externes.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Définition des couleurs associées aux états du superviseur
STATE_COLORS: dict[str, str] = {
    "IDLE": "#808080",  # Gris (inactif / arrêté)
    "STOPPED": "#808080",
    "DISCOVERING": "#FFA500",  # Orange (recherche / connexion)
    "CONNECTING": "#FFA500",
    "RECONNECTING": "#FFA500",
    "STREAMING": "#2ECC71",  # Vert vif (diffusion active)
    "ERROR": "#E74C3C",  # Rouge (erreur critique)
}

_ICON_CACHE: dict[str, Image.Image] = {}


def create_tray_icon_image(
    color_hex: str,
    size: int = 64,
) -> Image.Image:
    """Crée une icône vectorielle épurée (ondes audio + haut-parleur) à la couleur demandée."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Fond de l'icône : cercle adouci
    margin = size // 10
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color_hex,
        outline="#1A1A1A",
        width=max(2, size // 32),
    )

    # Dessin d'un symbole AirPlay / onde audio au centre en blanc pur
    cx = size // 2
    cy = size // 2
    white = (255, 255, 255, 255)

    # Triangle AirPlay au bas/centre
    tri_top = cy - size // 12
    tri_bottom = cy + size // 4
    tri_width = size // 4
    draw.polygon(
        [
            (cx, tri_top),
            (cx - tri_width // 2, tri_bottom),
            (cx + tri_width // 2, tri_bottom),
        ],
        fill=white,
    )

    # Arc 1 (onde rapprochée)
    arc1_r = size // 3
    draw.arc(
        [cx - arc1_r, cy - arc1_r, cx + arc1_r, cy + arc1_r],
        start=210,
        end=330,
        fill=white,
        width=max(2, size // 20),
    )

    return img


def get_icon(state_name: str) -> Image.Image:
    """Retourne l'icône PIL correspondant à un état donné (avec mise en cache)."""
    normalized_state = state_name.upper()
    color = STATE_COLORS.get(normalized_state, "#808080")

    if normalized_state not in _ICON_CACHE:
        _ICON_CACHE[normalized_state] = create_tray_icon_image(color)

    return _ICON_CACHE[normalized_state]


def save_ico_file(
    output_path: Path,
    color_hex: str = "#2ECC71",
) -> Path:
    """Génère un fichier .ico multi-résolution (16 à 256 px) pour le packaging Windows."""
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base_img = create_tray_icon_image(color_hex, size=256)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_img.save(
        str(output_path),
        format="ICO",
        sizes=sizes,
    )
    return output_path
