// Greece Annual Burned Area (2000-2025) — GEE Code Editor preview
// Paste into https://code.earthengine.google.com to compare with the HTML map.
// Same dataset, same palette, same year range as greece_wildfire_analysis.py

var START_YEAR = 2000;
var END_YEAR   = 2025;
var BURN_COLOR = 'ff3b30';

// Greece boundary
var greece = ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
  .filter(ee.Filter.eq('country_na', 'Greece'))
  .geometry();

// Greece raster mask
var mask = ee.Image().byte().paint(greece, 1);

// MODIS burned area collection
var mcd64 = ee.ImageCollection('MODIS/061/MCD64A1').select('BurnDate');

// Build one binary burned-area image per year
function annualBurn(year) {
  var start  = ee.Date.fromYMD(year, 1, 1);
  var burned = mcd64
    .filterDate(start, start.advance(1, 'year'))
    .map(function(img) { return img.gt(0).rename('burned').selfMask(); })
    .max();
  return burned.updateMask(mask).set('year', year);
}

var visParams = { min: 0, max: 1, palette: [BURN_COLOR] };

// Add each year as a layer (most recent on top)
for (var y = END_YEAR; y >= START_YEAR; y--) {
  Map.addLayer(annualBurn(y), visParams, String(y), false);
}

// Border outline
Map.addLayer(
  ee.Image().byte().paint({ featureCollection: greece, color: 0, width: 1 }),
  { palette: ['222222'] },
  'Greece border',
  true
);

Map.centerObject(greece, 7);
Map.setOptions('ROADMAP');
