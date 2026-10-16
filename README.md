# Prawdom_in

QGIS plugin for importing residential floor areas from public Prawdom house cards and map context from OpenStreetMap in a selected Moscow study area.

Version: 1.4.4.7 · QGIS 3.28–3.99 · Python 3 · experimental.

Author/contact supplied for this release: Só-Sun Hui, corporate@k-a-d-o-d-e.com.

## Use

Install the ZIP from QGIS → Plugins → Manage and Install Plugins → Install from ZIP. Open Plugins → Prawdom_in → Import area.

Draw a rectangle, use selected polygons, or use the current map extent. Choose an output folder and import. For direct imports, enable the links option and paste Prawdom house or street page URLs.

The plugin saves persistent GeoPackage layers, styles, a JSON journal and per-house source URLs and retrieval dates. Save your QGIS project to retain the map composition.

Default styling: mint study area, coral catering points, blue diamond grocery points and neutral background layers. All output layer names are in Russian.

## Method and limits

The automatic mode downloads OSM context and residential addresses, matches normalized street names and exact house numbers to the Moscow Prawdom directory, then reads residential floor area and coordinates from matching cards. Missing residential areas are not replaced with total building area.

Estimated residents = residential floor area divided by the selected norm, rounded to the nearest integer per house (default 18 square metres per person). This is not census population. The selected area is not a calculated walking isochrone. Unmatched addresses are not imputed. The grocery category deliberately excludes supermarkets to match the original study specification.

Automatic address lookup is limited to Moscow and areas up to 25 square kilometres. Direct card and street links are supported separately. External sites may be unavailable, incomplete or change format. Cancellation waits for the current request to finish (40-second timeout). Prawdom cache: seven days; OSM cache: one day.

The plugin downloads from prawdom.ru and api.openstreetmap.org; it does not send imported records to an analytics service. Each request includes the requested map extent or source URL. Cached pages and output files are saved locally in the selected folder.

OSM data: © OpenStreetMap contributors, ODbL. Prawdom house-card content retains its original source rights; the plugin code license does not grant rights over external data.

The About window includes the user-supplied signature “made for personal and public use, 2024 · published 2026”. The current implementation was prepared in October 2026.

## Проверка

Tested with QGIS 3.42.1 on macOS: native ZIP installation and launch, empty-area validation, manual import, invalid-source rejection, rectangle capture, automatic context/address import and network cancellation. Native automatic test: 134 residential house cards, 54 catering points and 44 grocery points. A second rectangle produced 59 house cards, 37 catering points and 38 grocery points. Native manual test: 1,000 repeated URLs produced one house, not duplicate records.

Offline stress checks used 839 cached real cards, 10,000 duplicate URLs and 10,000 synthetic output rows. Verified clipping, multipolygon holes, persistent GeoPackage reopening and cancellation. These are not 10,000 live website requests. Other operating systems and QGIS versions have not yet been tested.

## License

Plugin code: GNU GPL version 2 or later. See LICENSE. No external Python dependencies beyond QGIS are required.
