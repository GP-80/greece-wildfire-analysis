# greece_burned_2000_2025_all_on.py
# Annual burned area in Greece (2000–2025), all yearly layers ON by default.
# Basemap: Carto Voyager No Labels (pale blue sea, neutral land, no labels).
# Includes title and data source annotation.

from pathlib import Path
import ee
import folium

ee.Initialize()

# ---- Tunables ----
START_YEAR = 2000
END_YEAR   = 2025
RES_M      = 2000  # visualization resolution (1000–4000 good)
OUT_HTML   = Path("greece_burned_2000_2025.html")
PALETTE    = ['00000000', 'ff3b30']  # transparent -> red
MAP_TITLE  = "Annual Burned Area in Greece (2000–2025)"
DATA_SOURCE = "Data: MODIS MCD64A1 v6.1 — NASA / UMD"
ZOOM_START = 6
CENTER_LATLON = [38.5, 23.7]  # roughly central Greece
# ------------------

# 1) Greece geometry (simple border)
greece = (ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
          .filter(ee.Filter.eq('country_na', 'Greece'))
          .geometry())

# 2) Greece raster mask (for clipping)
mask = (ee.Image().byte().paint(greece, 1)
        .reproject(crs='EPSG:4326', scale=RES_M))

# 3) MODIS Burned Area (MCD64A1 BurnDate) and annual image builder
mcd64 = ee.ImageCollection('MODIS/061/MCD64A1').select('BurnDate')

def annual_burn(year):
    """Create annual burned area mask for a given year."""
    year = ee.Number(year)
    start = ee.Date.fromYMD(year, 1, 1)
    end   = start.advance(1, 'year')
    burned = (mcd64
              .filterDate(start, end)
              .map(lambda img: img.gt(0).rename('burned').selfMask())
              .max())
    burned = burned.updateMask(mask).reproject(crs='EPSG:4326', scale=RES_M)
    return burned

# 4) Map: Carto Voyager No Labels (pale blue sea, no country labels)
m = folium.Map(location=CENTER_LATLON, zoom_start=ZOOM_START, tiles=None)
folium.TileLayer(
    tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png",
    attr="© OpenStreetMap contributors © CARTO",
    name="Base (No labels)",
    control=False,
    show=True
).add_to(m)

# 5) Greece border outline
greece_geojson = ee.Feature(greece).getInfo()
folium.GeoJson(
    data=greece_geojson,
    name="Greece border",
    style_function=lambda x: {
        "fill": False,
        "color": "#222",
        "weight": 0.8,
        "opacity": 0.9,
    },
    control=False,
).add_to(m)

# 6) Add one burned-area layer per year (all ON by default)
vis = {'min': 0, 'max': 1, 'palette': PALETTE}
for y in range(START_YEAR, END_YEAR + 1):
    img = annual_burn(y)
    map_id = img.getMapId(vis)
    folium.raster_layers.TileLayer(
        tiles=map_id['tile_fetcher'].url_format,
        attr='Map Data © Google Earth Engine',
        name=str(y),
        overlay=True,
        control=True,
        show=True,  # all layers visible initially
    ).add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

# 7) Title and annotation
title_html = f"""
<div style="
position: fixed;
top: 10px; left: 50%; transform: translateX(-50%);
z-index: 9999;
background-color: rgba(255, 255, 255, 0.9);
padding: 8px 18px; border-radius: 8px;
font-size: 18px; font-weight: 700;
color: #222; text-align: center;">
{MAP_TITLE}
</div>
"""
m.get_root().html.add_child(folium.Element(title_html))

annotation_html = f"""
<div style="
position: fixed;
bottom: 10px; left: 10px;
z-index: 9999;
background-color: rgba(255, 255, 255, 0.85);
padding: 4px 8px; border-radius: 6px;
font-size: 12px; color: #333;">
{DATA_SOURCE}
</div>
"""
m.get_root().html.add_child(folium.Element(annotation_html))

# 8) Save the map
m.save(str(OUT_HTML))
print(f"✅ Saved: {OUT_HTML.resolve()}")

