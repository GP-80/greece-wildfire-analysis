"""
Burned-area comparison: GEE 500m (native) vs GEE 2000m (script resolution) vs PNG pixels.

Usage:
  python compare_areas.py --project geo-tut
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

DATA_DIR = Path(__file__).parent / "data"
START_YEAR = 2000
END_YEAR   = 2025


def burned_area_km2(mcd64, greece, year, scale):
    """Compute burned area in km^2 for one year at the given scale."""
    import ee
    start  = ee.Date.fromYMD(year, 1, 1)
    burned = (mcd64
              .filterDate(start, start.advance(1, "year"))
              .map(lambda img: img.gt(0).rename("burned").selfMask())
              .max()
              .clip(greece))
    total = (burned
             .multiply(ee.Image.pixelArea())
             .reduceRegion(
                 reducer=ee.Reducer.sum(),
                 geometry=greece,
                 scale=scale,
                 maxPixels=1e10,
             )
             .getNumber("burned")
             .divide(1e6))           # m^2 -> km^2
    val = total.getInfo()
    return val if val is not None else 0.0


def png_red_pixels(year):
    arr = np.array(Image.open(DATA_DIR / f"burned_{year}.png").convert("RGBA"))
    return int(np.sum(
        (arr[:, :, 0] > 150) & (arr[:, :, 1] < 100) &
        (arr[:, :, 2] < 100) & (arr[:, :, 3] > 0)
    ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="GEE project ID")
    args = parser.parse_args()

    import ee
    try:
        ee.Initialize(project=args.project)
    except Exception as exc:
        print(f"GEE init failed: {exc}")
        sys.exit(1)

    greece = (ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
              .filter(ee.Filter.eq("country_na", "Greece"))
              .geometry())
    mcd64 = ee.ImageCollection("MODIS/061/MCD64A1").select("BurnDate")

    col_w = 16
    hdr = (f"{'Year':>6} | {'GEE 500m (km2)':>{col_w}} | "
           f"{'GEE 2000m (km2)':>{col_w}} | "
           f"{'Ratio 2000/500':>{col_w}} | {'PNG pixels':>12}")
    sep = "-" * len(hdr)

    print(f"\nBurned area comparison ({START_YEAR}-{END_YEAR})")
    print(sep)
    print(hdr)
    print(sep)

    total_500  = 0.0
    total_2000 = 0.0
    total_png  = 0

    for year in range(START_YEAR, END_YEAR + 1):
        print(f"  {year} ...", end=" ", flush=True)
        a500  = burned_area_km2(mcd64, greece, year, 500)
        a2000 = burned_area_km2(mcd64, greece, year, 2000)
        px    = png_red_pixels(year)

        ratio = (a2000 / a500) if a500 > 0 else 0.0
        total_500  += a500
        total_2000 += a2000
        total_png  += px

        print(f"\r{year:>6} | {a500:>{col_w}.1f} | {a2000:>{col_w}.1f} | "
              f"{ratio:>{col_w}.3f} | {px:>12}")

    print(sep)
    total_ratio = (total_2000 / total_500) if total_500 > 0 else 0.0
    print(f"{'TOTAL':>6} | {total_500:>{col_w}.1f} | {total_2000:>{col_w}.1f} | "
          f"{total_ratio:>{col_w}.3f} | {total_png:>12}")
    print()
    print("Notes:")
    print("  GEE 500m  = MODIS native resolution, pixelArea()-weighted (authoritative)")
    print("  GEE 2000m = same data downsampled to our script resolution")
    print("  Ratio     = how much area is lost/gained by downsampling to 2000m")
    print("  PNG pixels= red pixel count in the embedded map PNGs (qualitative)")


if __name__ == "__main__":
    main()
