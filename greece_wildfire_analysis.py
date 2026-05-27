"""
Greece Annual Burned Area (2000-2025)

Usage:
  python greece_wildfire_analysis.py              # build map from data/
  python greece_wildfire_analysis.py --regenerate # fetch fresh data from GEE
"""

import argparse
import base64
import json
import sys
from pathlib import Path

import folium
import requests

# --- Tunables ----------------------------------------------------------------
START_YEAR    = 2000
END_YEAR      = 2025
RES_M         = 2000
THUMB_DIM     = 512
OUT_HTML      = Path(__file__).parent / "greece_burned_2000_2025.html"
DATA_DIR      = Path(__file__).parent / "data"
MAP_TITLE     = "Annual Burned Area in Greece (2000–2025)"
DATA_SOURCE   = "Data: MODIS MCD64A1 v6.1 — NASA / UMD"
ZOOM_START    = 7
CENTER_LATLON = [38.5, 23.7]
BURN_COLOR    = "ff3b30"
GEE_CREDS     = Path.home() / ".config" / "earthengine" / "credentials"
# -----------------------------------------------------------------------------


def _init_gee(project: str):
    import ee

    print(f"\nChecking for credentials at {GEE_CREDS} ...")
    if GEE_CREDS.exists():
        print("  -> Credentials found.")
    else:
        print("  -> No credentials found.")
        print("     A browser window will open — sign in with Google and")
        print("     approve Earth Engine access, then return here.")
        ee.Authenticate()
        print(f"  -> Credentials saved to {GEE_CREDS}")

    print("  -> Initializing Earth Engine ...", end=" ", flush=True)
    try:
        ee.Initialize(project=project)
        print("OK")
    except Exception as exc:
        print(f"\nInitialization failed: {exc}")
        sys.exit(1)


