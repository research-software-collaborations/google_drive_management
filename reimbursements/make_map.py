"""
Create an interactive Google Map from a list of location names.

Usage:
    python make_map.py --key YOUR_API_KEY --out map.html "MIT" "CERN" "INFN, Bari"

The script geocodes each location via the Maps Geocoding API and writes a
self-contained HTML file you can open in any browser.

Flags:
    --key       Google Maps API key (required)
    --out       Output HTML file (default: map.html)
    --title     Page title shown in the browser tab (default: Locations Map)
    locations   One or more location names as positional arguments
"""

import argparse
import json
import os
import sys
import requests

MAPS_KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "maps_api_key.txt")


def geocode(location, api_key):
    """Return (lat, lng, formatted_address) for a location string, or None on failure."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    r = requests.get(url, params={"address": location, "key": api_key})
    r.raise_for_status()
    data = r.json()
    if data["status"] != "OK" or not data["results"]:
        print(f"Warning: could not geocode '{location}' (status: {data['status']})", file=sys.stderr)
        return None
    result = data["results"][0]
    loc = result["geometry"]["location"]
    return loc["lat"], loc["lng"], result["formatted_address"]


def build_html(markers, title):
    markers_js = json.dumps([
        {"label": label, "lat": lat, "lng": lng, "address": addr}
        for label, lat, lng, addr in markers
    ])
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    body, html {{ margin: 0; padding: 0; height: 100%; }}
    #map {{ height: 100vh; width: 100%; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const MARKERS = {markers_js};

    const map = L.map("map");
    L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      attribution: "&copy; OpenStreetMap contributors"
    }}).addTo(map);

    const group = L.featureGroup();
    MARKERS.forEach(function(m) {{
      const marker = L.marker([m.lat, m.lng])
        .bindPopup("<strong>" + m.label + "</strong><br>" + m.address);
      marker.addTo(group);
    }});
    group.addTo(map);
    map.fitBounds(group.getBounds().pad(0.2));
  </script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Generate an interactive Google Map from location names")
    parser.add_argument("--key", default=None, help="Google Maps API key (default: read from maps_api_key.txt)")
    parser.add_argument("--out", default="map.html", help="Output HTML file (default: map.html)")
    parser.add_argument("--title", default="Locations Map", help="Page title")
    parser.add_argument("locations", nargs="+", help="Location names to map")
    args = parser.parse_args()

    if args.key is None:
        key_path = os.path.abspath(MAPS_KEY_FILE)
        try:
            with open(key_path) as f:
                args.key = f.read().strip()
        except FileNotFoundError:
            print(f"Error: no --key given and {key_path} not found.", file=sys.stderr)
            sys.exit(1)

    markers = []
    for loc in args.locations:
        result = geocode(loc, args.key)
        if result is None:
            continue
        lat, lng, address = result
        print(f"  {loc} -> {address} ({lat:.4f}, {lng:.4f})", file=sys.stderr)
        markers.append((loc, lat, lng, address))

    if not markers:
        print("Error: no locations could be geocoded.", file=sys.stderr)
        sys.exit(1)

    html = build_html(markers, args.title)
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Map saved: {args.out} ({len(markers)} location(s))", file=sys.stderr)


if __name__ == "__main__":
    main()
