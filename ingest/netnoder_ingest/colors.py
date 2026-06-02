"""`netnoder-colors` command: re-allocate the persisted colour scheme on demand.

The ingest registries (`layer_colours`, `broadcast_domain_colours`) are deliberately *stable* --
a token's colour never changes once allocated. This command exists to break that
stability when you want to *explore* different looks: it wipes the chosen registry/ies
and re-allocates them under a `--scheme` and `--seed`. Colours are read live by the API,
so the new scheme shows up on a browser refresh, and a later `netnoder-ingest` leaves it
in place (seeding is first-seen-wins and the rows now exist).

Run after at least one ingest -- the protocol long tail is derived from observed traffic.
"""
import argparse
import random
import sys
from pathlib import Path

import duckdb

from . import config, palette


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netnoder-colors",
        description="Shake up / re-allocate the persisted colour scheme.",
    )
    ap.add_argument("--scheme", choices=palette.SCHEMES, default="shuffle",
                    help="colour scheme (default: shuffle -- reuses the curated palette "
                         "in a new order; the rest are generated)")
    ap.add_argument("--seed", type=int, default=None,
                    help="seed for reproducibility (default: random, printed so you can "
                         "reproduce a look you like)")
    ap.add_argument("--target", choices=("both", "protocols", "broadcast-domains"),
                    default="both", help="which registry to re-allocate (default: both)")
    ap.add_argument("--keep-anchors", action="store_true",
                    help="keep curated anchor colours (tcp/dns/public/...) fixed and only "
                         "reshuffle the rest (default: shake everything)")
    args = ap.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)

    config.ensure_dirs()
    con = duckdb.connect(str(config.DB_PATH))
    con.execute((Path(__file__).parent / "schema.sql").read_text())
    try:
        layers = domains = None
        if args.target in ("both", "protocols"):
            layers = palette.regenerate_layer_colours(
                con, scheme=args.scheme, seed=seed, keep_anchors=args.keep_anchors)
        if args.target in ("both", "broadcast-domains"):
            domains = palette.regenerate_broadcast_domain_colours(
                con, scheme=args.scheme, seed=seed, keep_anchors=args.keep_anchors)
    finally:
        con.close()

    parts = []
    if layers is not None:
        parts.append(f"{layers} protocol layers")
    if domains is not None:
        parts.append(f"{domains} broadcast domains")
    anchors = "anchors kept" if args.keep_anchors else "anchors shaken"
    print(f"recoloured {', '.join(parts)}; scheme={args.scheme} seed={seed} ({anchors})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