def regenerate_from_gee(project: str = ""):
    import ee

    print("=" * 52)
    print(" Regenerate mode")
    print("=" * 52)
    print(f"Downloads MODIS burned area data from Google Earth")
    print(f"Engine ({START_YEAR}-{END_YEAR}). Requires a GEE account.")
    print("Sign up free at: https://code.earthengine.google.com\n")

    if not project:
        project = input("Enter your GEE project ID: ").strip()
    if not project:
        print("Error: project ID is required.")
        sys.exit(1)

    _init_gee(project)

    print("\nFetching Greece geometry ...", end=" ", flush=True)
    greece = (
        ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        .filter(ee.Filter.eq("country_na", "Greece"))
        .geometry()
    )
    border = ee.Feature(greece).getInfo()
    if not border:
        print("\nError: could not retrieve Greece geometry from GEE.")
        sys.exit(1)
    print("OK")

    # Bounding box saved alongside PNGs so build_map() needs no GEE
    raw_coords = greece.bounds().getInfo()["coordinates"][0]
    lon_min = min(c[0] for c in raw_coords)
    lon_max = max(c[0] for c in raw_coords)
    lat_min = min(c[1] for c in raw_coords)
    lat_max = max(c[1] for c in raw_coords)
    bounds = [[lat_min, lon_min], [lat_max, lon_max]]

    mask = (
        ee.Image().byte().paint(greece, 1)
        .reproject(crs="EPSG:4326", scale=RES_M)
    )
    mcd64 = ee.ImageCollection("MODIS/061/MCD64A1").select("BurnDate")

    def annual_burn(year):
        y = ee.Number(year)
        start = ee.Date.fromYMD(y, 1, 1)
        burned = (
            mcd64
            .filterDate(start, start.advance(1, "year"))
            .map(lambda img: img.gt(0).rename("burned").selfMask())
            .max()
        )
        return burned.updateMask(mask).reproject(crs="EPSG:4326", scale=RES_M)

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "bounds.json").write_text(json.dumps(bounds))
    (DATA_DIR / "border.json").write_text(json.dumps(border))

    import io
    import numpy as np
    from PIL import Image

    region = greece.bounds()

    total = END_YEAR - START_YEAR + 1
    print(f"\nDownloading {total} yearly PNGs:")
    failed = []
    for i, year in enumerate(range(START_YEAR, END_YEAR + 1), 1):
        print(f"  Year {year} ({i}/{total}) ...", end=" ", flush=True)
        try:
            # visualize() bakes the palette into RGB bands and preserves the mask,
            # so getThumbURL receives a plain RGB image rather than raw data +
            # separate vis params — more reliable across GEE API versions.
            vis = annual_burn(year).visualize(
                min=0, max=1, palette=[BURN_COLOR]
            )
            url = vis.getThumbURL({
                "dimensions": THUMB_DIM,
                "region": region,
                "crs": "EPSG:3857",
                "format": "png",
            })
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()

            # GEE may fill masked pixels with black rather than transparent.
            # Convert to RGBA and zero the alpha of any near-black pixel.
            arr = np.array(Image.open(io.BytesIO(resp.content)).convert("RGBA"))
            is_black = (arr[:, :, 0] < 30) & (arr[:, :, 1] < 30) & (arr[:, :, 2] < 30)
            arr[is_black, 3] = 0
            buf = io.BytesIO()
            Image.fromarray(arr, "RGBA").save(buf, format="PNG")
            png_data = buf.getvalue()
            (DATA_DIR / f"burned_{year}.png").write_bytes(png_data)
            print(f"OK ({len(png_data) // 1024}KB)")
        except Exception as exc:
            print(f"FAILED  ({exc})")
            failed.append(year)

    if failed:
        print(f"\nWarning: {len(failed)} year(s) failed: {', '.join(str(y) for y in failed)}")
        print("Re-run --regenerate to retry, or build the map with the available years.")
    else:
        print(f"\nAll {total} PNGs saved to {DATA_DIR.resolve()}/")

    # --- Burned area per year (native MODIS 500 m, pixelArea-weighted) -------
    print(f"\nComputing burned area per year (MODIS 500 m):")
    areas = {}
    for i, year in enumerate(range(START_YEAR, END_YEAR + 1), 1):
        print(f"  {year} ({i}/{total}) ...", end=" ", flush=True)
        try:
            start = ee.Date.fromYMD(year, 1, 1)
            burned_native = (
                mcd64
                .filterDate(start, start.advance(1, "year"))
                .map(lambda img: img.gt(0).rename("burned").selfMask())
                .max()
                .clip(greece)
            )
            val = (
                burned_native
                .multiply(ee.Image.pixelArea())
                .reduceRegion(
                    reducer=ee.Reducer.sum(),
                    geometry=greece,
                    scale=500,
                    maxPixels=1e10,
                )
                .getNumber("burned")
                .divide(1e6)
                .getInfo()
            )
            areas[str(year)] = round(val, 1) if val else 0.0
            print(f"OK ({areas[str(year)]:.1f} km2)")
        except Exception as exc:
            print(f"FAILED ({exc})")
            areas[str(year)] = 0.0

    (DATA_DIR / "areas.json").write_text(json.dumps(areas))
    print(f"Areas saved to {(DATA_DIR / 'areas.json').resolve()}")


