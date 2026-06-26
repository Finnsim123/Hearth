# Connection logos

The nav shows clickable logos that open your data provider and InfluxDB. To use
the **official** brand logos, drop their SVG files here with these exact names:

| File | Get the official asset from |
|---|---|
| `home-assistant.svg` | Home Assistant brand assets — https://www.home-assistant.io/ (press/brand) or the `home-assistant/assets` repository |
| `influxdb.svg` | InfluxData brand assets — https://www.influxdata.com/ (brand/press kit) |

**Either `.svg` or `.png` works** — the loader tries `home-assistant.svg`, then
`home-assistant.png`, then a built-in mark (same for `influxdb`). SVG is preferred
(crisp at any size); PNG is fine if that's what you have. Served locally (no CDN,
in keeping with Hearth's local-first rule), so the links always work even with no
file present.

These are third-party trademarks, included only to link to your own instances of
those tools; follow each project's brand-usage guidelines. They are intentionally
not committed here.
