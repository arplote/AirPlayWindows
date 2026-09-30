"""Point d'entrée principal en ligne de commande pour AirPlay Sender.

Phase 6 du projet AirPlayWindows.
Permet d'exécuter l'application avec :
  python -m airplay_sender [scan|pair|run|diag|tray]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from airplay_sender import __version__
from airplay_sender.cli import cmd_diag, cmd_gui, cmd_pair, cmd_run, cmd_scan, cmd_tray
from airplay_sender.config import load_config
from airplay_sender.logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    """Construit l'analyseur de ligne de commande principal."""
    parser = argparse.ArgumentParser(
        prog="airplay_sender",
        description=(
            "AirPlay Sender pour Windows — "
            "Diffuse tout le son système vers vos enceintes AirPlay/RAOP."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Chemin personnalisé vers le fichier config.json",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Activer la journalisation détaillée (DEBUG).",
    )

    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Démarrer l'application réduite dans la zone de notification (System Tray).",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Sous-commandes disponibles")

    # 1. SCAN
    scan_parser = subparsers.add_parser(
        "scan",
        help="Rechercher les enceintes AirPlay et RAOP sur le réseau local.",
    )
    scan_parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=5,
        help="Durée de recherche en secondes (défaut: 5)",
    )
    scan_parser.add_argument(
        "--json",
        action="store_true",
        help="Afficher le résultat sous forme de données JSON brutes.",
    )
    scan_parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Nom ou numéro d'enceinte à enregistrer comme cible par défaut.",
    )

    # 2. PAIR
    pair_parser = subparsers.add_parser(
        "pair",
        help="Associer / appairer le PC avec une enceinte.",
    )
    pair_parser.add_argument(
        "--device",
        "-d",
        type=str,
        default=None,
        help="Nom ou identifiant de l'enceinte à appairer.",
    )

    # 3. RUN
    run_parser = subparsers.add_parser(
        "run",
        help="Diffuser le son système Windows en mode console continu.",
    )
    run_parser.add_argument(
        "--device",
        "-d",
        type=str,
        default=None,
        help="Nom ou identifiant de l'enceinte cible (outrepasse la configuration).",
    )
    run_parser.add_argument(
        "--volume",
        type=float,
        default=None,
        help="Volume initial (0.0 à 1.0).",
    )
    run_parser.add_argument(
        "--buffer-ms",
        type=int,
        default=None,
        help="Taille du tampon audio en millisecondes (défaut: 800).",
    )

    # 4. DIAG
    diag_parser = subparsers.add_parser(
        "diag",
        help="Générer un rapport d'analyse de l'environnement pour le dépannage.",
    )
    diag_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Fichier de destination pour enregistrer le rapport.",
    )

    # 5. GUI
    gui_parser = subparsers.add_parser(
        "gui",
        help="Lancer l'interface graphique de contrôle (GUI) avec fenêtre et barre d'état.",
    )
    gui_parser.add_argument(
        "--minimized",
        action="store_true",
        help="Démarrer l'application directement dans la barre d'état sans ouvrir la fenêtre.",
    )

    # 6. TRAY
    subparsers.add_parser(
        "tray",
        help="Lancer l'application dans la zone de notification Windows (System Tray).",
    )

    return parser


def main() -> int:
    """Point d'entrée de la CLI."""
    parser = build_parser()
    args = parser.parse_args()

    # Configuration des logs
    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(level=log_level, verbose=args.verbose)

    # Chargement de la configuration
    config = load_config(args.config)

    if not args.subcommand or args.subcommand == "gui":
        # Par défaut (double-clic ou sous-commande 'gui'), lancer l'interface graphique complète
        return cmd_gui(args, config)

    if args.subcommand == "scan":
        return asyncio.run(cmd_scan(args, config))
    elif args.subcommand == "pair":
        return asyncio.run(cmd_pair(args, config))
    elif args.subcommand == "run":
        return asyncio.run(cmd_run(args, config))
    elif args.subcommand == "diag":
        return asyncio.run(cmd_diag(args, config))
    elif args.subcommand == "tray":
        return cmd_tray(args, config)

    return 0


if __name__ == "__main__":
    sys.exit(main())