def build_map():
    bounds_path = DATA_DIR / "bounds.json"
    border_path = DATA_DIR / "border.json"

    if not bounds_path.exists() or not border_path.exists():
        print("\nError: data/ is missing or incomplete.")
        print("Run:  python greece_wildfire_analysis.py --regenerate")
        sys.exit(1)

    missing = [
        y for y in range(START_YEAR, END_YEAR + 1)
        if not (DATA_DIR / f"burned_{y}.png").exists()
    ]
    if missing:
        print(
            f"\nError: missing PNGs for {len(missing)} year(s): "
            f"{', '.join(str(y) for y in missing)}"
        )
        print("Run:  python greece_wildfire_analysis.py --regenerate")
        sys.exit(1)

    bounds = json.loads(bounds_path.read_text())
    border = json.loads(border_path.read_text())

    areas_path = DATA_DIR / "areas.json"
    if areas_path.exists():
        raw = json.loads(areas_path.read_text())
        areas = {str(y): raw.get(str(y), 0.0) for y in range(START_YEAR, END_YEAR + 1)}
    else:
        print("Warning: data/areas.json not found - area display will show 0.")
        areas = {str(y): 0.0 for y in range(START_YEAR, END_YEAR + 1)}

    total = END_YEAR - START_YEAR + 1
    print(f"\nBuilding map ({total} layers) ...")

    m = folium.Map(location=CENTER_LATLON, zoom_start=ZOOM_START, tiles=None)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png",
        attr="© OpenStreetMap contributors © CARTO",
        name="Base (No labels)",
        control=False,
        show=True,
    ).add_to(m)

    folium.GeoJson(
        data=border,
        name="Greece border",
        style_function=lambda _: {
            "fill": False,
            "color": "#222",
            "weight": 0.8,
            "opacity": 0.9,
        },
        control=False,
    ).add_to(m)

    for i, year in enumerate(range(START_YEAR, END_YEAR + 1), 1):
        print(f"  Embedding {year} ({i}/{total}) ...", end=" ", flush=True)
        png_bytes = (DATA_DIR / f"burned_{year}.png").read_bytes()
        b64 = base64.b64encode(png_bytes).decode()
        folium.raster_layers.ImageOverlay(
            image=f"data:image/png;base64,{b64}",
            bounds=bounds,
            name=str(year),
            overlay=True,
            control=True,
            show=True,
            opacity=1.0,
        ).add_to(m)
        print("OK")

    folium.LayerControl(collapsed=True).add_to(m)

    # ---- viewport + responsive CSS -----------------------------------------
    m.get_root().header.add_child(folium.Element(
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
    ))
    m.get_root().header.add_child(folium.Element("""<style>
/* desktop: keep panel permanently open; hide the toggle icon */
@media (min-width: 769px) {
    .leaflet-control-layers-list   { display: block !important; }
    .leaflet-control-layers-toggle { display: none  !important; }
}
/* tablet: layer list scrollable so it never overflows the screen */
@media (max-width: 1024px) {
    .leaflet-control-layers-list {
        max-height: 55vh;
        overflow-y: auto;
    }
}
/* mobile: title moves to bottom-left, just above the data source label */
@media (max-width: 768px) {
    #_map_title {
        top: auto !important;
        bottom: 38px !important;
        left: 10px !important;
        transform: none !important;
        text-align: left !important;
        font-size: 13px !important;
        padding: 5px 10px !important;
        max-width: 55vw !important;
        white-space: normal !important;
        line-height: 1.3 !important;
    }
    #_title_year { display: block !important; }
    #_area_panel {
        padding: 5px 10px !important;
        bottom: 30px !important;
    }
    #_area_km2 { font-size: 15px !important; }
    #_area_n   { font-size: 10px !important; }
    .leaflet-control-layers { padding: 5px 10px !important; }
    .leaflet-control-layers-overlays label {
        font-size: 11px !important;
        line-height: 1.7 !important;
    }
    .leaflet-control-layers-list {
        max-height: 45vh !important;
        overflow-y: auto !important;
    }
}
</style>"""))

    # ---- static UI elements -------------------------------------------------
    m.get_root().html.add_child(folium.Element(f"""
<div id="_map_title" style="position:fixed;top:10px;left:50%;transform:translateX(-50%);
z-index:9999;background:rgba(255,255,255,0.9);padding:8px 18px;
border-radius:8px;font-size:18px;font-weight:700;color:#222;text-align:center;
white-space:nowrap;">Annual Burned Area in Greece<span id="_title_year"> (2000&#8211;2025)</span></div>"""))

    m.get_root().html.add_child(folium.Element(f"""
<div style="position:fixed;bottom:10px;left:10px;z-index:9999;
background:rgba(255,255,255,0.85);padding:4px 8px;border-radius:6px;
font-size:12px;color:#333;">{DATA_SOURCE}</div>"""))

    # ---- dynamic burned-area widget (bottom-right, above attribution) -------
    m.get_root().html.add_child(folium.Element("""
<div id="_area_panel" style="
  position:fixed; bottom:36px; right:10px; z-index:9999;
  background:rgba(255,255,255,0.9); padding:8px 14px; border-radius:8px;
  font-size:12px; color:#555; text-align:right;
  pointer-events:none; box-shadow:0 1px 5px rgba(0,0,0,0.15); line-height:1.7;">
  <div style="font-size:10px;font-weight:600;letter-spacing:.05em;
              text-transform:uppercase;color:#888;">Burned Area</div>
  <div id="_area_km2" style="font-size:20px;font-weight:700;
                              color:#ff3b30;letter-spacing:-.02em;">&#8212;</div>
  <div id="_area_n"   style="font-size:11px;color:#888;"></div>
</div>"""))

    map_var   = m.get_name()
    areas_js  = json.dumps(areas)

    m.get_root().html.add_child(folium.Element(f"""<script>
window.addEventListener('load', function() {{

    // layer control styling
    var s = document.createElement('style');
    s.textContent =
        '.leaflet-control-layers {{ padding: 8px 16px 8px !important; }}' +
        '.leaflet-control-layers-overlays label {{' +
        '    font-size: 13px !important; line-height: 2.1 !important;' +
        '    display: flex !important; align-items: center !important; }}' +
        '.leaflet-control-layers-overlays input[type="checkbox"] {{' +
        '    margin-right: 7px !important; flex-shrink: 0 !important; }}';
    document.head.appendChild(s);

    // layer control title and select-all / none footer
    var list = document.querySelector('.leaflet-control-layers-list');
    if (list) {{
        var hdr = document.createElement('div');
        hdr.style.cssText =
            'font-size:11px;font-weight:700;color:#666;letter-spacing:.06em;' +
            'text-transform:uppercase;padding-bottom:7px;' +
            'border-bottom:1px solid #e0e0e0;margin-bottom:2px;';
        hdr.textContent = 'Year';
        list.insertBefore(hdr, list.firstChild);

        var foot = document.createElement('div');
        foot.style.cssText =
            'display:flex;gap:10px;justify-content:flex-end;font-size:11px;' +
            'padding-top:8px;border-top:1px solid #e0e0e0;margin-top:4px;';
        foot.innerHTML =
            '<span id="_sa" style="cursor:pointer;color:#555;user-select:none;">Select all</span>' +
            '<span style="color:#ccc;">|</span>' +
            '<span id="_sn" style="cursor:pointer;color:#555;user-select:none;">None</span>';
        list.appendChild(foot);

        document.getElementById('_sa').onclick = function() {{
            document.querySelectorAll(
                '.leaflet-control-layers-overlays input[type="checkbox"]'
            ).forEach(function(b) {{ if (!b.checked) b.click(); }});
        }};
        document.getElementById('_sn').onclick = function() {{
            document.querySelectorAll(
                '.leaflet-control-layers-overlays input[type="checkbox"]'
            ).forEach(function(b) {{ if (b.checked) b.click(); }});
        }};
    }}

    var burned  = {areas_js};
    var visible = {{}};
    Object.keys(burned).forEach(function(y) {{ visible[y] = true; }});

    function fmt(n) {{
        return Math.round(n).toLocaleString('en') + ' km²';
    }}
    function update() {{
        var sel   = Object.keys(visible).filter(function(y) {{ return visible[y]; }});
        var total = sel.reduce(function(s, y) {{ return s + (burned[y] || 0); }}, 0);
        document.getElementById('_area_km2').textContent = fmt(total);
        document.getElementById('_area_n').textContent   =
            sel.length + (sel.length === 1 ? ' year' : ' years') + ' selected';
    }}

    var map = window['{map_var}'];
    if (window.innerWidth <= 768) {{ map.setZoom(5); }}
    map.on('overlayadd',    function(e) {{
        if (burned.hasOwnProperty(e.name)) {{ visible[e.name] = true;  update(); }}
    }});
    map.on('overlayremove', function(e) {{
        if (burned.hasOwnProperty(e.name)) {{ visible[e.name] = false; update(); }}
    }});
    update();
}});
</script>"""))

    m.save(str(OUT_HTML))
    print(f"\nSaved: {OUT_HTML.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="Greece wildfire map — build from local data or refresh from GEE."
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Fetch fresh data from GEE (requires a GEE account and registered project)",
    )
    parser.add_argument(
        "--project",
        default="",
        help="GEE project ID (skips the interactive prompt when used with --regenerate)",
    )
    args = parser.parse_args()

    if args.regenerate:
        regenerate_from_gee(project=args.project)

    build_map()


if __name__ == "__main__":
    main()
